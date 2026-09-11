"""Bounded, deterministic database diagnosis fixtures for the local benchmark."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


SignalName = Literal["statements", "locks", "indexes", "vacuum", "table_stats"]
SIGNAL_NAMES: tuple[SignalName, ...] = (
    "statements", "locks", "indexes", "vacuum", "table_stats",
)
_SAFE_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")
_FILTER_VALUE = Annotated[str, Field(min_length=1, max_length=128)]
SIGNAL_FILTER_FIELDS: dict[SignalName, frozenset[str]] = {
    "statements": frozenset({"query_id", "database"}),
    "locks": frozenset({"pid", "relation", "state"}),
    "indexes": frozenset({"table", "index_name"}),
    "vacuum": frozenset({"table"}),
    "table_stats": frozenset({"table"}),
}
SIGNAL_ALLOWS_EMPTY_FILTERS: dict[SignalName, bool] = {
    signal: True for signal in SIGNAL_NAMES
}
_MAX_FIXTURE_BYTES = 128 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DatabaseInspectionFilters(_StrictModel):
    """All database-only filter fields; omitted and explicit null are distinct."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    relation: _FILTER_VALUE = None
    query_id: _FILTER_VALUE = None
    table: _FILTER_VALUE = None
    database: _FILTER_VALUE = None
    state: _FILTER_VALUE = None
    index_name: _FILTER_VALUE = None
    pid: _FILTER_VALUE = None


