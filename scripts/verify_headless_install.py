"""Install a wheel in isolation and exercise headless commands with no terminal runtime."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import venv
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="vulnclaw-wheel-") as directory:
        root = Path(directory)
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        subprocess.run([str(python), "-m", "pip", "install", str(args.wheel.resolve())], check=True)
        env = {**os.environ, "PATH": str(scripts), "VULNCLAW_CONFIG_DIR": str(root / "config"), "VULNCLAW_HOME": str(root / "config")}
        env.pop("PYTHONPATH", None)
        source = root / "synthetic.py"
        source.write_text('eval(input("static fixture"))\n', encoding="utf-8")
        for command in (["--help"], ["--version"], ["config", "list"], ["code", "scan", str(source), "--layer", "L1,L2", "--output-format", "json"]):
            result = subprocess.run([str(python), "-m", "vulnclaw", *command], cwd=root, env=env, capture_output=True, text=True, check=True)
            if command[0] not in {"--help", "--version"}:
                assert json.loads(result.stdout)["status"] == "completed", result.stdout
        print("[headless-install] wheel works without Bun/OpenTUI on PATH")


if __name__ == "__main__":
    main()
