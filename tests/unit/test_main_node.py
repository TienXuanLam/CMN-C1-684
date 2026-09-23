# CMN-C1-684 — Unit Tests: MainNode

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.main_node import MainNode


def _state(**overrides) -> dict:
    plan = overrides.pop(
        "plan",
        {
            "backend": "psutil",
            "target_context": "baremetal",
            "target_id": "host-01",
            "metrics": ["cpu"],
            "thresholds": {
                "warn_cpu_pct": 70.0,
                "alert_cpu_pct": 90.0,
                "warn_memory_pct": 70.0,
                "alert_memory_pct": 90.0,
                "warn_disk_pct": 70.0,
                "alert_disk_pct": 90.0,
                "warn_gpu_pct": 70.0,
                "alert_gpu_pct": 90.0,
            },
            "output_format": "json",
        },
    )
    base = {
        "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
        "correlation_id": "test-corr-id",
        "node_history": [],
        "error_log": [],
        "validated_plan_json": json.dumps(plan),
    }
    base.update(overrides)
    return base


class TestMainNode:
    def setup_method(self):
        self.node = MainNode()

    def test_success_path_collects_and_evaluates(self, monkeypatch):
        import src.nodes.main_node as main_node_module

        monkeypatch.setattr(
            main_node_module,
            "collect_and_evaluate",
            lambda **kwargs: (
                {"cpu": {"value": 10.0, "unit": "%", "timestamp": "t"}},
                {"cpu": {"value": 10.0, "unit": "%", "status": "NORMAL"}},
            ),
        )
        monkeypatch.setattr(main_node_module, "compute_overall_status", lambda evaluated: "NORMAL")

        result = self.node.execute(_state())

        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["overall_status"] == "NORMAL"
        assert json.loads(result["raw_metrics_json"])["cpu"]["value"] == 10.0
        assert json.loads(result["evaluated_results_json"])["cpu"]["status"] == "NORMAL"

    def test_missing_validated_plan_returns_error_not_keyerror(self):
        """PB-6 regression pattern: MainNode invoked directly with a
        minimal state (no validated_plan_json) must not raise."""
        result = self.node.execute({})

        assert result["status"] == AgentStatus.ERROR.value
        assert len(result["error_log"]) > 0

    def test_malformed_plan_returns_stable_error(self):
        result = self.node.execute({"validated_plan_json": "[]"})

        assert result["status"] == AgentStatus.ERROR.value
        assert result["error_log"] == ["MainNode: validated_plan_json is malformed"]

    def test_trust_gate_blocks_anonymous_caller(self):
        state = _state(caller_trust_level=TrustLevel.ANONYMOUS.value)
        result = self.node(state)

        assert result["status"] == AgentStatus.ERROR.value

    def test_emit_trace_event_fires_on_success_path(self, monkeypatch):
        import src.nodes.main_node as main_node_module

        events = []
        monkeypatch.setattr(
            main_node_module,
            "emit_trace_event",
            lambda event_type, payload, state: events.append(event_type),
        )
        monkeypatch.setattr(
            main_node_module,
            "collect_and_evaluate",
            lambda **kwargs: ({}, {"cpu": {"value": 1.0, "unit": "%", "status": "NORMAL"}}),
        )
        monkeypatch.setattr(main_node_module, "compute_overall_status", lambda evaluated: "NORMAL")

        result = self.node.execute(_state())

        assert result["status"] == AgentStatus.SUCCESS.value
        assert "metrics_collected" in events
        assert "threshold_evaluated" in events

    def test_execute_method_signature(self):
        """Node contract: Node must implement execute(state), not _invoke_impl."""
        import inspect

        assert hasattr(MainNode, "execute")
        sig = inspect.signature(MainNode.execute)
        params = list(sig.parameters.keys())
        assert len(params) >= 2
        assert params[1] == "state"
        assert "_invoke_impl" not in MainNode.__dict__
