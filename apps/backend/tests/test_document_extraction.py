"""Proof tests for deterministic document extraction (Agent J)."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from docx import Document
from fastapi import HTTPException
from pydantic import ValidationError

from app.providers.errors import ProviderError, ProviderErrorClass
from app.routers import resumes as resumes_router
from app.schemas import ResumeData
from app.services.parser import (
    ExtractionError,
    ExtractionReason,
    assess_extracted_text,
    classify_ai_failure,
    detect_section_hints,
    extract_document,
    normalize_extracted_text,
    parse_resume_to_json,
    validate_upload,
)


def _minimal_pdf(text: str) -> bytes:
    """Build a tiny text PDF with a single content stream (no external deps)."""
    # Escape parentheses for PDF string literal.
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET"
    stream_bytes = stream.encode("latin-1", errors="replace")
    objects = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n",
        (
            b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
        ),
        (
            f"4 0 obj<< /Length {len(stream_bytes)} >>stream\n".encode("ascii")
            + stream_bytes
            + b"\nendstream\nendobj\n"
        ),
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (
            f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(out)


def _docx_bytes(paragraphs: list[str]) -> bytes:
    doc = Document()
    for para in paragraphs:
        doc.add_paragraph(para)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


SAMPLE_RESUME = """
Jane Doe
jane@example.com | +1-555-0100

Summary
Experienced software engineer building learning platforms.

Experience
Senior Engineer — Acme Corp
Jan 2020 - Present
- Built resume parsing pipelines and student dashboards.

Education
B.S. Computer Science — State University
2014 - 2018

Skills
Python, TypeScript, SQL

Projects
Code Quest Lab — local AI resume matcher

Certifications
AWS Cloud Practitioner

