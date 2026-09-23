# PB-01 — Backend-unreachable handling (proposal §11 Risk #2)
#
# A metric backend connection failure/timeout/HTTP error must never
# propagate as an unhandled exception, and must never be silently
# omitted from the report — it must surface as {"status": "ERROR",
# "reason": "backend_unreachable"} for that metric, and force
# overall_status to ALERT (failure-to-check is treated as an alert).

import requests

from src.services.metric_collection_service import collect_and_evaluate, compute_overall_status

_THRESHOLDS = {
    "warn_cpu_pct": 70.0,
    "alert_cpu_pct": 90.0,
    "warn_memory_pct": 70.0,
    "alert_memory_pct": 90.0,
    "warn_disk_pct": 70.0,
    "alert_disk_pct": 90.0,
    "warn_gpu_pct": 70.0,
    "alert_gpu_pct": 90.0,
}


class _UnreachableSource:
    def collect(self, metric, target_id):
        raise requests.ConnectionError("connection refused")


class TestPB01BackendUnreachable:
    def test_unreachable_backend_never_raises(self):
        """The function itself must not propagate the connection error."""
        raw, evaluated = collect_and_evaluate(
            "cadvisor", ["cpu"], "c1", _THRESHOLDS, source_override=_UnreachableSource()
        )
        assert evaluated["cpu"]["status"] == "ERROR"

    def test_unreachable_metric_is_not_silently_omitted(self):
        """A failed metric must appear in evaluated_results with an
        explicit ERROR entry — never absent (which would mask an outage)."""
        _, evaluated = collect_and_evaluate(
            "cadvisor", ["cpu", "memory"], "c1", _THRESHOLDS, source_override=_UnreachableSource()
        )
        assert "cpu" in evaluated
        assert "memory" in evaluated
        assert evaluated["cpu"] == {"status": "ERROR", "reason": "backend_unreachable"}

    def test_unreachable_metric_never_in_raw_metrics(self):
        raw, _ = collect_and_evaluate("cadvisor", ["cpu"], "c1", _THRESHOLDS, source_override=_UnreachableSource())
        assert "cpu" not in raw

    def test_unreachable_forces_overall_status_alert(self):
        """Failure-to-check must be treated as an alert, not ignored —
        proposal §11 Risk #2 explicit requirement."""
        _, evaluated = collect_and_evaluate(
            "cadvisor", ["cpu"], "c1", _THRESHOLDS, source_override=_UnreachableSource()
        )
        assert compute_overall_status(evaluated) == "ALERT"

    def test_timeout_also_treated_as_backend_unreachable(self):
        class _TimeoutSource:
            def collect(self, metric, target_id):
                raise requests.Timeout("timed out")

        _, evaluated = collect_and_evaluate("prometheus", ["cpu"], "p1", _THRESHOLDS, source_override=_TimeoutSource())
        assert evaluated["cpu"] == {"status": "ERROR", "reason": "backend_unreachable"}
