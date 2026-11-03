from __future__ import annotations

import json
import re
import shutil
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from vulnclaw.agent.context import SessionState
from vulnclaw.run_context import (
    RUN_SCHEMA_VERSION,
    RunCollisionError,
    RunMigrationError,
    RunNotFoundError,
    create_run_context,
    ensure_writable_run_layout,
    load_run_context,
    resolve_runs_root,
    target_group_dir,
)
from vulnclaw.target_state.store import save_target_state
from vulnclaw.targets import parse_target

TIME_DIR = re.compile(r"^(\d{8}T\d{6}Z)-([0-9a-f]{8})$")


def _run(root: Path, target: str = "https://example.com", **kwargs):
    return create_run_context(
        command="run",
        targets=[parse_target(target)],
        runs_dir=root,
        **kwargs,
    )


def test_new_run_is_grouped_by_target_and_creation_time(tmp_path):
    context = _run(tmp_path / "runs", run_name="demo-run")
    target = parse_target("https://example.com")

    assert context.run_dir.parent.parent == (tmp_path / "runs").resolve()
    assert context.run_dir.parent.name == f"{target_dir_slug(target)}-{target.target_id}"
    match = TIME_DIR.fullmatch(context.run_dir.name)
    assert match is not None
    assert match.group(2) == context.run_id.replace("-", "")[:8]
    assert context.manifest["schema_version"] == RUN_SCHEMA_VERSION
    assert context.run_name == "demo-run"
    assert context.run_dir.name != context.run_name


def target_dir_slug(target):
    from vulnclaw.run_context import target_dir_slug as slug

    return slug(target)


def test_target_group_name_is_stable_across_target_kinds_and_labels(tmp_path):
    for raw in (
        "https://example.com/path",
        "/tmp/local-repo",
        "[::1]:8080",
        "http://[2001:db8::1]:80/",
    ):
        target = parse_target(raw)
        first = target_group_dir(tmp_path / "runs", target)
        relabeled = replace(target, label="renamed display")
        assert target_group_dir(tmp_path / "runs", relabeled) == first
        assert first.name == f"{target_dir_slug(target)}-{target.target_id}"
        assert "/" not in first.name and "\\" not in first.name


def test_two_new_tasks_for_one_target_get_distinct_time_dirs(tmp_path):
    first = _run(tmp_path / "runs", "https://example.com/a")
    second = _run(tmp_path / "runs", "https://example.com/a")

    assert first.run_dir.parent == second.run_dir.parent
    assert first.run_dir != second.run_dir
    assert first.run_id != second.run_id


def test_explicit_name_is_unique_across_targets(tmp_path):
    _run(tmp_path / "runs", "https://a.example", run_name="shared-name")
    with pytest.raises(RunCollisionError):
        _run(tmp_path / "runs", "https://b.example", run_name="shared-name")


def test_name_resume_between_different_target_groups(tmp_path):
    runs = tmp_path / "runs"
    created = _run(runs, "https://a.example", run_name="named-run")
    save_target_state(
        "https://a.example", SessionState(target="https://a.example"),
        command="run", run_context=created, target_model=created.targets[0],
        checkpoint_reason="test",
    )
    loaded = load_run_context("named-run", runs_dir=runs)
    assert loaded.run_dir == created.run_dir
    assert loaded.run_id == created.run_id
    assert loaded.created_at == created.created_at


def test_missing_optional_directories_do_not_corrupt_the_run(tmp_path):
    context = _run(tmp_path / "runs", run_name="minimal-run")
    save_target_state(
        "https://example.com", SessionState(target="https://example.com"),
        command="run", run_context=context, target_model=context.targets[0],
        checkpoint_reason="test",
    )
    for relative in ("logs", "reports", "temp"):
        shutil.rmtree(context.run_dir / relative, ignore_errors=True)

    loaded = load_run_context("minimal-run", runs_dir=tmp_path / "runs")
    assert loaded.run_dir == context.run_dir


def test_concurrent_creation_never_reuses_a_directory(tmp_path):
    runs = tmp_path / "runs"
    created = []
    errors = []

    def worker(index):
        try:
            created.append(_run(runs, f"https://host{index}.example"))
        except Exception as exc:  # pragma: no cover - failure reporting only
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len({item.run_dir for item in created}) == 8
    assert len({item.run_id for item in created}) == 8


def _make_legacy_flat_run(tmp_path, *, name="legacy-flat"):
    runs = tmp_path / "runs"
    context = _run(runs, run_name=name)
    save_target_state(
        "https://example.com", SessionState(target="https://example.com"),
        command="run", run_context=context, target_model=context.targets[0],
        checkpoint_reason="test",
    )
    flat = runs / name
    shutil.move(str(context.run_dir), flat)
    shutil.rmtree(context.run_dir.parent, ignore_errors=True)
    manifest = json.loads((flat / "run.json").read_text(encoding="utf-8"))
    manifest["schema_version"] = 1
    manifest["run_name"] = name
    (flat / "run.json").write_text(json.dumps(manifest), encoding="utf-8")
    return flat, manifest


