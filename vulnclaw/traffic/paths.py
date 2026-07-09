"""Resolve where the traffic evidence store lives.

Inside the per-run Docker sandbox the store lives at ``<run>/evidence/traffic/``.
Until the run-directory PRD lands, resolution falls back to a config-scoped
evidence directory (overridable via ``VULNCLAW_EVIDENCE_DIR``), so headless/CI
runs still get a durable, addressable store.
"""

from __future__ import annotations

import os
from pathlib import Path

TRAFFIC_SUBDIR = "traffic"


def evidence_root() -> Path:
    override = os.environ.get("VULNCLAW_EVIDENCE_DIR")
    if override:
        return Path(override)
    from vulnclaw.config.settings import CONFIG_DIR

    return CONFIG_DIR / "evidence"


def traffic_dir(base: str | Path | None = None) -> Path:
    """Return the ``evidence/traffic`` directory for ``base`` (or the default)."""
    if base is not None:
        root = Path(base)
        # Accept either a run/evidence root or a direct traffic dir.
        if root.name == TRAFFIC_SUBDIR:
            return root
        if root.name == "evidence":
            return root / TRAFFIC_SUBDIR
        return root / "evidence" / TRAFFIC_SUBDIR
    return evidence_root() / TRAFFIC_SUBDIR
