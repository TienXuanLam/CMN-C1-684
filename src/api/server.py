"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here.
# For platform-level routing, AgentGateway calls agent.invoke() directly.

import os
import secrets
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from framework.utils.config_loader import load_config
from shared.secrets import factory as secrets_factory
from src.graph.graph import AgentHealthResourceMonitorAgent

app = FastAPI(title="AgentHealthResourceMonitorAgent")


class _DeterministicMetricSource:
    """Explicit STG-only adapter; production never silently falls back to it."""

    _VALUES = {"cpu": 12.5, "memory": 35.0, "disk": 42.0, "gpu": 5.0}

    def collect(self, metric: str, _target_id: str) -> dict[str, Any]:
        return {"value": self._VALUES[metric], "unit": "%"}


_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
_config = load_config(str(_CONFIG_PATH)) if _CONFIG_PATH.exists() else {}
if os.environ.get("STG_MOCK_MODE", "").lower() == "true":
    _config["metric_source"] = _DeterministicMetricSource()

agent = AgentHealthResourceMonitorAgent(config=_config)
agent.compile()
agent.provision_secrets(secrets_factory(namespace="cmn", agent_name="cmn-c1-684"))


class InvokeRequest(BaseModel):
    input: str = Field(default="", max_length=2000)
    session_id: str = ""
    # Required by PreProcessNode: query_intent, target_context, target_id
    # (proposal §4); optional threshold_config/output_format. Without this,
    # every request would fail PreProcessNode's S-2 domain validation.
    input_context: dict[str, Any] = Field(default_factory=dict)


def _bearer_matches(supplied: str, expected: str) -> bool:
    return secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode())


def _resolve_standalone_trust(
    current: TrustLevel,
    authorization: str,
    invoke_auth_token: str | None,
    internal_runner_token: str | None,
) -> TrustLevel:
    if current is not TrustLevel.ANONYMOUS:
        return current
    if internal_runner_token and _bearer_matches(authorization, internal_runner_token):
        return TrustLevel.INTERNAL
    if invoke_auth_token and _bearer_matches(authorization, invoke_auth_token):
        return TrustLevel.VERIFIED_EXTERNAL
    if internal_runner_token or invoke_auth_token:
        raise HTTPException(status_code=401, detail="Token is invalid or expired.")
    return TrustLevel.ANONYMOUS


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> dict[str, Any]:
    trust = _resolve_standalone_trust(
        getattr(request.state, "trust_level", TrustLevel.ANONYMOUS),
        request.headers.get("authorization", ""),
        os.environ.get("INVOKE_AUTH_TOKEN"),
        os.environ.get("STG_INTERNAL_RUNNER_TOKEN"),
    )
    # Standalone/STG caller auth: when
    # INVOKE_AUTH_TOKEN is set on the server environment, callers that no upstream
    # middleware vouched for (still ANONYMOUS) must present it as a Bearer token
    # and run at VERIFIED_EXTERNAL. Middleware-established trust is never demoted.
    # This adapter is the entry-point auth boundary (standalone equivalent of
    # platform AuthMiddleware) — a deployment-level caller credential, not an
    # agent secret, so ctx.secrets does not apply (no InvocationContext exists
    # before auth).
    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        return cast("dict[str, Any]", agent.invoke(req.input, ctx=ctx, input_context=req.input_context))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent": "AgentHealthResourceMonitorAgent"}
