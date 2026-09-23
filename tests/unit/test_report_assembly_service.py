# CMN-C1-684 — Unit Tests: report_assembly_service

import json

from src.services.report_assembly_service import (
    assemble_json_report,
    assemble_markdown_report,
    build_summary,
    hash_target_id,
)

_RESULTS_NORMAL = {"cpu": {"value": 20.0, "unit": "%", "status": "NORMAL"}}
_RESULTS_MIXED = {
    "cpu": {"value": 20.0, "unit": "%", "status": "NORMAL"},
    "memory": {"value": 95.0, "unit": "%", "status": "ALERT"},
}


class TestBuildSummary:
    def test_all_normal_summary(self):
        summary = build_summary("NORMAL", _RESULTS_NORMAL)
        assert "normal" in summary.lower()

    def test_mixed_summary_flags_non_normal(self):
        summary = build_summary("ALERT", _RESULTS_MIXED)
        assert "memory" in summary
        assert "ALERT" in summary


class TestHashTargetId:
    def test_hash_is_deterministic(self):
        assert hash_target_id("host-01") == hash_target_id("host-01")

    def test_hash_differs_per_input(self):
        assert hash_target_id("host-01") != hash_target_id("host-02")

    def test_hash_does_not_contain_raw_target_id(self):
        target_id = "internal-host-secretname"
        digest = hash_target_id(target_id)
        assert target_id not in digest


class TestAssembleJsonReport:
    def test_json_report_structure(self):
        report_str = assemble_json_report("NORMAL", _RESULTS_NORMAL, "audit-ref-123")
        report = json.loads(report_str)

        assert report["overall_status"] == "NORMAL"
        assert report["metrics"] == _RESULTS_NORMAL
        assert "summary" in report
        assert report["audit_ref"] == "audit-ref-123"

    def test_returns_json_string_not_dict(self):
        report_str = assemble_json_report("NORMAL", _RESULTS_NORMAL, "audit-ref-123")
        assert isinstance(report_str, str)


class TestAssembleMarkdownReport:
    def test_markdown_contains_table_and_status(self):
        md = assemble_markdown_report("ALERT", _RESULTS_MIXED, "audit-ref-456")

        assert "ALERT" in md
        assert "| cpu |" in md
        assert "| memory |" in md
        assert "audit-ref-456" in md

    def test_returns_plain_string(self):
        md = assemble_markdown_report("NORMAL", _RESULTS_NORMAL, "ref")
        assert isinstance(md, str)
        assert not md.startswith("{")
