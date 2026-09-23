# Template Design Specification

## Position in AgentCore Architecture

- **Agent Class**: `AgentHealthResourceMonitorAgent`
- **L1 Base**: `AgentBaseGraph`
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution)

> **Node mapping note:** The proposal's workflow (§4) describes exactly 3
> steps — `PreProcessNode` (input validation + backend resolution),
> `MainNode` (ToolCallingAgent — metric collection + threshold evaluation),
> `PostProcessNode` (output sanitization + report assembly). This maps 1:1
> onto `AgentBaseGraph`'s 3 fixed domain slots
> (`pre_process`/`main`/`post_process`) — no step-helper pattern is required
> (unlike some templates elsewhere in the fleet, which needed N-step → 3-slot reconciliation).
> All 3 nodes always execute; there is no conditional short-circuit routing
> (proposal §10 row 3: "no skip — callers need the NORMAL confirmation").

## Architecture Overview

```
[Caller: Orchestrator / MLOps Pipeline / On-demand]
    |
    ├─ query_intent      (list — ["cpu","memory","disk","gpu"] subset)
    ├─ target_context    (str — "baremetal"|"docker"|"kubernetes")
    ├─ target_id         (str — hostname/container_id/pod_name)
    ├─ threshold_config  (dict — warn_*_pct/alert_*_pct per dimension)
    └─ output_format     (str — "json"|"markdown", default "json")
    v
┌─────────────────────────────────────────┐
│ pre_process (PreProcessNode)             │
│  S-2: validate query_intent allowed-     │
│  values, target_id anti-injection,       │
│  threshold_config range (0-100)          │
│  Resolve backend from target_context;    │
│  build per-metric tool call plan         │
└──────────────┬────────────────────────────┘
               │ validated_plan_json{backend, metrics[], thresholds}
               v
┌─────────────────────────────────────────┐
│ main (MainNode — ToolCallingAgent)       │
│  Dispatch tool calls per backend:        │
│    psutil / cAdvisor REST / Prometheus   │
│  Collect raw metric values                │
│  Evaluate vs thresholds -> NORMAL/WARN/   │
│  ALERT per metric; aggregate overall     │
└──────────────┬────────────────────────────┘
               │ raw_metrics_json, evaluated_results_json, overall_status
               v
┌─────────────────────────────────────────┐
│ post_process (PostProcessNode)           │
│  S-3: structural allowlist check on      │
│  metric-result keys (value/unit/status/  │
│  reason only) — rejects output if an     │
│  undeclared key (e.g. a leaked label)    │
│  appears; labels are never returned by   │
│  the collector in the first place        │
│  Assemble JSON/Markdown report            │
│  S-4 audit log: invocation_id,            │
│  hashed target_id, query_intent,          │
│  overall_status, timestamp                │
└──────────────┬────────────────────────────┘
               v
[Output: Structured Health Report]
    ├─► Datadog / Grafana
    ├─► Parent orchestrator health gate
    └─► EU AI Act Art.12 / NISC audit log store
```

### Node Configuration

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | schema_version, session_id, trust_level | — | — | `InitializeNode` (default) |
| pre_process | S-2 input validation; resolve metric backend from `target_context`; build tool call plan | `input_context` (query_intent, target_context, target_id, threshold_config, output_format) | `validated_plan_json`, `status` | `FunctionNode` |
| main | Dispatch tool calls to resolved backend; collect raw metrics; evaluate thresholds; compute `overall_status` | `validated_plan_json` | `raw_metrics_json`, `evaluated_results_json`, `overall_status`, `status` | `FunctionNode` (ToolCallingAgent pattern — no LLM call) |
| post_process | S-3 structural label-leak check (rejects output on undeclared metric-result key); assemble JSON/Markdown report; S-4 audit log | `evaluated_results_json`, `overall_status`, `raw_metrics_json` | `formatted_output`, `status` | `FunctionNode` |
| finalize | Builds `response_metadata`, `total_time_ms` | `formatted_output` | (response envelope) | `FinalizeNode` (default) |

