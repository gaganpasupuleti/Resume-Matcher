"""Document parsing: deterministic extract, quality gates, then optional LLM normalize."""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from markitdown import MarkItDown
from pydantic import ValidationError

from app.llm import complete_json, get_llm_config
from app.prompts import PARSE_RESUME_PROMPT
from app.prompts.templates import RESUME_SCHEMA_EXAMPLE
from app.providers.errors import ProviderError, ProviderErrorClass
from app.schemas import CustomSection, ResumeData

logger = logging.getLogger(__name__)

# ponytail: fixed char floor catches scanned/corrupt extracts; raise if real resumes
# routinely fall under it (then switch to word-density + language detect).
MIN_USABLE_CHARS = 80
MIN_ALPHA_CHARS = 40

ALLOWED_EXTENSIONS = frozenset({".pdf", ".doc", ".docx"})
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
MAX_FILE_SIZE = 4 * 1024 * 1024  # 4MB

_REQUIRED_STRING_KEYS = frozenset(
    {
        "name",
        "title",
        "email",
        "phone",
        "location",
        "company",
        "years",
        "institution",
        "degree",
        "role",
    }
)

_SECTION_HINT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("summary", re.compile(r"(?im)^\s*(summary|profile|objective|about\s+me)\b")),
    (
        "experience",
        re.compile(
            r"(?im)^\s*(work\s+experience|professional\s+experience|experience|employment)\b"
        ),
    ),
    ("education", re.compile(r"(?im)^\s*(education|academic\s+background)\b")),
    ("skills", re.compile(r"(?im)^\s*(skills|technical\s+skills|competencies)\b")),
    ("projects", re.compile(r"(?im)^\s*(projects|personal\s+projects|key\s+projects)\b")),
    (
        "certifications",
        re.compile(r"(?im)^\s*(certifications?|licenses?|certificates?)\b"),
    ),
    ("links", re.compile(r"(?i)\b(https?://|linkedin\.com|github\.com)\b")),
)

# Matches date ranges like "Jan 2020 - Dec 2023", "May 2021 - Present",
# "January 2020 - Current", and single dates like "Jun 2023".
_MD_DATE_RE = re.compile(
    r"(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?"
    r"|Dec(?:ember)?)"
    r"\.?\s+\d{4})"
    r"(?:\s*[-–—]\s*"
    r"(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?"
    r"|Dec(?:ember)?)"
    r"\.?\s+\d{4}"
    r"|Present|Current|Now|Ongoing))?",
    re.IGNORECASE,
)


class ExtractionReason(str, Enum):
    """Stable reason codes for upload / extraction outcomes (no PII)."""

    EMPTY_FILE = "empty_file"
    UNSUPPORTED_TYPE = "unsupported_type"
    FILE_TOO_LARGE = "file_too_large"
    EXTRACTION_FAILED = "extraction_failed"
    LOW_TEXT = "low_text"
    OCR_NEEDED = "ocr_needed"
    AI_UNAVAILABLE = "ai_unavailable"
    AI_TIMEOUT = "ai_timeout"
    AI_INVALID_RESPONSE = "ai_invalid_response"
    SCHEMA_INVALID = "schema_invalid"
    OK = "ok"


class ExtractionError(Exception):
    """Deterministic extraction / upload validation failure (before AI)."""

    def __init__(
        self,
        reason: ExtractionReason,
        message: str,
        *,
        http_status: int = 422,
        diagnostics: ExtractionDiagnostics | None = None,
    ) -> None:
        self.reason = reason
        self.http_status = http_status
        self.diagnostics = diagnostics
        super().__init__(message)

    @property
    def reason_code(self) -> str:
        return self.reason.value


@dataclass
class ExtractionDiagnostics:
    """Quality report for a deterministic extract (safe to return to clients)."""

    char_count: int = 0
    alpha_count: int = 0
    line_count: int = 0
    usable: bool = False
    ocr_needed: bool = False
    reason_code: str = ExtractionReason.OK.value
    message: str = ""
    section_hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractionResult:
    """Normalized markdown plus diagnostics; never invents resume facts."""

    text: str
    diagnostics: ExtractionDiagnostics


