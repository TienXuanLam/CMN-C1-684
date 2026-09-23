# CMN-C1-684 — Unit Tests: query_plan_service

import pytest

from src.services.query_plan_service import QueryPlanValidationError, validate_and_build_plan


def _input_context(**overrides) -> dict:
    base = {
        "query_intent": ["cpu", "memory"],
        "target_context": "baremetal",
        "target_id": "host-01",
        "threshold_config": {"warn_cpu_pct": 60, "alert_cpu_pct": 85},
        "output_format": "json",
    }
    base.update(overrides)
    return base


class TestValidateAndBuildPlan:
    def test_valid_input_builds_plan(self):
        plan = validate_and_build_plan(_input_context())

        assert plan["backend"] == "psutil"
        assert plan["target_context"] == "baremetal"
        assert plan["target_id"] == "host-01"
        assert plan["metrics"] == ["cpu", "memory"]
        assert plan["output_format"] == "json"

    @pytest.mark.parametrize(
        "target_context,expected_backend",
        [("baremetal", "psutil"), ("docker", "cadvisor"), ("kubernetes", "prometheus")],
    )
    def test_backend_resolution_per_target_context(self, target_context, expected_backend):
        plan = validate_and_build_plan(_input_context(target_context=target_context))
        assert plan["backend"] == expected_backend

    def test_empty_query_intent_rejected(self):
        with pytest.raises(QueryPlanValidationError, match="query_intent"):
            validate_and_build_plan(_input_context(query_intent=[]))

    def test_disallowed_metric_rejected(self):
        with pytest.raises(QueryPlanValidationError, match="disallowed"):
            validate_and_build_plan(_input_context(query_intent=["cpu", "network"]))

    def test_non_string_metric_rejected_without_type_error(self):
        with pytest.raises(QueryPlanValidationError, match="strings"):
            validate_and_build_plan(_input_context(query_intent=["cpu", {"metric": "memory"}]))

    def test_duplicate_metric_rejected(self):
        with pytest.raises(QueryPlanValidationError, match="duplicate"):
            validate_and_build_plan(_input_context(query_intent=["cpu", "cpu"]))

    def test_invalid_target_context_rejected(self):
        with pytest.raises(QueryPlanValidationError, match="target_context"):
            validate_and_build_plan(_input_context(target_context="vmware"))

    def test_empty_target_id_rejected(self):
        with pytest.raises(QueryPlanValidationError, match="target_id"):
            validate_and_build_plan(_input_context(target_id=""))

    @pytest.mark.parametrize(
        "unsafe_target_id",
        [
            "host; rm -rf /",
            "host`whoami`",
            "host$(whoami)",
            "host && echo x",
            "host|cat /etc/passwd",
            'pod"} or vector(1) #',
            "../admin",
            "host/name",
        ],
    )
    def test_target_id_anti_injection(self, unsafe_target_id):
        with pytest.raises(QueryPlanValidationError, match="target_id"):
            validate_and_build_plan(_input_context(target_id=unsafe_target_id))

    def test_missing_threshold_config_applies_defaults(self):
        plan = validate_and_build_plan(_input_context(threshold_config={}))

        assert plan["thresholds"]["warn_cpu_pct"] == 70.0
        assert plan["thresholds"]["alert_cpu_pct"] == 90.0
        assert plan["thresholds"]["warn_gpu_pct"] == 70.0
        assert plan["thresholds"]["alert_gpu_pct"] == 90.0

    def test_partial_threshold_config_fills_only_missing_fields(self):
        plan = validate_and_build_plan(_input_context(threshold_config={"warn_cpu_pct": 50}))

        assert plan["thresholds"]["warn_cpu_pct"] == 50.0
        assert plan["thresholds"]["alert_cpu_pct"] == 90.0  # default filled

    def test_threshold_out_of_range_rejected(self):
        with pytest.raises(QueryPlanValidationError, match="warn_cpu_pct"):
            validate_and_build_plan(_input_context(threshold_config={"warn_cpu_pct": 150}))

    def test_threshold_non_numeric_rejected(self):
        with pytest.raises(QueryPlanValidationError, match="warn_cpu_pct"):
            validate_and_build_plan(_input_context(threshold_config={"warn_cpu_pct": "high"}))

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_threshold_rejected(self, value):
        with pytest.raises(QueryPlanValidationError, match="warn_cpu_pct"):
            validate_and_build_plan(_input_context(threshold_config={"warn_cpu_pct": value}))

    def test_warn_threshold_above_alert_rejected(self):
        with pytest.raises(QueryPlanValidationError, match="must not exceed"):
            validate_and_build_plan(_input_context(threshold_config={"warn_cpu_pct": 95, "alert_cpu_pct": 90}))

    def test_invalid_output_format_rejected(self):
        with pytest.raises(QueryPlanValidationError, match="output_format"):
            validate_and_build_plan(_input_context(output_format="xml"))

    def test_default_output_format_is_json(self):
        ctx = _input_context()
        del ctx["output_format"]
        plan = validate_and_build_plan(ctx)
        assert plan["output_format"] == "json"
