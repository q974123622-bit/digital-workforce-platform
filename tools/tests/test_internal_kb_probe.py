from __future__ import annotations

import io
import json
import socket
import unittest
from contextlib import redirect_stdout
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from tools.internal_kb_probe import (
    ENV_AUTHORIZATION,
    ENV_BASE_URL,
    ENV_X_ORG,
    ENV_X_TENANT,
    ENV_X_USER,
    ProbeConfig,
    ProbeError,
    list_knowledge_bases,
    print_retrieval_results,
    retrieve_chunks,
)


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.body


def config() -> ProbeConfig:
    return ProbeConfig(
        base_url="https://internal.invalid",
        x_org="org-value",
        x_tenant="tenant-value",
        x_user="user-value",
        authorization="secret-auth-value",
    )


class ProbeConfigTests(unittest.TestCase):
    def test_requires_all_environment_variables(self):
        with self.assertRaisesRegex(ProbeError, ENV_AUTHORIZATION):
            ProbeConfig.from_environment(
                {
                    ENV_BASE_URL: "https://internal.invalid",
                    ENV_X_ORG: "org-value",
                    ENV_X_TENANT: "tenant-value",
                    ENV_X_USER: "user-value",
                }
            )


class RequestTests(unittest.TestCase):
    def test_list_uses_documented_read_only_parameters(self):
        captured = {}

        def opener(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse(
                {"code": 0, "data": {"items": [{"id": 7, "name": "IT", "doc_num": 3}]}}
            )

        items = list_knowledge_bases(config(), "IT服务", opener=opener)

        request = captured["request"]
        query = parse_qs(urlparse(request.full_url).query)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(query["keywords"], ["IT服务"])
        self.assertEqual(query["page"], ["1"])
        self.assertEqual(query["page_size"], ["150"])
        self.assertEqual(query["strict"], ["true"])
        self.assertEqual(query["filter_system_created"], ["true"])
        self.assertEqual(items[0]["id"], 7)

    def test_retrieve_always_enables_filters(self):
        captured = {}

        def opener(request, timeout):
            captured["request"] = request
            return FakeResponse({"code": 0, "data": {"total": 0, "chunks": []}})

        retrieve_chunks(config(), 123, "VPN申请步骤", opener=opener)

        request = captured["request"]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(payload["kb_id"], 123)
        self.assertEqual(payload["question"], "VPN申请步骤")
        self.assertIs(payload["enable_filters"], True)
        self.assertEqual(payload["top_k"], 10)
        self.assertEqual(payload["top_n"], 5)

    def test_http_errors_do_not_expose_credentials(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 401, "Unauthorized secret-auth-value", {}, None)

        with self.assertRaises(ProbeError) as caught:
            list_knowledge_bases(config(), opener=opener)

        message = str(caught.exception)
        self.assertIn("HTTP 401", message)
        self.assertNotIn("secret-auth-value", message)
        self.assertNotIn("org-value", message)

    def test_timeout_has_clear_error(self):
        def opener(request, timeout):
            raise URLError(socket.timeout())

        with self.assertRaisesRegex(ProbeError, "请求超时"):
            list_knowledge_bases(config(), opener=opener)

    def test_business_error_is_reported_without_headers(self):
        def opener(request, timeout):
            return FakeResponse(
                {"code": 1002, "msg": "知识库不可访问 secret-auth-value tenant-value"}
            )

        with self.assertRaises(ProbeError) as caught:
            retrieve_chunks(config(), 123, "问题", opener=opener)

        message = str(caught.exception)
        self.assertIn("code=1002", message)
        self.assertIn("知识库不可访问", message)
        self.assertNotIn("secret-auth-value", message)
        self.assertNotIn("tenant-value", message)


class OutputTests(unittest.TestCase):
    def test_retrieval_output_prefers_rank_score_and_truncates_snippet(self):
        output = io.StringIO()
        with redirect_stdout(output):
            print_retrieval_results(
                [
                    {
                        "docnm_kwd": "VPN指南.pdf",
                        "rank_score": 0.91,
                        "similarity": 0.72,
                        "content_with_weight": "片段" * 200,
                    }
                ]
            )

        rendered = output.getvalue()
        self.assertIn("rank_score", rendered)
        self.assertIn("0.91", rendered)
        self.assertNotIn("0.72", rendered)
        self.assertLess(len(rendered), 500)

    def test_retrieval_output_falls_back_to_similarity(self):
        output = io.StringIO()
        with redirect_stdout(output):
            print_retrieval_results(
                [{"docnm_kwd": "指南.pdf", "rank_score": None, "similarity": 0.72}]
            )

        self.assertIn("分数(similarity): 0.72", output.getvalue())


if __name__ == "__main__":
    unittest.main()