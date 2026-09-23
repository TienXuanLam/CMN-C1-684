"""AgentCore Platform v1.0"""

# Node contract (concepts/node-contract.md):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus enum constants (.value) — never plain strings
#  - Read input_context via state.get("input_context", {}) — read-only
#  - Never import from mediator/, api/, or other agents

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.query_plan_service import QueryPlanValidationError, validate_and_build_plan


class PreProcessNode(FunctionNode):
    """Step 1 — S-2 input validation; resolve metric backend; build tool call plan."""

    # S-1 (docs/02_design.md "Security Design"): this agent is invoked by
    # orchestrators / MLOps pipelines, not anonymous callers.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        # S-2 (proposal §4 step 1 / §11): shallow structural check only —
        # input_context, when present, must be an object. Full domain
        # validation (allowed query_intent values, target_id anti-injection,
        # threshold_config range) runs in execute() via
        # validate_and_build_plan(), not duplicated here. This mirrors the
        # pattern used elsewhere in the fleet: S-2 rejects only malformed input
        # (wrong type), never "field absent" — an absent/empty input_context is
        # a normal execute()-level validation error, not an S-2 structural violation
        # (an S-2 reject here would short-circuit execute() entirely,
        # per FunctionNode._security_gate_input()'s status=error contract).
        input_context = state.get("input_context")
        if input_context is not None and not isinstance(input_context, dict):
            state = dict(state)
            state["status"] = AgentStatus.ERROR.value
            state["error_log"] = ["PreProcessNode: input_context must be an object"]
        return state

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        input_context = state.get("input_context") or {}
        if not isinstance(input_context, dict):
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PreProcessNode: input_context must be an object"],
            }
        try:
            plan = validate_and_build_plan(input_context)
        except QueryPlanValidationError as e:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PreProcessNode: {e}"],
            }

        emit_trace_event(
            "metric_backend_resolved",
            {"backend": plan["backend"], "metrics": plan["metrics"]},
            state,
        )

        return {
            # ADR-005: State fields must be flat primitives — serialize
            # the structured plan to a JSON string here, at the
            # FunctionNode boundary.
            "validated_plan_json": json.dumps(plan),
            "status": AgentStatus.SUCCESS.value,
        }