class DatabaseInspectionRequest(_StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scenario_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    signal: SignalName
    filters: DatabaseInspectionFilters
    limit: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def filters_match_signal(self) -> "DatabaseInspectionRequest":
        validate_database_signal_filters(self.signal, self.filters)
        return self


def validate_database_signal_filters(
    signal: SignalName,
    filters: DatabaseInspectionFilters,
) -> None:
    filter_values = filters.model_dump(exclude_none=True)
    disallowed = set(filter_values) - SIGNAL_FILTER_FIELDS[signal]
    if disallowed:
        raise ValueError("database_filter_not_allowed_for_signal")
    if not filter_values and not SIGNAL_ALLOWS_EMPTY_FILTERS[signal]:
        raise ValueError("database_filter_required_for_signal")


def database_inspection_contract(scenario_ids: list[str]) -> dict[str, Any]:
    """Return the public database-only contract used by models and resources."""

    return {
        "scenario_ids": list(scenario_ids),
        "signals": {
            signal: {
                "filter_fields": sorted(SIGNAL_FILTER_FIELDS[signal]),
                "allows_empty_filters": SIGNAL_ALLOWS_EMPTY_FILTERS[signal],
            }
            for signal in SIGNAL_NAMES
        },
        "filter_value": {"type": "string", "min_length": 1, "max_length": 128},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    }


def database_inspection_input_schema(scenario_ids: list[str]) -> dict[str, Any]:
    """Generate the persisted Tool schema from the runtime contract."""

    filter_properties = {
        field: {"type": "string", "minLength": 1, "maxLength": 128}
        for field in sorted({field for fields in SIGNAL_FILTER_FIELDS.values() for field in fields})
    }
    return {
        "type": "object",
        "required": ["scenario_id", "signal", "filters", "limit"],
        "properties": {
            "scenario_id": {"type": "string", "enum": list(scenario_ids)},
            "signal": {"type": "string", "enum": list(SIGNAL_NAMES)},
            "filters": {
                "type": "object",
                "properties": filter_properties,
                "additionalProperties": False,
                "maxProperties": 4,
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "additionalProperties": False,
        "x-signal-filter-fields": {
            signal: sorted(fields) for signal, fields in SIGNAL_FILTER_FIELDS.items()
        },
        "x-signal-allows-empty-filters": dict(SIGNAL_ALLOWS_EMPTY_FILTERS),
    }


class ExpectedDiagnosis(_StrictModel):
    root_cause_id: str = Field(min_length=1, max_length=128)
    aliases: tuple[str, ...] = ()
    secondary_causes: tuple[str, ...] = ()
    required_signals: tuple[SignalName, ...] = ()
    requires_followup_signals: tuple[SignalName, ...] = ()
    rationale: str = Field(min_length=1, max_length=1000)


class DatabaseScenario(_StrictModel):
    id: str
    title: str = Field(min_length=1, max_length=128)
    task: str = Field(min_length=1, max_length=2000)
    directory: str
    complexity: Literal["legacy", "coordination"] = "legacy"
    tool_budget: int = Field(default=5, ge=1, le=10)
    available_signals: tuple[SignalName, ...]
    expected: ExpectedDiagnosis


class SignalInspection(_StrictModel):
    scenario_id: str
    signal: SignalName
    rows: tuple[dict[str, Any], ...]
    evidence_id: str
    source: Literal["local_fixture"] = "local_fixture"


class DatabaseScenarioCatalog:
    """Load a fixed JSONL manifest and expose observations without answer labels."""

    def __init__(self, fixture_root: Path, scenarios: list[DatabaseScenario]) -> None:
        self._root = fixture_root
        self._ordered = tuple(scenarios)
        self._by_id = {item.id: item for item in scenarios}

    @classmethod
    def from_root(cls, root: Path) -> "DatabaseScenarioCatalog":
        fixture_root = (Path(root) / "mock-data" / "agentteams-benchmark" / "database").resolve()
        manifest = fixture_root / "scenarios.jsonl"
        try:
            raw = manifest.read_bytes()
            if not raw or len(raw) > _MAX_FIXTURE_BYTES:
                raise ValueError
            documents = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
            if not 1 <= len(documents) <= 64:
                raise ValueError
            scenarios: list[DatabaseScenario] = []
            for document in documents:
                if not isinstance(document, dict):
                    raise ValueError
                scenario_id = document.get("id")
                directory = document.get("directory")
                if not isinstance(scenario_id, str) or not _SAFE_ID.fullmatch(scenario_id):
                    raise ValueError
                if directory != scenario_id:
                    raise ValueError
                scenario_root = (fixture_root / directory).resolve()
                if scenario_root.parent != fixture_root or not scenario_root.is_dir():
                    raise ValueError
                expected = cls._read_json(scenario_root / "expected.json")
                for signal in SIGNAL_NAMES:
                    rows = cls._read_json(scenario_root / f"{signal}.json")
                    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                        raise ValueError
                scenarios.append(DatabaseScenario.model_validate({
                    **document, "available_signals": SIGNAL_NAMES, "expected": expected,
                }))
            if len({item.id for item in scenarios}) != len(scenarios):
                raise ValueError
            return cls(fixture_root, scenarios)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError, TypeError):
            raise ValueError("invalid_database_scenario_catalog") from None

    @staticmethod
    def _read_json(path: Path) -> Any:
        raw = path.read_bytes()
        if not raw or len(raw) > _MAX_FIXTURE_BYTES:
            raise ValueError
        return json.loads(raw.decode("utf-8"))

    def ids(self) -> list[str]:
        return [item.id for item in self._ordered]

    def root_cause_taxonomy(self) -> tuple[str, ...]:
        """Return the public canonical labels shared by every benchmark mode."""
        return tuple(sorted({item.expected.root_cause_id for item in self._ordered}))

    def get(self, scenario_id: str) -> DatabaseScenario:
        try:
            return self._by_id[scenario_id]
        except (KeyError, TypeError):
            raise ValueError("invalid_database_inspection") from None

    def inspect(
        self,
        scenario_id: str,
        signal: SignalName,
        *,
        filters: dict[str, str],
        limit: int,
    ) -> SignalInspection:
        request = self.validate_inspection_request(
            scenario_id, signal, filters=filters, limit=limit,
        )
        normalized_filters = request.filters.model_dump(exclude_none=True)
        fixture_path = self._root / scenario_id / f"{signal}.json"
        try:
            raw = fixture_path.read_bytes()
            if not raw or len(raw) > _MAX_FIXTURE_BYTES:
                raise ValueError
            rows = json.loads(raw.decode("utf-8"))
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError
            selected = [
                row for row in rows
                if all(str(row.get(key, "")) == value for key, value in normalized_filters.items())
            ][:limit]
            filter_json = json.dumps(normalized_filters, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = sha256(raw + b"\0" + filter_json.encode("utf-8")).hexdigest()[:16]
            return SignalInspection(
                scenario_id=scenario_id,
                signal=signal,
                rows=tuple(selected),
                evidence_id=f"db:{scenario_id}:{signal}:{digest}",
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError, TypeError):
            raise ValueError("invalid_database_inspection") from None

    def validate_inspection_request(
        self,
        scenario_id: str,
        signal: SignalName,
        *,
        filters: dict[str, str],
        limit: int,
    ) -> DatabaseInspectionRequest:
        try:
            request = DatabaseInspectionRequest.model_validate({
                "scenario_id": scenario_id,
                "signal": signal,
                "filters": filters,
                "limit": limit,
            })
        except ValidationError as exc:
            for error in exc.errors():
                message = str(error.get("ctx", {}).get("error", ""))
                if "database_filter_not_allowed_for_signal" in message:
                    raise ValueError("database_filter_not_allowed_for_signal") from None
                if "database_filter_required_for_signal" in message:
                    raise ValueError("database_filter_required_for_signal") from None
            raise ValueError("invalid_database_inspection") from None
        scenario = self._by_id.get(request.scenario_id)
        if scenario is None:
            raise ValueError("invalid_database_inspection")
        if request.signal not in scenario.available_signals:
            raise ValueError("database_signal_not_available_for_scenario")
        return request
