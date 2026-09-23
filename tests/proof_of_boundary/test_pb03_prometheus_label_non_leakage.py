# PB-03 — S-3 Prometheus label non-leakage (proposal §11 Risk #1)
#
# Prometheus metric responses carry rich label sets (pod names, namespace
# paths, internal service endpoints) that, if passed through, would reveal
# internal infrastructure topology to the caller. This boundary is enforced
# twice (defense in depth):
#   1. metric_collection_service._extract_prometheus_metric() never returns
#      raw labels in the first place — only value/unit.
#   2. PostProcessNode._extra_security_gate_output() structurally rejects
#      any undeclared key in the assembled report's metrics dict.

import json

from framework.schemas.trust_level import TrustLevel
from src.nodes.post_process_node import PostProcessNode
from src.services.metric_collection_service import _extract_prometheus_metric


class TestPB03PrometheusLabelNonLeakage:
    def test_extractor_never_surfaces_raw_labels(self):
        """Defense in depth #1: the collector itself drops labels."""
        payload = {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {
                        "metric": {
                            "__name__": "container_cpu_usage_seconds_total",
                            "pod": "internal-payment-service-7f9c8b",
                            "namespace": "prod-payments",
                            "instance": "10.244.1.7:9100",
                            "node": "gke-node-pool-3",
                        },
                        "value": [1234567890, "42.5"],
                    }
                ],
            },
        }

        result = _extract_prometheus_metric(payload)

        assert set(result) == {"value", "unit"}
        for leaked_field in ("pod", "namespace", "instance", "node"):
            assert leaked_field not in result

    def test_post_process_node_blocks_output_if_label_leaks_through(self):
        """Defense in depth #2: even if a label somehow reached
        evaluated_results (e.g. a future backend change), PostProcessNode's
        S-3 gate must block the output rather than silently pass it through."""
        node = PostProcessNode()
        state = {
            "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
            "correlation_id": "pb03-test",
            "node_history": [],
            "error_log": [],
            "validated_plan_json": json.dumps({"target_id": "pod-1", "output_format": "json"}),
            "evaluated_results_json": json.dumps(
                {
                    "cpu": {
                        "value": 42.5,
                        "unit": "%",
                        "status": "NORMAL",
                        "namespace": "prod-payments",  # simulated leak
                    }
                }
            ),
            "overall_status": "NORMAL",
        }

        result = node.execute(state)

        raised = False
        try:
            node._extra_security_gate_output(result)
        except RuntimeError as e:
            raised = True
            assert "namespace" in str(e)

        assert raised is True, "S-3 gate must reject a leaked label field, not pass it through"

    def test_post_process_node_allows_clean_output(self):
        """Sanity check: the S-3 gate is not overly strict — clean
        evaluated_results (no leaked labels) must pass through unmodified."""
        node = PostProcessNode()
        state = {
            "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
            "correlation_id": "pb03-test-2",
            "node_history": [],
            "error_log": [],
            "validated_plan_json": json.dumps({"target_id": "pod-1", "output_format": "json"}),
            "evaluated_results_json": json.dumps({"cpu": {"value": 42.5, "unit": "%", "status": "NORMAL"}}),
            "overall_status": "NORMAL",
        }

        result = node.execute(state)
        gated = node._extra_security_gate_output(result)

        assert gated == result
