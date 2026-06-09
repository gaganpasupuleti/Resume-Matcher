"""Tests for ResumeData schema coercion of LLM null values."""

from app.llm import get_resume_parse_timeout
from app.schemas import ResumeData
from app.services.parser import _sanitize_llm_nulls, get_parse_retries


def test_resume_data_accepts_null_required_strings_from_llm() -> None:
    """Ollama and other LLMs often emit null for missing optional-looking fields."""
    payload = {
        "personalInfo": {
            "name": "Gagan Kumar",
            "email": None,
            "location": None,
        },
        "personalProjects": [
            {"id": 1, "name": "Project A", "years": None},
            {"id": 2, "name": "Project B", "years": None},
            {"id": 3, "name": "Project C", "years": None},
        ],
        "customSections": {
            "projects": {
                "sectionType": "itemList",
                "items": [
                    {"id": 1, "title": "Custom A", "years": None},
                    {"id": 2, "title": "Custom B", "years": None},
                    {"id": 3, "title": "Custom C", "years": None},
                ],
            }
        },
    }

    resume = ResumeData.model_validate(payload)

    assert resume.personalInfo.email == ""
    assert resume.personalInfo.location == ""
    assert all(project.years == "" for project in resume.personalProjects)
    items = resume.customSections["projects"].items or []
    assert all(item.years == "" for item in items)


def test_sanitize_llm_nulls_converts_required_fields() -> None:
    payload = {
        "personalInfo": {"email": None, "location": None},
        "personalProjects": [{"years": None}],
    }
    sanitized = _sanitize_llm_nulls(payload)
    assert sanitized["personalInfo"]["email"] == ""
    assert sanitized["personalProjects"][0]["years"] == ""


def test_ollama_parse_timeout_exceeds_legacy_200s_limit() -> None:
    retries = get_parse_retries()
    timeout = get_resume_parse_timeout(retries=retries)
    assert timeout > 200
