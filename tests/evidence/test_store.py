from __future__ import annotations

import errno
import json

import pytest

from vulnclaw.evidence import (
    CapturedObservation,
    EvidenceArgumentError,
    EvidenceFilters,
    EvidenceIdConflictError,
    EvidenceScope,
    EvidenceScopeError,
    EvidenceStore,
    EvidenceUnavailableError,
    PersistenceErrorLimitExceeded,
    new_evidence_id,
)
from vulnclaw.evidence.models import (
    ObservationExecution,
    ObservationProducer,
    ObservationTool,
)
from vulnclaw.run_context import create_run_context
from vulnclaw.targets import parse_target

SCOPE = None


@pytest.fixture
def run_store(tmp_path):
    context = create_run_context(
        command="test",
        targets=[parse_target("https://example.test")],
        runs_dir=tmp_path / "runs",
        run_name="evidence-test",
    )
    store = EvidenceStore(context.run_dir, run_id=context.run_id, error_limit=5)
    scope = EvidenceScope(run_id=context.run_id, task_id="main", read_all=True)
    yield context, store, scope
    store.close()


def capture(store, text: str, *, tool: str = "fetch", target: str = "https://example.test"):
    evidence_id = new_evidence_id()
    return evidence_id, CapturedObservation(
        evidence_id=evidence_id,
        run_id=store.run_id,
        target=target,
        resource="https://example.test/",
        producer=ObservationProducer(task_id="main"),
        tool=ObservationTool(name=tool, arguments={"url": "https://example.test/"}),
        execution=ObservationExecution(outcome="succeeded", http_status=200),
        text=text,
        summary=f"{tool} succeeded",
    )


def test_commit_preserves_exact_text_and_character_byte_sizes(run_store):
    _context, store, scope = run_store
    text = "第一行\r\nemoji 😀\nlast"
    evidence_id, captured = capture(store, text)

    outcome = store.commit_observation(captured, scope=scope)

    assert outcome.durability == "committed"
    assert outcome.descriptor.size_chars == len(text)
    assert outcome.descriptor.size_bytes == len(text.encode("utf-8"))
    view = store.view(evidence_id, offset=0, limit=4, scope=scope)
    assert view.text == text[:4]
    assert view.end == 4
    assert view.next_offset == 4
    assert view.total_chars == len(text)
    tail = store.view(evidence_id, offset=view.end, limit=100, scope=scope)
    assert tail.text == text[4:]
    assert tail.next_offset is None
    eof = store.view(evidence_id, offset=len(text), scope=scope)
    assert eof.eof and eof.text == ""


def test_view_uses_code_points_not_bytes(run_store):
    _context, store, scope = run_store
    text = "a😀b"
    evidence_id, captured = capture(store, text)
    store.commit_observation(captured, scope=scope)

    assert store.view(evidence_id, offset=1, limit=1, scope=scope).text == "😀"
    assert store.view(evidence_id, offset=2, scope=scope).text == "b"


def test_same_body_from_two_executions_creates_two_observations(run_store):
    _context, store, scope = run_store
    first_id, first = capture(store, "identical")
    second_id, second = capture(store, "identical")

    store.commit_observation(first, scope=scope)
    store.commit_observation(second, scope=scope)

    assert first_id != second_id
    records = list((store.run_dir / "evidence" / "records").glob("*.json"))
    payloads = list((store.run_dir / "evidence" / "payloads").glob("*.txt"))
    assert len(records) == 2
    assert len(payloads) == 2


def test_same_id_retry_is_idempotent_and_content_conflict_is_explicit(run_store):
    _context, store, scope = run_store
    evidence_id, captured = capture(store, "first")
    store.commit_observation(captured, scope=scope)
    seq = store.max_commit_seq

    again = store.commit_observation(captured, scope=scope)
    assert again.evidence_id == evidence_id
    assert store.max_commit_seq == seq

    conflicting = CapturedObservation(
        evidence_id=evidence_id,
        run_id=store.run_id,
        tool=ObservationTool(name="fetch"),
        execution=ObservationExecution(outcome="succeeded"),
        text="different",
    )
    with pytest.raises(EvidenceIdConflictError):
        store.commit_observation(conflicting, scope=scope)
    assert store.view(evidence_id, scope=scope).text == "first"


