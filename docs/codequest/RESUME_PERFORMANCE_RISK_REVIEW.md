# Resume Performance and Risk Review (Code Quest)

**Status:** Frozen audit (Agent H / `phase-rm-a-provider-audit`)  
**Base:** `647acd9`  
**Rule:** Measure in verification (Agent F); numbers below are **as-built budgets / observed code limits**, not Lab SLA claims.

---

## 1. Performance targets (frozen for Lab)

Targets for Code Quest local Ollama mode. Treat as design budgets Agent I/J/F validate with wall-clock measurements.

| Operation | Target budget | As-built ceiling (code) | Risk if exceeded |
| --- | --- | --- | --- |
| Status / health probe | ≤ 5s preferred | Health LLM call up to **120s** | Embed stays on `loading` / false negatives |
| PDF/DOCX extract (`markitdown`) | ≤ 5s typical | Sync convert on request thread | Upload latency; large files |
| Ollama resume normalize (JSON) | ≤ 3–6 min preferred | Outer wait ≈ **750s** (Ollama) | UX abandonment; FE 900s cap |
| JD keyword + improve preview | ≤ 4 min | Hard **240s** | 504 to client |
| Cover letter / outreach | ≤ 2 min | Completion timeout × Ollama 2× factor | Feature feels hung |
| FE default API call | — | **240s** | Abort before backend on long improves |
| FE upload/retry | — | **900s** | Aligns with Next `proxyTimeout` |
| Next.js rewrite proxy | — | **900s** | Proxy must outlive Ollama parse |

**Policy:** keep Ollama **server-side**; never move model inference into the browser. Prefer fewer content retries on local models (already `retries=1` for Ollama parse). Do not “fix” timeouts by enabling cloud fallback.

### Measurement checklist (Agent F)

Record for a text-rich synthetic PDF + synthetic JD:

- extract ms
- Ollama normalize ms
- JD analysis / improve ms
- cover letter ms
- failed request counts / error codes
- whether FE aborted first vs backend timeout

---

## 2. Latency math (Ollama path)

From `apps/backend/app/llm.py`:

```text
JSON base timeout = 180s
provider_factor(ollama) = 2.0
→ per-attempt ≈ 360s

get_parse_retries(ollama) = 1
get_resume_parse_timeout = 360 * (1+1) + 30 ≈ 750s
```

LiteLLM Router may also retry transport timeouts (up to TimeoutErrorRetries=2) **inside** an attempt — worst case can approach the outer wait. Agent I should keep total wall time bounded and documented.

Legacy note: commit `6f2154f` introduced a fixed **200s** outer wait; `126d3e6` replaced it with adaptive `get_resume_parse_timeout` because local models exceeded 200s.

---

## 3. Risk register

| ID | Risk | Likelihood | Impact | Mitigation (policy / next agent) |
| --- | --- | --- | --- | --- |
| R1 | Default settings provider is still `openai` in standalone code | High if CQ forgets env | Paid API / key prompts | CQ Lab must force Ollama; Agent I policy layer |
| R2 | Paid adapters still selectable in Settings UI | High | Accidental cloud use | Disable in CQ mode (Agent I + optional FE gate) |
| R3 | Long Ollama JSON + Router retries → near 12+ min theoretical | Medium | Timeouts / stuck `processing` | Bound retries; reduce Router amplification; progress UX |
| R4 | Health check = real LLM completion (120s) | Medium | Slow `/status`, embed loading | Lightweight Ollama tags/version probe (Agent E/I) |
| R5 | Low-text / scanned PDF still calls LLM | High | Wasted minutes + `failed` | Text-quality gate before Ollama (Agent J) |
| R6 | Corrupt DOCX/PDF-as-text can “extract” tiny strings | Medium | Silent bad parses | Min char threshold + diagnostics |
| R7 | Improve path 240s vs parse 750s mismatch | Medium | Confusing timeouts | Align FE/backend messages (Agent C) |
| R8 | TinyDB single-file local store | Low (Lab) | Not multi-user safe | Keep Lab single-user; no production DB |
| R9 | Debug logs may include LLM response previews | Medium | PII leakage | Default `LOG_LLM=WARNING`; strip bodies (Agent I) |
| R10 | `CODEQUEST_INTEGRATION_MODE` mistaken for embed | Medium | Breaks editor/Ollama path | Docs forbid for this cutover |
| R11 | Port confusion `:8000` vs CQ `:8001` | Medium | `backend-unavailable` | Document `BACKEND_ORIGIN` / auto-start pairing |
| R12 | No LiteLLM model-group fallback today | Accepted | Single point of failure = local Ollama | **Do not** add paid fallback; harden local only |
| R13 | Keyword cache on jobs; stale after edits | Low | Wrong tailor keywords | Invalidate on JD content hash (already); resume-edit invalidation per program perf notes |
| R14 | Chromium PDF render for downloads | Low | Extra dependency / flaky on Windows | Separate from upload extract; lazy init already |

---

## 4. Cache policy (performance)

| Cache | Scope | Invalidation |
| --- | --- | --- |
| LiteLLM Router singleton | Process | Rebuild on provider/model/api_base/key fingerprint |
| Job `job_keywords` | TinyDB job row | Content hash mismatch |
| Refiner `_extract_all_text_cached` | Process LRU(100) | New JSON string |

Do not introduce cross-request caches of resume PII outside TinyDB without review.

---

## 5. Failure modes vs UX

| Failure | Backend | Embed / UI |
| --- | --- | --- |
| Unsupported MIME / empty / oversized | 400/413 | Upload error |
| Extract exception | 422 | Upload error |
| Ollama timeout / bad JSON | `processing_status=failed` | `parsing-failed` banner; retry endpoint |
| LLM not configured | status flags | `connector-unavailable` |
| API down | fetch error | `backend-unavailable` |
| Improve timeout | 504 | Client error toast |

Extraction fallback policy (aligned with parsing pipeline): **never discard successful markdown** when AI fails.

---

## 6. Privacy / logging risks

- Resume markdown and structured JSON contain PII by nature — stored locally in TinyDB.
- Avoid logging full prompts/responses; prefer resume_id + error class.
- Mask API keys in config responses (already).
- postMessage must stay identity-only (already).
- No secrets in these docs or commits.

---

## 7. What Agent I should harden first

1. Ollama-only policy enforcement (no silent cloud).
2. Bounded timeouts + clear error classes (`unavailable`, `timeout`, `model_missing`, `invalid_response`, `capacity`, `internal`).
3. Health check that does not need a 120s generative call when possible.
4. Safe JSON repair without inventing resume facts.
5. Logging without resume content by default.

Agent J should add extraction quality gates so Ollama is not called on empty/low-text extracts.

---

## 8. Ready for Agent I

**Yes.** Risks and budgets are explicit; no paid fallback; Ollama path and embed/connector boundaries are documented. Implementation remains out of scope for Agent H.

---

## 9. Evidence anchors

| Topic | Source |
| --- | --- |
| Timeouts / Router | `apps/backend/app/llm.py` |
| Parse retries | `apps/backend/app/services/parser.py` |
| Upload wait_for | `apps/backend/app/routers/resumes.py` |
| Improve 240s | same |
| FE / proxy budgets | `apps/frontend/lib/api/client.ts`, `next.config.ts` |
| Ollama timeout test | `apps/backend/tests/test_resume_data_validation.py` |
| Synthetic extract failures | Agent H local markitdown probes (see parsing pipeline) |
| Historical timeout bump | commits `6f2154f`, `126d3e6` |
