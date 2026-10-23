from __future__ import annotations

import json
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
PYPROJECT = ROOT / "pyproject.toml"


def read_project_version() -> str:
    version_line = next(
        line for line in PYPROJECT.read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )
    return version_line.split('"')[1]


def verify_dist_artifacts(dist_dir: Path = DIST_DIR, version: str | None = None) -> list[Path]:
    resolved_version = version or read_project_version()
    if not dist_dir.exists():
        raise FileNotFoundError(f"dist directory not found: {dist_dir}")

    wheel_matches = list(dist_dir.glob(f"*{resolved_version}*.whl"))
    sdist_matches = list(dist_dir.glob(f"*{resolved_version}*.tar.gz"))

    if not wheel_matches:
        raise FileNotFoundError(f"wheel artifact for version {resolved_version} not found in {dist_dir}")
    if not sdist_matches:
        raise FileNotFoundError(f"sdist artifact for version {resolved_version} not found in {dist_dir}")

    artifacts = wheel_matches + sdist_matches
    empty = [path for path in artifacts if path.stat().st_size <= 0]
    if empty:
        names = ", ".join(path.name for path in empty)
        raise ValueError(f"empty dist artifacts detected: {names}")

    for wheel in wheel_matches:
        with zipfile.ZipFile(wheel) as archive:
            schema = json.loads(archive.read("vulnclaw/protocol/session-v2.schema.json"))
            if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
                raise ValueError(f"Unexpected session schema in {wheel.name}")
            metadata = archive.read(next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))).decode()
            if any("opentui" in line.lower() or "bun" in line.lower() for line in metadata.splitlines() if line.startswith("Requires-Dist:")):
                raise ValueError("The Python wheel must install independently of the terminal runtime")
    for sdist in sdist_matches:
        with tarfile.open(sdist) as archive:
            names = archive.getnames()
            for resource in ("protocol/session-v2.schema.json", "tui/package.json", "tui/bun.lock", "tui/src/main.tsx"):
                if not any(name.endswith("/" + resource) for name in names):
                    raise ValueError(f"Missing {resource} in {sdist.name}")
            if any("/node_modules/" in name or "/tui/dist/" in name for name in names):
                raise ValueError(f"Dependency/build directories leaked into {sdist.name}")
    return artifacts


def main() -> int:
    artifacts = verify_dist_artifacts()
    print("[verify-dist] artifacts:")
    for path in artifacts:
        print(f"  - {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
