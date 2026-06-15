from fastapi import APIRouter
from models import HealthResponse

router = APIRouter(tags=["meta"])


@router.get("/v1/health", response_model=HealthResponse)
async def health():
    return HealthResponse()


@router.get("/v1/meta/agents")
async def meta_agents():
    """Sprint 2+: query SlimOrchestrator for live agent status."""
    return {
        "agents": [
            {"name": "SlimOrchestrator", "status": "pending_integration"},
            {"name": "Clinician", "status": "pending_integration"},
            {"name": "KC", "status": "pending_integration"},
            {"name": "CM", "status": "pending_integration"},
            {"name": "Critic", "status": "pending_integration"},
            {"name": "VoiceWrapper", "status": "pending_integration"},
        ],
        "note": "Live status available in Sprint 2",
    }


@router.get("/v1/meta/schema")
async def meta_schema():
    """Returns envelope + payload schema reference."""
    import config
    schema_path = config.ORCHESTRATOR_SCRIPTS / "agent_schema.py"
    return {
        "schema_source": str(schema_path),
        "note": "See DentistWang scripts/agent_schema.py for envelope + payload schemas",
    }
