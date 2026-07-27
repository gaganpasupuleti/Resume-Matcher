# Resume Parsing Pipeline (Code Quest)

**Status:** Frozen audit (Agent H / `phase-rm-a-provider-audit`)  
**Base:** `647acd9`  
**Scope:** Docs only. Agent J owns extraction hardening implementation.

---

## 1. End-to-end pipeline (as-built)

```text
Upload (PDF / DOC / DOCX)
  │
  ├─ MIME allowlist + size ≤ 4MB + non-empty bytes
  │
  ├─ parse_document(bytes, filename)          [markitdown]
  │     temp file → MarkItDown.convert → markdown string
  │
  ├─ TinyDB create_resume_atomic_master
  │     content = markdown
  │     original_markdown = markdown (kept for date repair)
  │     processing_status = "processing"
  │
  └─ parse_resume_to_json(markdown)           [Ollama via complete_json]
        ├─ PARSE_RESUME_PROMPT + RESUME_SCHEMA_EXAMPLE
        ├─ retries = 1 if ollama else 2
        ├─ restore_dates_from_markdown(...)
        ├─ _sanitize_llm_nulls(...)
        └─ ResumeData.model_validate → processed_data
              success → processing_status = "ready"
              timeout/error → processing_status = "failed" (markdown kept)
```

Retry path: `POST /api/v1/resumes/{resume_id}/retry-processing` re-runs JSON parse on stored markdown for `failed` or stuck `processing` resumes.

---

## 2. PDF / DOCX extraction

| Step | Implementation |
| --- | --- |
| Library | `markitdown[docx]==0.1.4` (+ `pdfminer.six`, `python-docx`) |
| Entry | `apps/backend/app/services/parser.py` → `parse_document` |
| Formats | PDF, DOC, DOCX via content-type allowlist in `routers/resumes.py` |
| OCR | **None** today — image-only / scanned PDFs yield empty or near-empty markdown |
| Quality gate | **None** today — low-text files can still invoke the LLM |

Upload guards:

| Condition | HTTP | Detail |
| --- | --- | --- |
| Wrong MIME | 400 | Allowed: PDF, DOC, DOCX |
| Size > 4MB | 413 | Max 4MB |
| Empty bytes | 400 | `Empty file` |
| markitdown throws | 422 | Generic “valid PDF or DOCX” message |

---

## 3. Synthetic PDF / malformed file failure evidence

Reproduced locally with `markitdown` during this audit (Agent H). No fixture PDFs live in-repo.

| Synthetic input | markitdown result | Upload outcome (expected) |
| --- | --- | --- |
| Empty `.pdf` (0 bytes) | Blocked earlier by empty-file check (400) if uploaded as empty; empty file written to disk still yields `FileConversionException` / `PDFSyntaxError: No /Root object!` | 400 or 422 |
| Minimal invalid PDF (`%PDF-1.4` without `/Root`) | `FileConversionException` / `PDFSyntaxError: No /Root object! - Is this really a PDF?` | **422** |
| Plain-text bytes named `.pdf` | Conversion **succeeds**; text extracted as-is | Upload may proceed; AI parse quality undefined |
| Corrupt DOCX (`PK` header + garbage) | Conversion can **succeed** with tiny text (`len≈8`) | Low-text → LLM likely fails or empty sections → `processing_status=failed` |
| Image-only / scanned PDF | Not reproduced here (no OCR); expected empty/low markdown | Should not claim structured success (Agent J must detect) |

**Root causes to document for Agent J:**

1. **Structural PDF failure** — pdfminer rejects non-PDF / broken PDF → 422 before TinyDB write.
2. **False-positive extract** — wrong extension / corrupt package still returns short text → LLM called with unusable input.
3. **Scanned / image PDF** — extract “succeeds” with no usable text; no OCR path.
4. **AI-stage failure after good extract** — timeout, malformed JSON, schema validation, empty Ollama response → markdown persisted, status `failed`.
5. **Null-field schema failures (mitigated)** — Ollama often emits `null` for optional-looking fields; `_sanitize_llm_nulls` + Pydantic coercers convert to `""` (`test_resume_data_validation.py`).

---

## 4. Schema validation

Canonical schema: `ResumeData` in `apps/backend/app/schemas/models.py`.

