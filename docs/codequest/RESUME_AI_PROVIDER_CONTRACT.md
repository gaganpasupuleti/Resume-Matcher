# Resume AI Provider Contract (Code Quest)

**Status:** Frozen audit (Agent H / `phase-rm-a-provider-audit`)  
**Base:** RM PR #1 `647acd9` (`feat(embed): add Code Quest Resume Lab mode`)  
**Scope:** Docs only — no implementation in this phase.

This contract freezes how Resume Matcher talks to AI for the Code Quest Resume Lab cutover. Paid cloud providers must not activate for Code Quest local mode.

---

## 1. Provider policy (decision)

| Rule | Decision |
| --- | --- |
| **Enabled for Code Quest** | **Ollama only** |
| **API key** | Not required for Ollama |
| **Paid / cloud adapters** | **Disabled for Code Quest** (OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter remain in codebase as future adapters; must not silently activate in CQ local mode) |
| **LiteLLM** | Keep as transport wrapper for now; do **not** delete blindly. Agent I may add a thin `ResumeAIProvider` policy layer on top |
| **Fallback to paid APIs** | **Forbidden** |
| **Automatic provider switching** | **Forbidden** |
| **Browser secrets** | **Forbidden** — API keys never in frontend bundles or postMessage |

### Target shape (Agent I+)

```text
ResumeAIProvider
  ├── OllamaProvider              — enabled (Code Quest local)
  ├── FutureOpenAICompatibleProvider — disabled
  └── FutureCloudProvider         — disabled
```

Until that layer exists, Code Quest deployments must set runtime config to Ollama explicitly (env + Settings), and treat any non-Ollama provider as out of policy for the Lab.

---

## 2. As-built map (at `647acd9`)

### Config sources (priority)

