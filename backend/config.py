import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).parent
REPO_ROOT = BASE_DIR.parent
WEBAPP_DIR = REPO_ROOT / "webapp"

# Auth
JWT_SECRET: str = os.environ.get("BACKEND_JWT_SECRET") or secrets.token_hex(32)
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRE_MINUTES: int = int(os.environ.get("JWT_EXPIRE_MINUTES", "10080"))  # 7 days

USERS_YAML: Path = Path(os.environ.get("USERS_YAML_PATH", str(WEBAPP_DIR / "config" / "users.yaml")))

# Storage
DB_PATH: Path = Path(os.environ.get("BACKEND_DB_PATH", str(BASE_DIR / "data" / "cases.db")))
GBRAIN_DB_PATH: Path = Path(os.environ.get("GBRAIN_DB_PATH", str(BASE_DIR / "data" / "gbrain.db")))
TRACK1_DB_PATH: Path = Path(os.environ.get("TRACK1_DB_PATH", str(BASE_DIR / "data" / "track1.db")))

# IX-2.1 ensemble reads: 0 = off (default), 2 = 2 independent full IR reads with disagreement-escalate
ENSEMBLE_IR_READS: int = int(os.environ.get("ENSEMBLE_IR_READS", "0"))

# IX-2.2~2.6 + IX-3 interaction state machine:
#   IX_ENABLED=False (default OFF). Set IX_ENABLED=1 to activate.
#   Loop: HYPOTHESIZE→DISPATCH→GATHER→CONVERGENCE up to IX_MAX_ROUNDS.
#   Without KBadvisor T1-1 edges, all falsification_check stubs return "unknown" → always escalates.
IX_ENABLED: bool = bool(int(os.environ.get("IX_ENABLED", "0")))
IX_MAX_ROUNDS: int = int(os.environ.get("IX_MAX_ROUNDS", "3"))
# V4 cleanup: Stage D Critic can be rollback-enabled with STAGE_D_CRITIC_ENABLED=1.
# Default OFF removes @Critic dispatch/wait while preserving neutral downstream behavior.
STAGE_D_CRITIC_ENABLED: bool = bool(int(os.environ.get("STAGE_D_CRITIC_ENABLED", "0")))
# V4 diagnosis-accuracy spec §5/§8 (task #140): inject positive-diagnosis card
# DEFAULT-projection advisory context into Stage C, matched by the v4 sagittal gate.
# DEFAULT OFF (David 2026-06-11 dev directive frozen_boundary): wiring is written but
# has ZERO live-user effect until the unified test passes + human review. When OFF the
# Stage C payload is byte-identical to current. Set POSITIVE_DIAGNOSIS_ADVISORY_ENABLED=1
# only in the offline unified-test harness.
POSITIVE_DIAGNOSIS_ADVISORY_ENABLED: bool = bool(
    int(os.environ.get("POSITIVE_DIAGNOSIS_ADVISORY_ENABLED", "0"))
)
UPLOAD_DIR: Path = Path(os.environ.get("UPLOAD_DIR", str(BASE_DIR / "uploads")))

# Read-tool advisory: HRNet ceph landmark detection + measurement stage.
# DEFAULT OFF. Runs as shadow (stage_info only, no downstream effect) when ON.
# Live-enable gated on: Walter +5 digital-fix confirm + David wiring approval.
READ_TOOL_ADVISORY_ENABLED: bool = bool(
    int(os.environ.get("READ_TOOL_ADVISORY_ENABLED", "0"))
)

# Audit log (reuse DentistWang's audit infrastructure path)
DENTIST_WORKSPACE: Path = Path(os.environ.get(
    "DENTIST_WORKSPACE",
    "/Users/aidoc/.slock/agents/54a301b5-268d-43d5-a9b9-2a991f84fa42"
))
AUDIT_LOG_DIR: Path = DENTIST_WORKSPACE / "notes" / "audit" / "agent_cowork"
ORCHESTRATOR_SCRIPTS: Path = Path(os.environ.get(
    "ORCHESTRATOR_SCRIPTS",
    str(REPO_ROOT / "runtime_scripts"),
))

# Sprint 2: Slock identity used by SlockCLIAdapter as `from_agent_id`.
# Default: @WebAppDev (Option B fallback until @SlimOrchestrator Slock agent is created).
# Switch to "@SlimOrchestrator" once admin creates that agent identity.
ORCHESTRATOR_FROM_AGENT: str = os.environ.get("ORCHESTRATOR_FROM_AGENT", "@WebAppDev")
# Per-agent dispatch timeout. Default 720s (Critic 5-12 min per interface; Scene 3 TMD ~486s+ observed).
# Phase C (Critic) is heaviest due to independent KB re-anchor of all prior phase payloads.
ORCHESTRATOR_DISPATCH_TIMEOUT_SEC: int = int(os.environ.get("ORCHESTRATOR_DISPATCH_TIMEOUT_SEC", "720"))
# Change 29: Hard SLA timeout for processing cases. Watchdog scans every 5 min.
ORCHESTRATOR_HARD_TIMEOUT_SEC: int = int(os.environ.get("ORCHESTRATOR_HARD_TIMEOUT_SEC", "1800"))