def test_view_argument_errors_are_not_unlimited_reads(run_store):
    _context, store, scope = run_store
    evidence_id, captured = capture(store, "abc")
    store.commit_observation(captured, scope=scope)

    with pytest.raises(EvidenceArgumentError):
        store.view(evidence_id, limit=0, scope=scope)
    with pytest.raises(EvidenceArgumentError):
        store.view(evidence_id, offset=-1, scope=scope)
    with pytest.raises(EvidenceArgumentError):
        store.view(evidence_id, offset=4, scope=scope)


def test_missing_payload_is_unavailable_not_empty(run_store):
    _context, store, scope = run_store
    evidence_id, captured = capture(store, "payload")
    store.commit_observation(captured, scope=scope)
    record = store.run_dir / "evidence" / "records" / f"{evidence_id}.json"
    payload_path = json.loads(record.read_text())["payload"]["path"]
    (store.run_dir / payload_path).unlink()

    descriptor = store.describe(evidence_id, scope=scope)
    assert descriptor.ledger_state == "unavailable"
    with pytest.raises(EvidenceUnavailableError):
        store.view(evidence_id, scope=scope)


def test_rebuild_index_recovers_catalog_after_index_deletion(run_store):
    _context, store, scope = run_store
    ids = []
    for index in range(3):
        evidence_id, captured = capture(store, f"body-{index}")
        store.commit_observation(captured, scope=scope)
        ids.append(evidence_id)
    assert store.flush_index() is True
    store.index_path.unlink()

    result = store.rebuild_index()

    assert result.total_records == 3
    assert result.available == 3
    assert result.index_written is True
    payload = json.loads(store.index_path.read_text())
    assert {item["id"] for item in payload["records"]} == set(ids)


def test_list_filters_and_cursor_are_stable(run_store):
    _context, store, scope = run_store
    for index in range(5):
        evidence_id, captured = capture(store, f"body-{index}", tool="fetch" if index % 2 else "shell")
        store.commit_observation(captured, scope=scope)

    first = store.list_records(limit=2, scope=scope)
    assert first.returned_count == 2
    assert first.next_cursor
    second = store.list_records(cursor=first.next_cursor, limit=2, scope=scope)
    assert {item.id for item in first.items}.isdisjoint({item.id for item in second.items})
    only_shell = store.list_records(
        EvidenceFilters(tool="shell"), limit=10, scope=scope
    )
    assert only_shell.candidate_total == 3
    assert all(item.tool == "shell" for item in only_shell.items)


def test_search_reports_real_offsets_and_pending_durability(run_store):
    _context, store, scope = run_store
    evidence_id, captured = capture(store, "prefix hello world suffix")
    store.commit_observation(captured, scope=scope)

    result = store.search("hello", limit=1, context_chars=3, scope=scope)
    assert result.scan_complete is True
    hit = result.hits[0]
    assert hit.evidence_id == evidence_id
    assert hit.match_start == 7
    assert hit.match_end == 12
    assert hit.text == "prefix hello world suffix"[4:15]
    assert hit.durability == "committed"


def test_search_budget_marks_unsearched_scope(run_store):
    _context, store, scope = run_store
    for index in range(4):
        evidence_id, captured = capture(store, f"needle-{index}-" + "x" * 100)
        store.commit_observation(captured, scope=scope)

    result = store.search("needle", scope=scope, scan_budget=150, limit=50)
    assert result.scan_complete is False
    assert result.unsearched_ids
    assert result.next_cursor


def test_scope_isolates_sibling_tasks(run_store):
    context, store, scope = run_store
    evidence_id, captured = capture(store, "parent-only")
    store.commit_observation(captured, scope=scope)
    sibling = EvidenceScope(run_id=context.run_id, task_id="a-0002")

    with pytest.raises(EvidenceScopeError):
        store.view(evidence_id, scope=sibling)
    assert store.list_records(scope=sibling).returned_count == 0

    inherited = EvidenceScope(
        run_id=context.run_id,
        task_id="a-0002",
        inherited_evidence_ids=frozenset({evidence_id}),
    )
    assert store.view(evidence_id, scope=inherited).text == "parent-only"