def _sanitize_llm_nulls(value: Any) -> Any:
    """Convert null to empty string for required text fields LLMs often omit."""
    if isinstance(value, dict):
        return {
            key: (
                ""
                if item is None and key in _REQUIRED_STRING_KEYS
                else _sanitize_llm_nulls(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_llm_nulls(item) for item in value]
    return value


def _drop_invalid_custom_sections(data: dict[str, Any]) -> dict[str, Any]:
    """Drop customSections entries that fail schema validation.

    Ollama often copies example keys (publications, volunteer_work) with
    malformed shapes. Removing invalid sections preserves the rest of the
    extract without inventing replacement content.
    """
    custom = data.get("customSections")
    if custom is None:
        data["customSections"] = {}
        return data
    if not isinstance(custom, dict):
        logger.info("Dropping non-object customSections (%s)", type(custom).__name__)
        data["customSections"] = {}
        return data

    kept: dict[str, Any] = {}
    for key, section in custom.items():
        try:
            CustomSection.model_validate(section)
            kept[str(key)] = section
        except ValidationError:
            logger.info("Dropping invalid custom section %s", key)
    data["customSections"] = kept
    return data


def get_parse_retries() -> int:
    """Local Ollama models are slower — fewer retries keeps total wait predictable."""
    return 1 if get_llm_config().provider == "ollama" else 2


def validate_upload(
    *,
    content: bytes,
    filename: str | None,
    content_type: str | None,
) -> None:
    """Validate extension/MIME/size before any extract or AI call."""
    if not content:
        raise ExtractionError(
            ExtractionReason.EMPTY_FILE,
            "Empty file",
            http_status=400,
        )

    if len(content) > MAX_FILE_SIZE:
        raise ExtractionError(
            ExtractionReason.FILE_TOO_LARGE,
            f"File too large. Maximum size: {MAX_FILE_SIZE // (1024 * 1024)}MB",
            http_status=413,
        )

    suffix = Path(filename or "").suffix.lower()
    if suffix and suffix not in ALLOWED_EXTENSIONS:
        raise ExtractionError(
            ExtractionReason.UNSUPPORTED_TYPE,
            f"Invalid file extension: {suffix}. Allowed: PDF, DOC, DOCX",
            http_status=400,
        )

    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        # Some browsers omit or mis-set MIME; allow when extension is known-good.
        if not suffix or suffix not in ALLOWED_EXTENSIONS:
            raise ExtractionError(
                ExtractionReason.UNSUPPORTED_TYPE,
                f"Invalid file type: {content_type}. Allowed: PDF, DOC, DOCX",
                http_status=400,
            )

    if not suffix and (not content_type or content_type not in ALLOWED_CONTENT_TYPES):
        raise ExtractionError(
            ExtractionReason.UNSUPPORTED_TYPE,
            "Invalid file type. Allowed: PDF, DOC, DOCX",
            http_status=400,
        )


def normalize_extracted_text(text: str) -> str:
    """Normalize whitespace while preserving useful line breaks."""
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for raw_line in normalized.split("\n"):
        # Collapse horizontal whitespace; keep blank lines as structure.
        collapsed = re.sub(r"[ \t\f\v]+", " ", raw_line).strip()
        lines.append(collapsed)
    # Cap runs of blank lines at one separator (two newlines).
    out: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run <= 1:
                out.append("")
            continue
        blank_run = 0
        out.append(line)
    return "\n".join(out).strip()


def detect_section_hints(text: str) -> list[str]:
    """Return ordered unique section hint labels found in extracted text."""
    hints: list[str] = []
    for label, pattern in _SECTION_HINT_PATTERNS:
        if pattern.search(text) and label not in hints:
            hints.append(label)
    return hints


def assess_extracted_text(text: str, *, filename: str | None = None) -> ExtractionDiagnostics:
    """Score extract usability; flag scanned/low-text PDFs that need OCR."""
    normalized = normalize_extracted_text(text)
    char_count = len(normalized)
    alpha_count = sum(1 for ch in normalized if ch.isalpha())
    line_count = len([ln for ln in normalized.split("\n") if ln.strip()]) if normalized else 0
    section_hints = detect_section_hints(normalized)
    usable = char_count >= MIN_USABLE_CHARS and alpha_count >= MIN_ALPHA_CHARS

    suffix = Path(filename or "").suffix.lower()
    ocr_needed = False
    if not usable:
        # Empty/near-empty PDF extract ⇒ scanned or image-only; no fake success.
        if suffix == ".pdf" or char_count == 0:
            ocr_needed = True
            reason = ExtractionReason.OCR_NEEDED
            message = (
                "Extracted text is too sparse for AI parsing. "
                "This file may be scanned or image-only; OCR is required."
            )
        else:
            reason = ExtractionReason.LOW_TEXT
            message = (
                "Extracted text is too short or low-quality for AI parsing. "
                "Re-upload a text-based PDF or DOCX."
            )
        return ExtractionDiagnostics(
            char_count=char_count,
            alpha_count=alpha_count,
            line_count=line_count,
            usable=False,
            ocr_needed=ocr_needed,
            reason_code=reason.value,
            message=message,
            section_hints=section_hints,
        )

    return ExtractionDiagnostics(
        char_count=char_count,
        alpha_count=alpha_count,
        line_count=line_count,
        usable=True,
        ocr_needed=False,
        reason_code=ExtractionReason.OK.value,
        message="Extraction usable",
        section_hints=section_hints,
    )


async def extract_document(content: bytes, filename: str) -> ExtractionResult:
    """Deterministic PDF/DOCX → markdown extract with quality diagnostics.

    Does not call Ollama. Raises ExtractionError on converter failure.
    """
    suffix = Path(filename).suffix.lower() or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        md = MarkItDown()
        result = md.convert(str(tmp_path))
        raw = result.text_content or ""
    except Exception as exc:
        logger.error("Document extraction failed: %s", type(exc).__name__)
        raise ExtractionError(
            ExtractionReason.EXTRACTION_FAILED,
            "Failed to parse document. Please ensure it's a valid PDF or DOCX file.",
            http_status=422,
        ) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    text = normalize_extracted_text(raw)
    diagnostics = assess_extracted_text(text, filename=filename)
    return ExtractionResult(text=text, diagnostics=diagnostics)


async def parse_document(content: bytes, filename: str) -> str:
    """Convert PDF/DOCX to Markdown using markitdown (legacy string API)."""
    result = await extract_document(content, filename)
    return result.text


def _extract_markdown_dates(markdown: str) -> list[str]:
    """Extract all month-inclusive date ranges from markdown text."""
    return _MD_DATE_RE.findall(markdown)


def restore_dates_from_markdown(
    parsed_data: dict[str, Any],
    markdown: str,
) -> dict[str, Any]:
    """Patch year-only dates in parsed data with month-inclusive dates from markdown.

    The LLM sometimes drops months during parsing (e.g. "Jun 2020 - Aug 2021"
    becomes "2020 - 2021"). This function extracts all month-inclusive dates
    from the raw markdown and replaces year-only entries where a match exists.
    """
    md_dates = _extract_markdown_dates(markdown)
    if not md_dates:
        return parsed_data

    # Build a lookup: "2020 - 2021" → "Jun 2020 - Aug 2021"
    year_to_full: dict[str, str] = {}
    year_only_re = re.compile(r"\d{4}")
    for md_date in md_dates:
        years_in_date = year_only_re.findall(md_date)
        if years_in_date:
            # Create year-only key like "2020 - 2021" or "2023"
            year_key = " - ".join(years_in_date)
            # Keep the first (most specific) match
            if year_key not in year_to_full:
                # Normalize separators
                normalized = re.sub(r"\s*[-–—]\s*", " - ", md_date.strip())
                year_to_full[year_key] = normalized

    if not year_to_full:
        return parsed_data

    patched = 0
    for section_key in ("workExperience", "education", "personalProjects"):
        for entry in parsed_data.get(section_key, []):
            if not isinstance(entry, dict):
                continue
            years = entry.get("years", "")
            if not isinstance(years, str) or not years:
                continue
            # Skip if already has months
            if re.search(
                r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)",
                years,
                re.IGNORECASE,
            ):
                continue
            # Try to find a matching month-inclusive date
            if years in year_to_full:
                entry["years"] = year_to_full[years]
                patched += 1

    # Custom sections
    custom = parsed_data.get("customSections", {})
    if isinstance(custom, dict):
        for section in custom.values():
            if not isinstance(section, dict) or section.get("sectionType") != "itemList":
                continue
            for item in section.get("items", []):
                if not isinstance(item, dict):
                    continue
                years = item.get("years", "")
                if not isinstance(years, str) or not years:
                    continue
                if re.search(
                    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)",
                    years,
                    re.IGNORECASE,
                ):
                    continue
                if years in year_to_full:
                    item["years"] = year_to_full[years]
                    patched += 1

    if patched:
        logger.info("Restored months in %d date fields from raw markdown", patched)

    return parsed_data


