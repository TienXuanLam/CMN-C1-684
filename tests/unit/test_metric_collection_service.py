# CMN-C1-684 — Unit Tests: metric_collection_service

import requests
import pytest

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


class _FakeSource:
    """Test-only MetricSource stub (dependency injection via source_override)."""

    def __init__(self, values: dict | None = None, errors: dict | None = None):
        self._values = values or {}
        self._errors = errors or {}

    def collect(self, metric, target_id):
        if metric in self._errors:
            raise self._errors[metric]
        return self._values[metric]


class TestCollectAndEvaluate:
    def test_normal_classification_below_warn(self):
        source = _FakeSource({"cpu": {"value": 30.0, "unit": "%"}})
        raw, evaluated = collect_and_evaluate("psutil", ["cpu"], "h1", _THRESHOLDS, source_override=source)

        assert raw["cpu"]["value"] == 30.0
        assert "timestamp" in raw["cpu"]
        assert evaluated["cpu"]["status"] == "NORMAL"

    def test_warn_classification_at_boundary_inclusive(self):
        source = _FakeSource({"cpu": {"value": 70.0, "unit": "%"}})
        _, evaluated = collect_and_evaluate("psutil", ["cpu"], "h1", _THRESHOLDS, source_override=source)
        assert evaluated["cpu"]["status"] == "WARN"

    def test_alert_classification_at_boundary_inclusive(self):
        source = _FakeSource({"cpu": {"value": 90.0, "unit": "%"}})
        _, evaluated = collect_and_evaluate("psutil", ["cpu"], "h1", _THRESHOLDS, source_override=source)
        assert evaluated["cpu"]["status"] == "ALERT"

    def test_just_below_warn_is_normal(self):
        source = _FakeSource({"cpu": {"value": 69.999, "unit": "%"}})
        _, evaluated = collect_and_evaluate("psutil", ["cpu"], "h1", _THRESHOLDS, source_override=source)
        assert evaluated["cpu"]["status"] == "NORMAL"

    def test_backend_unreachable_yields_error_status(self):
        source = _FakeSource(errors={"cpu": requests.ConnectionError("refused")})
        raw, evaluated = collect_and_evaluate("cadvisor", ["cpu"], "c1", _THRESHOLDS, source_override=source)

        assert "cpu" not in raw
        assert evaluated["cpu"] == {"status": "ERROR", "reason": "backend_unreachable"}

    def test_permission_denied_yields_error_status(self):
        source = _FakeSource(errors={"cpu": PermissionError("denied")})
        _, evaluated = collect_and_evaluate("psutil", ["cpu"], "h1", _THRESHOLDS, source_override=source)

        assert evaluated["cpu"] == {"status": "ERROR", "reason": "permission_denied"}

    def test_multiple_metrics_collected_independently(self):
        source = _FakeSource(
            {
                "cpu": {"value": 20.0, "unit": "%"},
                "memory": {"value": 95.0, "unit": "%"},
            }
        )
        _, evaluated = collect_and_evaluate("psutil", ["cpu", "memory"], "h1", _THRESHOLDS, source_override=source)

        assert evaluated["cpu"]["status"] == "NORMAL"
        assert evaluated["memory"]["status"] == "ALERT"

    def test_one_metric_failure_does_not_block_others(self):
        source = _FakeSource(
            values={"cpu": {"value": 10.0, "unit": "%"}},
            errors={"memory": requests.Timeout("timed out")},
        )
        raw, evaluated = collect_and_evaluate("cadvisor", ["cpu", "memory"], "c1", _THRESHOLDS, source_override=source)

        assert evaluated["cpu"]["status"] == "NORMAL"
        assert evaluated["memory"] == {"status": "ERROR", "reason": "backend_unreachable"}
        assert "memory" not in raw

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, 101.0, True, "50"])
    def test_invalid_metric_value_is_reported_not_classified(self, value):
        source = _FakeSource({"cpu": {"value": value, "unit": "%"}})
        raw, evaluated = collect_and_evaluate("psutil", ["cpu"], "h1", _THRESHOLDS, source_override=source)

        assert raw == {}
        assert evaluated["cpu"] == {"status": "ERROR", "reason": "invalid_backend_response"}

    def test_invalid_metric_unit_is_reported(self):
        source = _FakeSource({"cpu": {"value": 0.5, "unit": "cores"}})
        _, evaluated = collect_and_evaluate("cadvisor", ["cpu"], "c1", _THRESHOLDS, source_override=source)

        assert evaluated["cpu"] == {"status": "ERROR", "reason": "invalid_backend_response"}


class TestComputeOverallStatus:
    def test_any_alert_wins(self):
        results = {"cpu": {"status": "NORMAL"}, "memory": {"status": "ALERT"}, "disk": {"status": "WARN"}}
        assert compute_overall_status(results) == "ALERT"

    def test_any_warn_no_alert(self):
        results = {"cpu": {"status": "NORMAL"}, "memory": {"status": "WARN"}}
        assert compute_overall_status(results) == "WARN"

    def test_all_normal(self):
        results = {"cpu": {"status": "NORMAL"}, "memory": {"status": "NORMAL"}}
        assert compute_overall_status(results) == "NORMAL"

    def test_error_status_treated_as_alert(self):
        results = {"cpu": {"status": "NORMAL"}, "memory": {"status": "ERROR", "reason": "backend_unreachable"}}
        assert compute_overall_status(results) == "ALERT"

    def test_na_status_does_not_affect_aggregate(self):
        results = {"cpu": {"status": "NORMAL"}, "gpu": {"status": "N/A", "reason": "pynvml_unavailable"}}
        assert compute_overall_status(results) == "NORMAL"


class TestGpuFallback:
    def test_gpu_metric_falls_back_to_na_when_pynvml_missing(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "pynvml":
                raise ImportError("no module named pynvml")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        _, evaluated = collect_and_evaluate("psutil", ["gpu"], "h1", _THRESHOLDS)

        assert evaluated["gpu"] == {"status": "N/A", "reason": "pynvml_unavailable"}
