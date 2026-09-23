"""AgentCore Platform v1.0"""

# Domain logic: validate the caller's query intent, resolve the metric
# backend from target_context, and build the per-metric tool call plan.
# No agenticstar/framework imports — pure Python only (called by
# PreProcessNode.execute(), which owns the S-1/S-2/S-4 boundary).

from __future__ import annotations

import re
import math
from typing import Any

ALLOWED_METRICS = frozenset({"cpu", "memory", "disk", "gpu"})
ALLOWED_TARGET_CONTEXTS = frozenset({"baremetal", "docker", "kubernetes"})
_BACKEND_BY_TARGET_CONTEXT = {
    "baremetal": "psutil",
    "docker": "cadvisor",
    "kubernetes": "prometheus",
}

# Defaults applied to any missing threshold field (proposal §11 Risk #4).
_DEFAULT_WARN_PCT = 70.0
_DEFAULT_ALERT_PCT = 90.0

# Conservative anti-injection check for target_id (proposal §4 step 1). No
# subprocess call is ever made anywhere in this agent — target_id only
# reaches HTTP query params (cAdvisor/Prometheus) or is unused (psutil reads
# local host metrics regardless of target_id) — this is defense-in-depth,
# not a shell-injection prevention (docs/02_design.md "Security Design").
_TARGET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class QueryPlanValidationError(Exception):
    """Raised when the caller-supplied query intent fails S-2 validation."""


def validate_and_build_plan(input_context: dict[str, Any]) -> dict[str, Any]:
    """Validate query_intent/target_context/target_id/threshold_config and
    build the per-metric tool call plan.

    Returns: {"backend": str, "target_context": str, "target_id": str,
              "metrics": list[str], "thresholds": dict, "output_format": str}

    Raises QueryPlanValidationError on any invalid input — the caller
    (PreProcessNode) converts this into status=error, never lets it
    propagate as an unhandled exception past the node boundary.
    """
    query_intent = input_context.get("query_intent")
    if not isinstance(query_intent, list) or not query_intent:
        raise QueryPlanValidationError("query_intent must be a non-empty list")
    if len(query_intent) > len(ALLOWED_METRICS):
        raise QueryPlanValidationError("query_intent exceeds the supported metric count")
    if not all(isinstance(metric, str) for metric in query_intent):
        raise QueryPlanValidationError("query_intent entries must be strings")
    if len(query_intent) != len(set(query_intent)):
        raise QueryPlanValidationError("query_intent must not contain duplicate metrics")
    invalid_metrics = set(query_intent) - ALLOWED_METRICS
    if invalid_metrics:
        raise QueryPlanValidationError(f"query_intent contains disallowed metric(s): {sorted(invalid_metrics)}")

    target_context = input_context.get("target_context")
    if target_context not in ALLOWED_TARGET_CONTEXTS:
        raise QueryPlanValidationError(
            f"target_context must be one of {sorted(ALLOWED_TARGET_CONTEXTS)}, got {target_context!r}"
        )

    target_id = input_context.get("target_id", "")
    if not isinstance(target_id, str) or not target_id.strip():
        raise QueryPlanValidationError("target_id must be a non-empty string")
    if not _TARGET_ID_PATTERN.fullmatch(target_id) or ".." in target_id:
        raise QueryPlanValidationError("target_id does not match the permitted identifier format")

    threshold_config = input_context.get("threshold_config") or {}
    if not isinstance(threshold_config, dict):
        raise QueryPlanValidationError("threshold_config must be an object")
    thresholds = _resolve_thresholds(threshold_config)

    output_format = input_context.get("output_format", "json")
    if output_format not in ("json", "markdown"):
        raise QueryPlanValidationError("output_format must be 'json' or 'markdown'")

    return {
        "backend": _BACKEND_BY_TARGET_CONTEXT[target_context],
        "target_context": target_context,
        "target_id": target_id,
        "metrics": list(query_intent),
        "thresholds": thresholds,
        "output_format": output_format,
    }


def _resolve_thresholds(threshold_config: dict[str, Any]) -> dict[str, float]:
    """Fill any missing warn_*_pct/alert_*_pct field with safe defaults
    (proposal §11 Risk #4) and validate supplied values are in [0, 100]."""
    resolved: dict[str, float] = {}
    for dim in ("cpu", "memory", "disk", "gpu"):
        for kind, default in (("warn", _DEFAULT_WARN_PCT), ("alert", _DEFAULT_ALERT_PCT)):
            key = f"{kind}_{dim}_pct"
            value = threshold_config.get(key, default)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise QueryPlanValidationError(f"{key} must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric) or not (0 <= numeric <= 100):
                raise QueryPlanValidationError(f"{key} must be in [0, 100], got {value}")
            resolved[key] = numeric
        if resolved[f"warn_{dim}_pct"] > resolved[f"alert_{dim}_pct"]:
            raise QueryPlanValidationError(f"warn_{dim}_pct must not exceed alert_{dim}_pct")
    return resolved
