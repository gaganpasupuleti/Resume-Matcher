"""Cover letter, outreach/application email, and resume title generation.

Hardened for Agent K: no fabricated experience/skills/metrics; warn when
resume evidence is insufficient; return editable plain text with warnings.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.llm import complete, get_llm_config
from app.prompts import get_language_name
from app.prompts.templates import (
    COVER_LETTER_PROMPT,
    GENERATE_TITLE_PROMPT,
    OUTREACH_MESSAGE_PROMPT,
)
from app.providers.errors import ProviderError
from app.providers.ollama import new_correlation_id
from app.providers.policy import is_codequest_local_mode
from app.services.jd_matcher import assess_resume_sufficiency
from app.services.refiner import _extract_all_text

logger = logging.getLogger(__name__)

_METRIC_RE = re.compile(
    r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*%|\b\d{2,}(?:,\d{3})+\b|\b\d+\s*(?:x|X)\b"
)

_GENERATION_TRUTH_SUFFIX = """

CRITICAL TRUTHFULNESS:
- Use ONLY experience, skills, tools, employers, and metrics present in the resume JSON.
- Do NOT invent achievements, percentages, headcount, revenue, or tools.
- If evidence is thin, write a shorter honest letter and avoid padding.
- Prefer qualitative phrasing over fabricated numbers.
"""


def _provider_meta() -> dict[str, Any]:
    try:
        config = get_llm_config()
    except ProviderError as exc:
        return {
            "provider": exc.provider or "unknown",
            "model": exc.model,
            "codequest_local_mode": is_codequest_local_mode(),
            "error_code": exc.error_code,
            "correlation_id": exc.correlation_id,
        }
    return {
        "provider": config.provider,
        "model": config.model,
        "api_base": config.api_base,
        "codequest_local_mode": is_codequest_local_mode(),
    }


def find_fabricated_metrics(content: str, resume_data: dict[str, Any]) -> list[str]:
    """Return metric-like tokens in content that do not appear in the resume text."""
    resume_text = _extract_all_text(resume_data)
    resume_norm = resume_text.lower()
    fabricated: list[str] = []
    for match in _METRIC_RE.findall(content or ""):
        token = match.strip()
        if token.lower() not in resume_norm:
            fabricated.append(token)
    return fabricated


def _generation_base_warnings(resume_data: dict[str, Any]) -> list[str]:
    warnings = assess_resume_sufficiency(resume_data)
    if warnings:
        warnings.append("generation_may_be_thin")
    return warnings


async def generate_cover_letter(
    resume_data: dict[str, Any],
    job_description: str,
    language: str = "en",
) -> str:
    """Generate a cover letter (plain text). Prefer generate_cover_letter_result."""
    result = await generate_cover_letter_result(resume_data, job_description, language)
    return result["content"]


async def generate_outreach_message(
    resume_data: dict[str, Any],
    job_description: str,
    language: str = "en",
) -> str:
    """Generate an application/outreach email (plain text)."""
    result = await generate_outreach_message_result(
        resume_data, job_description, language
    )
    return result["content"]


async def generate_cover_letter_result(
    resume_data: dict[str, Any],
    job_description: str,
    language: str = "en",
) -> dict[str, Any]:
    """Cover letter with warnings and provider metadata (no secrets)."""
    cid = new_correlation_id()
    warnings = _generation_base_warnings(resume_data)
    output_language = get_language_name(language)

    if "insufficient_resume_text" in warnings:
        logger.info(
            "Cover letter insufficient data correlation_id=%s",
            cid,
        )
        return {
            "content": "",
            "warnings": warnings + ["insufficient_data"],
            "insufficient": True,
            "editable": True,
            "correlation_id": cid,
            "provider": _provider_meta(),
            "message": "Insufficient resume data to generate a truthful cover letter",
        }

    prompt = (
        COVER_LETTER_PROMPT.format(
            job_description=job_description,
            resume_data=json.dumps(resume_data, indent=2),
            output_language=output_language,
        )
        + _GENERATION_TRUTH_SUFFIX
    )

    try:
        result = await complete(
            prompt=prompt,
            system_prompt=(
                "You are a professional career coach. Write truthful cover letters. "
                "Never invent experience, skills, or metrics."
            ),
            max_tokens=2048,
        )
    except ProviderError as exc:
        logger.warning(
            "Cover letter provider error correlation_id=%s error_code=%s",
            cid,
            exc.error_code,
        )
        raise
    except Exception:
        logger.warning("Cover letter generation failed correlation_id=%s", cid)
        raise

    content = (result or "").strip()
    fabricated = find_fabricated_metrics(content, resume_data)
    if fabricated:
        warnings.append("possible_fabricated_metrics")
        logger.warning(
            "Cover letter metric warning correlation_id=%s count=%d",
            cid,
            len(fabricated),
        )
        # Strip invented metric tokens rather than shipping fabricated numbers
        scrubbed = content
        for token in fabricated:
            scrubbed = scrubbed.replace(token, "[evidence not in resume]")
        content = scrubbed
        warnings.append("fabricated_metrics_scrubbed")

    logger.info(
        "Cover letter generated correlation_id=%s chars=%d",
        cid,
        len(content),
    )

    return {
        "content": content,
        "warnings": warnings,
        "insufficient": False,
        "editable": True,
        "correlation_id": cid,
        "provider": _provider_meta(),
        "message": "Cover letter generated successfully",
    }


async def generate_outreach_message_result(
    resume_data: dict[str, Any],
    job_description: str,
    language: str = "en",
) -> dict[str, Any]:
    """Application/outreach email with warnings and provider metadata."""
    cid = new_correlation_id()
    warnings = _generation_base_warnings(resume_data)
    output_language = get_language_name(language)

    if "insufficient_resume_text" in warnings:
        logger.info(
            "Outreach insufficient data correlation_id=%s",
            cid,
        )
        return {
            "content": "",
            "warnings": warnings + ["insufficient_data"],
            "insufficient": True,
            "editable": True,
            "correlation_id": cid,
            "provider": _provider_meta(),
            "message": "Insufficient resume data to generate a truthful application email",
        }

    prompt = (
        OUTREACH_MESSAGE_PROMPT.format(
            job_description=job_description,
            resume_data=json.dumps(resume_data, indent=2),
            output_language=output_language,
        )
        + _GENERATION_TRUTH_SUFFIX
        + "\nThis is a concise application / outreach email, not a full cover letter."
    )

    try:
        result = await complete(
            prompt=prompt,
            system_prompt=(
                "You are a professional networking coach. Write genuine outreach. "
                "Never invent experience, skills, or metrics."
            ),
            max_tokens=1024,
        )
    except ProviderError as exc:
        logger.warning(
            "Outreach provider error correlation_id=%s error_code=%s",
            cid,
            exc.error_code,
        )
        raise
    except Exception:
        logger.warning("Outreach generation failed correlation_id=%s", cid)
        raise

    content = (result or "").strip()
    fabricated = find_fabricated_metrics(content, resume_data)
    if fabricated:
        warnings.append("possible_fabricated_metrics")
        scrubbed = content
        for token in fabricated:
            scrubbed = scrubbed.replace(token, "[evidence not in resume]")
        content = scrubbed
        warnings.append("fabricated_metrics_scrubbed")

    logger.info(
        "Outreach generated correlation_id=%s chars=%d",
        cid,
        len(content),
    )

    return {
        "content": content,
        "warnings": warnings,
        "insufficient": False,
        "editable": True,
        "correlation_id": cid,
        "provider": _provider_meta(),
        "message": "Application email generated successfully",
    }


async def generate_resume_title(
    job_description: str,
    language: str = "en",
) -> str:
    """Generate a short descriptive title from a job description."""
    output_language = get_language_name(language)

    prompt = GENERATE_TITLE_PROMPT.format(
        job_description=job_description,
        output_language=output_language,
    )

    result = await complete(
        prompt=prompt,
        system_prompt="You extract job titles and company names from job descriptions.",
        max_tokens=60,
        temperature=0.3,
    )

    title = result.strip().strip("\"'")
    return title[:80]
