from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
TERMINAL = ROOT / "tui"


def run_step(name: str, command: list[str], cwd: Path | None = None) -> None:
    print(f"[preflight] {name}: {' '.join(command)}")
    subprocess.run(command, cwd=str(cwd or ROOT), env={**os.environ, "VULNCLAW_PYTHON": sys.executable}, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run VulnClaw release preflight checks.")
    parser.add_argument("--build", action="store_true", help="Also build dist artifacts after tests and typechecks")
    args = parser.parse_args()

    run_step("version-check", [sys.executable, "-m", "pytest", "-q", "tests/meta/test_release.py"])
    run_step("backend-tests", [sys.executable, "-m", "pytest", "-q"])
    npm_cmd = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm_cmd:
        raise FileNotFoundError("npm was not found in PATH")
    run_step("frontend-types", [npm_cmd, "exec", "--", "tsc", "-b"], cwd=FRONTEND)
    bun_cmd = shutil.which("bun")
    if not bun_cmd:
        raise FileNotFoundError("Bun 1.3.14 was not found in PATH")
    run_step("terminal-protocol", [bun_cmd, "run", "generate", "--check"], cwd=TERMINAL)
    run_step("terminal-types", [bun_cmd, "run", "typecheck"], cwd=TERMINAL)
    run_step("terminal-tests", [bun_cmd, "test"], cwd=TERMINAL)
    if args.build:
        run_step("frontend-build", [npm_cmd, "run", "build"], cwd=FRONTEND)
        run_step("terminal-build", [bun_cmd, "run", "build"], cwd=TERMINAL)
        run_step("build-package", [sys.executable, "-m", "build"])
        run_step("verify-dist", [sys.executable, "scripts/verify_dist_artifacts.py"])
        from verify_dist_artifacts import read_project_version

        wheel = next((ROOT / "dist").glob(f"vulnclaw-{read_project_version()}-*.whl"))
        run_step("headless-install", [sys.executable, "scripts/verify_headless_install.py", str(wheel)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
