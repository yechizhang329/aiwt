"""Thin HTTP client for FastAPI backend calls from Streamlit pages.

Uses a webapp_service account (admin role) so Streamlit does not need to
store per-user plaintext passwords. The submitting doctor's identity is
recorded in the case payload (submitter.doctor_id field).
"""

import os
from pathlib import Path

import requests
import streamlit as st

_BASE_URL = os.environ.get("BACKEND_API_URL", "http://127.0.0.1:8600")


class SufficiencyError(Exception):
    """Raised when backend returns 422 needs_more_info (Change 20 sufficiency gate)."""
    pass
_CREDS_FILE = Path(__file__).parent.parent / "backend" / "secure" / "webapp_service_token.txt"
_TOKEN_KEY = "_api_token"


def _load_creds() -> tuple[str, str]:
    lines = [l.strip() for l in _CREDS_FILE.read_text().splitlines()
             if l.strip() and not l.startswith("#")]
    return lines[0], lines[1]


def _fetch_token(username: str, password: str) -> str:
    r = requests.post(
        f"{_BASE_URL}/v1/auth/token",
        data={"username": username, "password": password},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def get_token() -> str:
    """Return cached JWT, refreshing from service account creds if absent."""
    if _TOKEN_KEY not in st.session_state:
        u, p = _load_creds()
        st.session_state[_TOKEN_KEY] = _fetch_token(u, p)
    return st.session_state[_TOKEN_KEY]


def _auth() -> dict:
    return {"Authorization": f"Bearer {get_token()}"}


def upload_attachment(file_bytes: bytes, filename: str, content_type: str) -> str:
    """Upload a file to /v1/attachment and return its attachment_id."""
    r = requests.post(
        f"{_BASE_URL}/v1/attachment",
        headers=_auth(),
        files={"file": (filename, file_bytes, content_type)},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["attachment_id"]


def submit_case(payload: dict) -> dict:
    """POST /v1/case — returns {case_id, trace_id, status}."""
    r = requests.post(
        f"{_BASE_URL}/v1/case",
        headers=_auth(),
        json=payload,
        timeout=30,
    )
    if r.status_code == 422:
        try:
            detail = r.json().get("detail", {})
            if isinstance(detail, dict) and detail.get("code") == "needs_more_info":
                raise SufficiencyError(detail.get("message", "提交内容不完整"))
        except SufficiencyError:
            raise
        except Exception:
            pass
    r.raise_for_status()
    return r.json()


def list_cases(scene: str = None, limit: int = 50) -> list:
    """GET /v1/case — list cases for current service account (admin sees all)."""
    params = {"limit": limit}
    if scene:
        params["scene"] = scene
    r = requests.get(f"{_BASE_URL}/v1/case", headers=_auth(), params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def get_case(case_id: str) -> dict:
    """GET /v1/case/{case_id} — returns full CaseStatusResponse."""
    r = requests.get(
        f"{_BASE_URL}/v1/case/{case_id}",
        headers=_auth(),
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def get_case_audit(case_id: str) -> dict:
    """GET /v1/case/{case_id}/audit — returns audit trace entries."""
    r = requests.get(
        f"{_BASE_URL}/v1/case/{case_id}/audit",
        headers=_auth(),
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def delete_case(case_id: str) -> dict:
    """DELETE /v1/case/{case_id} — admin only hard delete."""
    r = requests.delete(
        f"{_BASE_URL}/v1/case/{case_id}",
        headers=_auth(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def get_kpi(range: str = "today") -> dict:
    """GET /v1/audit/kpi — aggregate KPI metrics. range=today|7d."""
    r = requests.get(
        f"{_BASE_URL}/v1/audit/kpi",
        headers=_auth(),
        params={"range": range},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def get_stage_info(case_id: str) -> dict:
    """GET /v1/case/{case_id} — return stage_info dict for STAGE_INFO polling."""
    case = get_case(case_id)
    return case.get("stage_info") or {}


def get_attachment_bytes(attachment_id: str) -> bytes:
    """GET /v1/attachment/{id} — returns raw file bytes for inline rendering."""
    r = requests.get(
        f"{_BASE_URL}/v1/attachment/{attachment_id}",
        headers=_auth(),
        timeout=30,
    )
    r.raise_for_status()
    return r.content


def retry_case(case_id: str) -> dict:
    """POST /v1/case/{case_id}/retry — re-queue a failed case."""
    r = requests.post(
        f"{_BASE_URL}/v1/case/{case_id}/retry",
        headers=_auth(),
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def abort_case(case_id: str) -> dict:
    """POST /v1/case/{case_id}/abort — cancel an in-flight case."""
    r = requests.post(
        f"{_BASE_URL}/v1/case/{case_id}/abort",
        headers=_auth(),
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def escalate_case(case_id: str, reason: str = "") -> dict:
    """POST /v1/case/{case_id}/escalate — mark case for human review."""
    r = requests.post(
        f"{_BASE_URL}/v1/case/{case_id}/escalate",
        headers=_auth(),
        params={"reason": reason},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()
