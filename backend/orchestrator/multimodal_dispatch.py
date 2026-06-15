"""Multimodal image_refs utilities for v2 Slock dispatch.

Passes image metadata to agents; each agent reads and base64-encodes locally.
Dispatch payloads stay small — no base64 data in Slock DMs.
"""

from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import db


def load_image_refs_for_case(case_payload: dict) -> list[dict]:
    """Build image_refs list from a case payload's attachment_ids.

    Returns metadata-only refs (no base64 data) — agents read storage_path locally:
      {"ref_id": str, "storage_path": str, "mime_type": str, "label": str, "attachment_id": str}
    """
    attachment_ids = case_payload.get("attachment_ids") or []
    # Scene 3: also include imaging_provided attachment_ids
    for item in (case_payload.get("imaging_provided") or []):
        if isinstance(item, dict):
            aid = item.get("attachment_id")
            if aid and aid not in attachment_ids:
                attachment_ids.append(aid)

    refs = []
    for i, aid in enumerate(attachment_ids):
        meta = db.get_attachment(aid)
        if meta is None:
            continue
        mime = meta.get("mime_type", "image/jpeg")
        if not mime.startswith("image/"):
            continue
        storage_path = meta.get("storage_path", "")
        if not Path(storage_path).exists():
            continue
        refs.append({
            "ref_id": f"img_{i:03d}",
            "storage_path": storage_path,
            "mime_type": mime,
            "label": meta.get("filename", f"image_{i}"),
            "attachment_id": aid,
        })
    return refs


def build_image_blocks(image_refs: list[dict]) -> list[dict[str, Any]]:
    """Convert image_refs list into Anthropic API content blocks."""
    blocks = []
    for ref in image_refs:
        if ref["type"] == "base64":
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": ref["media_type"],
                    "data": ref["data"],
                },
            })
        elif ref["type"] == "presigned_url":
            blocks.append({
                "type": "image",
                "source": {"type": "url", "url": ref["url"]},
            })
    return blocks