1. `apps/backend/data/config.json` (Settings UI / `PUT /api/v1/config/llm-api-key`)
2. Environment / pydantic settings (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`, `LOG_LLM`)
3. Hardcoded settings defaults in `apps/backend/app/config.py` (default provider today is still `openai` in standalone product — **Code Quest must override**)

Key resolution (`resolve_api_key` in `apps/backend/app/llm.py`):

```text
stored.api_key → stored.api_keys[provider] → settings.llm_api_key
```

Ollama health treats missing key as OK (`provider == "ollama"`).

### Declared providers (LiteLLM-backed)

| Provider id | LiteLLM model prefix | Key required | Code Quest policy |
| --- | --- | --- | --- |
| `ollama` | `ollama/` | No | **Enabled** |
| `openai` | (none) | Yes | Disabled adapter |
| `anthropic` | `anthropic/` | Yes | Disabled adapter |
| `gemini` | `gemini/` | Yes | Disabled adapter |
| `deepseek` | `deepseek/` | Yes | Disabled adapter |
| `openrouter` | `openrouter/` | Yes | Disabled adapter |

Frontend defaults (`PROVIDER_INFO`): Ollama default model `gemma3:4b`; Settings auto-fills `api_base` to `http://localhost:11434` when switching to Ollama.

### Ollama request path (direct backend → Ollama)

```text
Client (RM FE / embed)
  → Next rewrite /api/* → FastAPI :8000 (or CQ auto-start :8001)
    → app.llm.get_router() / complete_json() / complete()
      → LiteLLM Router acompletion(model="primary")
        → litellm_params.model = "ollama/<model>"
        → litellm_params.api_base = LLM_API_BASE (e.g. http://localhost:11434)
          → Ollama HTTP API on host
```

**Decision:** AI calls are **direct backend-to-Ollama**. Code Quest does **not** proxy LLM tokens. The CQ “connector” is process/host wiring + iframe UX, not an LLM gateway (see `RESUME_EMBED_AND_CONNECTOR_CONTRACT.md`).

### Model selection

| Layer | Behavior |
| --- | --- |
| Stored config | `config.json` `model` field |
| Env default | `LLM_MODEL` |
| FE suggestion | `gemma3:4b` for Ollama |
| Normalization | `get_model_name()` → `ollama/<model>` unless already prefixed |
| JSON mode | `_supports_json_mode()` via LiteLLM model registry; Ollama often falls back to **prompt-only JSON** |

Do not hardcode a single model in Code Quest docs beyond the recommended default; inspect local `ollama list` / config before Agent I locks a default.

### Timeouts and retries (as-built)

| Knob | Value | Notes |
| --- | --- | --- |
| Health check | 120s | `LLM_TIMEOUT_HEALTH_CHECK`; uses direct `litellm.acompletion` |
| Text completion | 120s base × provider factor | Ollama factor **2.0** → ~240s per attempt |
| JSON completion | 180s base × provider factor | Ollama → **360s** per attempt |
| Outer resume parse | `per_attempt × (retries+1) + 30` | Ollama retries=**1** → **~750s** wall |
| Improve preview | hard **240s** `asyncio.wait_for` | Separate from parse budget |
| LiteLLM Router | `num_retries=3`; Timeout retries=2; RateLimit=3; Auth/BadRequest=0 | Cooldowns **disabled** (single deployment) |
| App JSON retries | Ollama **1**, others **2** | Content-quality only (malformed JSON / truncation / empty) |
| FE `apiFetch` default | 240s | Improve-aligned |
| FE resume process | `RESUME_PROCESS_TIMEOUT_MS = 900_000` | Upload/retry |
| Next proxy | `proxyTimeout: 900_000` | Local Ollama |

**Policy for Agent I:** keep Ollama timeouts **bounded and predictable**; prefer fewer content retries on local models (already started); never fall through to a cloud provider on timeout.

### Error / privacy logging

- Server logs full exceptions for health/completion failures; clients get generic messages / error codes (`api_key_missing`, `empty_content`, `not_found_404`, …).
- API keys masked in config GET responses.
- **Policy:** do not log resume body, PII, or raw LLM prompts/completions at INFO by default (`LOG_LLM` default `WARNING`). Debug previews of LLM content exist today — Agent I must keep PII out of default logs and prefer correlation IDs without personal data.

---

## 3. Required decisions (frozen)

### Direct backend-to-Ollama

Yes. Resume Matcher backend is the only process that calls Ollama. Code Quest shell never holds provider keys for Lab AI.

### Fallback behavior

| Failure | Behavior |
| --- | --- |
| Ollama down / timeout / invalid JSON | Mark AI step failed; **do not** switch provider |
| Upload extract OK, AI parse fail | Persist markdown + `processing_status=failed`; allow `POST /resumes/{id}/retry-processing` |
| Auth / missing paid key | Irrelevant under Ollama-only policy |
| Model missing | Surface as unhealthy / failed completion (Agent I error class `model_missing`) |

### Structured schemas

JSON workflows must validate against Pydantic `ResumeData` (and related improve/enrichment schemas). Prompt examples live in `RESUME_SCHEMA_EXAMPLE` / `IMPROVE_SCHEMA_EXAMPLE`. Null coercion for required strings is required for Ollama (see parsing pipeline).

### Cache policy

- LiteLLM Router instance cached by config fingerprint (provider/model/key-hash/api_base).
- JD keyword extraction cached on job docs via content hash (`job_keywords` + `job_keywords_hash`).
- Refiner text extraction uses `@lru_cache` on JSON string.
- **No** cross-user shared cache of resume content beyond local TinyDB file.

### Privacy / logging policy

- No secrets in docs, commits, postMessage, or frontend env beyond public URLs.
- Mask keys in API responses.
- Prefer logging status codes and resume **ids**, not content.
- TinyDB is local single-user storage under `apps/backend/data/` — not a multi-tenant store.

---

## 4. Ready for Agent I

**Yes** — provider path is mapped; Ollama-only Code Quest policy is frozen; LiteLLM retained as transport with disabled future adapters; timeouts/retries and no-paid-fallback rules are explicit enough for `phase-rm-b-ollama-provider-hardening`.

---

## 5. Evidence files

| Area | Path |
| --- | --- |
| LiteLLM wrapper | `apps/backend/app/llm.py` |
| Settings | `apps/backend/app/config.py` |
| Config routes | `apps/backend/app/routers/config.py` |
| Health/status | `apps/backend/app/routers/health.py` |
| FE provider catalog | `apps/frontend/lib/api/config.ts` |
| FE timeouts | `apps/frontend/lib/api/client.ts` |
| Proxy timeout | `apps/frontend/next.config.ts` |
| Setup (Ollama) | `SETUP.md` |
