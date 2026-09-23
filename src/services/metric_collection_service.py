"""AgentCore Platform v1.0"""

# Domain logic: dispatch metric collection to the resolved backend
# (psutil / cAdvisor REST / Prometheus query API), then evaluate each
# collected value against caller-supplied thresholds. No agenticstar/
# framework imports — pure Python + requests/psutil only (called by
# MainNode.execute(), which owns the S-1/S-4 boundary).

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Protocol

import requests

_CADVISOR_TIMEOUT_S = 5
_PROMETHEUS_TIMEOUT_S = 5


class MetricSource(Protocol):
    def collect(self, metric: str, target_id: str) -> dict[str, Any]:
        """Return {"value": float, "unit": str} or raise on collection failure."""
        ...


class PsutilMetricSource:
    """Bare-metal metrics via psutil (proposal §4 step 2)."""

    def collect(self, metric: str, target_id: str) -> dict[str, Any]:
        import psutil

        if metric == "cpu":
            return {"value": psutil.cpu_percent(interval=0.1), "unit": "%"}
        if metric == "memory":
            return {"value": psutil.virtual_memory().percent, "unit": "%"}
        if metric == "disk":
            return {"value": psutil.disk_usage("/").percent, "unit": "%"}
        if metric == "gpu":
            return _collect_gpu_pynvml()
        raise ValueError(f"unsupported metric for psutil backend: {metric}")


class CAdvisorMetricSource:
    """Docker container metrics via cAdvisor REST API (proposal §4 step 2)."""

    def __init__(self, base_url: str = "http://localhost:8080") -> None:
        self._base_url = base_url.rstrip("/")

    def collect(self, metric: str, target_id: str) -> dict[str, Any]:
        url = f"{self._base_url}/api/v2/stats/{target_id}"
        resp = requests.get(url, timeout=_CADVISOR_TIMEOUT_S)
        resp.raise_for_status()
        stats = resp.json()
        return _extract_cadvisor_metric(stats, metric)


class PrometheusMetricSource:
    """Kubernetes pod metrics via Prometheus instant query API (proposal §4 step 2)."""

    _QUERY_TEMPLATES = {
        "cpu": 'rate(container_cpu_usage_seconds_total{{pod="{target_id}"}}[5m]) * 100',
        "memory": 'container_memory_working_set_bytes{{pod="{target_id}"}} / container_spec_memory_limit_bytes{{pod="{target_id}"}} * 100',
        "disk": 'container_fs_usage_bytes{{pod="{target_id}"}} / container_fs_limit_bytes{{pod="{target_id}"}} * 100',
        "gpu": 'DCGM_FI_DEV_GPU_UTIL{{pod="{target_id}"}}',
    }

    def __init__(self, base_url: str = "http://localhost:9090") -> None:
        self._base_url = base_url.rstrip("/")

    def collect(self, metric: str, target_id: str) -> dict[str, Any]:
        template = self._QUERY_TEMPLATES.get(metric)
        if template is None:
            raise ValueError(f"unsupported metric for prometheus backend: {metric}")
        query = template.format(target_id=target_id)
        resp = requests.get(f"{self._base_url}/api/v1/query", params={"query": query}, timeout=_PROMETHEUS_TIMEOUT_S)
        resp.raise_for_status()
        payload = resp.json()
        return _extract_prometheus_metric(payload)


_SOURCE_BY_BACKEND = {
    "psutil": PsutilMetricSource,
    "cadvisor": CAdvisorMetricSource,
    "prometheus": PrometheusMetricSource,
}


