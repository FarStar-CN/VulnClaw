from __future__ import annotations

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

    return artifacts


def main() -> int:
    artifacts = verify_dist_artifacts()
    print("[verify-dist] artifacts:")
    for path in artifacts:
        print(f"  - {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