### Step Detail

| Step | Core Logic | Output |
|---|---|---|
| 1. `PreProcessNode` | S-2 input validation (query_intent allowed values, target_id anti-injection, threshold_config 0-100 range); resolve backend (`baremetal`→psutil, `docker`→cAdvisor REST, `kubernetes`→Prometheus); apply threshold defaults for missing fields (`warn_*_pct=70`, `alert_*_pct=90`, proposal §11 Risk #4); build per-metric tool call plan | `validated_plan_json{backend, metrics[], thresholds}` |
| 2. `MainNode` | Dispatch tool calls to resolved backend (psutil / cAdvisor REST `GET /api/v2/stats/{container_id}` / Prometheus `GET /api/v1/query`); collect raw values with timestamps; evaluate each against thresholds (`value<warn`→NORMAL, `warn<=value<alert`→WARN, `value>=alert`→ALERT); backend unreachable → `{"status":"ERROR","reason":"backend_unreachable"}` per metric, forces `overall_status=ALERT` (proposal §11 Risk #2); GPU unavailable (no pynvml/no NVIDIA device) → `{"status":"N/A","reason":"pynvml_unavailable"}`, dimension excluded from tool call plan (proposal §11 Risk #5); aggregate `overall_status` (any ALERT→ALERT; else any WARN→WARN; else NORMAL) | `raw_metrics_json`, `evaluated_results_json`, `overall_status` |
| 3. `PostProcessNode` | S-3: structural allowlist check — each metric-result dict may only carry `value`/`unit`/`status`/`reason`; an undeclared key (e.g. a leaked Prometheus label) raises `RuntimeError` and blocks output (proposal §11 Risk #1; labels themselves are stripped earlier, at collection time — see `metric_collection_service._extract_prometheus_metric()`); assemble output in `output_format` (json/markdown); S-4 audit log (`invocation_id`, **hashed** `target_id` — never raw, `query_intent`, `overall_status`, `timestamp`) | Final structured health report + audit log entry |

### Data Flow

```
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                            ↓ (retry, max 3)
                                          pre_process
```

No conditional short-circuit: all 3 domain nodes always execute regardless of
`overall_status` (even an all-NORMAL result requires the full report — proposal
§10 row 3). The retry loop is the framework's standard `AgentBaseGraph` error-recovery
path (routes back to `pre_process` on `status=error`, up to `max_retry`), not a
domain-specific branch.

### State Definition

| Field | Type | Purpose | Producer | Consumer | ADR-005 note |
|-------|------|---------|----------|----------|--------------|
| `validated_plan_json` | `str \| None` (JSON: `{backend: str, metrics: list[str], thresholds: dict}`) | Resolved backend + per-metric tool call plan | `pre_process` | `main` | JSON string (ADR-005) — backend/metrics/thresholds is a structured dict |
| `raw_metrics_json` | `str \| None` (JSON: `dict[str, {value: float, unit: str, timestamp: str}]`) | Raw collected metric values before threshold evaluation | `main` | `post_process` | JSON string |
| `evaluated_results_json` | `str \| None` (JSON: `dict[str, {value, unit, status}]`) | Per-metric NORMAL/WARN/ALERT/ERROR/N-A annotation | `main` | `post_process` | JSON string |
| `overall_status` | `str \| None` (`"NORMAL"\|"WARN"\|"ALERT"`) | Aggregate health verdict across all evaluated metrics | `main` | `post_process` | Plain string — not structured, no `_json` suffix needed |
| `formatted_output` | `str \| None` (JSON string when `output_format="json"`, else Markdown text) | Final assembled payload (report + audit_ref) | `post_process` | `AgentBaseGraph.get_output()` (top-level `/invoke` response `output` field) | **No `_json` suffix** — this is the literal key the framework's `get_output()` reads (renaming this field silently breaks the `/invoke` response) |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types) — every structured
  field above is stored as a JSON string (`*_json` suffix), written with
  `json.dumps()` by the producing node and read with `json.loads()` by the
  consuming node (ADR-005). Exception: `formatted_output` (no suffix — literal
  framework contract key, see above) and `overall_status` (already a plain string).
- No JWT, API keys, credentials in State (checkpoint DB leakage) — resource
  metrics contain no APPI personal data (proposal §10 row 5).
- InvocationContext via `InvocationContext.from_state(state)` inside `execute()`
  only (not stored in State).
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible).

## Security Design

| Layer | Implementation | Risk Mitigated (proposal §11) |
|-------|----------------|-------------------------------|
| S-1 | `required_trust_level = TrustLevel.VERIFIED_EXTERNAL` on all 3 domain nodes — this agent is invoked by orchestrators/MLOps pipelines, not anonymous callers | — |
| S-2 | `PreProcessNode._extra_security_gate_input()`: `query_intent` must be a unique list of at most four allowed strings (`cpu`/`memory`/`disk`/`gpu`); `target_id` must match the strict backend-safe identifier grammar and cannot contain `..`; threshold values must be finite percentages in `[0,100]`, with every warn threshold less than or equal to its alert threshold | Prevents malformed input and backend query/path injection from reaching dispatch |
| S-3 | Two-layer defense: (1) `metric_collection_service._extract_prometheus_metric()` never returns raw labels (pod names, namespace paths, internal service endpoints) in the first place — only `value`/`unit`; (2) `PostProcessNode._extra_security_gate_output()` is a structural **allowlist** check (`value`/`unit`/`status`/`reason` only) that **rejects the entire output** (raises `RuntimeError`) if an undeclared key is present, rather than silently stripping it | Risk #1 (label leakage exposing internal service topology) — allowlist chosen over denylist per proposal §10 row 5 rationale |
| S-4 | `emit_trace_event()` in every node's `execute()`; `PostProcessNode` additionally logs `invocation_id`, **hashed** `target_id` (never raw — avoids exposing infrastructure identifiers), `query_intent`, `overall_status`, `timestamp` | EU AI Act Article 12 / NISC compliance audit trail (proposal §10 row 5, §12 dep #8) |

**Design decisions specific to this agent's risk profile:**
- **Backend-unreachable handling (Risk #2):** every metric backend call is
  wrapped so a connection failure/timeout/4xx/5xx yields
  `{"status": "ERROR", "reason": "backend_unreachable"}` for that metric only
  — never an unhandled exception. `overall_status` is forced to `ALERT`
  (failure-to-check treated as alert, not silently ignored).
  cAdvisor/Prometheus prerequisites are deployment-time, documented in
  `docs/07_operation_guide.md`, not a build blocker (proposal §12 deps #6/#7).
- **psutil permission handling (Risk #3):** only system-level aggregates
  (`cpu_percent`, `virtual_memory`, `disk_usage`) are called — no per-process
  metrics requiring elevated privileges. A `PermissionError` on a specific
  metric returns `{"status": "ERROR", "reason": "permission_denied"}` for
  that metric, not a node-level failure.
- **Missing threshold config (Risk #4):** `PreProcessNode` fills any missing
  `warn_*_pct`/`alert_*_pct` field with safe defaults (`warn=70`, `alert=90`)
  before building the tool call plan — evaluation never fails due to an
  incomplete `threshold_config`.
- **Backend value normalization:** every collected metric is validated as a
  finite percentage in `[0,100]`. cAdvisor CPU is calculated from two
  cumulative-usage samples and normalized by elapsed time and CPU count;
  memory is normalized as working-set bytes divided by the container limit.
  Malformed backend payloads become an explicit `invalid_backend_response`.
- **GPU non-availability (Risk #5):** `pynvml` import / NVIDIA device
  presence is checked in `pre_process`; if unavailable, `"gpu"` is dropped
  from the tool call plan and `post_process` reports
  `{"gpu": {"status": "N/A", "reason": "pynvml_unavailable"}}` rather than
  failing the whole invocation.

## Framework Utilization

### Shared Components Used
- [x] `InvocationContext.from_state(state)` — no secrets required by this
      agent (all 3 backends are unauthenticated-by-default in the reference
      implementation: local psutil calls, cAdvisor/Prometheus assumed
      reachable over the cluster-internal network; if a deployment requires
      a bearer token for cAdvisor/Prometheus, it would be declared under
      `requires.secrets` in `agent.yaml` and read via `ctx.secrets.require()`
      — not needed for the MVP scope)
- [x] S-2: `_extra_security_gate_input()` — query_intent/target_id/threshold_config validation (see Security Design)
- [x] S-3: `_extra_security_gate_output()` — Prometheus label allowlist (see Security Design)
- [x] S-4: `emit_trace_event()` — domain events per node (`metric_backend_resolved`, `metrics_collected`/`threshold_evaluated`, `health_report_generated`)

> **S-2/S-3 gate behaviour (ADR-017):** All 3 domain nodes are `FunctionNode`
> subclasses — the framework `@final` gate always runs automatically; domain
> checks are added via `_extra_security_gate_input()` / `_extra_security_gate_output()`
> only, never by overriding the `@final` methods.

### Composition Pattern

- **Pattern**: Standalone (no `GraphNode`/`RemoteAgentNode` composition in
  this template — it is itself the reusable Cat 1 primitive that Cat 2
  orchestrators compose via `RemoteAgentNode`, out of scope for this repo)
- **Composition target**: N/A
- **Error propagation strategy**: N/A

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework/` and `shared/` only (no `agents/base/` required)

## Class Name Consistency

| Location | Value |
|----------|-------|
| `config/agent.yaml` → `class` | `src.graph.graph.AgentHealthResourceMonitorAgent` |
| `config/agent.yaml` → `base_type` | `AgentBaseGraph` |
| `src/graph/graph.py` → class name | `AgentHealthResourceMonitorAgent` |
| `src/graph/graph.py` → `name` property | `"AgentHealthResourceMonitorAgent"` |
| `src/api/server.py` → imported class | `AgentHealthResourceMonitorAgent` |
| `src/api/server.py` → `secrets_factory(namespace=..., agent_name=...)` | `"cmn", "cmn-c1-684"` |

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | `AgentBaseGraph` | `AutonomousBaseGraph` | **A** | Fixed 3-step pipeline (proposal §4); no autonomous think/act loop needed — metric collection is deterministic, not LLM-decided |
| Node-count reconciliation | Step-helper pattern (N steps → 3 slots) | Direct 1:1 mapping | **B** | Proposal describes exactly 3 steps that map cleanly onto `pre_process`/`main`/`post_process` — no reconciliation needed, unlike some templates elsewhere in the fleet |
| S-3 Prometheus sanitization strategy | Denylist (strip known-sensitive labels) | Allowlist (retain only known-safe fields) | **B (allowlist)** | Denylist requires anticipating every possible sensitive label name across arbitrary Prometheus exporters — a new exporter could introduce an unanticipated sensitive label. Implemented as two layers: `_extract_prometheus_metric()` never returns labels at all (only `value`/`unit`), and `PostProcessNode`'s structural gate rejects the whole output if an undeclared key ever appears — fail-safe by blocking, not by silently stripping (proposal §10 row 5) |
| Backend-unreachable status | Silent skip (omit metric from report) | Explicit `ERROR` status + force `overall_status=ALERT` | **B** | A silently-omitted metric could mask a real outage from the caller (e.g., disk metric silently absent because cAdvisor is down, while disk is actually full). Treating "failed to check" as "must alert" is the safer default (proposal §11 Risk #2) |
| LLM usage | LLM-assisted narrative summary | Fully deterministic (no LLM call) | **B** | Proposal explicitly scopes this as zero-LLM-cost (§10 row 4) — metric collection, threshold comparison, and status aggregation are all deterministic arithmetic; no hallucination risk on a compliance-relevant health signal |
