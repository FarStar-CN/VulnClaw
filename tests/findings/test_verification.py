import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vulnclaw.agent.context import SessionState, VulnerabilityFinding
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.findings.intake import submit_finding
from vulnclaw.findings.types import EvidenceReview, FindingAssessment
from vulnclaw.findings.verification import prepare_report
from vulnclaw.report.findings_output import build_findings_document, to_sarif
from vulnclaw.report.generator import generate_report


@pytest.fixture
def candidate(tmp_path):
    config = VulnClawConfig()
    config.session.output_dir = tmp_path
    config.session.auto_save = False
    state = SessionState(target='https://lab.test')
    state.agent_state.remember_tool_result(
        tool='fetch', arguments={'url': 'https://lab.test/login'}, target=state.target,
        output='Observed a SQL error on the actual login request',
    )
    agent = SimpleNamespace(config=config, context=SimpleNamespace(state=state), session_state=state)
    submit_finding(agent, {'title': 'Injection candidate', 'description': 'Observed request error',
                          'vuln_type': 'SQLi', 'severity': 'High', 'endpoint': 'https://lab.test/login',
                          'evidence_ids': ['e001']})
    return config, state


def review(confidence='high'):
    return EvidenceReview(confidence=confidence, reason='A direct observation remains unverified',
                          observations=[{'evidence_id': 'e001', 'quote': 'Observed a SQL error'}],
                          missing_checks=['Independent reproduction'])


async def test_default_disabled_poc_still_reviews_and_cannot_promote(candidate, monkeypatch):
    config, state = candidate
    calls = []
    monkeypatch.setattr('vulnclaw.findings.verification._review_with_llm', lambda *args: calls.append(args) or review())
    poc = AsyncMock()
    monkeypatch.setattr('vulnclaw.findings.verification.execute_plan', poc)
    state.findings[0].mark_verified('Previous conclusion')
    view = await prepare_report(state, config=config)
    assert len(calls) == 1 and poc.await_count == 0
    assert state.findings[0].verified
    assert not view.findings[0].verified
    assert view.findings[0].report_assessment.outcome == 'suspected_high'
    assert view.findings[0].previous_verification['verification_status'] == 'verified'
    assert to_sarif(view.findings)['runs'][0]['results'] == []


@pytest.mark.parametrize('invalid', [
    EvidenceReview(confidence='high', reason='unsupported', observations=[]),
    EvidenceReview(confidence='high', reason='wrong id', observations=[{'evidence_id': 'e999', 'quote': 'SQL error'}]),
    EvidenceReview(confidence='high', reason='invented quote', observations=[{'evidence_id': 'e001', 'quote': 'RCE confirmed'}]),
])
async def test_review_invalid_references_are_incomplete_low(candidate, monkeypatch, invalid, i18n_language):
    config, state = candidate
    i18n_language("zh")  # pin the incomplete marker regardless of auto-detection
    monkeypatch.setattr('vulnclaw.findings.verification._review_with_llm', lambda *args: invalid)
    view = await prepare_report(state, config=config)
    result = view.findings[0].report_assessment
    assert result.outcome == 'suspected_low' and not result.complete
    assert '校验未完成' in result.reason


async def test_snapshot_tampering_never_reaches_review(candidate, monkeypatch):
    config, state = candidate
    ref = state.findings[0].evidence_snapshots[0]
    (Path(ref.root) / ref.path).write_text('{}')
    monkeypatch.setattr('vulnclaw.findings.verification._review_with_llm', lambda *args: pytest.fail('Tampered snapshots'))
    view = await prepare_report(state, config=config)
    assert not view.findings[0].report_assessment.complete
    assert 'integrity' in view.findings[0].report_assessment.reason


async def test_poc_unavailable_falls_back_to_review(candidate, monkeypatch):
    config, state = candidate
    monkeypatch.setattr('vulnclaw.findings.verification._review_with_llm', lambda *args: review('low'))
    view = await prepare_report(state, config=config, poc_verify=True)
    result = view.findings[0].report_assessment
    assert result.method == 'evidence_review' and result.outcome == 'suspected_low'
    assert 'No supported' in result.failure_reason