def classify_ai_failure(error: Exception) -> ExtractionReason:
    """Map provider / schema failures to upload reason codes."""
    if isinstance(error, ProviderError):
        if error.error_class == ProviderErrorClass.TIMEOUT:
            return ExtractionReason.AI_TIMEOUT
        if error.error_class in (
            ProviderErrorClass.UNAVAILABLE,
            ProviderErrorClass.MODEL_MISSING,
            ProviderErrorClass.CAPACITY,
        ):
            return ExtractionReason.AI_UNAVAILABLE
        if error.error_class == ProviderErrorClass.INVALID_RESPONSE:
            return ExtractionReason.AI_INVALID_RESPONSE
        return ExtractionReason.AI_UNAVAILABLE
    if isinstance(error, TimeoutError):
        return ExtractionReason.AI_TIMEOUT
    name = type(error).__name__
    if "Validation" in name:
        return ExtractionReason.SCHEMA_INVALID
    return ExtractionReason.AI_UNAVAILABLE


def schema_failure_fields(error: Exception, *, limit: int = 8) -> list[str]:
    """Return dotted field paths from a Pydantic ValidationError (no values)."""
    errors_fn = getattr(error, "errors", None)
    if not callable(errors_fn):
        return []
    try:
        raw_errors = errors_fn()
    except Exception:
        return []
    fields: list[str] = []
    for err in raw_errors[:limit]:
        if not isinstance(err, dict):
            continue
        loc = err.get("loc") or ()
        fields.append(".".join(str(part) for part in loc))
    return fields


