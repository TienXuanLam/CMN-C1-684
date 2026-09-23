# CMN-C1-684 — Unit Tests: cAdvisor / Prometheus metric extractors
# (proposal §11 Risk #1 — Prometheus label leakage; see also PB-03)

import pytest

from src.services.metric_collection_service import _extract_cadvisor_metric, _extract_prometheus_metric


class TestExtractCadvisorMetric:
    def _stats(self, mem_usage=50, mem_limit=100, fs_usage=50, fs_capacity=100):
        return {
            "container-abc": {
                "stats": [
                    {
                        "timestamp": "2026-01-01T00:00:00Z",
                        "cpu": {"usage": {"total": 1_000_000_000, "per_cpu_usage": [500_000_000, 500_000_000]}},
                        "memory": {"usage": mem_usage, "limit": mem_limit},
                        "filesystem": [{"usage": fs_usage, "capacity": fs_capacity}],
                    },
                    {
                        "timestamp": "2026-01-01T00:00:01Z",
                        "cpu": {"usage": {"total": 2_000_000_000, "per_cpu_usage": [1_000_000_000, 1_000_000_000]}},
                        "memory": {"usage": mem_usage, "limit": mem_limit},
                        "filesystem": [{"usage": fs_usage, "capacity": fs_capacity}],
                    },
                ]
            }
        }

    def test_cpu_extraction(self):
        result = _extract_cadvisor_metric(self._stats(), "cpu")
        assert result == {"value": 50.0, "unit": "%"}

    def test_memory_extraction(self):
        result = _extract_cadvisor_metric(self._stats(), "memory")
        assert result == {"value": 50.0, "unit": "%"}

    def test_disk_extraction(self):
        result = _extract_cadvisor_metric(self._stats(fs_usage=25, fs_capacity=100), "disk")
        assert result == {"value": 25.0, "unit": "%"}

    def test_unsupported_metric_raises(self):
        with pytest.raises(ValueError, match="unsupported metric"):
            _extract_cadvisor_metric(self._stats(), "network")

    def test_only_value_and_unit_keys_returned_no_label_leak(self):
        result = _extract_cadvisor_metric(self._stats(), "cpu")
        assert set(result) == {"value", "unit"}


class TestExtractPrometheusMetric:
    def _payload(self, value="72.4", labels=None):
        metric_labels = {"__name__": "container_cpu_usage_seconds_total"}
        if labels:
            metric_labels.update(labels)
        return {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": metric_labels, "value": [1234567890, value]}],
            },
        }

    def test_extracts_scalar_value(self):
        result = _extract_prometheus_metric(self._payload(value="42.5"))
        assert result == {"value": 42.5, "unit": "%"}

    def test_empty_result_raises(self):
        payload = {"status": "success", "data": {"resultType": "vector", "result": []}}
        with pytest.raises(ValueError, match="no result"):
            _extract_prometheus_metric(payload)

    def test_only_value_and_unit_keys_returned_no_label_leak(self):
        """PB-03: even when the Prometheus response carries rich labels
        (pod name, namespace, internal service topology), the extractor
        must never surface them — only the scalar value/unit."""
        payload = self._payload(
            labels={
                "pod": "internal-service-7f9c8b",
                "namespace": "prod-internal",
                "instance": "10.244.1.7:9100",
            }
        )
        result = _extract_prometheus_metric(payload)

        assert set(result) == {"value", "unit"}
        assert "pod" not in result
        assert "namespace" not in result
        assert "instance" not in result
