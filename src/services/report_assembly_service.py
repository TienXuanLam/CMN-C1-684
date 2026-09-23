"""AgentCore Platform v1.0"""

# Domain logic: assemble the final structured health report (JSON or
# Markdown) from evaluated metric results. No agenticstar/framework
# imports — pure Python only (called by PostProcessNode.execute(), which
# owns the S-1/S-3/S-4 boundary).

from __future__ import annotations

import hashlib
import json
from typing import Any


def build_summary(overall_status: str, evaluated_results: dict[str, Any]) -> str:
    """One-sentence human-readable summary of the health check outcome."""
    if overall_status == "NORMAL":
        return "All monitored metrics are within normal range."
    flagged = [
        f"{metric} ({r['status']})"
        for metric, r in evaluated_results.items()
        if r["status"] in ("WARN", "ALERT", "ERROR")
    ]
    return f"Overall status {overall_status} — flagged: {', '.join(flagged)}."


def hash_target_id(target_id: str) -> str:
    """One-way hash of target_id for the S-4 audit log (proposal §10 row 5:
    never log the raw target_id — it is an infrastructure identifier)."""
    return hashlib.sha256(target_id.encode("utf-8")).hexdigest()[:16]


def assemble_json_report(overall_status: str, evaluated_results: dict[str, Any], audit_ref: str) -> str:
    """Build the JSON report payload (proposal §4 output schema) and return
    it as a JSON string — the FunctionNode boundary always writes
    `formatted_output` as a JSON string (ADR-005 spirit), never a raw dict."""
    payload = {
        "overall_status": overall_status,
        "metrics": evaluated_results,
        "summary": build_summary(overall_status, evaluated_results),
        "audit_ref": audit_ref,
    }
    return json.dumps(payload)


def assemble_markdown_report(overall_status: str, evaluated_results: dict[str, Any], audit_ref: str) -> str:
    """Build the Markdown report (proposal §4, output_format="markdown")."""
    lines = [
        "# Resource Health Report",
        "",
        f"**Overall status:** {overall_status}",
        "",
        "| Metric | Value | Unit | Status |",
        "|---|---|---|---|",
    ]
    for metric, r in evaluated_results.items():
        value = r.get("value", "-")
        unit = r.get("unit", "-")
        lines.append(f"| {metric} | {value} | {unit} | {r['status']} |")
    lines += [
        "",
        build_summary(overall_status, evaluated_results),
        "",
        f"_Audit ref: {audit_ref}_",
    ]
    return "\n".join(lines)
