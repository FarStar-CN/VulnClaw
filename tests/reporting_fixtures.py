"""Assessed snapshots for renderer tests; verification is exercised in tests/findings."""

import pytest

from vulnclaw.findings.types import FindingAssessment


@pytest.fixture
def renderer_assessment(monkeypatch):
    async def prepared(session, **kwargs):
        view = session.model_copy(deep=True)
        for finding in view.findings:
            outcome = 'verified' if finding.verified else 'rejected' if finding.verification_status == 'rejected' else 'suspected_low'
            finding.report_assessment = FindingAssessment(
                outcome=outcome, method='poc' if outcome in {'verified', 'rejected'} else 'evidence_review',
                reason='Controlled assessment fixture',
            )
            # Mirror what prepare_report leaves on the assessed snapshot.
            if outcome == 'suspected_low':
                finding.verified = False
                finding.verification_status = 'pending'
                finding.lifecycle_status = 'needs_manual_review'
        view._vulnerabilities.findings = view.findings
        return view
    monkeypatch.setattr('vulnclaw.findings.verification.prepare_report', prepared)
