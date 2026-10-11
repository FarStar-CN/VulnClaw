"""The explicit model-facing candidate submission boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

from vulnclaw.config.domain_models import EvidenceRef, VulnerabilityFinding
from vulnclaw.config.finding_similarity import normalize_vuln_type
from vulnclaw.findings.evidence import scope_identity, snapshot_records
from vulnclaw.findings.types import FindingSubmission


def submit_finding(agent, arguments: dict) -> str:
    submission = FindingSubmission.model_validate(arguments)
    session = agent.context.state
    target = session.target or ""
    if not target:
        raise ValueError("Select a target before submitting a finding")
    endpoint = submission.endpoint
    if endpoint:
        base = target if "://" in target else "https://" + target
        endpoint = urljoin(base, endpoint)
        if scope_identity(endpoint)[0] != scope_identity(target)[0]:
            raise ValueError("Finding endpoint belongs to another target")
        from vulnclaw.agent.builtin_tools import (
            enforce_host_path_constraints,
            enforce_port_constraints,
        )
        from vulnclaw.config.url_utils import infer_port_from_url

        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ValueError("Finding endpoint must be an HTTP(S) URL without credentials")
        violation = enforce_host_path_constraints(
            agent, host=parsed.hostname or "", path=parsed.path, target=endpoint,
        ) or enforce_port_constraints(agent, [infer_port_from_url(endpoint)], target=endpoint)
        if violation:
            raise ValueError(violation)
    if submission.code_location:
        location_path = submission.code_location.rsplit(":", 1)[0]
        target_path = Path(target).expanduser().resolve()
        location = Path(location_path).expanduser().resolve()
        if location != target_path and not location.is_relative_to(target_path):
            raise ValueError("Code location is outside the selected source target")
    records = [session.agent_state.get_evidence(key) for key in submission.evidence_ids]
    if endpoint:
        supplied = urlsplit(endpoint)
        matching = [record for record in records if record is not None and record.arguments.get("url")
                    and scope_identity(str(record.arguments["url"]))[0] == supplied.hostname
                    and urlsplit(str(record.arguments["url"])).path == supplied.path]
        if not matching:
            raise ValueError("No cited tool request concerns the submitted endpoint")
        if submission.method and not any(
            str(record.arguments.get("method", "GET")).upper() == submission.method.upper()
            for record in matching
        ):
            raise ValueError("No cited tool request uses the submitted HTTP method")
    snapshots = snapshot_records(agent, submission.evidence_ids)
    kind = normalize_vuln_type(submission.vuln_type)
    method = submission.method.upper() if submission.method else (
        str(matching[0].arguments.get("method", "GET")).upper() if endpoint else None
    )
    location = endpoint or submission.code_location or target
    identity = hashlib.sha256(
        json.dumps([kind, target, location, method], ensure_ascii=False).encode()
    ).hexdigest()[:32]
    existing = next((f for f in session.findings if f.finding_id == identity), None)
    references = [EvidenceRef(kind="file", path=ref.path) for ref in snapshots]
    if existing is not None:
        known = {ref.sha256 for ref in existing.evidence_snapshots}
        additions = [ref for ref in snapshots if ref.sha256 not in known]
        if additions:
            existing.evidence_snapshots.extend(additions)
            existing.evidence_refs.extend(
                EvidenceRef(kind="file", path=ref.path) for ref in additions
            )
            session._notify_checkpoint("finding_updated")
        return json.dumps({"finding_id": identity, "action": "updated" if additions else "duplicate",
                           "verification_status": existing.verification_status})
    fields = submission.model_dump(exclude={"evidence_ids", "endpoint", "method", "vuln_type"})
    finding = VulnerabilityFinding(
        **fields, target=target, endpoint=endpoint, method=method, vuln_type=kind,
        finding_id=identity, submission_id=str(uuid4()), evidence_snapshots=snapshots,
        evidence_refs=references, evidence="\n".join(
            f"[{ref.evidence_id}] saved tool evidence: {ref.path}" for ref in snapshots
        ), evidence_level="L2", lifecycle_status="pending_verification",
    )
    session.add_finding(finding)
    stored = next((f for f in session.findings if f.finding_id == identity), None)
    if stored is None:
        raise RuntimeError("Structured finding was not retained by the finding store")
    return json.dumps({"finding_id": identity, "action": "created", "verification_status": "pending"})
