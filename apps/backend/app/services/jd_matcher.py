"""Deterministic JD matching with optional Ollama enhancement (Agent K).

Score is always explainable from deterministic components. Ollama may enrich
strengths / gaps / recommendations; failures degrade to deterministic-only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import OrderedDict
from typing import Any

from app.llm import complete_json, get_llm_config
from app.providers.errors import ProviderError
from app.providers.ollama import new_correlation_id, scrub_for_logs
from app.providers.policy import is_codequest_local_mode
from app.services.refiner import _extract_all_text, _keyword_in_text

logger = logging.getLogger(__name__)

# ponytail: in-process LRU is enough for local single-user Lab; upgrade to TinyDB
# if multi-worker / restart-stable cache is required.
_CACHE_MAX = 64
_analysis_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()

_STOP_WORDS = frozenset(
    """
    a an the and or but if then else when while for with without about into from
    to of on in at by as is are was were be been being have has had do does did
    will would could should may might must shall can need this that these those
    it its they them their you your we our i me my he she his her who whom which
    what where when how why not no nor so than too very just also only other such
    own same each every both few more most other some such any all over under
    again further once here there up down out off above below between through
    during before after job jobs role roles team teams work working experience
    experiences required requirements preferred prefer qualification qualifications
    responsibility responsibilities duty duties ability able strong good great
    excellent candidate candidates company companies opportunity opportunities
    including include includes using use used via per etc etcetera year years
    month months day days including across within using based using please
    """.split()
)

_TECH_MULTIWORD = (
    "machine learning",
    "deep learning",
    "data science",
    "data engineering",
    "software engineering",
    "computer science",
    "rest api",
    "graphql",
    "ci cd",
    "unit testing",
    "system design",
    "distributed systems",
    "cloud computing",
    "power bi",
    "sql server",
    "react native",
    "node js",
    "next js",
    "type script",
)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,40}")
_TITLE_SPLIT_RE = re.compile(r"[|/,\-–—]+")

JD_ENHANCE_PROMPT = """You enrich a deterministic resume–job match analysis.
Output ONLY a JSON object with this shape:
{{
  "strengths": ["..."],
  "gaps": ["..."],
  "recommendations": ["..."],
  "missing_skills": ["..."],
  "wording_improvements": ["..."],
  "role_suggestions": ["..."]
}}

Rules:
- Use ONLY facts present in the resume and job description summaries below.
- Do NOT invent employers, degrees, skills, tools, metrics, or titles.
- Prefer concrete, actionable wording.
- Keep each list to at most 5 short items.
- If evidence is thin, return empty lists rather than guessing.

