"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption. Extend AgentState with agent-specific
# fields only. Do NOT add credentials, secrets, or Pydantic models.

from framework.schemas.agent_state import AgentState


# The AgentCore wheel does not currently publish TypedDict typing metadata, so
# mypy cannot infer that its runtime AgentState accepts TypedDict's `total` flag.
class State(AgentState, total=False):  # type: ignore[call-arg]
    """Agent state for AgentHealthResourceMonitorAgent.

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, formatted_output, etc.) are inherited from
    AgentState. Fields below are specific to the resource-metric
    ingestion -> threshold evaluation -> report generation pipeline. See
    docs/02_design.md "State Definition" for the field-by-field
    producer/consumer mapping.

    ADR-005: State must be a flat TypedDict of primitives only (str, int,
    float, bool, None) — LangGraph checkpoints use msgpack serialization,
    and nested dict/list values risk serialization/restore
    inconsistencies. Every structured field below is therefore stored as
    a JSON string (`*_json`), written with json.dumps() by the producing
    node and read with json.loads() by the consuming node. Exception:
    `formatted_output` (inherited from AgentState, no `_json` suffix —
    the literal key AgentBaseGraph.get_output() reads for the /invoke
    response's top-level `output` field) and `overall_status` (already a
    plain string, not structured).

    total=False: all domain fields below are populated incrementally by
    pre_process/main/post_process, not present from initialize() — a
    checkpoint restored mid-pipeline legitimately lacks fields not yet
    produced by that point.
    """

    # Produced by pre_process (S-2 input validation + backend resolution)
    validated_plan_json: (
        str | None
    )  # json.dumps({"backend": "psutil"|"cadvisor"|"prometheus", "metrics": [str, ...], "thresholds": {warn_cpu_pct: float, alert_cpu_pct: float, ...}})

    # Produced by main (ToolCallingAgent — metric collection + threshold evaluation)
    raw_metrics_json: str | None  # json.dumps({"cpu": {"value": float, "unit": str, "timestamp": str}, ...})
    evaluated_results_json: (
        str | None
    )  # json.dumps({"cpu": {"value": float, "unit": str, "status": "NORMAL"|"WARN"|"ALERT"|"ERROR"|"N/A", "reason": str|None}, ...})
    overall_status: str | None  # "NORMAL" | "WARN" | "ALERT" — plain string, not JSON

    # Produced by post_process (S-3 output sanitization + report assembly)
    # Uses the inherited `formatted_output` field (AgentState base, no
    # `_json` suffix) — that exact key is what AgentBaseGraph.get_output()
    # reads for the /invoke response's top-level `output` field. Value is
    # a JSON string (json.dumps({"overall_status": str, "metrics": dict,
    # "summary": str, "audit_ref": str})) when output_format="json", or
    # plain Markdown text when output_format="markdown".
