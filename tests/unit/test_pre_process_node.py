# CMN-C1-684 — Unit Tests: PreProcessNode

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.pre_process_node import PreProcessNode


def _state(**overrides) -> dict:
    input_context = overrides.pop(
        "input_context",
        {
            "query_intent": ["cpu", "memory"],
            "target_context": "baremetal",
            "target_id": "host-01",
            "threshold_config": {"warn_cpu_pct": 60, "alert_cpu_pct": 85},
            "output_format": "json",
        },
    )
    base = {
        "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
        "correlation_id": "test-corr-id",
        "node_history": [],
        "error_log": [],
        "input_context": input_context,
    }
    base.update(overrides)
    return base


class TestPreProcessNode:
    def setup_method(self):
        self.node = PreProcessNode()

    def test_success_path_builds_validated_plan(self):
        result = self.node.execute(_state())

        assert result["status"] == AgentStatus.SUCCESS.value
        plan = json.loads(result["validated_plan_json"])
        assert plan["backend"] == "psutil"
        assert plan["metrics"] == ["cpu", "memory"]

    def test_disallowed_metric_returns_error(self):
        result = self.node.execute(
            _state(
                input_context={
                    "query_intent": ["network"],
                    "target_context": "baremetal",
                    "target_id": "host-01",
                }
            )
        )

        assert result["status"] == AgentStatus.ERROR.value
        assert len(result["error_log"]) > 0

    def test_missing_target_id_returns_error(self):
        result = self.node.execute(
            _state(input_context={"query_intent": ["cpu"], "target_context": "baremetal", "target_id": ""})
        )

        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_gate_blocks_anonymous_caller(self):
        """TC-08: must call via __call__(), not execute() directly, to
        exercise the S-1 trust gate."""
        state = _state(caller_trust_level=TrustLevel.ANONYMOUS.value)
        result = self.node(state)

        assert result["status"] == AgentStatus.ERROR.value
        assert "trust gate" in result["error_log"][0].lower()

    def test_trust_gate_allows_verified_external(self):
        state = _state(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL.value)
        result = self.node(state)

        assert result["status"] == AgentStatus.SUCCESS.value

    def test_extra_security_gate_input_rejects_non_object_input_context(self):
        """S-2 rejects only malformed (wrong-type) input_context — deep
        domain validation (query_intent values, etc.) is execute()'s job,
        so it must not short-circuit execute() via the S-2 gate (that
        would break PB-6 invoke-order verification with a minimal state)."""
        state = _state(input_context="not-an-object")
        gated = self.node._extra_security_gate_input(state)

        assert gated["status"] == AgentStatus.ERROR.value

    def test_extra_security_gate_input_passthrough_when_valid(self):
        state = _state()
        gated = self.node._extra_security_gate_input(state)

        assert gated.get("status") != AgentStatus.ERROR.value

    def test_extra_security_gate_input_passthrough_when_input_context_absent(self):
        """A minimal state with no input_context at all must pass S-2 —
        execute() surfaces the missing-fields error, not the gate."""
        state = {
            "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
            "correlation_id": "test-corr-id",
        }
        gated = self.node._extra_security_gate_input(state)

        assert gated.get("status") != AgentStatus.ERROR.value

    def test_deep_validation_error_surfaces_from_execute_not_gate(self):
        """query_intent=[] passes the S-2 shallow gate (it's a well-formed
        dict) but must still be rejected — by execute()."""
        state = _state(input_context={"query_intent": [], "target_context": "baremetal", "target_id": "h1"})
        gated = self.node._extra_security_gate_input(state)
        assert gated.get("status") != AgentStatus.ERROR.value  # S-2 passthrough

        result = self.node.execute(state)
        assert result["status"] == AgentStatus.ERROR.value  # execute() rejects

    def test_emit_trace_event_fires_on_success_path(self, monkeypatch):
        import src.nodes.pre_process_node as pre_process_node_module

        events = []
        monkeypatch.setattr(
            pre_process_node_module,
            "emit_trace_event",
            lambda event_type, payload, state: events.append(event_type),
        )

        result = self.node.execute(_state())

        assert result["status"] == AgentStatus.SUCCESS.value
        assert "metric_backend_resolved" in events

    def test_execute_method_signature(self):
        """Node contract: Node must implement execute(state), not _invoke_impl."""
        import inspect

        assert hasattr(PreProcessNode, "execute")
        sig = inspect.signature(PreProcessNode.execute)
        params = list(sig.parameters.keys())
        assert len(params) >= 2
        assert params[1] == "state"
        assert "_invoke_impl" not in PreProcessNode.__dict__
