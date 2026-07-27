# Resume Embed and Connector Contract (Code Quest)

**Status:** Frozen audit (Agent H / `phase-rm-a-provider-audit`)  
**Base:** `647acd9`  
**Related:** `docs/CODEQUEST_EMBED.md` (product embed notes)

---

## 1. Two different “modes” (do not conflate)

| Mode | Where | Purpose |
| --- | --- | --- |
| **Embed mode** (`embed=codequest`) | Resume Matcher **frontend** | iframe chrome for Code Quest Resume Lab |
| **`CODEQUEST_INTEGRATION_MODE`** | Resume Matcher **backend** (separate branch/work) | API lockdown / service-token integration |

**Code Quest Lab cutover decision:** use **embed mode + Ollama path**. Do **not** enable `CODEQUEST_INTEGRATION_MODE` for this cutover (per `CODEQUEST_EMBED.md`).

---

## 2. Roles

### Code Quest host (connector responsibility)

Owns:

- Student shell / Resume Lab route
- iframe `src` via `VITE_RESUME_APP_URL` (or equivalent) pointing at RM embed URL
- Optional auto-start of RM **backend** process (`AUTO_START_RESUME_BACKEND`, `RESUME_BACKEND_HOST/PORT`, typically `:8001`)
- Auth boundary for the student session in CQ
- Host-side handshake helpers (CQ PR #118 ownership)

Does **not** own:

- LiteLLM / Ollama completion calls
- PDF/DOCX extraction
- Resume TinyDB persistence
- Provider API keys

### Resume Matcher backend

Owns direct Ollama calls, parsing, JD tailor, generation, TinyDB.

### Resume Matcher frontend (embed child)

Owns editor UI, Settings, status banners, postMessage child side, CSP `frame-ancestors`.

---

## 3. Direct backend-to-Ollama vs connector

**Decision:** LLM traffic is **backend → Ollama** (`LLM_API_BASE`, default `http://localhost:11434`).

The word **connector** in embed UX means “AI/provider readiness wiring,” not an MCP/LLM proxy:

| Embed UI state | Meaning (as-built) |
| --- | --- |
| `loading` | `/status` still loading |
| `backend-unavailable` | Status fetch failed (RM API down) |
| `connector-unavailable` | `systemStatus.llm_configured === false` (non-Ollama without key, or misconfig) |
| `parsing-failed` | Master resume `processing_status === 'failed'` |
| `ready` | Otherwise |

`llm_configured` is true when `api_key` present **or** `provider == "ollama"` (`routers/health.py`). Ollama can be “configured” yet still unhealthy if the daemon/model is down — Agent E/I should separate configured vs healthy more clearly in diagnostics.

---

## 4. Embed activation

| Method | Example |
| --- | --- |
| Query (preferred) | `http://localhost:3000/dashboard?embed=codequest&parentOrigin=http://localhost:5000` |
| Hash | `...#embed=codequest&parentOrigin=http://localhost:5000` |

- `embed=codequest` enables embed chrome.
- `parentOrigin` accepted **only** if allowlisted; used as `postMessage` `targetOrigin`.
- While framed, embed persists via `sessionStorage` key `rm.codequest.embed`.

Allowlist env:

```env
NEXT_PUBLIC_CODEQUEST_EMBED_PARENT_ORIGINS=http://localhost:5000,http://127.0.0.1:5000
```

Same list feeds postMessage accept/reject and CSP `frame-ancestors` (`apps/frontend/next.config.ts`).

---

## 5. postMessage contract (`codequest-resume-embed/v1`)

| Direction | Type | Payload |
| --- | --- | --- |
| Child → parent | `ready` | `{ protocol, type: "ready", app: "resume-matcher" }` |
| Parent → child (optional) | `parent` | `{ protocol, type: "parent", parent: "codequest" }` |
| Child → parent | `parent-ack` | `{ protocol, type: "parent-ack" }` |

Rules:

- Identity-only handshake — **no tokens, JWTs, API keys, resume bodies**.
- Unknown origins ignored.
- Agent D may harden handshake; must not move LLM secrets into messages.

Constants: `apps/frontend/lib/embed/constants.ts`  
Provider: `apps/frontend/components/embed/embed-provider.tsx`

---

## 6. Embed UI behavior

- Hides standalone landing / outer branding chrome.
- Keeps inner builder navigation and Settings.
- Full viewport height; avoids nested outer scroll chrome.
- Surfaces status banners listed above.
- Settings remains the place to point `api_base` at local Ollama for Lab.

---

## 7. Code Quest environment (host side — reference)

Typical local wiring (from program / CQ backend README; no secrets):

```env
# CQ host → RM UI embed
VITE_RESUME_APP_URL=http://localhost:3000/dashboard?embed=codequest&parentOrigin=http://localhost:5000

# Optional CQ auto-start of RM API only
AUTO_START_RESUME_BACKEND=true
RESUME_BACKEND_HOST=127.0.0.1
RESUME_BACKEND_PORT=8001
```

RM AI config (RM backend / Settings — Ollama only for Lab):

```env
LLM_PROVIDER=ollama
LLM_MODEL=gemma3:4b
LLM_API_BASE=http://localhost:11434
```

Port note: RM standalone often uses backend `:8000` with Next rewrite; CQ auto-start docs use `:8001`. Connector/docs and smoke checks must state which port the FE `BACKEND_ORIGIN` targets.

---

## 8. Privacy / security

- No secrets in postMessage or public embed query params beyond origins.
- Wrong parent origin → no messaging target (fail closed).
- CSP `frame-ancestors` limits who may iframe the app.
- Resume content stays in RM TinyDB / browser session for the Lab user — not echoed to CQ via handshake.

---

## 9. Ready for Agent I / D

| Agent | Ready? | Why |
| --- | --- | --- |
| **I** (provider) | **Yes** | Connector vs direct-Ollama boundary frozen |
| **D** (handshake) | Yes (baseline exists) | Implement against this + `CODEQUEST_EMBED.md` |

---

## 10. Evidence files

| Area | Path |
| --- | --- |
| Embed docs | `docs/CODEQUEST_EMBED.md` |
| Embed lib | `apps/frontend/lib/embed/**` |
| Embed UI | `apps/frontend/components/embed/**` |
| Embed tests | `apps/frontend/tests/embed-*.test.ts(x)` |
| CSP / proxy | `apps/frontend/next.config.ts` |
| Status → connector banner | `embed-provider.tsx` + `routers/health.py` |
