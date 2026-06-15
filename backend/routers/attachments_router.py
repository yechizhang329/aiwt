"""
Attachment upload/download endpoints — Sprint 4.
Storage: LocalFSAttachmentStore (swappable to S3 in Phase 2.5+).
RBAC: upload = any role except audit_agent; download = uploader + admin.
"""

import os
import stat
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse

import auth
import config
import db
from models import AttachmentUploadResponse

router = APIRouter(prefix="/v1/attachment", tags=["attachments"])

MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


# ── AttachmentStore interface (Phase 2.5+: swap LocalFS → S3) ────────────────

class AttachmentStore(ABC):
    @abstractmethod
    def put(self, attachment_id: str, file_bytes: bytes, mime_type: str, filename: str) -> str:
        """Store file and return storage path."""

    @abstractmethod
    def get(self, attachment_id: str, storage_path: str) -> tuple[bytes, str]:
        """Return (bytes, resolved_path_str) for FileResponse."""

    @abstractmethod
    def delete(self, attachment_id: str, storage_path: str) -> bool:
        """Delete file. Returns True if deleted."""


class LocalFSAttachmentStore(AttachmentStore):
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def put(self, attachment_id: str, file_bytes: bytes, mime_type: str, filename: str) -> str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        day_dir = self.base_dir / date_str
        day_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(filename).suffix or ""
        path = day_dir / f"{attachment_id}{ext}"
        path.write_bytes(file_bytes)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        return str(path)

    def get(self, attachment_id: str, storage_path: str) -> tuple[bytes, str]:
        path = Path(storage_path)
        if not path.exists():
            raise FileNotFoundError(attachment_id)
        return path.read_bytes(), str(path)

    def delete(self, attachment_id: str, storage_path: str) -> bool:
        path = Path(storage_path)
        if path.exists():
            path.unlink()
            return True
        return False


_store = LocalFSAttachmentStore(config.UPLOAD_DIR)


# ── RBAC helpers ──────────────────────────────────────────────────────────────

def require_upload_allowed(user: dict = Depends(auth.get_current_user)) -> dict:
    if user.get("role") == "audit_agent":
        raise HTTPException(status_code=403, detail="audit_agent role cannot upload attachments")
    return user


def _require_download_allowed(attachment_id: str, user: dict) -> dict:
    """Admin can download any attachment; others only their own uploads."""
    if user.get("role") == "admin":
        return user
    meta = db.get_attachment(attachment_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if meta["uploaded_by"] != user["username"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return user


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=AttachmentUploadResponse, status_code=201)
async def upload_attachment(
    file: UploadFile,
    user: dict = Depends(require_upload_allowed),
):
    data = await file.read()
    if len(data) > MAX_SIZE_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large (max {MAX_SIZE_BYTES // 1024 // 1024}MB)")

    attachment_id = str(uuid.uuid4())
    filename = file.filename or "upload"
    mime_type = file.content_type or "application/octet-stream"

    storage_path = _store.put(attachment_id, data, mime_type, filename)
    db.create_attachment(
        attachment_id=attachment_id,
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(data),
        uploaded_by=user["username"],
        storage_path=storage_path,
    )

    return AttachmentUploadResponse(
        attachment_id=attachment_id,
        url=f"/v1/attachment/{attachment_id}",
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(data),
    )


@router.get("/{attachment_id}/presigned")
async def presigned_attachment_url(
    attachment_id: str,
    user: dict = Depends(auth.get_current_user),
):
    """Return the download URL for an attachment (for multimodal dispatch)."""
    meta = db.get_attachment(attachment_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    _require_download_allowed(attachment_id, user)
    return {"attachment_id": attachment_id, "url": f"/v1/attachment/{attachment_id}",
            "mime_type": meta["mime_type"], "filename": meta["filename"]}


@router.get("/{attachment_id}")
async def download_attachment(
    attachment_id: str,
    user: dict = Depends(auth.get_current_user),
):
    meta = db.get_attachment(attachment_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    _require_download_allowed(attachment_id, user)

    try:
        _, resolved_path = _store.get(attachment_id, meta["storage_path"])
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Attachment file missing from storage")

    return FileResponse(
        resolved_path,
        media_type=meta["mime_type"],
        filename=meta["filename"],
    )
