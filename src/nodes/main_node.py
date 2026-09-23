"""AgentCore Platform v1.0"""

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.metric_collection_service import MetricSource, collect_and_evaluate, compute_overall_status


class MainNode(FunctionNode):
    """Step 2 (ToolCallingAgent) — dispatch tool calls to the resolved
    backend, collect raw metrics, evaluate against thresholds, aggregate
    overall_status. No LLM call on this critical path (proposal §10 row 4)."""

    # S-1 (docs/02_design.md "Security Design"): this agent is invoked by
    # orchestrators / MLOps pipelines, not anonymous callers.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, metric_source: MetricSource | None = None) -> None:
        self._metric_source = metric_source

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        validated_plan_json = state.get("validated_plan_json")
        if not validated_plan_json:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["MainNode: validated_plan_json missing from state"],
            }
        try:
            plan = json.loads(validated_plan_json)
            if not isinstance(plan, dict):
                raise ValueError("plan must be an object")
            backend = plan["backend"]
            metrics = plan["metrics"]
            target_id = plan["target_id"]
            thresholds = plan["thresholds"]
            if (
                not isinstance(backend, str)
                or not isinstance(metrics, list)
                or not all(isinstance(metric, str) for metric in metrics)
                or not isinstance(target_id, str)
                or not isinstance(thresholds, dict)
            ):
                raise ValueError("plan fields have invalid types")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["MainNode: validated_plan_json is malformed"],
            }

        raw_metrics, evaluated_results = collect_and_evaluate(
            backend=backend,
            metrics=metrics,
            target_id=target_id,
            thresholds=thresholds,
            source_override=self._metric_source,
        )
        overall_status = compute_overall_status(evaluated_results)

        emit_trace_event(
            "metrics_collected",
            {"backend": backend, "metric_count": len(evaluated_results)},
            state,
        )
        emit_trace_event(
            "threshold_evaluated",
            {"overall_status": overall_status},
            state,
        )

        return {
            # ADR-005: serialize structured values at the FunctionNode boundary.
            "raw_metrics_json": json.dumps(raw_metrics),
            "evaluated_results_json": json.dumps(evaluated_results),
            "overall_status": overall_status,
            "status": AgentStatus.SUCCESS.value,
        }
