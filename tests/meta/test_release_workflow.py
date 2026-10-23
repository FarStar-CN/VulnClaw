from __future__ import annotations

import io
import json
import tarfile
import zipfile


def test_verify_dist_artifacts_accepts_matching_version(tmp_path) -> None:
    from scripts.verify_dist_artifacts import verify_dist_artifacts

    wheel = tmp_path / "vulnclaw-0.4.0-py3-none-any.whl"
    sdist = tmp_path / "vulnclaw-0.4.0.tar.gz"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("vulnclaw/protocol/session-v2.schema.json", json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema"}))
        archive.writestr("vulnclaw-0.4.0.dist-info/METADATA", "Name: vulnclaw\nVersion: 0.4.0\n")
    with tarfile.open(sdist, "w:gz") as archive:
        for name in ("protocol/session-v2.schema.json", "tui/package.json", "tui/bun.lock", "tui/src/main.tsx"):
            entry = tarfile.TarInfo("vulnclaw-0.4.0/" + name)
            data = b"fixture"
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))

    artifacts = verify_dist_artifacts(tmp_path, version="0.4.0")
    names = {path.name for path in artifacts}
    assert wheel.name in names
    assert sdist.name in names


def test_verify_dist_artifacts_rejects_missing_files(tmp_path) -> None:
    from scripts.verify_dist_artifacts import verify_dist_artifacts

    (tmp_path / "vulnclaw-0.4.0-py3-none-any.whl").write_bytes(b"wheel")

    try:
        verify_dist_artifacts(tmp_path, version="0.4.0")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError for missing sdist artifact")


def test_verify_dist_artifacts_rejects_empty_files(tmp_path) -> None:
    from scripts.verify_dist_artifacts import verify_dist_artifacts

    (tmp_path / "vulnclaw-0.4.0-py3-none-any.whl").write_bytes(b"")
    (tmp_path / "vulnclaw-0.4.0.tar.gz").write_bytes(b"sdist")

    try:
        verify_dist_artifacts(tmp_path, version="0.4.0")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty dist artifact")
