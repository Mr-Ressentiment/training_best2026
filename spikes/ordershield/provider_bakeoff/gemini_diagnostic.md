# Gemini Phase-1 diagnostic

Scope: h01 only, Flash-Lite/minimal; at most two explicit single attempts. No Qwen, benchmark rerun, automatic retry, or model substitution.

## Offline comparison (completed before any live diagnostic call)

| Area | Successful tiny preflight | Original Phase 1 |
|---|---|---|
| Endpoint / version | generativelanguage.googleapis.com/v1beta/models/{model}:generateContent | Identical |
| Headers / authentication | Content-Type application/json; x-goog-api-key from GEMINI_API_KEY | Identical; header values never persisted |
| Model ID | 3.8 Flash passed; 3.5 Flash-Lite passed remediation | Same exact model IDs; model in URL, no body model field |
| Body structure | contents: one user role, one text part; generationConfig | Identical envelope; text is frozen prompt + synthetic catalog + one fixture |
| thinkingConfig | thinkingLevel low (3.8), minimal (successful Lite remediation) | Identical per-model levels; no thinkingBudget or includeThoughts |
| Response MIME / format | responseMimeType: application/json | responseFormat.text.mimeType: application/json |
| JSON schema | None (two-field synthetic object, locally checked) | Full unchanged application JSON Schema at responseFormat.text.schema |
| Max output | 256 | 4096 |
| Temperature | Omitted (provider default) | 0 |
| Deprecated / unsupported parameters | responseMimeType marked deprecated by current API reference | responseFormat documented; no definite unsupported schema keyword found offline |
| Tools / search / grounding | None | None |
| Encoding / transport | json.dumps default ASCII; POST; 30s timeout; no redirects/retries | UTF-8 ensure_ascii=False; POST; 180s timeout; no redirects/retries |

Flash-Lite was added by the prior remediation driver, not the original preflight.py candidate list; its recorded successful configuration was minimal thinking with tiny JSON MIME output.

The complete frozen schema is retained in the JSON artifact. Its eight keywords (type, properties, required, additionalProperties, items, minItems, minimum, maximum), including eight nullable string unions, are supported by the [current Google JSON Schema documentation](https://ai.google.dev/gemini-api/docs/generate-content/structured-output). Every object remains closed and fully required. No schema or scoring relaxation is justified offline.

Smallest suspicion: the newly introduced responseFormat.text/schema wire configuration. Documentation describes it, so incompatibility with the actual endpoint is an inference awaiting the provider error; authentication is unchanged and missing credentials are not the working hypothesis.

## Diagnostic calls

- Call 1: gemini-3.5-flash-lite / minimal / h01_paper / original; HTTP 400; 364.02 ms; state=COMPLETED; schema valid=False; classification=PROVIDER_4XX.
```json
{
  "http_status": 400,
  "gemini_error_status": "INVALID_ARGUMENT",
  "gemini_error_message": "Invalid value at 'generation_config.response_format.text.mime_type' (type.googleapis.com/google.ai.generativelanguage.v1beta.TextResponseFormat.MimeType), \"application/json\"",
  "error_info_reasons": [],
  "request_id": null,
  "requested_model_id": "gemini-3.5-flash-lite",
  "message_limit_characters": 2048
}
```
- Call 2: gemini-3.5-flash-lite / minimal / h01_paper / corrected; HTTP 200; 2006.6 ms; state=COMPLETED; schema valid=True; classification=None.
  Tokens: {"promptTokenCount": 812, "candidatesTokenCount": 442, "totalTokenCount": 1254}.
  Returned model: gemini-3.5-flash-lite.
  Mandatory fields: 8/8; SKU correct: True; semantic errors: [].

## Conclusion

Confirmed request-construction defect: responseFormat.text.mimeType requires APPLICATION_JSON, not application/json. The original h01 body was reproduced exactly and returned INVALID_ARGUMENT. Changing only this enum value returned HTTP 200 and passed the unchanged schema, all eight mandatory fields, determinate SKU and grounding checks.

Correction: Shared Gemini build_request: responseFormat.text.mimeType changes only from application/json to APPLICATION_JSON. Full wire schema, local schema, input, thinking, limits and scorer are unchanged.

The [REST API reference](https://ai.google.dev/api/generate-content#TextResponseFormat) defines TextResponseFormat.mimeType as an enum and lists APPLICATION_JSON. This contradicts the MIME string in the structured-output guide example. The enum reference was inspected after Call 1 identified the exact rejected field.

Clean Phase-1 remediation: Safe to prepare a separately authorized clean Gemini remediation run using the corrected shared request path and unchanged schema/scorer. Flash-Lite/h01 is live-verified. The same defective field is present in the 3.8/low path and is corrected offline; 3.8 and the other nine fixtures were not rerun. Historical errors cannot be recovered retrospectively because their bodies were discarded. No benchmark or Phase 2 was started.

Live diagnostic attempts: 2/2. Previous preflight and Phase-1 evidence preserved: True. Frozen inputs, schema, labels, and scorer remain unchanged.

## Offline verification

- python -B -m unittest discover -s spikes/ordershield/provider_bakeoff -p "test_*.py" -q: 35 tests PASS
- python -B spikes/ordershield/provider_bakeoff/phase1.py validate: PASS
- git diff --check: PASS
- Protected preflight, original Phase-1 evidence, corpus and frozen reference SHA-256 comparison: PASS
- Diagnostic artifact secret scan (values inspected only in memory): PASS