Job title hint: {job_title}
Deterministic matched keywords: {matched}
Deterministic missing keywords: {missing}
Resume summary (truncated): {resume_summary}
Job summary (truncated): {job_summary}
"""


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resume_payload_hash(resume_data: dict[str, Any]) -> str:
    serialized = json.dumps(resume_data, sort_keys=True, default=str, separators=(",", ":"))
    return content_hash(serialized)


def provider_metadata_safe() -> dict[str, Any]:
    """Provider/model metadata with no secrets."""
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
        # Never expose api_key
    }


def _cache_key(resume_hash: str, jd_hash: str, model: str, version: str = "v1") -> str:
    return f"{resume_hash}:{jd_hash}:{model}:{version}"


def _cache_get(key: str) -> dict[str, Any] | None:
    hit = _analysis_cache.get(key)
    if hit is None:
        return None
    _analysis_cache.move_to_end(key)
    # Return a deep-ish copy so callers can mutate freely
    return json.loads(json.dumps(hit))


def _cache_put(key: str, value: dict[str, Any]) -> None:
    _analysis_cache[key] = json.loads(json.dumps(value))
    _analysis_cache.move_to_end(key)
    while len(_analysis_cache) > _CACHE_MAX:
        _analysis_cache.popitem(last=False)


def clear_analysis_cache() -> None:
    """Test helper."""
    _analysis_cache.clear()


def normalize_skill_token(token: str) -> str:
    return re.sub(r"\s+", " ", token.strip().lower())


def extract_jd_keywords_deterministic(job_description: str) -> list[str]:
    """Stopword-filtered keywords + known multi-word tech phrases."""
    text = job_description or ""
    lower = text.lower()
    found: list[str] = []
    seen: set[str] = set()

    for phrase in _TECH_MULTIWORD:
        if phrase in lower and phrase not in seen:
            seen.add(phrase)
            found.append(phrase)

    for raw in _TOKEN_RE.findall(text):
        token = normalize_skill_token(raw)
        if len(token) < 2 or token in _STOP_WORDS or token in seen:
            continue
        if token.isdigit():
            continue
        seen.add(token)
        found.append(token)

    return found[:80]


def _resume_skills(resume_data: dict[str, Any]) -> list[str]:
    additional = resume_data.get("additional") or {}
    skills = additional.get("technicalSkills") or []
    out: list[str] = []
    for item in skills:
        if isinstance(item, str) and item.strip():
            out.append(normalize_skill_token(item))
        elif isinstance(item, dict):
            name = item.get("name") or item.get("label") or item.get("value")
            if isinstance(name, str) and name.strip():
                out.append(normalize_skill_token(name))
    return out


def _section_presence(resume_data: dict[str, Any]) -> dict[str, bool]:
    additional = resume_data.get("additional") or {}
    return {
        "summary": bool(str(resume_data.get("summary") or "").strip()),
        "workExperience": bool(resume_data.get("workExperience")),
        "education": bool(resume_data.get("education")),
        "personalProjects": bool(resume_data.get("personalProjects")),
        "skills": bool(additional.get("technicalSkills")),
        "certifications": bool(additional.get("certificationsTraining")),
    }


def _title_tokens(text: str) -> set[str]:
    parts = _TITLE_SPLIT_RE.split(text or "")
    tokens: set[str] = set()
    for part in parts:
        for tok in _TOKEN_RE.findall(part):
            norm = normalize_skill_token(tok)
            if norm and norm not in _STOP_WORDS and len(norm) > 1:
                tokens.add(norm)
    return tokens


def _infer_job_title(job_description: str) -> str:
    first_line = (job_description or "").strip().splitlines()[0] if job_description else ""
    return first_line.strip()[:120]


def assess_resume_sufficiency(resume_data: dict[str, Any]) -> list[str]:
    """Warnings when resume lacks enough signal for reliable matching/generation."""
    warnings: list[str] = []
    sections = _section_presence(resume_data)
    if not sections["workExperience"] and not sections["personalProjects"]:
        warnings.append("insufficient_experience")
    if not sections["skills"]:
        warnings.append("insufficient_skills")
    if not sections["summary"] and not sections["workExperience"]:
        warnings.append("insufficient_summary")
    text = _extract_all_text(resume_data).strip()
    if len(text) < 80:
        warnings.append("insufficient_resume_text")
    return warnings


def analyze_deterministic(
    resume_data: dict[str, Any],
    job_description: str,
) -> dict[str, Any]:
    """Pure deterministic match analysis with explained component scores."""
    jd_keywords = extract_jd_keywords_deterministic(job_description)
    resume_text = _extract_all_text(resume_data)
    resume_skills = set(_resume_skills(resume_data))
    sections = _section_presence(resume_data)

    matched: list[str] = []
    missing: list[str] = []
    for kw in jd_keywords:
        if _keyword_in_text(kw, resume_text) or normalize_skill_token(kw) in resume_skills:
            matched.append(kw)
        else:
            missing.append(kw)

    coverage = (len(matched) / len(jd_keywords) * 100.0) if jd_keywords else 0.0

    present_count = sum(1 for v in sections.values() if v)
    section_score = (present_count / len(sections)) * 100.0

    exp_entries = resume_data.get("workExperience") or []
    exp_score = min(100.0, len(exp_entries) * 25.0) if exp_entries else 0.0
    if any(
        isinstance(e, dict) and (e.get("description") or e.get("years"))
        for e in exp_entries
    ):
        exp_score = min(100.0, exp_score + 25.0)

    edu = resume_data.get("education") or []
    certs = (resume_data.get("additional") or {}).get("certificationsTraining") or []
    edu_score = 0.0
    if edu:
        edu_score += 60.0
    if certs:
        edu_score += 40.0
    edu_score = min(100.0, edu_score)

    job_title = _infer_job_title(job_description)
    resume_title = str((resume_data.get("personalInfo") or {}).get("title") or "")
    job_tokens = _title_tokens(job_title) | _title_tokens(job_description[:400])
    resume_tokens = _title_tokens(resume_title)
    for exp in exp_entries[:3]:
        if isinstance(exp, dict):
            resume_tokens |= _title_tokens(str(exp.get("title") or ""))
    if job_tokens:
        overlap = len(job_tokens & resume_tokens) / len(job_tokens)
        title_score = overlap * 100.0
    else:
        title_score = 0.0

    skill_overlap = 0.0
    if resume_skills and jd_keywords:
        jd_norm = {normalize_skill_token(k) for k in jd_keywords}
        skill_overlap = len(resume_skills & jd_norm) / max(len(jd_norm), 1) * 100.0

    components = [
        {
            "id": "keyword_coverage",
            "label": "Keyword coverage",
            "score": round(coverage, 1),
            "weight": 0.35,
            "reason": f"{len(matched)} of {len(jd_keywords)} JD keywords found in resume",
        },
        {
            "id": "normalized_skills",
            "label": "Normalized skills overlap",
            "score": round(skill_overlap, 1),
            "weight": 0.20,
            "reason": f"{len(resume_skills & {normalize_skill_token(k) for k in jd_keywords})} skills overlap JD tokens",
        },
        {
            "id": "sections_present",
            "label": "Sections present",
            "score": round(section_score, 1),
            "weight": 0.10,
            "reason": f"{present_count}/{len(sections)} core sections populated",
        },
        {
            "id": "experience_indicators",
            "label": "Experience indicators",
            "score": round(exp_score, 1),
            "weight": 0.20,
            "reason": f"{len(exp_entries)} work experience entries with detail signals",
        },
        {
            "id": "education_certification",
            "label": "Education / certifications",
            "score": round(edu_score, 1),
            "weight": 0.05,
            "reason": (
                f"education={'yes' if edu else 'no'}, "
                f"certifications={'yes' if certs else 'no'}"
            ),
        },
        {
            "id": "title_similarity",
            "label": "Title similarity",
            "score": round(title_score, 1),
            "weight": 0.10,
            "reason": f"title token overlap against job title hint '{job_title[:60]}'",
        },
    ]

    overall = sum(c["score"] * c["weight"] for c in components)
    overall = round(min(100.0, max(0.0, overall)), 1)

    strengths: list[str] = []
    gaps: list[str] = []
    recommendations: list[str] = []

    if coverage >= 50:
        strengths.append("Solid keyword coverage against the job description")
    if skill_overlap >= 40:
        strengths.append("Listed skills align with several JD requirements")
    if exp_score >= 50:
        strengths.append("Work experience section provides usable evidence")

    if missing[:5]:
        gaps.append("Missing keywords: " + ", ".join(missing[:5]))
        recommendations.append(
            "Rephrase existing experience to surface missing keywords only where truthful"
        )
    if not sections["skills"]:
        gaps.append("Technical skills section is empty")
        recommendations.append("Add skills already evidenced in experience or projects")
    if exp_score < 25:
        gaps.append("Little or no work experience detail")
        recommendations.append("Expand experience bullets with concrete responsibilities you already had")
    if title_score < 20:
        recommendations.append("Align headline/title wording with the target role when accurate")

    warnings = assess_resume_sufficiency(resume_data)
    if not jd_keywords:
        warnings.append("no_jd_keywords_extracted")
    if len((job_description or "").strip()) < 40:
        warnings.append("insufficient_job_description")

    return {
        "overall_score": overall,
        "breakdown": components,
        "matched_keywords": matched,
        "missing_keywords": missing,
        "jd_keywords": jd_keywords,
        "strengths": strengths,
        "gaps": gaps,
        "recommendations": recommendations,
        "warnings": warnings,
        "sections_present": sections,
        "job_title_hint": job_title,
        "enhancement_status": "skipped",
    }


async def _enhance_with_ollama(
    *,
    resume_data: dict[str, Any],
    job_description: str,
    deterministic: dict[str, Any],
    correlation_id: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Optional Ollama enrichment. Returns (payload, warnings)."""
    warnings: list[str] = []
    resume_summary = scrub_for_logs(_extract_all_text(resume_data), limit=600)
    job_summary = scrub_for_logs(job_description, limit=600)
    prompt = JD_ENHANCE_PROMPT.format(
        job_title=deterministic.get("job_title_hint") or "",
        matched=", ".join(deterministic.get("matched_keywords") or [])[:400],
        missing=", ".join(deterministic.get("missing_keywords") or [])[:400],
        resume_summary=resume_summary,
        job_summary=job_summary,
    )
    try:
        raw = await complete_json(
            prompt=prompt,
            system_prompt="You are a careful resume–JD analyst. Output valid JSON only.",
            max_tokens=1024,
        )
    except ProviderError as exc:
        logger.warning(
            "JD enhancement provider error correlation_id=%s error_code=%s",
            correlation_id,
            exc.error_code,
        )
        warnings.append(f"ollama_enhancement_{exc.error_code}")
        return None, warnings
    except Exception:
        logger.warning(
            "JD enhancement failed correlation_id=%s",
            correlation_id,
        )
        warnings.append("ollama_enhancement_failed")
        return None, warnings

    if not isinstance(raw, dict):
        warnings.append("ollama_enhancement_malformed")
        return None, warnings

    allowed_keys = (
        "strengths",
        "gaps",
        "recommendations",
        "missing_skills",
        "wording_improvements",
        "role_suggestions",
    )
    cleaned: dict[str, Any] = {}
    for key in allowed_keys:
        value = raw.get(key)
        if isinstance(value, list):
            cleaned[key] = [
                str(item).strip()
                for item in value
                if isinstance(item, (str, int, float)) and str(item).strip()
            ][:5]
        else:
            cleaned[key] = []
    return cleaned, warnings


