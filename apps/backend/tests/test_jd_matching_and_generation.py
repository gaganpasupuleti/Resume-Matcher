"""Proof tests for JD matching and generation workflows (Agent K)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.providers.errors import ProviderError, ProviderErrorClass
from app.services.cover_letter import (
    find_fabricated_metrics,
    generate_cover_letter_result,
    generate_outreach_message_result,
)
from app.services.jd_matcher import (
    analyze_deterministic,
    analyze_jd_match,
    assess_resume_sufficiency,
    clear_analysis_cache,
    extract_jd_keywords_deterministic,
    provider_metadata_safe,
)


SAMPLE_RESUME = {
    "personalInfo": {
        "name": "Jane Doe",
        "title": "Software Engineer",
        "email": "jane@example.com",
        "phone": "",
        "location": "",
        "website": "",
        "linkedin": "",
        "github": "",
    },
    "summary": "Software engineer building learning platforms with Python and TypeScript.",
    "workExperience": [
        {
            "id": 1,
            "title": "Senior Engineer",
            "company": "Acme Corp",
            "location": "Remote",
            "years": "Jan 2020 - Present",
            "description": [
                "Built resume parsing pipelines and student dashboards.",
                "Improved API latency using Redis caching.",
            ],
        }
    ],
    "education": [
        {
            "id": 1,
            "institution": "State University",
            "degree": "B.S. Computer Science",
            "years": "2014 - 2018",
            "description": "",
        }
    ],
    "personalProjects": [
        {
            "id": 1,
            "name": "Code Quest Lab",
            "role": "Creator",
            "years": "2024 - Present",
            "description": ["Local AI resume matcher"],
        }
    ],
    "additional": {
        "technicalSkills": ["Python", "TypeScript", "SQL", "Redis"],
        "languages": ["English"],
        "certificationsTraining": ["AWS Cloud Practitioner"],
        "awards": [],
    },
    "customSections": {},
    "sections": [],
}

SAMPLE_JD = """
Software Engineer
We need a Python and TypeScript engineer with SQL, Redis, and Docker experience.
Responsibilities include building APIs, machine learning features, and mentoring.
Preferred: Kubernetes and GraphQL.
"""


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_analysis_cache()


def test_successful_jd_analysis_has_explained_score() -> None:
    result = analyze_deterministic(SAMPLE_RESUME, SAMPLE_JD)

    assert result["overall_score"] > 0
    assert result["breakdown"], "score must include component breakdown"
    for item in result["breakdown"]:
        assert "reason" in item and item["reason"]
        assert 0 <= item["score"] <= 100
    assert "python" in [k.lower() for k in result["matched_keywords"]]
    assert "provider" not in result or "api_key" not in str(result.get("provider"))


def test_missing_keywords_detected() -> None:
    result = analyze_deterministic(SAMPLE_RESUME, SAMPLE_JD)
    missing_lower = [k.lower() for k in result["missing_keywords"]]
    assert "docker" in missing_lower or "kubernetes" in missing_lower
    assert any("Missing keywords" in g for g in result["gaps"])


@pytest.mark.asyncio
async def test_repeated_request_uses_safe_cache_keys() -> None:
    with patch(
        "app.services.jd_matcher.get_llm_config",
        return_value=type(
            "C",
            (),
            {
                "provider": "ollama",
                "model": "gemma3:4b",
                "api_key": "SECRET-KEY-SHOULD-NOT-APPEAR",
                "api_base": "http://localhost:11434",
            },
        )(),
    ):
        with patch(
            "app.services.jd_matcher.complete_json",
            new_callable=AsyncMock,
        ) as mock_json:
            mock_json.return_value = {
                "strengths": ["Python experience matches"],
                "gaps": [],
                "recommendations": ["Mention Redis explicitly in summary"],
                "missing_skills": ["Docker"],
                "wording_improvements": [],
                "role_suggestions": [],
            }
            first = await analyze_jd_match(SAMPLE_RESUME, SAMPLE_JD, use_ollama=True)
            second = await analyze_jd_match(SAMPLE_RESUME, SAMPLE_JD, use_ollama=True)

    assert first["cached"] is False
    assert second["cached"] is True
    assert first["overall_score"] == second["overall_score"]
    assert mock_json.await_count == 1
    cache_key = second["cache_key"]
    assert set(cache_key) >= {"resume_hash", "jd_hash", "model", "version"}
    assert "api_key" not in second["provider"]
    assert "SECRET" not in str(second["provider"])


def test_insufficient_resume_data_warns() -> None:
    thin = {
        "personalInfo": {"name": "X", "title": "", "email": "", "phone": ""},
        "summary": "",
        "workExperience": [],
        "education": [],
        "personalProjects": [],
        "additional": {"technicalSkills": []},
        "customSections": {},
        "sections": [],
    }
    warnings = assess_resume_sufficiency(thin)
    assert "insufficient_resume_text" in warnings
    result = analyze_deterministic(thin, SAMPLE_JD)
    assert "insufficient_resume_text" in result["warnings"]


@pytest.mark.asyncio
async def test_ollama_unavailable_falls_back_to_deterministic() -> None:
    with patch(
        "app.services.jd_matcher.get_llm_config",
        return_value=type(
            "C",
            (),
            {
                "provider": "ollama",
                "model": "gemma3:4b",
                "api_key": "",
                "api_base": "http://localhost:11434",
            },
        )(),
    ):
        with patch(
            "app.services.jd_matcher.complete_json",
            new_callable=AsyncMock,
            side_effect=ProviderError(
                ProviderErrorClass.UNAVAILABLE,
                "Ollama endpoint is unavailable",
                correlation_id="cid-unavail",
                provider="ollama",
                model="gemma3:4b",
            ),
        ):
            result = await analyze_jd_match(SAMPLE_RESUME, SAMPLE_JD, use_ollama=True)

    assert result["overall_score"] > 0
    assert result["enhancement_status"] == "unavailable"
    assert any("unavailable" in w for w in result["warnings"])
    assert result["breakdown"]


@pytest.mark.asyncio
async def test_malformed_ollama_response_falls_back() -> None:
    with patch(
        "app.services.jd_matcher.get_llm_config",
        return_value=type(
            "C",
            (),
            {
                "provider": "ollama",
                "model": "gemma3:4b",
                "api_key": "",
                "api_base": "http://localhost:11434",
            },
        )(),
    ):
        with patch(
            "app.services.jd_matcher.complete_json",
            new_callable=AsyncMock,
            side_effect=ProviderError(
                ProviderErrorClass.INVALID_RESPONSE,
                "Malformed JSON",
                correlation_id="cid-bad",
                provider="ollama",
            ),
        ):
            result = await analyze_jd_match(SAMPLE_RESUME, SAMPLE_JD, use_ollama=True)

    assert result["enhancement_status"] == "unavailable"
    assert any("invalid_response" in w for w in result["warnings"])
    assert result["matched_keywords"]


@pytest.mark.asyncio
async def test_cover_letter_generation_truthful() -> None:
    with patch(
        "app.services.cover_letter.complete",
        new_callable=AsyncMock,
        return_value=(
            "I am excited about your Python and TypeScript role. "
            "At Acme Corp I built resume parsing pipelines."
        ),
    ):
        with patch(
            "app.services.cover_letter.get_llm_config",
            return_value=type(
                "C",
                (),
                {
                    "provider": "ollama",
                    "model": "gemma3:4b",
                    "api_key": "secret",
                    "api_base": "http://localhost:11434",
                },
            )(),
        ):
            result = await generate_cover_letter_result(SAMPLE_RESUME, SAMPLE_JD)

    assert result["content"]
    assert result["editable"] is True
    assert "api_key" not in (result["provider"] or {})
    assert "secret" not in str(result["provider"])


@pytest.mark.asyncio
async def test_application_email_generation() -> None:
    with patch(
        "app.services.cover_letter.complete",
        new_callable=AsyncMock,
        return_value=(
            "Your Redis and SQL focus matches work I did on API latency. Worth a quick chat?"
        ),
    ):
        with patch(
            "app.services.cover_letter.get_llm_config",
            return_value=type(
                "C",
                (),
                {
                    "provider": "ollama",
                    "model": "gemma3:4b",
                    "api_key": "",
                    "api_base": "http://localhost:11434",
                },
            )(),
        ):
            result = await generate_outreach_message_result(SAMPLE_RESUME, SAMPLE_JD)

    assert result["content"]
    assert result["editable"] is True
    assert result["insufficient"] is False


@pytest.mark.asyncio
async def test_insufficient_data_blocks_fabrication_prone_generation() -> None:
    thin = {
        "personalInfo": {"name": "X"},
        "summary": "",
        "workExperience": [],
        "education": [],
        "personalProjects": [],
        "additional": {"technicalSkills": []},
        "customSections": {},
        "sections": [],
    }
    with patch("app.services.cover_letter.complete", new_callable=AsyncMock) as mock_complete:
        result = await generate_cover_letter_result(thin, SAMPLE_JD)

    assert result["insufficient"] is True
    assert result["content"] == ""
    assert "insufficient_data" in result["warnings"]
    mock_complete.assert_not_awaited()


def test_no_fabrication_metric_scrub() -> None:
    content = "I increased revenue by 47% and scaled to 10,000 users."
    fabricated = find_fabricated_metrics(content, SAMPLE_RESUME)
    assert fabricated
    assert "47%" in fabricated or any("%" in f for f in fabricated)


@pytest.mark.asyncio
async def test_fabricated_metrics_scrubbed_from_cover_letter() -> None:
    with patch(
        "app.services.cover_letter.complete",
        new_callable=AsyncMock,
        return_value="I increased conversion by 47% while using Python at Acme Corp.",
    ):
        with patch(
            "app.services.cover_letter.get_llm_config",
            return_value=type(
                "C",
                (),
                {
                    "provider": "ollama",
                    "model": "gemma3:4b",
                    "api_key": "",
                    "api_base": "http://localhost:11434",
                },
            )(),
        ):
            result = await generate_cover_letter_result(SAMPLE_RESUME, SAMPLE_JD)

    assert "47%" not in result["content"]
    assert "fabricated_metrics_scrubbed" in result["warnings"]


def test_provider_metadata_has_no_secrets() -> None:
    with patch(
        "app.services.jd_matcher.get_llm_config",
        return_value=type(
            "C",
            (),
            {
                "provider": "ollama",
                "model": "gemma3:4b",
                "api_key": "sk-secret-value",
                "api_base": "http://localhost:11434",
            },
        )(),
    ):
        meta = provider_metadata_safe()
    assert "api_key" not in meta
    assert "sk-secret" not in str(meta)


def test_no_pii_in_jd_analysis_info_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.jd_matcher"):
        analyze_deterministic(SAMPLE_RESUME, SAMPLE_JD)
    joined = " ".join(r.message for r in caplog.records)
    assert "jane@example.com" not in joined.lower()
    assert "jane doe" not in joined.lower()


def test_keyword_extraction_filters_stopwords() -> None:
    keywords = extract_jd_keywords_deterministic(
        "The candidate should have Python and Docker experience for the role"
    )
    lower = [k.lower() for k in keywords]
    assert "python" in lower
    assert "docker" in lower
    assert "the" not in lower
    assert "should" not in lower


@pytest.mark.asyncio
async def test_enhancement_skipped_when_insufficient() -> None:
    thin = {
        "personalInfo": {"name": "X"},
        "summary": "Hi",
        "workExperience": [],
        "education": [],
        "personalProjects": [],
        "additional": {"technicalSkills": []},
        "customSections": {},
        "sections": [],
    }
    with patch(
        "app.services.jd_matcher.get_llm_config",
        return_value=type(
            "C",
            (),
            {
                "provider": "ollama",
                "model": "gemma3:4b",
                "api_key": "",
                "api_base": "http://localhost:11434",
            },
        )(),
    ):
        with patch(
            "app.services.jd_matcher.complete_json",
            new_callable=AsyncMock,
        ) as mock_json:
            result = await analyze_jd_match(thin, SAMPLE_JD, use_ollama=True)

    mock_json.assert_not_awaited()
    assert result["enhancement_status"] == "skipped_insufficient_data"
    assert "ollama_enhancement_skipped_insufficient_data" in result["warnings"]