https://linkedin.com/in/janedoe
https://github.com/janedoe
""".strip()


def test_normalize_preserves_line_breaks() -> None:
    raw = "Name\r\n\r\n\r\n  Experience  \t  section\n\n\nEducation"
    normalized = normalize_extracted_text(raw)
    assert "\r" not in normalized
    assert "Name\n\nExperience section\n\nEducation" == normalized
    assert "  " not in normalized


def test_section_hints_detected() -> None:
    hints = detect_section_hints(SAMPLE_RESUME)
    for expected in (
        "summary",
        "experience",
        "education",
        "skills",
        "projects",
        "certifications",
        "links",
    ):
        assert expected in hints


def test_assess_usable_text() -> None:
    diag = assess_extracted_text(SAMPLE_RESUME, filename="resume.pdf")
    assert diag.usable is True
    assert diag.ocr_needed is False
    assert diag.reason_code == ExtractionReason.OK.value
    assert diag.char_count >= 80


def test_assess_low_text_and_ocr_for_pdf() -> None:
    diag = assess_extracted_text("hi", filename="scan.pdf")
    assert diag.usable is False
    assert diag.ocr_needed is True
    assert diag.reason_code == ExtractionReason.OCR_NEEDED.value


def test_assess_low_text_docx() -> None:
    diag = assess_extracted_text("tiny", filename="resume.docx")
    assert diag.usable is False
    assert diag.reason_code == ExtractionReason.LOW_TEXT.value


def test_validate_empty_file() -> None:
    with pytest.raises(ExtractionError) as exc:
        validate_upload(content=b"", filename="a.pdf", content_type="application/pdf")
    assert exc.value.reason == ExtractionReason.EMPTY_FILE
    assert exc.value.http_status == 400


def test_validate_unsupported_extension() -> None:
    with pytest.raises(ExtractionError) as exc:
        validate_upload(
            content=b"hello",
            filename="notes.txt",
            content_type="text/plain",
        )
    assert exc.value.reason == ExtractionReason.UNSUPPORTED_TYPE
    assert exc.value.http_status == 400


def test_validate_oversized() -> None:
    with pytest.raises(ExtractionError) as exc:
        validate_upload(
            content=b"x" * (4 * 1024 * 1024 + 1),
            filename="big.pdf",
            content_type="application/pdf",
        )
    assert exc.value.reason == ExtractionReason.FILE_TOO_LARGE
    assert exc.value.http_status == 413


@pytest.mark.asyncio
async def test_extract_text_rich_pdf() -> None:
    pdf = _minimal_pdf(
        "Jane Doe Summary Experience Education Skills Python TypeScript "
        "Projects Certifications LinkedIn profile text enough for quality gate"
    )
    result = await extract_document(pdf, "resume.pdf")
    assert result.diagnostics.char_count > 0
    # markitdown/pdfminer should recover the embedded string
    assert "Jane" in result.text or result.diagnostics.char_count >= 40


@pytest.mark.asyncio
async def test_extract_docx() -> None:
    content = _docx_bytes(
        [
            "Jane Doe",
            "Summary",
            "Experienced engineer.",
            "Experience",
            "Acme Corp — Senior Engineer",
            "Education",
            "State University",
            "Skills",
            "Python TypeScript SQL Docker Kubernetes cloud platforms",
        ]
    )
    result = await extract_document(content, "resume.docx")
    assert result.diagnostics.usable is True
    assert "Experience" in result.text
    assert "skills" in result.diagnostics.section_hints or "Experience" in result.text


@pytest.mark.asyncio
async def test_extract_malformed_pdf() -> None:
    # pdfminer rejects some broken PDFs outright
    with pytest.raises(ExtractionError) as exc:
        await extract_document(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n", "bad.pdf")
    assert exc.value.reason == ExtractionReason.EXTRACTION_FAILED
    assert exc.value.http_status == 422


@pytest.mark.asyncio
async def test_extract_false_positive_pdf_bytes_quality_gated() -> None:
    # Some malformed PDFs "convert" as plain text — must not look like success.
    result = await extract_document(b"%PDF-1.4\nnot a real pdf", "bad.pdf")
    assert result.diagnostics.usable is False
    assert result.diagnostics.reason_code == ExtractionReason.OCR_NEEDED.value


@pytest.mark.asyncio
async def test_extract_low_text_pdf_flags_ocr() -> None:
    pdf = _minimal_pdf("x")
    result = await extract_document(pdf, "scan.pdf")
    assert result.diagnostics.usable is False
    assert result.diagnostics.ocr_needed is True
    assert result.diagnostics.reason_code == ExtractionReason.OCR_NEEDED.value


@pytest.mark.asyncio
async def test_parse_resume_skips_ollama_when_unusable() -> None:
    with patch("app.services.parser.complete_json", new_callable=AsyncMock) as mock_json:
        with pytest.raises(ExtractionError) as exc:
            await parse_resume_to_json("hi")
        mock_json.assert_not_awaited()
    assert exc.value.reason in (
        ExtractionReason.OCR_NEEDED,
        ExtractionReason.LOW_TEXT,
    )


@pytest.mark.asyncio
async def test_parse_resume_schema_validation() -> None:
    payload = {
        "personalInfo": {"name": "Jane Doe", "email": "jane@example.com"},
        "summary": "Engineer",
        "workExperience": [],
        "education": [],
        "personalProjects": [],
        "additional": {"technicalSkills": ["Python"]},
    }

    with patch(
        "app.services.parser.complete_json",
        new_callable=AsyncMock,
        return_value=payload,
    ):
        result = await parse_resume_to_json(SAMPLE_RESUME)

    validated = ResumeData.model_validate(result)
    assert validated.personalInfo.name == "Jane Doe"
    assert validated.additional.technicalSkills == ["Python"]


def test_schema_rejects_invented_structure() -> None:
    with pytest.raises(ValidationError):
        ResumeData.model_validate(
            {
                "personalInfo": "not-an-object",
                "workExperience": "bad",
            }
        )


def test_classify_ai_unavailable() -> None:
    err = ProviderError(
        ProviderErrorClass.UNAVAILABLE,
        "ollama down",
        correlation_id="abc",
        provider="ollama",
    )
    assert classify_ai_failure(err) == ExtractionReason.AI_UNAVAILABLE


@pytest.mark.asyncio
async def test_upload_ollama_unavailable_keeps_extract() -> None:
    file = MagicMock()
    file.filename = "resume.pdf"
    file.content_type = "application/pdf"
    file.read = AsyncMock(return_value=b"unused")

    stored: dict = {}

    async def fake_create(**kwargs):  # type: ignore[no-untyped-def]
        stored.update(kwargs)
        stored["resume_id"] = "r1"
        stored["is_master"] = True
        return dict(stored)

    mock_db = MagicMock()
    mock_db.create_resume_atomic_master = AsyncMock(side_effect=fake_create)
    mock_db.update_resume = MagicMock()

    from app.services.parser import ExtractionResult

    text = normalize_extracted_text(SAMPLE_RESUME)
    diag = assess_extracted_text(text, filename="resume.pdf")

    with (
        patch.object(resumes_router, "db", mock_db),
        patch.object(
            resumes_router,
            "extract_document",
            AsyncMock(return_value=ExtractionResult(text=text, diagnostics=diag)),
        ),
        patch.object(
            resumes_router,
            "parse_resume_to_json",
            AsyncMock(
                side_effect=ProviderError(
                    ProviderErrorClass.UNAVAILABLE,
                    "Ollama unreachable",
                    correlation_id="cid",
                    provider="ollama",
                )
            ),
        ),
        patch.object(resumes_router, "get_resume_parse_timeout", return_value=30.0),
        patch.object(resumes_router, "get_parse_retries", return_value=1),
    ):
        response = await resumes_router.upload_resume(file)

    assert response.processing_status == "failed"
    assert response.extraction_usable is True
    assert response.ai_normalization_status == "unavailable"
    assert response.reason_code == ExtractionReason.AI_UNAVAILABLE.value
    assert "kept" in response.message.lower() or "unavailable" in response.message.lower()
    mock_db.create_resume_atomic_master.assert_awaited()
    assert mock_db.update_resume.called


@pytest.mark.asyncio
async def test_upload_empty_file_no_ollama() -> None:
    file = MagicMock()
    file.filename = "empty.pdf"
    file.content_type = "application/pdf"
    file.read = AsyncMock(return_value=b"")

    with patch(
        "app.services.parser.complete_json", new_callable=AsyncMock
    ) as mock_json:
        with pytest.raises(HTTPException) as exc:
            await resumes_router.upload_resume(file)
        mock_json.assert_not_awaited()
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_unsupported_no_ollama() -> None:
    file = MagicMock()
    file.filename = "notes.txt"
    file.content_type = "text/plain"
    file.read = AsyncMock(return_value=b"hello world")

    with patch(
        "app.services.parser.complete_json", new_callable=AsyncMock
    ) as mock_json:
        with pytest.raises(HTTPException) as exc:
            await resumes_router.upload_resume(file)
        mock_json.assert_not_awaited()
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_scanned_skips_ollama() -> None:
    file = MagicMock()
    file.filename = "scan.pdf"
    file.content_type = "application/pdf"
    file.read = AsyncMock(return_value=_minimal_pdf("x"))

    mock_db = MagicMock()
    mock_db.create_resume_atomic_master = AsyncMock(
        return_value={"resume_id": "r2", "is_master": True, "processing_status": "failed"}
    )

    with (
        patch.object(resumes_router, "db", mock_db),
        patch.object(
            resumes_router, "parse_resume_to_json", new_callable=AsyncMock
        ) as mock_parse,
    ):
        response = await resumes_router.upload_resume(file)

    mock_parse.assert_not_awaited()
    assert response.processing_status == "failed"
    assert response.ocr_needed is True
    assert response.ai_normalization_status == "skipped"
    assert response.reason_code == ExtractionReason.OCR_NEEDED.value
