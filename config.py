"""
Central configuration for Scene 1 web app.
All constants and secrets live here.
Secrets read from environment; safe defaults for local dev only.
"""

import os
import secrets
from pathlib import Path

# --- Paths ---
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "jobs.db"
UPLOAD_DIR = BASE_DIR / "uploads"
USERS_YAML = BASE_DIR / "config" / "users.yaml"

# --- Auth ---
# SCENE1_COOKIE_KEY must be set in production via environment variable.
# Falls back to a random key for local dev (sessions won't survive restarts).
COOKIE_KEY: str = os.environ.get("SCENE1_COOKIE_KEY") or secrets.token_hex(32)
COOKIE_NAME: str = "scene1_auth"
COOKIE_EXPIRY_DAYS: int = 7

# --- Slock integration ---
DENTIST_HANDLE: str = "@DentistWang"
SUBMISSION_TAG: str = "[Scene 1 case submission v1] [立即处理]"
SUPPLEMENT_TAG: str = "[Scene 1 supplement v1] [立即处理]"
COMPLETED_TAG: str = "[Scene 1 cowork completed v1]"
SUFFICIENCY_TAG: str = "[Scene 1 sufficiency check v1]"

# --- Polling ---
POLL_INTERVAL_SECONDS: int = 20

# --- Job timeouts ---
JOB_TIMEOUT_MINUTES: int = 30  # processing → timeout if no reply after this

# --- File upload limits ---
MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB
MAX_ATTACHMENTS: int = 20
MAX_IMAGE_SIDE: int = 2048  # px, PIL compress long side