def _merge_enhancement(
    deterministic: dict[str, Any],
    enhancement: dict[str, Any] | None,
) -> dict[str, Any]:
    result = dict(deterministic)
    if not enhancement:
        return result

    def _merge_list(base: list[str], extra: list[str]) -> list[str]:
        seen = {normalize_skill_token(x) for x in base}
        out = list(base)
        for item in extra:
            key = normalize_skill_token(item)
            if key and key not in seen:
                seen.add(key)
                out.append(item)
        return out[:8]

    result["strengths"] = _merge_list(
        list(result.get("strengths") or []), list(enhancement.get("strengths") or [])
    )
    result["gaps"] = _merge_list(
        list(result.get("gaps") or []), list(enhancement.get("gaps") or [])
    )
    result["recommendations"] = _merge_list(
        list(result.get("recommendations") or []),
        list(enhancement.get("recommendations") or [])
        + list(enhancement.get("wording_improvements") or [])
        + list(enhancement.get("role_suggestions") or []),
    )
    # Surface LLM missing skills into missing_keywords without inventing matches
    extra_missing = [
        s
        for s in enhancement.get("missing_skills") or []
        if normalize_skill_token(s)
        not in {normalize_skill_token(m) for m in result.get("matched_keywords") or []}
    ]
    result["missing_keywords"] = _merge_list(
        list(result.get("missing_keywords") or []), extra_missing
    )
    result["enhancement"] = enhancement
    result["enhancement_status"] = "applied"
    return result


