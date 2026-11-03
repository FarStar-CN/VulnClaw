from __future__ import annotations

from types import SimpleNamespace

from vulnclaw.agent.agent_state import AgentState, VerifiedClaim
from vulnclaw.agent.context import SessionState
from vulnclaw.agent.subagent.integration import _rewrite_evidence_ids
from vulnclaw.agent.subagent.merge import EvidenceCommitter, merge_agent_state
from vulnclaw.evidence.models import EvidenceDescriptor, new_evidence_id


def _descriptor(evidence_id: str, *, tool: str = "fetch") -> EvidenceDescriptor:
    return EvidenceDescriptor(
        id=evidence_id,
        tool=tool,
        target="https://example.test",
        producer_task_id="main",
        summary=f"{tool} succeeded",
        size_chars=4,
        fingerprint=evidence_id,
    )


def test_stable_ids_are_never_merged_by_equal_content():
    parent = AgentState()
    first_id = new_evidence_id()
    second_id = new_evidence_id()
    parent.evidence.append(_descriptor(first_id))
    child = AgentState()
    child.evidence.append(_descriptor(first_id))
    child.evidence.append(_descriptor(second_id))
    child.verified_claims.append(
        VerifiedClaim(id="c1", claim="two observations", evidence_ids=[second_id])
    )

    id_map = merge_agent_state(parent, child, max_records=48)

    assert id_map == {second_id: second_id}
    assert [item.id for item in parent.evidence] == [first_id, second_id]
    assert parent.verified_claims[-1].evidence_ids == [second_id]


def test_rewriting_leaves_stable_ids_global_and_namespaces_legacy_ids():
    stable = new_evidence_id()
    text = f"cite {stable}, e001 and e999"

    rewritten = _rewrite_evidence_ids(text, {"e001": "parent-001"}, allowed_unmapped=set())

    assert stable in rewritten
    assert "parent-001" in rewritten
    assert "[uncommitted evidence]" in rewritten


def test_committer_publishes_new_task_scope_without_renaming_ids():
    parent_state = SessionState(target="https://example.test", task_id="main")
    child_state = SessionState(target="https://example.test", task_id="a-0001")
    parent_state.agent_state.evidence.append(_descriptor(new_evidence_id()))
    new_id = new_evidence_id()
    child_state.agent_state.evidence.append(_descriptor(new_id))

    published = []

    def refresh_evidence_scope(**kwargs):
        published.extend(kwargs.get("published_task_ids") or [])

    parent_agent = SimpleNamespace(
        context=SimpleNamespace(state=parent_state),
        refresh_evidence_scope=refresh_evidence_scope,
    )
    report = EvidenceCommitter().commit(
        parent_agent,
        child_state,
        task_id="a-0001",
        agent_type="leaf",
        baseline_fingerprints=set(),
        include_session_state=True,
    )

    assert new_id in report.evidence_ids
    assert any(item.id == new_id for item in parent_state.agent_state.evidence)
    assert "a-0001" in published
