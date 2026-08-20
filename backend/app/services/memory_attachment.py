"""附件记忆（Step 7）：上传文本文件，提取摘要，存 MemoryEntry。

PoC 范围：仅支持 .txt/.md 等文本文件；PDF/Word 解析后续扩展。
文件存 backend/storage/（.gitignore 已忽略）；摘要存 MemoryEntry（kind=attachment）。
"""

from pathlib import Path

from sqlalchemy.orm import Session

from .. import models
from .llm import DeepSeekProvider

# backend/storage/ 目录
STORAGE_DIR = Path(__file__).resolve().parents[2] / "storage"


def _summarize_text(text: str) -> str:
    """生成文本摘要：优先 LLM，无密钥/失败时降级为取前 200 字。"""
    try:
        provider = DeepSeekProvider()
        resp = provider.chat(
            [{"role": "user", "content": f"请用一段话（不超过 100 字）概括以下文本的要点：\n{text[:3000]}", "source": "demo"}]
        )
        summary = (resp.content or "").strip()
        if summary:
            return summary[:200]
    except Exception:
        pass
    # 降级：取前 200 字
    return text.strip()[:200] or "（空文件）"


def save_attachment(
    db: Session,
    *,
    subject_no: str,
    filename: str,
    content: str,
) -> models.MemoryEntry:
    """保存附件记忆：文件存 storage/，摘要存 MemoryEntry（kind=attachment）。"""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    file_path = STORAGE_DIR / f"{subject_no}_{safe_name}"
    file_path.write_text(content, encoding="utf-8")

    summary = _summarize_text(content)
    entry = models.MemoryEntry(
        subject_type="human",
        subject_no=subject_no,
        kind="attachment",
        content=summary,
        content_type="text",
        file_ref=str(file_path),
        visibility="personal",
        data_level="L1",
        lifecycle="active",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