async def analyze_jd_match(
    resume_data: dict[str, Any],
    job_description: str,
    *,
    use_ollama: bool = True,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Run deterministic JD analysis and optionally enhance via Ollama."""
    cid = correlation_id or new_correlation_id()
    meta = provider_metadata_safe()
    model = str(meta.get("model") or "unknown")
    r_hash = resume_payload_hash(resume_data)
    j_hash = content_hash(job_description or "")
    key = _cache_key(r_hash, j_hash, model)

    cached = _cache_get(key)
    if cached is not None:
        cached["cached"] = True
        cached["correlation_id"] = cid
        cached["provider"] = meta
        logger.info(
            "JD analysis cache hit correlation_id=%s resume_hash=%s jd_hash=%s",
            cid,
            r_hash[:12],
            j_hash[:12],
        )
        return cached

    logger.info(
        "JD analysis start correlation_id=%s resume_hash=%s jd_hash=%s",
        cid,
        r_hash[:12],
        j_hash[:12],
    )

    deterministic = analyze_deterministic(resume_data, job_description)
    warnings = list(deterministic.get("warnings") or [])
    enhancement = None
    enhancement_warnings: list[str] = []

    insufficient = bool(
        {"insufficient_resume_text", "insufficient_experience", "insufficient_skills"}
        & set(warnings)
    )

    if use_ollama and not insufficient:
        enhancement, enhancement_warnings = await _enhance_with_ollama(
            resume_data=resume_data,
            job_description=job_description,
            deterministic=deterministic,
            correlation_id=cid,
        )
        warnings.extend(enhancement_warnings)
        if enhancement is None and enhancement_warnings:
            deterministic["enhancement_status"] = "unavailable"
    elif use_ollama and insufficient:
        warnings.append("ollama_enhancement_skipped_insufficient_data")
        deterministic["enhancement_status"] = "skipped_insufficient_data"
    else:
        deterministic["enhancement_status"] = "disabled"

    result = _merge_enhancement(deterministic, enhancement)
    result["warnings"] = warnings
    result["cached"] = False
    result["correlation_id"] = cid
    result["provider"] = meta
    result["cache_key"] = {
        "resume_hash": r_hash,
        "jd_hash": j_hash,
        "model": model,
        "version": "v1",
    }

    _cache_put(key, result)
    return result
