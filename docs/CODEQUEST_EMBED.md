# Code Quest Resume Lab — embed mode

Frontend-only embed mode for iframe hosting inside Code Quest Resume Lab.  
**Not** the same as `CODEQUEST_INTEGRATION_MODE` (backend API lock-down). Embed mode keeps the editor and Ollama path.

## Activate

| Method | Example |
| --- | --- |
| Query (preferred) | `http://localhost:3000/dashboard?embed=codequest&parentOrigin=http://localhost:5000` |
| Hash | `http://localhost:3000/dashboard#embed=codequest&parentOrigin=http://localhost:5000` |

- `embed=codequest` turns on embed chrome.
- `parentOrigin` is optional; accepted **only** if it is on the allowlist (used as `postMessage` `targetOrigin`).

While framed, embed mode persists across in-app navigations via `sessionStorage`.

## Parent origin allowlist

Env (frontend):

```env
NEXT_PUBLIC_CODEQUEST_EMBED_PARENT_ORIGINS=http://localhost:5000,http://127.0.0.1:5000
```

Defaults match local Code Quest (`:5000`). Same list feeds:

1. postMessage accept/reject
2. CSP `frame-ancestors` (see `apps/frontend/next.config.ts`)

Wrong / unknown origins are ignored — no secrets are ever placed in postMessage payloads.

## postMessage contract (`codequest-resume-embed/v1`)

| Direction | Type | Payload |
| --- | --- | --- |
| Child → parent | `ready` | `{ protocol, type: "ready", app: "resume-matcher" }` |
| Parent → child (optional) | `parent` | `{ protocol, type: "parent", parent: "codequest" }` |
| Child → parent | `parent-ack` | `{ protocol, type: "parent-ack" }` |

Parent identity is optional and identity-only (no tokens, JWTs, or pairing secrets).

## Embed UI behavior

- Hides standalone landing / dashboard header+footer branding.
- Keeps inner resume navigation (builder tabs, back to dashboard, settings).
- Full viewport height/width; avoids nested outer scroll chrome where possible.
- Status banners: `loading`, `backend-unavailable`, `connector-unavailable` (LLM/Ollama not configured), `parsing-failed`.

## Code Quest host (R3)

Point `VITE_RESUME_APP_URL` at the embed URL, e.g.:

```text
http://localhost:3000/dashboard?embed=codequest&parentOrigin=http://localhost:5000
```

Do **not** enable `CODEQUEST_INTEGRATION_MODE` for this cutover.
