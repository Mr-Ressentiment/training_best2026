"""Unit tests for app.services.advisory: read-only invariants, grounding, and isolation."""
from __future__ import annotations

from decimal import Decimal
import json
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.cli import seed_baseline
from app.database import Base
from app.models.advisory import AdvisoryStatus
from app.models.entities import (
    AuditEvent,
    CatalogProduct,
    ContractPriceTier,
    CustomerContract,
    DiscrepancyFlag,
    DraftLineItem,
    FieldProvenance,
    OrderDraft,
    PurchaseOrderDocument,
    VerifiedOrderRecord,
)
from app.services.ai_provider import FixtureAIProvider
from app.services.document_parser import parse_document
from app.services.order_service import DraftNotFoundError, ingest_order
from app.services.advisory import build_draft_advisory


@pytest.fixture
def seeded_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    seed_baseline(session)
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _ingest_fixture(db: Session, fixture_id: str, filename: str, text: str) -> OrderDraft:
    parsed_doc = parse_document(
        text.encode("utf-8"),
        filename=filename,
        content_type="text/plain",
    )
    provider = FixtureAIProvider(fixture_id=fixture_id)
    draft = ingest_order(db, document=parsed_doc, provider=provider)
    db.commit()
    return draft


def _take_db_snapshot(db: Session) -> dict[str, list[dict[str, Any]]]:
    tables = [
        PurchaseOrderDocument,
        OrderDraft,
        DraftLineItem,
        FieldProvenance,
        DiscrepancyFlag,
        AuditEvent,
        VerifiedOrderRecord,
        CatalogProduct,
        CustomerContract,
        ContractPriceTier,
    ]
    snapshot: dict[str, list[dict[str, Any]]] = {}
    with db.no_autoflush:
        for t in tables:
            rows = db.scalars(select(t)).all()
            serialized = []
            for r in rows:
                row_dict = {}
                for col in r.__table__.columns:
                    val = getattr(r, col.name)
                    if isinstance(val, (int, str, bool, float, type(None))):
                        row_dict[col.name] = val
                    else:
                        row_dict[col.name] = str(val)
                serialized.append(row_dict)
            serialized.sort(key=lambda x: str(x.get("id", x.get("sku", ""))))
            snapshot[t.__tablename__] = serialized
    return snapshot


def test_build_draft_advisory_not_found(seeded_db):
    with pytest.raises(DraftNotFoundError):
        build_draft_advisory(seeded_db, "non-existent-id")


def test_build_draft_advisory_read_only_invariants(seeded_db, po_clean_acme_text):
    draft = _ingest_fixture(seeded_db, "fixture-clean-acme", "clean.txt", po_clean_acme_text)
    draft_id = draft.id

    # 1. Add uncommitted pending object to caller session to assert it survives untouched
    pending_doc = PurchaseOrderDocument(
        filename="pending.txt",
        content_type="text/plain",
        raw_text="Pending document text",
        status="Ingested",
    )
    seeded_db.add(pending_doc)
    assert pending_doc in seeded_db.new

    # 2. Attach strict SQLAlchemy listeners that must NOT trigger on caller session
    mutation_events = []

    def fail_event(name):
        def handler(*args, **kwargs):
            mutation_events.append(name)
        return handler

    event.listen(seeded_db, "before_flush", fail_event("before_flush"))
    event.listen(seeded_db, "after_flush", fail_event("after_flush"))
    event.listen(seeded_db, "after_commit", fail_event("after_commit"))
    event.listen(seeded_db, "after_rollback", fail_event("after_rollback"))

    # 3. Take full DB snapshot before advisory
    before_snapshot = _take_db_snapshot(seeded_db)

    # 4. Spy on commit/flush/rollback
    with patch.object(seeded_db, "commit", wraps=seeded_db.commit) as spy_commit, \
         patch.object(seeded_db, "flush", wraps=seeded_db.flush) as spy_flush, \
         patch.object(seeded_db, "rollback", wraps=seeded_db.rollback) as spy_rollback:

        res = build_draft_advisory(seeded_db, draft_id)

    # Assert no DB mutations occurred on caller session
    assert spy_commit.call_count == 0
    assert spy_flush.call_count == 0
    assert spy_rollback.call_count == 0
    assert mutation_events == []

    # Assert pending uncommitted object is still pending in session.new
    assert pending_doc in seeded_db.new

    # Assert database state is 100% identical
    after_snapshot = _take_db_snapshot(seeded_db)
    # Remove the pending doc from after_snapshot to compare persistent state
    after_snapshot["purchase_order_documents"] = [
        d for d in after_snapshot["purchase_order_documents"] if d["filename"] != "pending.txt"
    ]
    assert before_snapshot == after_snapshot

    # Clean up pending
    seeded_db.expunge(pending_doc)


