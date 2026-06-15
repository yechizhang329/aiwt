"""
Legacy bridge: FastAPI backend -> SlimOrchestrator + SlockCLIAdapter.

This file is quarantined for historical reference only. The current runtime
uses `orchestrator.v2_orchestrator.run_v2`; the old `SlimOrchestrator.py`
dependency is intentionally not part of this source-of-truth branch.
"""
import sys
import config

# SlimOrchestrator and its deps (agent_schema, audit_log, HardRuleWrapper)
# all live under ORCHESTRATOR_SCRIPTS. Add that dir + the adapters subdir.
_scripts = str(config.ORCHESTRATOR_SCRIPTS)
_adapters = str(config.ORCHESTRATOR_SCRIPTS / "adapters")
for _p in (_scripts, _adapters):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from SlimOrchestrator import Orchestrator  # noqa: E402
from slock_cli_adapter import SlockCLIAdapter  # noqa: E402


def make_orchestrator() -> Orchestrator:
    adapter = SlockCLIAdapter(
        from_agent_id=config.ORCHESTRATOR_FROM_AGENT,
        dispatch_timeout=config.ORCHESTRATOR_DISPATCH_TIMEOUT_SEC,
    )
    return Orchestrator(adapter=adapter)
