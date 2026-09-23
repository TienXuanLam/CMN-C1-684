# CMN-C1-684 — Unit Tests: PostProcessNode

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.post_process_node import PostProcessNode


def _state(**overrides) -> dict:
    plan = overrides.pop(
        "plan",
        {"target_id": "host-01", "output_format": "json"},
    )
    evaluated_results = overrides.pop(
        "evaluated_results",
        {"cpu": {"value": 20.0, "unit": "%", "status": "NORMAL"}},
    )
    base = {
        "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
        "correlation_id": "test-corr-id",
        "node_history": [],
        "error_log": [],
        "validated_plan_json": json.dumps(plan),
        "evaluated_results_json": json.dumps(evaluated_results),
        "overall_status": overrides.pop("overall_status", "NORMAL"),
    }
    base.update(overrides)
    return base


class TestPostProcessNode:
    def setup_method(self):
        self.node = PostProcessNode()

    def test_success_path_assembles_json_report(self):
        result = self.node.execute(_state())

        assert result["status"] == AgentStatus.SUCCESS.value
        report = json.loads(result["formatted_output"])
        assert report["overall_status"] == "NORMAL"
        assert "cpu" in report["metrics"]
        assert "audit_ref" in report

    def test_success_path_assembles_markdown_report(self):
        result = self.node.execute(_state(plan={"target_id": "host-01", "output_format": "markdown"}))

        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"].startswith("# Resource Health Report")

    def test_target_id_hashed_not_raw_in_audit_log(self, monkeypatch):
        import src.nodes.post_process_node as post_process_node_module

        events = []
        monkeypatch.setattr(
            post_process_node_module,
            "emit_trace_event",
            lambda event_type, payload, state: events.append((event_type, payload)),
        )

        self.node.execute(_state(plan={"target_id": "super-secret-hostname", "output_format": "json"}))

        health_report_events = [p for e, p in events if e == "health_report_generated"]
        assert len(health_report_events) == 1
        payload = health_report_events[0]
        assert "super-secret-hostname" not in json.dumps(payload)
        assert "target_id_hash" in payload

    def test_missing_evaluated_results_returns_error_not_keyerror(self):
        result = self.node.execute({})

        assert result["status"] == AgentStatus.ERROR.value
        assert len(result["error_log"]) > 0

    def test_malformed_upstream_json_returns_stable_error(self):
        result = self.node.execute(_state(evaluated_results={}) | {"evaluated_results_json": "not-json"})

        assert result["status"] == AgentStatus.ERROR.value
        assert result["error_log"] == ["PostProcessNode: upstream report data is malformed"]

    def test_trust_gate_blocks_anonymous_caller(self):
        state = _state(caller_trust_level=TrustLevel.ANONYMOUS.value)
        result = self.node(state)

        assert result["status"] == AgentStatus.ERROR.value

    def test_extra_security_gate_output_passthrough_when_clean(self):
        result = self.node.execute(_state())
        gated = self.node._extra_security_gate_output(result)

        assert gated == result

    def test_extra_security_gate_output_blocks_undeclared_metric_key(self):
        """S-3 structural check: a leaked Prometheus label (e.g. 'pod')
        surfacing in evaluated_results must block output."""
        state = _state(
            evaluated_results={"cpu": {"value": 20.0, "unit": "%", "status": "NORMAL", "pod": "internal-svc-abc"}}
        )
        result = self.node.execute(state)

        raised = False
        try:
            self.node._extra_security_gate_output(result)
        except RuntimeError:
            raised = True

        assert raised is True

    def test_execute_method_signature(self):
        """Node contract: Node must implement execute(state), not _invoke_impl."""
        import inspect

        assert hasattr(PostProcessNode, "execute")
        sig = inspect.signature(PostProcessNode.execute)
        params = list(sig.parameters.keys())
        assert len(params) >= 2
        assert params[1] == "state"
        assert "_invoke_impl" not in PostProcessNode.__dict__