def test_build_draft_advisory_terminal_draft(seeded_db, po_clean_acme_text):
    draft = _ingest_fixture(seeded_db, "fixture-clean-acme", "clean.txt", po_clean_acme_text)
    draft.status = "Approved"
    seeded_db.commit()

    with patch("app.services.advisory.search_counterfactual_plans") as spy_cf:
        res = build_draft_advisory(seeded_db, draft.id)

    assert spy_cf.call_count == 0
    assert res.advisory_status == AdvisoryStatus.NOT_APPLICABLE_TERMINAL
    assert res.status_persisted == "Approved"
    if res.baseline:
        assert res.baseline.simulated_status is None
    if res.counterfactuals:
        assert res.counterfactuals.successful_plans == []
        assert res.counterfactuals.coverage.total_scenarios_evaluated == 0


def test_build_draft_advisory_section_filtering_and_lazy_evaluation(seeded_db, po_clean_acme_text):
    draft = _ingest_fixture(seeded_db, "fixture-clean-acme", "clean.txt", po_clean_acme_text)

    # Request only baseline and trace
    with patch("app.services.advisory.search_counterfactual_plans") as spy_cf:
        res = build_draft_advisory(seeded_db, draft.id, sections=["baseline", "trace"])

    # Counterfactual search MUST NOT run when counterfactuals section is omitted
    assert spy_cf.call_count == 0

    assert res.baseline is not None
    assert res.trace is not None
    assert res.counterfactuals is None
    assert res.review_priority is None
    assert res.sku_confidence is None
    assert res.included_sections == ["baseline", "trace"]
    assert res.sections_omitted == ["counterfactuals", "review_priority", "sku_confidence"]


def test_build_draft_advisory_stale_evaluation_detection(seeded_db, po_clean_acme_text):
    draft = _ingest_fixture(seeded_db, "fixture-clean-acme", "clean.txt", po_clean_acme_text)
    # Artificially set status to Needs Review in test DB without changing data
    draft.status = "Needs Review"
    seeded_db.commit()

    res = build_draft_advisory(seeded_db, draft.id, sections=["baseline", "trace"])
    assert res.baseline is not None
    assert res.baseline.status_persisted == "Needs Review"
    assert res.baseline.simulated_status == "Ready for Approval"
    assert res.baseline.is_stale_evaluation is True

    # Check trace includes staleness warning step
    stale_steps = [s for s in res.trace.steps if s.stage.value == "staleness"]
    assert len(stale_steps) == 1
    assert stale_steps[0].outcome == "warning"

    # Database was not altered
    seeded_db.refresh(draft)
    assert draft.status == "Needs Review"


def test_trace_grounding_and_bidirectional_coverage(seeded_db, po_discrepancy_apex_text):
    draft = _ingest_fixture(seeded_db, "fixture-discrepancy-apex", "discrepancy.txt", po_discrepancy_apex_text)
    res = build_draft_advisory(seeded_db, draft.id)

    assert res.trace is not None
    assert res.baseline is not None

    raw_lines = po_discrepancy_apex_text.splitlines()

    # Grounding check: all document evidence must match raw_text exactly at the given coordinates
    doc_evidence_count = 0
    for step in res.trace.steps:
        for ev in step.evidence:
            if ev.source_type == "document" and ev.document_evidence is not None:
                doc_ev = ev.document_evidence
                loc = doc_ev.location.root if hasattr(doc_ev.location, "root") else doc_ev.location
                if loc.type == "txt":
                    line_idx = loc.line_number - 1
                    assert 0 <= line_idx < len(raw_lines)
                    line_text = raw_lines[line_idx]
                    start = loc.char_offset
                    end = start + len(doc_ev.verbatim_snippet)
                    snippet_in_file = line_text[start:end]
                    assert snippet_in_file == doc_ev.verbatim_snippet, (
                        f"Hallucinated document evidence: expected '{doc_ev.verbatim_snippet}', "
                        f"found '{snippet_in_file}' at line {loc.line_number}:{start}"
                    )
                    doc_evidence_count += 1

    assert doc_evidence_count > 0, "Trace should contain grounded document evidence citations"

    # Bi-directional check: every blocker in baseline.blockers must have >=1 trace step
    for blocker in res.baseline.blockers:
        matching_steps = [
            s for s in res.trace.steps
            if (
                (blocker.line_number is not None and s.subject == f"line:{blocker.line_number}")
                or (blocker.scope == "order" and s.subject == "order")
            )
            and (s.rule_or_decision == blocker.discrepancy_type or s.outcome == "blocking")
        ]
        assert len(matching_steps) >= 1, f"Blocker {blocker} has no corresponding trace step"