def collect_and_evaluate(
    backend: str,
    metrics: list[str],
    target_id: str,
    thresholds: dict[str, float],
    *,
    source_override: MetricSource | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Collect raw metric values and evaluate each against thresholds.

    Returns (raw_metrics, evaluated_results). Backend-unreachable and
    permission-denied failures are captured per metric (proposal §11 Risk
    #2/#3) — never propagated as an unhandled exception from this function.

    source_override is a test-only seam (dependency injection) — production
    callers never pass it; the backend/source mapping above is used.
    """
    source_type = _SOURCE_BY_BACKEND.get(backend)
    if source_override is None and source_type is None:
        raise ValueError("unsupported metric backend")
    source: MetricSource = source_override or source_type()  # type: ignore[misc]
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()

    raw_metrics: dict[str, Any] = {}
    evaluated_results: dict[str, Any] = {}

    for metric in metrics:
        try:
            collected = source.collect(metric, target_id)
        except _GpuUnavailableError as e:
            evaluated_results[metric] = {"status": "N/A", "reason": str(e)}
            continue
        except PermissionError:
            evaluated_results[metric] = {"status": "ERROR", "reason": "permission_denied"}
            continue
        except (requests.RequestException, OSError):
            evaluated_results[metric] = {"status": "ERROR", "reason": "backend_unreachable"}
            continue
        except (ValueError, KeyError, TypeError, IndexError, StopIteration):
            evaluated_results[metric] = {"status": "ERROR", "reason": "invalid_backend_response"}
            continue

        try:
            value, unit = _validate_collected_metric(collected)
        except (ValueError, KeyError, TypeError):
            evaluated_results[metric] = {"status": "ERROR", "reason": "invalid_backend_response"}
            continue
        raw_metrics[metric] = {"value": value, "unit": unit, "timestamp": timestamp}
        evaluated_results[metric] = {
            "value": value,
            "unit": unit,
            "status": _classify(metric, value, thresholds),
        }

    return raw_metrics, evaluated_results


def compute_overall_status(evaluated_results: dict[str, Any]) -> str:
    """Aggregate per-metric status into one overall verdict.

    any ALERT or ERROR -> ALERT (failure-to-check is treated as an alert,
    proposal §11 Risk #2); else any WARN -> WARN; else NORMAL. N/A
    (unavailable dimension, e.g. no GPU) does not affect the aggregate.
    """
    statuses = {r["status"] for r in evaluated_results.values()}
    if "ALERT" in statuses or "ERROR" in statuses:
        return "ALERT"
    if "WARN" in statuses:
        return "WARN"
    return "NORMAL"


def _classify(metric: str, value: float, thresholds: dict[str, float]) -> str:
    warn = thresholds[f"warn_{metric}_pct"]
    alert = thresholds[f"alert_{metric}_pct"]
    if value >= alert:
        return "ALERT"
    if value >= warn:
        return "WARN"
    return "NORMAL"


def _validate_collected_metric(collected: dict[str, Any]) -> tuple[float, str]:
    value = collected["value"]
    unit = collected["unit"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("metric value must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric) or not (0.0 <= numeric <= 100.0):
        raise ValueError("metric value must be a finite percentage")
    if unit != "%":
        raise ValueError("metric unit must be percentage")
    return numeric, unit


class _GpuUnavailableError(Exception):
    """Internal signal: pynvml not importable or no NVIDIA device found."""


def _collect_gpu_pynvml() -> dict[str, Any]:
    try:
        import pynvml
    except ImportError as e:
        raise _GpuUnavailableError("pynvml_unavailable") from e

    try:
        pynvml.nvmlInit()
        if pynvml.nvmlDeviceGetCount() == 0:
            raise _GpuUnavailableError("pynvml_unavailable")
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        return {"value": float(util.gpu), "unit": "%"}
    except pynvml.NVMLError as e:
        raise _GpuUnavailableError("pynvml_unavailable") from e
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


def _extract_cadvisor_metric(stats: dict[str, Any], metric: str) -> dict[str, Any]:
    """Extract the latest sample for `metric` from a cAdvisor v2 stats payload.

    cAdvisor's real response shape is {container_id: {"stats": [...]}} —
    each stats entry carries cpu/memory/filesystem sub-objects. Only the
    fields this agent needs are read; no other stats/labels are retained.
    """
    container_stats = next(iter(stats.values()))
    samples = container_stats["stats"]
    latest = samples[-1]
    if metric == "cpu":
        if len(samples) < 2:
            raise ValueError("cAdvisor CPU percentage requires two samples")
        previous = samples[-2]
        elapsed = (
            dt.datetime.fromisoformat(latest["timestamp"].replace("Z", "+00:00"))
            - dt.datetime.fromisoformat(previous["timestamp"].replace("Z", "+00:00"))
        ).total_seconds()
        usage_delta = float(latest["cpu"]["usage"]["total"]) - float(previous["cpu"]["usage"]["total"])
        per_cpu = latest["cpu"]["usage"].get("per_cpu_usage") or [0]
        if elapsed <= 0 or usage_delta < 0:
            raise ValueError("invalid cAdvisor CPU sample interval")
        value = usage_delta / 1e9 / elapsed / len(per_cpu) * 100
        return {"value": value, "unit": "%"}
    if metric == "memory":
        usage = float(latest["memory"]["usage"])
        limit = float(latest["memory"]["limit"])
        if limit <= 0:
            raise ValueError("invalid cAdvisor memory limit")
        return {"value": usage / limit * 100, "unit": "%"}
    if metric == "disk":
        fs = latest["filesystem"][0]
        pct = (fs["usage"] / fs["capacity"]) * 100 if fs.get("capacity") else 0.0
        return {"value": pct, "unit": "%"}
    raise ValueError(f"unsupported metric for cadvisor backend: {metric}")


def _extract_prometheus_metric(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the scalar value from a Prometheus instant-query response.

    Only `value[1]` (the metric value) is read here — the full label set
    (payload["data"]["result"][0]["metric"]) is intentionally NOT returned
    from this function. Label sanitization (S-3 allowlist) happens in
    PostProcessNode, but this collector never surfaces raw labels upstream
    in the first place, as defense in depth.
    """
    result = payload.get("data", {}).get("result", [])
    if not result:
        raise ValueError("prometheus query returned no result series")
    value = float(result[0]["value"][1])
    return {"value": value, "unit": "%"}