def test_read_only_load_of_a_flat_run_does_not_move_it(tmp_path):
    flat, manifest = _make_legacy_flat_run(tmp_path)

    loaded = load_run_context("legacy-flat", runs_dir=tmp_path / "runs")

    assert loaded.run_dir == flat
    assert loaded.legacy_layout is True
    assert flat.exists()
    assert not (tmp_path / "runs" / f"example.com-{manifest['targets'][0]['target_id']}").exists()


def test_writable_resume_moves_a_flat_run_and_preserves_identity(tmp_path):
    flat, manifest = _make_legacy_flat_run(tmp_path)
    loaded = load_run_context("legacy-flat", runs_dir=tmp_path / "runs")

    moved = ensure_writable_run_layout(loaded)

    assert not flat.exists()
    assert moved.run_dir.exists()
    assert moved.run_dir.parent.name.startswith("example.com-")
    assert moved.run_id == manifest["run_id"]
    assert moved.created_at == manifest["created_at"]
    assert moved.manifest["schema_version"] == RUN_SCHEMA_VERSION
    assert moved.run_name == "legacy-flat"
    assert moved.manifest["targets"][0]["state_path"] == manifest["targets"][0]["state_path"]
    assert json.loads((moved.run_dir / "evidence" / "imports" / "layout-migration.json").read_text())[
        "run_id"
    ] == manifest["run_id"]
    assert load_run_context("legacy-flat", runs_dir=tmp_path / "runs").run_dir == moved.run_dir


def test_migration_refuses_to_merge_into_an_existing_destination(tmp_path):
    flat, manifest = _make_legacy_flat_run(tmp_path)
    loaded = load_run_context("legacy-flat", runs_dir=tmp_path / "runs")
    target = parse_target("https://example.com")
    # Build the exact expected destination name from the manifest timestamp/run id.
    from datetime import datetime

    created = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
    expected = target_group_dir(tmp_path / "runs", target) / (
        f"{created.strftime('%Y%m%dT%H%M%SZ')}-{manifest['run_id'].replace('-', '')[:8]}"
    )
    expected.mkdir(parents=True)
    (expected / "run.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RunMigrationError):
        ensure_writable_run_layout(loaded)
    assert flat.exists()
    assert expected.exists()


def test_interrupted_move_is_finished_from_the_new_location(tmp_path):
    runs = tmp_path / "runs"
    flat, manifest = _make_legacy_flat_run(tmp_path)
    target = parse_target("https://example.com")
    from datetime import datetime

    created = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
    destination = target_group_dir(runs, target) / (
        f"{created.strftime('%Y%m%dT%H%M%SZ')}-{manifest['run_id'].replace('-', '')[:8]}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    (flat / "evidence" / "imports" / "layout-migration.json").write_text(
        json.dumps({
            "schema_version": 1,
            "run_id": manifest["run_id"],
            "old_run_dir": str(flat.resolve()),
            "old_run_name": "legacy-flat",
        }),
        encoding="utf-8",
    )
    # Crash window: the directory moved but the manifest/navigation was not updated.
    flat.rename(destination)
    assert not flat.exists()

    loaded = load_run_context("legacy-flat", runs_dir=runs)
    assert loaded.legacy_layout is False
    assert loaded.manifest["schema_version"] == 1

    fixed = ensure_writable_run_layout(loaded)

    assert fixed.run_dir == destination
    assert fixed.manifest["schema_version"] == RUN_SCHEMA_VERSION
    assert fixed.run_id == manifest["run_id"]
    assert not (runs / "legacy-flat").exists()
    assert load_run_context("legacy-flat", runs_dir=runs).run_id == manifest["run_id"]


def test_run_name_lookup_does_not_use_the_directory_basename(tmp_path):
    context = _run(tmp_path / "runs", run_name="display-name")
    save_target_state(
        "https://example.com", SessionState(target="https://example.com"),
        command="run", run_context=context, target_model=context.targets[0],
        checkpoint_reason="test",
    )
    with pytest.raises(RunNotFoundError):
        load_run_context(context.run_dir.name, runs_dir=tmp_path / "runs")


def test_resume_command_quotes_a_custom_runs_root_with_spaces(tmp_path):
    from types import SimpleNamespace

    from vulnclaw.run_context import build_completion_summary

    runs = tmp_path / "runs with spaces"
    context = _run(runs, run_name="quoted-run")
    summary = build_completion_summary(
        context=context,
        session=SimpleNamespace(target="https://example.com", findings=[]),
        command="run",
        restored=False,
    )
    assert context.runs_root == runs.resolve()
    assert summary["run_name"] == "quoted-run"
    assert "--runs-dir" in summary["resume_command"]
    assert "'" in summary["resume_command"] or "\\ " in summary["resume_command"]


def test_relative_runs_dir_is_resolved_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = resolve_runs_root("relative-runs")
    assert resolved == (tmp_path / "relative-runs").resolve()