async def parse_resume_to_json(markdown_text: str) -> dict[str, Any]:
    """Parse resume markdown to structured JSON using LLM.

    After LLM parsing, patches any year-only dates with month-inclusive
    dates extracted from the raw markdown. This ensures months are never
    lost regardless of LLM behavior.

    Raises ExtractionError if text is not usable (never calls Ollama empty).
    """
    diagnostics = assess_extracted_text(markdown_text)
    if not diagnostics.usable:
        try:
            reason = ExtractionReason(diagnostics.reason_code)
        except ValueError:
            reason = ExtractionReason.LOW_TEXT
        raise ExtractionError(
            reason,
            diagnostics.message or "Extracted text is not usable for AI parsing",
            http_status=422,
            diagnostics=diagnostics,
        )

    prompt = PARSE_RESUME_PROMPT.format(
        schema=RESUME_SCHEMA_EXAMPLE,
        resume_text=markdown_text,
    )

    retries = get_parse_retries()
    result = await complete_json(
        prompt=prompt,
        system_prompt="You are a JSON extraction engine. Output only valid JSON, no explanations.",
        retries=retries,
    )

    # Patch dates: restore months the LLM may have dropped
    result = restore_dates_from_markdown(result, markdown_text)
    result = _sanitize_llm_nulls(result)
    if isinstance(result, dict):
        result = _drop_invalid_custom_sections(result)

    # Validate against schema — processed_data only after valid
    validated = ResumeData.model_validate(result)
    return validated.model_dump()


__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "ALLOWED_EXTENSIONS",
    "ExtractionDiagnostics",
    "ExtractionError",
    "ExtractionReason",
    "ExtractionResult",
    "MAX_FILE_SIZE",
    "MIN_ALPHA_CHARS",
    "MIN_USABLE_CHARS",
    "assess_extracted_text",
    "classify_ai_failure",
    "detect_section_hints",
    "extract_document",
    "get_parse_retries",
    "normalize_extracted_text",
    "parse_document",
    "parse_resume_to_json",
    "restore_dates_from_markdown",
    "schema_failure_fields",
    "validate_upload",
]
