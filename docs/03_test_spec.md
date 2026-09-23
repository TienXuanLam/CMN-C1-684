# Test Specification

## Test Strategy
- Coverage target: all 3 domain nodes + 3 service modules + framework
  compliance (TC-01→11) + proof-of-boundary (PB-1→6, PB-7 auto-waived)
- Test types: Unit (per-service, per-node) / Proof-of-Boundary (framework
  boundary) / Integration (compiled-graph e2e)

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result |
|-------|------|----------------|
| TC-01 | State contract: flat TypedDict | `State` is a `TypedDict` subtype; no Pydantic/dataclass field |
| TC-02 | Domain input validation rejects invalid input | `PreProcessNode` sets `status=error` on disallowed `query_intent`/`target_context`/`target_id`/`threshold_config` |
| TC-03 | No JWT/Credential in `src/` | CI `gate-credential-scan`: 0 violations |
| TC-04 | `InvocationContext` accessed via `from_state()` only | No direct `InvocationContext(...)` construction inside any node |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start`/`node_complete`/`node_error` absent from all 3 nodes' `execute()` bodies |
| TC-06 | S-2: `_security_gate_input()` not overridden (`FunctionNode` subclass) | `TypeError` at class definition if overridden — not attempted anywhere in `src/nodes/` |
| TC-07 | S-3: `_security_gate_output()` not overridden (`FunctionNode` subclass) | `TypeError` at class definition if overridden — not attempted anywhere in `src/nodes/` |
| TC-08 | `required_trust_level` enforced (call via `__call__`, not `execute()`) | `TrustLevel.ANONYMOUS` caller refused (`status=error`) on all 3 domain nodes (`VERIFIED_EXTERNAL` required) |
| TC-09 | S-2: `_extra_security_gate_input()` non-trivial (`PreProcessNode`) | `query_intent`/`target_id`/`threshold_config` validation executes correctly and rejects malformed input |
| TC-10 | S-3: `_extra_security_gate_output()` non-trivial (`PostProcessNode`) | Undeclared metric-result key (label leak) raises `RuntimeError`, blocking output |
| TC-11 | S-4: at least one domain `emit_trace_event()` inside each `execute()` | `metric_backend_resolved` (pre_process), `metrics_collected` + `threshold_evaluated` (main), `health_report_generated` (post_process) |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result |
|-------|----------|------|----------------|
| PB-01 | Backend-unreachable handling (proposal §11 Risk #2) | Metric collection raises `requests.RequestException`/`OSError` | That metric returns `{"status": "ERROR", "reason": "backend_unreachable"}`; `overall_status` forced to `ALERT` |
| PB-02 | GPU graceful fallback (proposal §11 Risk #5) | `pynvml` unavailable / no NVIDIA device | `{"gpu": {"status": "N/A", "reason": "pynvml_unavailable"}}`; `overall_status` unaffected by N/A |
| PB-03 | S-3 Prometheus label non-leakage (proposal §11 Risk #1) | `_extract_prometheus_metric()` never returns raw label set; `PostProcessNode` allowlist blocks any undeclared metric-result key | No label key (`pod`, `namespace`, `instance`, etc.) ever reaches `formatted_output` |
| PB-04 | Import isolation | No Level 0 (`agenticstar`) imports anywhere in `src/` | AST scan: 0 violations |
| PB-05 | Invoke execution order | `__call__()`: S-1 trust gate → S-4 `node_start` → S-2 `_extra_security_gate_input` → `execute()` → S-3 `_extra_security_gate_output` → S-4 `node_complete` | Order verified via `node_history` + mock audit logger call sequence |
| PB-06 | State serialization | Post-invoke State is primitives only (`validated_plan_json`, `raw_metrics_json`, `evaluated_results_json` are JSON strings; `overall_status`/`formatted_output` are plain strings) | No dict/list bare annotation in `src/schemas/state.py` |
| PB-07 | HITL interrupt propagation *(conditional)* | `config/config.yaml` has no `hitl.enabled: true` and no node calls `interrupt()` | **Auto-waived — non-HITL** (proposal describes no human-review checkpoint; two explicit `@pytest.mark.skip` cases document the N/A determination) |

> **Pre-CoE gate checklist:** PB-01 through PB-06 are mandatory (PB-01/02/03
> are this template's domain-specific proof-of-boundary tests, mapped 1:1 to
> proposal §11 risks; PB-04/05/06 are the framework-standard boundary tests).
> PB-07 is auto-waived — non-HITL (no `interrupt()` call anywhere in `src/`).

## Business Logic Tests

| TC-ID | Test | Input | Expected Result |
|-------|------|-------|----------------|
| BL-01 | Threshold classification boundaries | `value` exactly at `warn`/`alert` threshold | `>= warn` → WARN (not NORMAL); `>= alert` → ALERT (not WARN) — inclusive boundary |
| BL-02 | Overall status aggregation — any ALERT wins | One metric ALERT, others NORMAL/WARN | `overall_status == "ALERT"` |
| BL-03 | Overall status aggregation — any WARN (no ALERT) | One metric WARN, others NORMAL | `overall_status == "WARN"` |
| BL-04 | Overall status aggregation — all NORMAL | All metrics NORMAL | `overall_status == "NORMAL"` |
| BL-05 | Missing threshold fields get safe defaults (proposal §11 Risk #4) | `threshold_config = {}` | `warn_*_pct=70`, `alert_*_pct=90` applied for every dimension |
| BL-06 | psutil permission-denied handling (proposal §11 Risk #3) | `PermissionError` raised by a psutil call | `{"status": "ERROR", "reason": "permission_denied"}` for that metric only |
| BL-07 | Backend resolution from `target_context` | `target_context in {"baremetal","docker","kubernetes"}` | Maps to `{"psutil","cadvisor","prometheus"}` respectively |
| BL-08 | `target_id` anti-injection rejection | `target_id` outside the strict identifier grammar, including PromQL metacharacters, path traversal (`..`), or shell syntax | `QueryPlanValidationError` raised, `PreProcessNode` returns `status=error` |
| BL-09 | Markdown vs JSON output format | `output_format="markdown"` vs `"json"` | `formatted_output` is Markdown text vs a JSON string respectively |
| BL-10 | `target_id` never logged raw (proposal §10 row 5) | Any invocation | `emit_trace_event()` payload for `health_report_generated` contains only `target_id_hash` (sha256[:16]), never the raw `target_id` |
| BL-11 | Threshold invariants | NaN/Infinity, values outside `[0,100]`, or `warn > alert` | `QueryPlanValidationError` raised before backend dispatch |
| BL-12 | cAdvisor percentage normalization | Two cumulative CPU samples and memory usage/limit | CPU and memory are emitted as finite percentages in `[0,100]` |
| BL-13 | Backend response validation | Missing keys, non-numeric/non-finite/out-of-range value, or unit other than `%` | Metric returns `ERROR/invalid_backend_response`; no invalid value reaches threshold evaluation |

## Test Execution Summary
- Execution date: 2026-08-20
- Total tests: 124 collected — 121 passed, 3 skipped (PB-7 and the
  pre-checkpoint-ingress case are conditionally not applicable)
- Coverage: unit (5 service test files + 3 node test files) + PB-01→06,
  standalone runtime trust/STG proof tests + integration (6 compiled-graph e2e cases: success,
  markdown output, GPU N/A fallback, disallowed-metric error, missing-target-id
  error, anonymous-caller blocked)
- Local gates: `ruff check` + `ruff format --check` (clean), `mypy src/`
  (15 files, no issues),
  `check_cat_consistency.py`/`check_dep_pinning.py`/`check_stub_tests.py`/
  `check_trust_level.py` (4/4 PASS), `check-security.sh` (8/8 PASS),
  `check-local.sh` full run — all gates PASS, including Stage 5 STG invoke
  evidence (`deploy/invoke_payload.json` customized with a valid
  `input_context`; `overall: PASS`, all 7 assertions true: manifest_parsed,
  payload_json_valid, health_200, invoke_200, response_json_valid,
  agent_status_success, no_security_violation)
