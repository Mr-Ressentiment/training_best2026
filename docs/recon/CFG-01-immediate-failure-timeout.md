# Reconnaissance Report: CFG-01 Immediate Failure Timeout

- **Contract**: VLD-CFG-01A / HG-CFG-01
- **Author**: Vladimir Barbalat (Project Brain / Integrator)
- **Status**: Ready for Human Gate Decision (`HG-CFG-01`)
- **Scope**: Reconcile canonical status of `IMMEDIATE_FAILURE_TIMEOUT`

---

## 1. Executive Summary

OrderShield exposes an environment variable and settings attribute `IMMEDIATE_FAILURE_TIMEOUT=5.0` in `app/config.py` and `.env.example`.
However, codebase audit reveals that:
1. `immediate_failure_timeout` is **never consumed** by `LiveAIProvider` or any network/service caller.
2. Actual provider network calls are governed solely by `LIVE_INFERENCE_TIMEOUT` (bounded at ≤15.0 s).
3. Immediately detectable failures (connection refusal, auth rejection, quota errors, unextractable documents) naturally terminate immediately upon detection (sub-second to ~1s), satisfying the SC-006 SLA target (≤5s) without any separate timer.
4. Exposing `IMMEDIATE_FAILURE_TIMEOUT` as a runtime configuration knob is misleading because changing its value has zero effect on runtime behavior.

---

## 2. Comprehensive Reference Audit

### 2.1 Code & Configuration References
- `app/config.py`:
  - Line 21-29: `_get_float_env("IMMEDIATE_FAILURE_TIMEOUT", 5.0)`
  - Line 45, 67-71: `Settings.__init__(..., immediate_failure_timeout=5.0)`
  - Line 90-91: `@property def IMMEDIATE_FAILURE_TIMEOUT(self) -> float`
- `.env.example`:
  - Line 14: `IMMEDIATE_FAILURE_TIMEOUT=5.0`
- `app/services/ai_provider.py`:
  - **Zero references.** `LiveAIProvider.__init__` reads only `settings.live_inference_timeout` and enforces `httpx.Timeout(min(budget, 15.0))`.
- `tests/`:
  - `tests/unit/test_ai_provider.py`: Does not reference `immediate_failure_timeout`. Tests immediate failures via mock transport that returns 401, connection error, 429, or 503 synchronously.
  - `tests/unit/test_cli.py`: Does not reference `immediate_failure_timeout`.
  - `tests/integration/test_quickstart_scenarios.py`: Validates Scenario 3 (immediate failure returns HTTP 503) using mocked immediate failure without referencing the setting.

### 2.2 Specification, ADR, and Governance References
- **ADR 0001** (§5.2):
  - Defines the approved SC-006 reconciled policy across four accepted categories:
    1. *Immediately Detectable Failures*: Target ≤5s from request intake (unreadable document, connection refusal, auth failure, quota rejection, explicit upstream 4xx/5xx).
    2. *Healthy Live Inference*: Bounded ≤15s budget based on empirical bake-off latency (p50 ~7.3s, max ~9.4s).
    3. *Silent/Stalled Provider Inference*: Aborted at ≤15s with explicit diagnostic error, zero partial persistence, zero silent fallback.
    4. *Zero Automatic Failover or Fixture Substitution*.
  - Note: ADR 0001 specifies ≤5s as a **performance target from request intake** for immediately detectable errors, NOT as an independent socket/HTTP client timeout variable.
- **Specification (`spec.md`)**:
  - SC-006: "In the event of an unreadable document or immediately detectable AI provider failure (...), the system presents an explicit diagnostic error with a target of ≤5 seconds from request intake without partial data corruption or silent fallback."
- **Plan (`plan.md`)**:
  - Mentions target ≤5 seconds for immediately detectable failures (SC-006).
- **Tasks (`tasks.md`)**:
  - T003 specifies adding `IMMEDIATE_FAILURE_TIMEOUT=5.0` to `app/config.py` and `.env.example`.
- **README (`README.md`)**:
  - Line 144: Note explains: "`IMMEDIATE_FAILURE_TIMEOUT=5.0`: Reference/configuration value for the immediate-failure target used by the accepted requirements/tests; LiveAIProvider does not currently enforce it as a separate timer."

---

## 3. Detailed Answers to Reconnaissance Questions

### Q1: Is the setting semantically required?
**No.**  
The semantic requirement defined by SC-006 and ADR 0001 is that immediately detectable failures must fail fast and present an explicit diagnostic error with a target of ≤5 seconds from intake. This is an invariant of error propagation (fail-closed, no retries, no failover), not a configurable duration.

### Q2: Is it actually consumed anywhere?
**No.**  
In `app/`, only `app/config.py` defines it. Neither `LiveAIProvider`, `order_service.py`, `document_parser.py`, nor any API route inspects or uses `settings.immediate_failure_timeout`.

### Q3: Would removing it change runtime behavior?
**No.**  
Runtime behavior is completely governed by:
- Immediate detection: Synchronous failure upon local parse error or HTTP error response (sub-second).
- Network deadline: `settings.live_inference_timeout` (capped at ≤15.0s) passed to `httpx.Timeout`.
Removing `immediate_failure_timeout` from `Settings` has zero impact on error handling or request processing.

### Q4: Which accepted artifacts currently require or reference it?
1. `app/config.py`: Attribute and property on `Settings`.
2. `.env.example`: Non-secret placeholder.
3. `README.md`: Environment reference and table row (which already had to document that it is not enforced as a separate timer).
4. `specs/001-ordershield-po-reconciliation/tasks.md`: Task T003 description.

### Q5: Is a distinct 5-second timer technically meaningful?
**No, and attempting to implement one would be harmful.**  
Consider what an independent 5-second timer would do:
- If a provider request is healthy and progressing normally, bake-off data proves it takes 7.0–9.8s. A 5-second timeout on the request would abort healthy inferences (false positives), which is why ADR 0001 explicitly rejected a 4.5–5s client timeout.
- For immediately detectable errors (DNS failure, connection refused, 401, 403, 429, 503), the transport fails immediately without waiting 5 seconds.
- For stalled or silent requests (server hung without sending data), the client cannot know within 5 seconds whether the server is computing or dead, so the bounded ≤15s deadline applies per ADR 0001 §5.2.
Thus, a 5-second client timer cannot distinguish healthy inference from hung inference and would break live operation.

---

## 4. Recommended Action

**Action: REMOVE AS MISLEADING RUNTIME CONFIG**

### Rationale:
- Retaining an unused environment variable `IMMEDIATE_FAILURE_TIMEOUT` confuses developers, judges, and operators into believing they can tune immediate failure behavior, or that requests will time out after 5 seconds.
- The approved SC-006 semantics (target ≤5s for immediately detectable errors, bounded ≤15s for inference) remain 100% intact as architectural and SLA requirements.
- Removing `immediate_failure_timeout` from `app/config.py`, `.env.example`, and `README.md` cleans up dead configuration without touching any business logic, API contract, or test.

---

## 5. Decision Submission for Human Gate HG-CFG-01

Recommended Decision:
```text
REMOVE AS MISLEADING RUNTIME CONFIG
```

Awaiting Human Gate HG-CFG-01 review before applying VLD-CFG-01B.
