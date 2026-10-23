"""CLI coverage for the human experience-review gate."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from vulnclaw.cli.main import app
from vulnclaw.kb.experience import (
    EvidenceRefs,
    ExperienceStore,
    Lesson,
    LessonScope,
    LessonSignal,
    LessonStatus,
    LessonTags,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def experience_store(monkeypatch, tmp_path) -> ExperienceStore:
    import vulnclaw.kb.experience as experience_module

    monkeypatch.setattr(experience_module, "KB_DIR", tmp_path)
    return ExperienceStore()


def _lesson(lesson_id: str, *, status: LessonStatus = LessonStatus.PENDING) -> Lesson:
    return Lesson(
        id=lesson_id,
        scope=LessonScope.TECHNIQUE,
        status=status,
        signal=LessonSignal.SUCCESS,
        tags=LessonTags(tech=["http"], vuln_type="xss", service="nginx"),
        context="Nginx application reflects a query parameter",
        lesson="Confirm reflection before attempting a context-appropriate payload.",
        evidence_refs=EvidenceRefs(run_id="run-123", finding_id="finding-456", path="/search?q=x"),
        confidence=0.8,
        source_runs=["run-123"],
    )


def test_experience_list_shows_only_pending_lessons(runner, experience_store):
    experience_store.add(_lesson("pending-one"))
    approved = experience_store.add(_lesson("approved-one"))
    experience_store.approve(approved.id)

    result = runner.invoke(app, ["experience", "list"])

    assert result.exit_code == 0
    assert "pending-one" in result.output
    assert "approved-one" not in result.output


def test_experience_review_alias_lists_pending_lessons(runner, experience_store):
    experience_store.add(_lesson("pending-one"))

    result = runner.invoke(app, ["experience", "review"])

    assert result.exit_code == 0
    assert "pending-one" in result.output


def test_experience_show_includes_provenance(runner, experience_store):
    experience_store.add(_lesson("detail-one"))

    result = runner.invoke(app, ["experience", "show", "detail-one"])

    assert result.exit_code == 0
    assert "run-123" in result.output
    assert "finding-456" in result.output
    assert "/search?q=x" in result.output
    assert "Confirm reflection" in result.output


@pytest.mark.parametrize(("command", "expected"), [("approve", LessonStatus.APPROVED), ("reject", LessonStatus.REJECTED)])
def test_experience_review_decisions_are_durable(runner, experience_store, command, expected):
    experience_store.add(_lesson("review-one"))

    result = runner.invoke(app, ["experience", command, "review-one"])

    assert result.exit_code == 0
    assert experience_store.get("review-one").status is expected
    reloaded = ExperienceStore(store_dir=experience_store.store_dir)
    assert reloaded.get("review-one").status is expected


def test_experience_edit_persists_amended_text(runner, experience_store):
    experience_store.add(_lesson("edit-one"))

    result = runner.invoke(
        app,
        [
            "experience",
            "edit",
            "edit-one",
            "--context",
            "Updated matching condition",
            "--lesson",
            "Updated operator guidance",
        ],
    )

    assert result.exit_code == 0
    edited = experience_store.get("edit-one")
    assert edited.context == "Updated matching condition"
    assert edited.lesson == "Updated operator guidance"
    assert edited.status is LessonStatus.PENDING
    assert edited.evidence_refs.run_id == "run-123"


@pytest.mark.parametrize("command", ["show", "approve", "reject", "edit"])
def test_experience_unknown_id_fails_cleanly(runner, experience_store, command):
    args = ["experience", command, "missing-id"]
    if command == "edit":
        args.extend(["--lesson", "Replacement text"])

    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert "Lesson not found: missing-id" in result.output



@pytest.mark.parametrize("text,expected", [
    ("approve shared", LessonStatus.APPROVED),
    ('edit shared --context "new ctx" --lesson "new operator guidance"', LessonStatus.PENDING),
])
async def test_terminal_adapter_uses_the_same_durable_store(experience_store, text, expected):
    from types import SimpleNamespace

    from vulnclaw.config.schema import VulnClawConfig
    from vulnclaw.management_service import adapter_operation, execute_management

    experience_store.add(_lesson("shared"))
    operation, arguments = adapter_operation("experience", text)
    result = await execute_management(SimpleNamespace(config=VulnClawConfig()), operation, arguments)
    item = experience_store.get("shared")
    assert item.status is expected
    assert result["lesson"]["id"] == "shared"
    if text.startswith("edit"):
        assert (item.context, item.lesson) == ("new ctx", "new operator guidance")


async def test_terminal_adapter_reports_missing_lesson_without_exiting(experience_store):
    from types import SimpleNamespace

    from vulnclaw.config.schema import VulnClawConfig
    from vulnclaw.management_service import execute_management

    with pytest.raises(ValueError, match="Lesson not found"):
        await execute_management(SimpleNamespace(config=VulnClawConfig()), "experience.show", {"lesson_id": "missing"})


@pytest.mark.parametrize("status,expected", [
    ("pending", ["pending-one"]), ("approved", ["approved-one"]),
    ("rejected", ["rejected-one"]), ("all", ["pending-one", "approved-one", "rejected-one"]),
])
async def test_terminal_experience_status_filters_share_the_durable_store(
    experience_store, status, expected,
):
    from types import SimpleNamespace

    from vulnclaw.config.schema import VulnClawConfig
    from vulnclaw.management_service import adapter_operation, execute_management

    for identifier in ["pending-one", "approved-one", "rejected-one"]:
        experience_store.add(_lesson(identifier))
    experience_store.approve("approved-one")
    experience_store.reject("rejected-one")
    operation, args = adapter_operation("experience", f"list --status {status}")
    result = await execute_management(SimpleNamespace(config=VulnClawConfig()), operation, args)
    assert result["status"] == status
    assert [item["id"] for item in result["lessons"]] == expected
    assert all(item["evidence_refs"]["run_id"] == "run-123" for item in result["lessons"])


@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "all"])
async def test_empty_experience_views_have_a_structured_empty_list(experience_store, status):
    from types import SimpleNamespace

    from vulnclaw.management_service import execute_management

    result = await execute_management(SimpleNamespace(config=None), "experience.list", {"status": status})
    assert result["status"] == status
    assert result["lessons"] == []


@pytest.mark.parametrize("status", ["unknown", "", None, [], {}])
async def test_experience_list_rejects_invalid_status_without_changing_lessons(experience_store, status):
    from types import SimpleNamespace

    from vulnclaw.management_service import execute_management

    original = experience_store.add(_lesson("unchanged"))
    with pytest.raises(ValueError, match="status must be"):
        await execute_management(SimpleNamespace(config=None), "experience.list", {"status": status})
    assert experience_store.get(original.id) == original


async def test_editing_an_approved_lesson_preserves_review_status_and_provenance(experience_store):
    from types import SimpleNamespace

    from vulnclaw.management_service import execute_management

    experience_store.add(_lesson("approved-edit"))
    original = experience_store.approve("approved-edit")
    result = await execute_management(SimpleNamespace(config=None), "experience.edit", {
        "lesson_id": "approved-edit", "context": "中文上下文\nsecond line", "lesson": "## 中文经验\n\n  keep spaces",
    })
    assert result["lesson"]["status"] == "approved"
    saved = experience_store.get("approved-edit")
    assert saved.evidence_refs == original.evidence_refs
    assert saved.tags == original.tags
    assert saved.source_runs == original.source_runs
    assert saved.context == "中文上下文\nsecond line"
    assert saved.lesson == "## 中文经验\n\n  keep spaces"
    with pytest.raises(ValueError, match="Invalid lesson update"):
        await execute_management(SimpleNamespace(config=None), "experience.edit", {"lesson_id": saved.id, "lesson": ""})
    assert experience_store.get(saved.id) == saved
