# CMN-C1-684 — Integration Tests: full graph compile + invoke
#
# Requires the `framework`/`shared` packages (agenticstar-agentcore wheel).
# No secrets binding needed — this template declares requires.secrets: []
# (config/agent.yaml) and no code path under test calls ctx.secrets.require().

import json

import pytest
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import AgentHealthResourceMonitorAgent


@pytest.fixture
def agent():
    a = AgentHealthResourceMonitorAgent(config={"max_retry": 1})
    a.compile()
    return a


def _ctx(trust_level: TrustLevel = TrustLevel.VERIFIED_EXTERNAL) -> InvocationContext:
    return InvocationContext(session_id="integration-test-session", caller_trust_level=trust_level)


def test_full_pipeline_success_baremetal(agent):
    result = agent.invoke(
        "Check host health.",
        ctx=_ctx(),
        input_context={
            "query_intent": ["cpu", "memory", "disk"],
            "target_context": "baremetal",
            "target_id": "localhost",
            "threshold_config": {"warn_cpu_pct": 50, "alert_cpu_pct": 90},
            "output_format": "json",
        },
    )

    assert result["status"] == "success"
    assert result["output"] is not None
    assert "InitializeNode" in result["node_history"]
    assert "PreProcessNode" in result["node_history"]
    assert "MainNode" in result["node_history"]
    assert "PostProcessNode" in result["node_history"]
    assert "FinalizeNode" in result["node_history"]

    report = json.loads(result["output"])
    assert report["overall_status"] in ("NORMAL", "WARN", "ALERT")
    assert "cpu" in report["metrics"]
    assert "memory" in report["metrics"]
    assert "disk" in report["metrics"]


def test_full_pipeline_markdown_output_format(agent):
    result = agent.invoke(
        "Check host health.",
        ctx=_ctx(),
        input_context={
            "query_intent": ["cpu"],
            "target_context": "baremetal",
            "target_id": "localhost",
            "output_format": "markdown",
        },
    )

    assert result["status"] == "success"
    assert result["output"].startswith("# Resource Health Report")


def test_full_pipeline_gpu_na_on_non_nvidia_host(agent):
    """This test environment has no NVIDIA GPU — gpu must degrade to N/A,
    not fail the whole invocation (proposal §11 Risk #5)."""
    result = agent.invoke(
        "Check GPU health.",
        ctx=_ctx(),
        input_context={
            "query_intent": ["gpu"],
            "target_context": "baremetal",
            "target_id": "localhost",
        },
    )

    assert result["status"] == "success"
    report = json.loads(result["output"])
    assert report["metrics"]["gpu"]["status"] == "N/A"


def test_full_pipeline_error_on_disallowed_metric(agent):
    result = agent.invoke(
        "Check host health.",
        ctx=_ctx(),
        input_context={
            "query_intent": ["network"],  # not in ALLOWED_METRICS
            "target_context": "baremetal",
            "target_id": "localhost",
        },
    )

    assert result["status"] == "error"


def test_full_pipeline_error_on_missing_target_id(agent):
    result = agent.invoke(
        "Check host health.",
        ctx=_ctx(),
        input_context={"query_intent": ["cpu"], "target_context": "baremetal", "target_id": ""},
    )

    assert result["status"] == "error"


def test_full_pipeline_blocks_anonymous_caller(agent):
    result = agent.invoke(
        "Check host health.",
        ctx=_ctx(trust_level=TrustLevel.ANONYMOUS),
        input_context={"query_intent": ["cpu"], "target_context": "baremetal", "target_id": "localhost"},
    )

    assert result["status"] == "error"
