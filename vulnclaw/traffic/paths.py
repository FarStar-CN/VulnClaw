"""Resolve where the traffic evidence store lives.

Inside the per-run Docker sandbox the store lives at ``<run>/evidence/traffic/``.
Until the run-directory PRD lands, resolution falls back to a config-scoped
evidence directory (overridable via ``VULNCLAW_EVIDENCE_DIR``), so headless/CI
runs still get a durable, addressable store.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vulnclaw.traffic.store import TrafficStore

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


def resolve_traffic_store(run_dir: str | Path | None = None) -> "TrafficStore":
    """Resolve the traffic store both writers and the report reader share.

    Prefers ``run_dir``'s ``evidence/traffic`` when it already holds captures;
    otherwise falls back to the config-scoped default. This single seam keeps the
    agent's writes and the report generator's reads pointed at the same store
    until the run-directory PRD provides an explicit per-run path.
    """
    from vulnclaw.traffic.store import INDEX_FILENAME, TrafficStore

    candidates: list[Path] = []
    if run_dir is not None:
        candidates.append(traffic_dir(run_dir))
    candidates.append(traffic_dir(None))  # config-scoped default

    for path in candidates:
        if (path / INDEX_FILENAME).exists():
            return TrafficStore(path)
    # Nothing captured anywhere yet: honor the caller's run dir, else default.
    return TrafficStore(candidates[0])