@pytest.mark.parametrize('outcome', ['verified', 'rejected'])
async def test_conclusive_poc_updates_new_session_and_audits(candidate, monkeypatch, outcome):
    config, state = candidate
    monkeypatch.setattr('vulnclaw.findings.verification.build_plan', lambda *args: object())
    assessment = FindingAssessment(outcome=outcome, method='poc', reason='A controlled test gave a conclusive result', evidence_ids=['e001'])
    monkeypatch.setattr('vulnclaw.findings.verification.execute_plan', AsyncMock(return_value=assessment))
    path = state.save(config.session.output_dir / 'new-session.json')
    view = await prepare_report(state, config=config, poc_verify=True)
    restored = SessionState.load(path)
    assert restored.findings[0].verification_status == outcome
    assert restored.findings[0].verification_history[-1].reason == assessment.reason
    document = build_findings_document(view)
    assert document['schema_version'] == '1.1'
    assert document['summary'][outcome] == 1
    assert len(to_sarif(view.findings)['runs'][0]['results']) == (1 if outcome == 'verified' else 0)


async def test_changed_input_does_not_receive_stale_verdict(candidate, monkeypatch):
    config, state = candidate
    async def changed(*args, **kwargs):
        state.findings[0].description = 'Updated by the running task'
        return FindingAssessment(outcome='verified', method='poc', reason='Old result')
    monkeypatch.setattr('vulnclaw.findings.verification.assess_finding', changed)
    view = await prepare_report(state, config=config)
    assert not state.findings[0].verified and state.findings[0].verification_history == []
    assert not view.findings[0].report_assessment.complete


async def test_cancel_stops_generation_without_fallback(candidate, monkeypatch):
    config, state = candidate
    monkeypatch.setattr('vulnclaw.findings.verification.build_plan', lambda *args: object())
    monkeypatch.setattr('vulnclaw.findings.verification.execute_plan', AsyncMock(side_effect=asyncio.CancelledError()))
    monkeypatch.setattr('vulnclaw.findings.verification._review_with_llm', lambda *args: pytest.fail('cancelled'))
    with pytest.raises(asyncio.CancelledError):
        await prepare_report(state, config=config, poc_verify=True)
    assert state.findings[0].verification_history == []


async def test_legacy_state_is_only_read_and_never_rewritten(tmp_path):
    config = VulnClawConfig()
    config.session.output_dir = tmp_path
    path = tmp_path / 'old.json'
    path.write_text(json.dumps({'target': 'lab.test', 'findings': [{'title': 'Old claim', 'verified': True}]}))
    original = path.read_bytes()
    state = SessionState.load(path)
    view = await prepare_report(state, config=config)
    assert path.read_bytes() == original
    assert state.findings[0].verified and not view.findings[0].verified
    assert not view.findings[0].report_assessment.complete


async def test_report_and_machine_outputs_use_current_review(candidate, monkeypatch):
    config, state = candidate
    monkeypatch.setattr('vulnclaw.findings.verification._review_with_llm', lambda *args: review())
    path = await generate_report(state, str(config.session.output_dir / 'report.md'), llm_attack_summary='A suspected finding requires reproduction', config=config)
    text = path.read_text()
    assert 'suspected' in text or '疑似' in text
    artifact_dir = path.parent / 'report.artifacts' / 'findings'
    document = json.loads((artifact_dir / 'findings.json').read_text())
    assert document['summary']['suspected_high'] == 1
    assert document['verified'] == []
    assert json.loads((artifact_dir / 'findings.sarif').read_text())['runs'][0]['results'] == []


def test_export_cannot_bypass_assessment():
    state = SessionState()
    state.add_finding(VulnerabilityFinding(title='Self-certified', verified=True))
    with pytest.raises(ValueError, match='assessment'):
        to_sarif(state.findings)
