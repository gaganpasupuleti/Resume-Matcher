"""Regression tests for improve workflow, persistence, and schema failure fields."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.providers.errors import ProviderError, ProviderErrorClass
from app.routers import resumes as resumes_router
from app.schemas import (
    ImproveResumeConfirmRequest,
    ImproveResumeRequest,
    ImprovementSuggestion,
    ResumeData,
)
from app.services.improver import generate_improvements
from app.services.parser import schema_failure_fields


SAMPLE_RESUME: dict[str, Any] = {
    "personalInfo": {
        "name": "Alex Review",
        "title": "",
        "email": "alex.review@example.com",
        "phone": "",
        "location": "",
        "website": None,
        "linkedin": None,
        "github": None,
    },
    "summary": "Engineer",
    "workExperience": [
        {
            "id": 1,
            "title": "Software Engineer",
            "company": "Acme Labs",
            "location": "",
            "years": "2021 - 2025",
            "description": ["Built FastAPI services."],
        }
    ],
    "education": [
        {
            "id": 1,
            "institution": "Example University",
            "degree": "B.S. Computer Science",
            "years": "",
            "description": None,
        }
    ],
    "personalProjects": [],
    "additional": {
        "technicalSkills": ["Python", "TypeScript"],
        "languages": [],
        "certificationsTraining": [],
        "awards": [],
    },
    "sectionMeta": [],
    "customSections": {},
}


class TestImproveHelpers(unittest.TestCase):
    def test_generate_improvements_tolerates_null_lists(self) -> None:
        """Explicit null keyword fields must not 500 the improve response builder."""
        result = generate_improvements(
            {
                "required_skills": None,
                "key_responsibilities": None,
                "preferred_skills": None,
                "keywords": None,
            }
        )
        self.assertTrue(result)
        self.assertIn("suggestion", result[0])

    def test_schema_failure_fields_from_validation_error(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            ResumeData.model_validate({"workExperience": "not-a-list"})
        fields = schema_failure_fields(ctx.exception)
        self.assertTrue(fields)
        self.assertIn("workExperience", fields[0])

    def test_drop_invalid_custom_sections_keeps_valid(self) -> None:
        from app.services.parser import _drop_invalid_custom_sections

        data = {
            "customSections": {
                "publications": "not-an-object",
                "skills": {
                    "sectionType": "stringList",
                    "strings": ["Python"],
                },
            }
        }
        repaired = _drop_invalid_custom_sections(data)
        self.assertNotIn("publications", repaired["customSections"])
        self.assertIn("skills", repaired["customSections"])

    def test_pdf_schema_invalid_records_fields(self) -> None:
        """When schema still fails, field paths are reported (no invented fill)."""
        with self.assertRaises(ValidationError) as ctx:
            ResumeData.model_validate({"additional": "not-an-object"})
        fields = schema_failure_fields(ctx.exception)
        self.assertTrue(any("additional" in f for f in fields))


class TestImprovePersistence(unittest.IsolatedAsyncioTestCase):
    async def test_improve_preview_success_with_null_keyword_lists(self) -> None:
        request = ImproveResumeRequest(
            resume_id="r1", job_id="j1", prompt_id="keywords"
        )
        resume = {
            "resume_id": "r1",
            "content": "Alex Review\nPython FastAPI",
            "content_type": "md",
            "processed_data": SAMPLE_RESUME,
            "original_markdown": "Alex Review\nPython FastAPI",
        }
        job = {
            "job_id": "j1",
            "content": "Need Python FastAPI engineer.",
            "job_keywords": {
                "required_skills": ["Python", "FastAPI"],
                "preferred_skills": None,
                "key_responsibilities": None,
                "keywords": ["Ollama"],
            },
            "job_keywords_hash": resumes_router._hash_job_content(
                "Need Python FastAPI engineer."
            ),
            "preview_hashes": {},
        }

        with (
            patch.object(
                resumes_router,
                "improve_resume",
                new=AsyncMock(return_value=SAMPLE_RESUME),
            ),
            patch.object(resumes_router.db, "get_master_resume", return_value=None),
            patch.object(resumes_router.db, "update_job", return_value=job),
        ):
            resp = await resumes_router._improve_preview_flow(
                request=request,
                resume=resume,
                job=job,
                language="en",
                prompt_id="keywords",
            )

        self.assertIsNone(resp.data.resume_id)
        self.assertEqual(resp.data.resume_preview.personalInfo.name, "Alex Review")
        self.assertTrue(resp.data.improvements)

    async def test_improve_preview_invalid_resume_id(self) -> None:
        request = ImproveResumeRequest(resume_id="missing", job_id="j1")
        with patch.object(resumes_router.db, "get_resume", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                await resumes_router.improve_resume_preview_endpoint(request)
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_improve_preview_ollama_unavailable_preserves_status(self) -> None:
        request = ImproveResumeRequest(resume_id="r1", job_id="j1")
        resume = {"resume_id": "r1", "content": "x", "processed_data": SAMPLE_RESUME}
        job = {
            "job_id": "j1",
            "content": "JD",
            "job_keywords": {"required_skills": ["Python"]},
            "job_keywords_hash": resumes_router._hash_job_content("JD"),
        }
        provider_err = ProviderError(
            ProviderErrorClass.UNAVAILABLE,
            "ollama down",
            correlation_id="cid-1",
            provider="ollama",
            model="gemma3:4b",
        )

        with (
            patch.object(resumes_router.db, "get_resume", return_value=resume),
            patch.object(resumes_router.db, "get_job", return_value=job),
            patch.object(
                resumes_router,
                "_improve_preview_flow",
                new=AsyncMock(side_effect=provider_err),
            ),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await resumes_router.improve_resume_preview_endpoint(request)

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.detail["error_code"], "unavailable")
        self.assertIn("not modified", ctx.exception.detail["message"].lower())

    async def test_improve_preview_malformed_json_maps_to_502(self) -> None:
        request = ImproveResumeRequest(resume_id="r1", job_id="j1")
        resume = {"resume_id": "r1", "content": "x", "processed_data": SAMPLE_RESUME}
        job = {
            "job_id": "j1",
            "content": "JD",
            "job_keywords": {"required_skills": ["Python"]},
            "job_keywords_hash": resumes_router._hash_job_content("JD"),
        }
        provider_err = ProviderError(
            ProviderErrorClass.INVALID_RESPONSE,
            "bad json",
            correlation_id="cid-2",
            provider="ollama",
            model="gemma3:4b",
        )

        with (
            patch.object(resumes_router.db, "get_resume", return_value=resume),
            patch.object(resumes_router.db, "get_job", return_value=job),
            patch.object(
                resumes_router,
                "_improve_preview_flow",
                new=AsyncMock(side_effect=provider_err),
            ),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await resumes_router.improve_resume_preview_endpoint(request)

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(ctx.exception.detail["error_code"], "invalid_response")

    async def test_improve_confirm_success(self) -> None:
        improved = ResumeData.model_validate(SAMPLE_RESUME)
        request = ImproveResumeConfirmRequest(
            resume_id="r1",
            job_id="j1",
            improved_data=improved,
            improvements=[ImprovementSuggestion(suggestion="Emphasized Python")],
        )
        resume = {
            "resume_id": "r1",
            "content": "Alex",
            "content_type": "md",
            "processed_data": SAMPLE_RESUME,
            "filename": "resume.docx",
        }
        preview_hash = resumes_router._hash_improved_data(improved.model_dump())
        job = {
            "job_id": "j1",
            "content": "Need Python",
            "preview_hashes": {"keywords": preview_hash},
        }
        tailored = {
            "resume_id": "tailored-1",
            "parent_id": "r1",
            "processed_data": SAMPLE_RESUME,
        }

        with (
            patch.object(resumes_router.db, "get_resume", return_value=resume),
            patch.object(resumes_router.db, "get_job", return_value=job),
            patch.object(
                resumes_router,
                "_generate_auxiliary_messages",
                new=AsyncMock(return_value=(None, None, "Title", [])),
            ),
            patch.object(resumes_router.db, "create_resume", return_value=tailored) as mock_create,
            patch.object(resumes_router.db, "create_improvement", return_value={}),
        ):
            resp = await resumes_router.improve_resume_confirm_endpoint(request)

        self.assertEqual(resp.data.resume_id, "tailored-1")
        mock_create.assert_called_once()

    async def test_update_resume_persists_without_duplicate(self) -> None:
        resume_id = "r1"
        existing = {
            "resume_id": resume_id,
            "content": "{}",
            "content_type": "json",
            "created_at": "t0",
            "processed_data": SAMPLE_RESUME,
            "processing_status": "ready",
        }
        edited = ResumeData.model_validate(
            {
                **SAMPLE_RESUME,
                "personalInfo": {
                    **SAMPLE_RESUME["personalInfo"],
                    "name": "Alex Edited",
                },
            }
        )
        updated = {
            **existing,
            "processed_data": edited.model_dump(),
            "content": "json",
        }

        with (
            patch.object(
                resumes_router.db, "get_resume", side_effect=[existing, updated, existing, updated]
            ),
            patch.object(
                resumes_router.db, "update_resume", return_value=updated
            ) as mock_update,
            patch.object(resumes_router.db, "create_resume") as mock_create,
        ):
            resp = await resumes_router.update_resume_endpoint(resume_id, edited)
            again = await resumes_router.update_resume_endpoint(resume_id, edited)

        self.assertEqual(resp.data.resume_id, resume_id)
        self.assertIsNotNone(resp.data.processed_resume)
        assert resp.data.processed_resume is not None
        self.assertEqual(resp.data.processed_resume.personalInfo.name, "Alex Edited")
        self.assertEqual(again.data.resume_id, resume_id)
        self.assertEqual(mock_update.call_count, 2)
        mock_create.assert_not_called()

    async def test_get_resume_by_path_matches_query_fetch(self) -> None:
        resume = {
            "resume_id": "r1",
            "content": "md",
            "content_type": "md",
            "created_at": "t0",
            "processed_data": SAMPLE_RESUME,
            "processing_status": "ready",
            "cover_letter": "CL",
            "outreach_message": "OM",
            "parent_id": None,
            "title": "T",
        }
        with patch.object(resumes_router.db, "get_resume", return_value=resume):
            by_query = await resumes_router.get_resume("r1")
            by_path = await resumes_router.get_resume_by_path("r1")

        self.assertEqual(by_query.data.resume_id, "r1")
        self.assertEqual(by_path.data.resume_id, "r1")
        self.assertEqual(by_path.data.cover_letter, "CL")
        self.assertIsNotNone(by_path.data.processed_resume)

    async def test_cover_letter_generation_success(self) -> None:
        resume = {
            "resume_id": "t1",
            "parent_id": "r1",
            "processed_data": SAMPLE_RESUME,
        }
        improvement = {"job_id": "j1"}
        job = {"job_id": "j1", "content": "Need Python engineer"}

        with (
            patch.object(resumes_router.db, "get_resume", return_value=resume),
            patch.object(
                resumes_router.db,
                "get_improvement_by_tailored_resume",
                return_value=improvement,
            ),
            patch.object(resumes_router.db, "get_job", return_value=job),
            patch.object(
                resumes_router,
                "generate_cover_letter_result",
                new=AsyncMock(
                    return_value={
                        "content": "Dear Hiring Manager,\nI built FastAPI services."
                    }
                ),
            ),
            patch.object(
                resumes_router.db, "update_resume", return_value=resume
            ) as mock_update,
        ):
            resp = await resumes_router.generate_cover_letter_endpoint("t1")

        self.assertIn("FastAPI", resp.content)
        mock_update.assert_called()

    async def test_outreach_generation_success(self) -> None:
        resume = {
            "resume_id": "t1",
            "parent_id": "r1",
            "processed_data": SAMPLE_RESUME,
        }
        improvement = {"job_id": "j1"}
        job = {"job_id": "j1", "content": "Need Python engineer"}

        with (
            patch.object(resumes_router.db, "get_resume", return_value=resume),
            patch.object(
                resumes_router.db,
                "get_improvement_by_tailored_resume",
                return_value=improvement,
            ),
            patch.object(resumes_router.db, "get_job", return_value=job),
            patch.object(
                resumes_router,
                "generate_outreach_message_result",
                new=AsyncMock(
                    return_value={
                        "content": "Subject: Opportunity\n\nHello, I use Python and FastAPI."
                    }
                ),
            ),
            patch.object(resumes_router.db, "update_resume", return_value=resume),
        ):
            resp = await resumes_router.generate_outreach_endpoint("t1")

        self.assertIn("Python", resp.content)

    async def test_cover_letter_provider_failure_keeps_resume(self) -> None:
        resume = {
            "resume_id": "t1",
            "parent_id": "r1",
            "processed_data": SAMPLE_RESUME,
        }
        improvement = {"job_id": "j1"}
        job = {"job_id": "j1", "content": "Need Python"}
        err = ProviderError(
            ProviderErrorClass.UNAVAILABLE,
            "down",
            correlation_id="c3",
            provider="ollama",
            model="gemma3:4b",
        )

        with (
            patch.object(resumes_router.db, "get_resume", return_value=resume),
            patch.object(
                resumes_router.db,
                "get_improvement_by_tailored_resume",
                return_value=improvement,
            ),
            patch.object(resumes_router.db, "get_job", return_value=job),
            patch.object(
                resumes_router,
                "generate_cover_letter_result",
                new=AsyncMock(side_effect=err),
            ),
            patch.object(resumes_router.db, "update_resume") as mock_update,
        ):
            with self.assertRaises(HTTPException) as ctx:
                await resumes_router.generate_cover_letter_endpoint("t1")

        self.assertEqual(ctx.exception.status_code, 503)
        mock_update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
