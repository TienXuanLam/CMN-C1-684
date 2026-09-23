# PB-02 — GPU graceful fallback (proposal §11 Risk #5)
#
# On a non-NVIDIA host (pynvml unavailable, or no NVIDIA device found),
# the GPU dimension must never fail the whole invocation — it must
# degrade to {"status": "N/A", "reason": "pynvml_unavailable"}, and
# overall_status must not be affected by an N/A dimension.

import builtins

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


def _block_pynvml_import(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pynvml":
            raise ImportError("no module named pynvml")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


class TestPB02GpuGracefulFallback:
    def test_pynvml_unavailable_does_not_raise(self, monkeypatch):
        _block_pynvml_import(monkeypatch)

        # Must not raise — collect_and_evaluate handles the ImportError internally.
        _, evaluated = collect_and_evaluate("psutil", ["gpu"], "h1", _THRESHOLDS)
        assert evaluated["gpu"]["status"] == "N/A"

    def test_pynvml_unavailable_reason_is_explicit(self, monkeypatch):
        _block_pynvml_import(monkeypatch)

        _, evaluated = collect_and_evaluate("psutil", ["gpu"], "h1", _THRESHOLDS)
        assert evaluated["gpu"] == {"status": "N/A", "reason": "pynvml_unavailable"}

    def test_gpu_na_does_not_appear_in_raw_metrics(self, monkeypatch):
        _block_pynvml_import(monkeypatch)

        raw, _ = collect_and_evaluate("psutil", ["gpu"], "h1", _THRESHOLDS)
        assert "gpu" not in raw

    def test_overall_status_unaffected_by_gpu_na_when_others_normal(self, monkeypatch):
        """A source_override replaces the metric source wholesale, so a
        fake source for one metric would also intercept "gpu" instead of
        exercising the real _collect_gpu_pynvml() fallback path — route
        each metric explicitly so "gpu" genuinely takes that path."""
        _block_pynvml_import(monkeypatch)

        class _MixedSource:
            def collect(self, metric, target_id):
                if metric == "cpu":
                    return {"value": 10.0, "unit": "%"}  # well below warn_cpu_pct=70
                if metric == "gpu":
                    from src.services.metric_collection_service import _collect_gpu_pynvml

                    return _collect_gpu_pynvml()
                raise AssertionError(f"unexpected metric: {metric}")

        _, evaluated = collect_and_evaluate("psutil", ["cpu", "gpu"], "h1", _THRESHOLDS, source_override=_MixedSource())

        assert evaluated["cpu"]["status"] == "NORMAL"
        assert evaluated["gpu"]["status"] == "N/A"
        assert compute_overall_status(evaluated) == "NORMAL"

    def test_overall_status_still_reflects_alert_when_other_metric_alerts(self, monkeypatch):
        _block_pynvml_import(monkeypatch)

        class _MemoryAlertSource:
            def collect(self, metric, target_id):
                if metric == "memory":
                    return {"value": 95.0, "unit": "%"}  # above alert_memory_pct=90
                if metric == "gpu":
                    from src.services.metric_collection_service import _collect_gpu_pynvml

                    return _collect_gpu_pynvml()
                raise AssertionError(f"unexpected metric: {metric}")

        _, evaluated = collect_and_evaluate(
            "psutil", ["memory", "gpu"], "h1", _THRESHOLDS, source_override=_MemoryAlertSource()
        )
        assert evaluated["gpu"]["status"] == "N/A"
        assert compute_overall_status(evaluated) == "ALERT"