Core sections:

- `personalInfo` (name, title, email, phone, location, links)
- `summary`
- `workExperience[]`, `education[]`, `personalProjects[]`
- `additional` (skills, languages, certifications, awards)
- `customSections`, `sections` metadata

Post-LLM repairs (as-built):

| Repair | Purpose |
| --- | --- |
| `restore_dates_from_markdown` | Recover month names dropped to year-only ranges |
| `_sanitize_llm_nulls` | `null` → `""` for required string keys |
| Pydantic `mode="before"` coercers | Nested dict/list → string/list |

**Policy:** validate before marking `ready`. Do not invent missing experience/education content. Safe formatting repair only (Agent I / J).

---

## 5. Persistence (TinyDB)

| Field | Role |
| --- | --- |
| `resume_id` | UUID |
| `content` | Markdown at upload; may later hold builder JSON |
| `original_markdown` | Immutable extract for date repair |
| `processed_data` | Structured `ResumeData` dump when ready |
| `processing_status` | `pending` \| `processing` \| `ready` \| `failed` |
| `is_master` / `parent_id` | Master resume + tailored children |
| `cover_letter` / `outreach_message` / `title` | Generation outputs |
| `created_at` / `updated_at` | ISO timestamps |

Store: `apps/backend/data/database.json` (local). Master assignment uses `asyncio.Lock`; failed/processing masters can be demoted on next upload.

---

## 6. JD matching (map)

Two layers:

1. **Tailoring (LLM):** `extract_job_keywords` → `improve_resume` (+ optional refiner) in `services/improver.py` / `refiner.py`. Keywords cached on job record by content hash. Preview hard timeout **240s**.
2. **Builder JD Match UI (deterministic):** frontend `keyword-matcher.ts` highlights JD keywords on tailored resume; `GET /resumes/{id}/job-description` supplies JD text. No second LLM call for the highlight view.

Code Quest Lab must keep LLM JD analysis on Ollama only; UI match % is client-side.

---

## 7. Cover letter / outreach email

| Feature | Service | LLM API | Feature flag |
| --- | --- | --- | --- |
| Cover letter | `generate_cover_letter` | `complete()` text | `enable_cover_letter` |
| Outreach / application email | `generate_outreach_message` | `complete()` text | `enable_outreach_message` |
| Resume title from JD | `generate_resume_title` | `complete()` | (improve flow) |

Flags default **false** in config until enabled via Settings. PDF download routes render via headless Chromium (`app/pdf.py`) — separate from upload extraction.

---

## 8. Frozen policies for Agents I / J

### Extraction fallback

| Case | Required behavior |
| --- | --- |
| Extract OK, AI fail | Keep extracted markdown; status `failed`; allow retry; show AI unavailable / parsing failed |
| Extract fail | Clear reason (422/400); **do not** call Ollama with empty input (gap today — Agent J) |
| Scanned / low-text | Report OCR needed; do not pretend structured parse succeeded; no heavy OCR without approval |

### AI unavailable

- Status API: `llm_configured` / `llm_healthy`
- Embed banner: `connector-unavailable` when not configured; parsing failure separate
- Upload still stores markdown when AI fails after extract

### Malformed JSON

- App retries with stricter “JSON only” prompt (bounded)
- Temperature bump on retry attempts
- Final failure → `ValueError` → upload `failed`
- No provider switch

### Timeout / retry

- Outer parse budget ≈ **750s** on Ollama (see provider contract)
- Content retries reduced on Ollama
- Transport retries via LiteLLM Router only
- User-visible retry: `retry-processing`

---

## 9. Evidence files

| Area | Path |
| --- | --- |
| Parser | `apps/backend/app/services/parser.py` |
| Upload / retry | `apps/backend/app/routers/resumes.py` |
| Schemas | `apps/backend/app/schemas/models.py` |
| Prompts | `apps/backend/app/prompts/templates.py` |
| Improver | `apps/backend/app/services/improver.py` |
| Cover letter | `apps/backend/app/services/cover_letter.py` |
| DB | `apps/backend/app/database.py` |
| Null/timeout tests | `apps/backend/tests/test_resume_data_validation.py` |
| JD UI | `docs/agent/features/jd-match.md` |
