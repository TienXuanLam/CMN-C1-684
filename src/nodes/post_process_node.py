"""AgentCore Platform v1.0"""

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.report_assembly_service import (
    assemble_json_report,
    assemble_markdown_report,
    hash_target_id,
)

# S-3 allowlist (proposal §11 Risk #1 / docs/02_design.md "Security Design"):
# any per-metric result dict may only carry these keys. metric_collection_service
# never returns raw Prometheus/cAdvisor labels in the first place (defense in
# depth #1) — this is defense in depth #2, a structural check that blocks
# output if an unexpected key (e.g. an accidentally-merged label set) appears.
_ALLOWED_METRIC_RESULT_KEYS = frozenset({"value", "unit", "status", "reason"})


def _extract_metrics_dict(formatted_output: str) -> dict[str, Any] | None:
    """Return the `metrics` dict from a JSON-formatted report, or None if
    formatted_output is not JSON (Markdown output_format)."""
    try:
        parsed = json.loads(formatted_output)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    metrics = parsed.get("metrics")
    return metrics if isinstance(metrics, dict) else None


class PostProcessNode(FunctionNode):
    """Step 3 — S-3 output sanitization; assemble JSON/Markdown report; S-4 audit log."""

    # S-1 (docs/02_design.md "Security Design"): this agent is invoked by
    # orchestrators / MLOps pipelines, not anonymous callers.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        # S-3 structural check: reject output if any per-metric result dict
        # carries an undeclared key — a Prometheus/cAdvisor label leak would
        # show up here as an unexpected key (e.g. "pod", "namespace").
        # `result` is the partial state update this node just produced (node
        # contract: only changed keys) — `formatted_output` is the only field
        # here that carries metric data; evaluated_results_json is an
        # upstream-produced *input*, not part of this node's own output.
        formatted_output = result.get("formatted_output")
        if not isinstance(formatted_output, str):
            return result
        metrics = _extract_metrics_dict(formatted_output)
        if metrics is None:
            return result  # Markdown output — structural JSON check does not apply
        for metric, metric_result in metrics.items():
            if not isinstance(metric_result, dict):
                continue
            unexpected = set(metric_result) - _ALLOWED_METRIC_RESULT_KEYS
            if unexpected:
                raise RuntimeError(
                    f"PostProcessNode S-3: metric '{metric}' result contains "
                    f"undeclared key(s) {sorted(unexpected)} — possible label leak"
                )
        return result

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        validated_plan_json = state.get("validated_plan_json")
        evaluated_results_json = state.get("evaluated_results_json")
        overall_status = state.get("overall_status")

        if not evaluated_results_json or not overall_status:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PostProcessNode: evaluated_results_json/overall_status missing from state"],
            }

        try:
            plan = json.loads(validated_plan_json) if validated_plan_json else {}
            evaluated_results = json.loads(evaluated_results_json)
            if not isinstance(plan, dict) or not isinstance(evaluated_results, dict):
                raise ValueError("report inputs must be objects")
        except (json.JSONDecodeError, TypeError, ValueError):
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PostProcessNode: upstream report data is malformed"],
            }
        output_format = plan.get("output_format", "json")
        target_id = plan.get("target_id", "")

        audit_ref = hash_target_id(target_id)

        if output_format == "markdown":
            formatted_output = assemble_markdown_report(overall_status, evaluated_results, audit_ref)
        else:
            formatted_output = assemble_json_report(overall_status, evaluated_results, audit_ref)

        # S-4 (proposal §10 row 5 / EU AI Act Art.12): log invocation_id
        # (correlation_id), HASHED target_id (never raw), query_intent,
        # overall_status, timestamp (timestamp is added by emit_trace_event
        # itself).
        emit_trace_event(
            "health_report_generated",
            {
                "target_id_hash": audit_ref,
                "query_intent": list(evaluated_results.keys()),
                "overall_status": overall_status,
            },
            state,
        )

        return {
            # No `_json` suffix — this is the literal key AgentBaseGraph.get_output()
            # reads for the top-level /invoke response's `output` field
            # (docs/02_design.md "State Definition" — ADR-005 exception, same
            # framework contract confirmed elsewhere in the fleet).
            "formatted_output": formatted_output,
            "status": AgentStatus.SUCCESS.value,
        }
