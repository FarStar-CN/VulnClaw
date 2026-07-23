"""Language-aware display helpers for canonical pentest phases."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Any

from vulnclaw.i18n import I18nLoader, _

PHASE_IDS = frozenset(
    {
        "idle",
        "recon",
        "vuln_discovery",
        "exploitation",
        "post_exploitation",
        "reporting",
    }
)

# Compatibility bridge for sessions created before phase identity became
# language-neutral. New call sites should pass a canonical ID or PentestPhase.
_LEGACY_PHASE_IDS = {
    "就绪": "idle",
    "信息收集": "recon",
    "漏洞发现": "vuln_discovery",
    "漏洞利用": "exploitation",
    "后渗透": "post_exploitation",
    "报告生成": "reporting",
}


def canonical_phase_id(phase: Any) -> str | None:
    """Return the language-neutral ID for a phase-like value, if known."""
    if phase is None:
        return None

    explicit_id = getattr(phase, "id", None)
    if explicit_id is not None:
        normalized = _normalize_token(explicit_id)
        if normalized in PHASE_IDS:
            return normalized

    if isinstance(phase, Enum):
        member_name = phase.name.lower()
        if member_name in PHASE_IDS:
            return member_name
        phase = phase.value

    normalized = _normalize_token(phase)
    if normalized in PHASE_IDS:
        return normalized
    return _LEGACY_PHASE_IDS.get(str(phase).strip())


def localized_phase_name(phase: Any, *, lang: str | None = None) -> str:
    """Resolve a canonical phase ID to its display name in the active language."""
    phase_id = canonical_phase_id(phase)
    if phase_id is not None:
        return _translate(f"phase.{phase_id}", lang=lang)
    if isinstance(phase, Enum):
        return str(phase.value)
    return "" if phase is None else str(phase)


def localized_prompt_phase_heading(phase: Any, *, lang: str | None = None) -> str:
    """Render the prompt heading for a known phase in the active language."""
    phase_id = canonical_phase_id(phase)
    if phase_id is None:
        return ""
    return _translate(
        "prompt.phase_heading",
        lang=lang,
        phase=localized_phase_name(phase_id, lang=lang),
    )


def localized_report_phase_heading(phase: Any, count: int) -> str:
    """Render a report subsection heading for a known or legacy phase value."""
    return _(
        "report.phase_heading_one" if count == 1 else "report.phase_heading_many",
        phase=localized_phase_name(phase),
        count=count,
    )


def _normalize_token(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _translate(key: str, *, lang: str | None = None, **kwargs: Any) -> str:
    if lang is None:
        return _(key, **kwargs)
    return _loader(lang).t(key, **kwargs)


@lru_cache(maxsize=2)
def _loader(lang: str) -> I18nLoader:
    return I18nLoader(lang)
