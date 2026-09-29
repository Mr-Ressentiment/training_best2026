# OrderShield provider preflight

Live model calls attempted: **3 / 3 maximum**.
Free-only account settings: USER_CONFIRMED.

| Provider | Requested model | Result | HTTP | Latency ms | Phase 1 eligible |
|---|---|---|---|---|---|
| Google Gemini API | gemini-3.7-flash | PASSED | 200 | 13682.8 | True |
| Google Gemini API | gemini-3.8-flash | FAILED | 503 | 1091.58 | False |
| Alibaba Model Studio (Singapore) | qwen3.8-flash | FAILED | 403 | 904.18 | False |

## Evidence and limitations

- gemini-3.7-flash: access=ACCEPTED; quota=REQUEST_ACCEPTED; remaining quota unknown; failure=None; blockers=none.
- gemini-3.8-flash: access=UNVERIFIED; quota=UNVERIFIED; failure=PROVIDER_5XX; blockers=none.
- qwen3.8-flash: access=DENIED; quota=UNVERIFIED; failure=AUTH_OR_ACCESS; blockers=none.

Null measurements mean no observation, never zero latency or zero tokens.
NOT_CALLED is a local prerequisite block, not a provider failure or proof of unavailable models.
No h01-h10 fixture, real document, product implementation, or Phase 1 evaluation is used.
Qwen uses enable_thinking=false and compact JSON prompting because exact Singapore structured-output support is unverified.
Gemini uses thinkingConfig.thinkingLevel=low, JSON MIME type, and no tools/search/grounding.
One accepted tiny request does not establish remaining evaluation quota, quality, or production latency compliance.
The 30-second preflight socket timeout is diagnostic only; it does not redefine the frozen product timeout.
MALFORMED_JSON also covers valid JSON that fails the required object validation.
No raw provider bodies, prompts with real data, keys, environment values, or exception messages are persisted.

## Run

`python -B spikes/ordershield/provider_bakeoff/preflight.py` records blocked prerequisites without sending requests.
After supplying the three environment variables to the execution process and confirming both account settings:
`python -B spikes/ordershield/provider_bakeoff/preflight.py --confirm-free-only`
The flag attests Gemini Free tier and active Alibaba Free Quota Only for this exact model; it does not configure billing.
A persisted live attempt prevents all later runs, including automatic retries after interruption.
`python -B -m unittest discover -s spikes/ordershield/provider_bakeoff -p "test_*.py" -v` runs offline tests.

## Provider references

- https://ai.google.dev/gemini-api/docs/generate-content/thinking
- https://ai.google.dev/gemini-api/docs/pricing
- https://www.alibabacloud.com/help/en/model-studio/deep-thinking
- https://www.alibabacloud.com/help/en/model-studio/new-free-quota

## Access remediation round 1

The preceding initial-round evidence is preserved verbatim.
New live calls attempted: **3 / 3 maximum**; lifetime preflight calls: **6**.
Exactly one new attempt per candidate; no retries, redirects, substitutions, or evaluation.
Free-only account settings use the user confirmation already supplied in this session.
Qwen is permitted only at a workspace-specific Singapore endpoint read from QWEN_BASE_URL; endpoint value is never recorded.

| Provider | Requested model | Thinking | Result | HTTP | Wall latency ms | JSON/schema | Returned model | Phase 1 eligible |
|---|---|---|---|---|---|---|---|---|
| Alibaba Model Studio (Singapore) | qwen3.8-flash | disabled | PASSED | 200 | 1820.32 | PASS | qwen3.8-flash | True |
| Google Gemini API | gemini-3.8-flash | low | PASSED | 200 | 2902.99 | PASS | gemini-3.8-flash | True |
| Google Gemini API | gemini-3.5-flash-lite | minimal | PASSED | 200 | 852.87 | PASS | gemini-3.5-flash-lite | True |

### Access, quota, and token evidence

- qwen3.8-flash: access=ACCEPTED; quota=REQUEST_ACCEPTED; remaining quota unknown; failure=None; token usage={"prompt_tokens": 43, "total_tokens": 54, "completion_tokens": 11, "prompt_tokens_details": {"cached_tokens": 0, "text_tokens": 43}}; blockers=none.
- gemini-3.8-flash: access=ACCEPTED; quota=REQUEST_ACCEPTED; remaining quota unknown; failure=None; token usage={"promptTokenCount": 19, "candidatesTokenCount": 11, "totalTokenCount": 30}; blockers=none.
- gemini-3.5-flash-lite: access=ACCEPTED; quota=REQUEST_ACCEPTED; remaining quota unknown; failure=None; token usage={"promptTokenCount": 19, "candidatesTokenCount": 21, "totalTokenCount": 40}; blockers=none.

### Configuration and limitations

The tiny prompt and JSON object validation are identical to the initial round. Schema success requires a JSON object with status=ok and item=synthetic-widget.
Qwen uses enable_thinking=false, stream=false, max_tokens=128, compact JSON prompting and local validation; structured-output support for the exact endpoint/model is unverified.
Gemini 3.8 uses thinkingConfig.thinkingLevel=low. Gemini 3.5 Flash-Lite uses thinkingConfig.thinkingLevel=minimal, the lowest supported level; both use JSON MIME type and maxOutputTokens=256.
No tools, search, grounding, real documents, or h01-h10 fixtures are sent.
Phase 1 eligibility is limited to the tiny access/format check and does not authorize or start Phase 1.
A successful request does not establish remaining free quota or the frozen product latency guarantee.
HTTP errors have no generated-object observation; null usage or model ID means not exposed/recorded.
The 30-second socket timeout is diagnostic only and does not redefine product requirements.
Only preflight.json and preflight.md are updated. The existing adapter/parser is reused from an ephemeral Python stdin driver; adapter/test sources remain unchanged.

References: [thinking levels](https://ai.google.dev/gemini-api/docs/generate-content/thinking), [free-tier pricing](https://ai.google.dev/gemini-api/docs/pricing).

The corrected QWEN_BASE_URL was read from Windows user environment and used in a transient process override because the inherited process value was stale. No endpoint value was printed or persisted.