def test_pending_save_and_flush_recover_the_same_id_and_acquisition(run_store, monkeypatch):
    _context, store, scope = run_store
    evidence_id, captured = capture(store, "retry-me")

    def fail(path, data):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(store, "_write_payload", fail)
    outcome = store.commit_observation(captured, scope=scope)
    assert outcome.durability == "pending"
    assert store.pending_count == 1
    assert store.describe(evidence_id, scope=scope).ledger_state == "pending"
    assert store.view(evidence_id, scope=scope).durability == "pending"
    pending_hits = store.search("retry-me", scope=scope)
    assert pending_hits.hits[0].durability == "pending"

    monkeypatch.undo()
    flushed = store.flush_pending(boundary_id="test_boundary")
    assert flushed.committed_ids == [evidence_id]
    assert store.pending_count == 0
    assert store.view(evidence_id, scope=scope).durability == "committed"
    assert store.health.failure_total == 1


def test_sixth_final_save_failure_stops_and_success_does_not_reset(run_store, monkeypatch):
    _context, store, scope = run_store
    store.error_limit = 5

    def fail(path, data):
        raise OSError(errno.ENOSPC, "disk full")

    failing = {"active": True}

    def maybe_fail(path, data):
        if failing["active"]:
            fail(path, data)
        store._write_payload_original(path, data)

    store._write_payload_original = store.__class__._write_payload.__get__(store)
    monkeypatch.setattr(store, "_write_payload", maybe_fail)

    successes = []
    for index in range(6):
        evidence_id, captured = capture(store, f"fail-{index}")
        if index == 2:
            failing["active"] = False
            success_id, success_capture = capture(store, "success")
            successes.append(store.commit_observation(success_capture, scope=scope))
            failing["active"] = True
            continue
        outcome = store.commit_observation(captured, scope=scope)
        assert outcome.durability == "pending"
    assert store.health.failure_total == 5
    assert successes[0].durability == "committed"

    evidence_id, captured = capture(store, "fail-last")
    with pytest.raises(PersistenceErrorLimitExceeded) as exc_info:
        store.commit_observation(captured, scope=scope)
    assert exc_info.value.failure_total == 6
    assert exc_info.value.limit == 5

    with pytest.raises(PersistenceErrorLimitExceeded):
        store.check_writable_execution_allowed()
    # The first five attempts each ran once; the sixth stopped immediately.
    assert store.health.failure_total == 6


def test_index_write_failure_is_diagnostic_only(run_store, monkeypatch):
    _context, store, scope = run_store
    evidence_id, captured = capture(store, "committed")
    store.commit_observation(captured, scope=scope)

    def fail(path, data):
        raise OSError(errno.EACCES, "read only")

    monkeypatch.setattr("vulnclaw.evidence.store.atomic_write_json", fail)
    assert store.flush_index() is False
    assert store.health.index_write_failures == 1
    assert store.health.failure_total == 0
    store.check_writable_execution_allowed()
    assert store.view(evidence_id, scope=scope).text == "committed"


def test_payload_path_escape_is_refused(run_store):
    _context, store, scope = run_store
    evidence_id, captured = capture(store, "safe")
    store.commit_observation(captured, scope=scope)
    record_path = store.run_dir / "evidence" / "records" / f"{evidence_id}.json"
    payload = json.loads(record_path.read_text())
    payload["payload"]["path"] = "../../outside.txt"
    record_path.write_text(json.dumps(payload))
    store._scan_records()

    with pytest.raises(EvidenceUnavailableError):
        store.view(evidence_id, scope=scope)


def test_read_all_scope_still_requires_this_run(run_store):
    context, store, _scope = run_store
    other = EvidenceScope(run_id="other-run", task_id="main", read_all=True)
    evidence_id, captured = capture(store, "x")
    store.commit_observation(captured, scope=EvidenceScope(run_id=context.run_id))
    with pytest.raises(EvidenceScopeError):
        store.view(evidence_id, scope=other)
