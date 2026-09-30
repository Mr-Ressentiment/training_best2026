"""Unit tests for SQLAlchemy ORM domain models (app/models/entities.py)."""

from datetime import date, datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base
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


@pytest.fixture
def db_session():
    """Create a fresh in-memory SQLite database session for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_catalog_product_valid_insert(db_session):
    """Verify valid CatalogProduct can be inserted and queried."""
    product = CatalogProduct(
        sku="SKU-BOX-100",
        name="Heavy Duty Storage Box",
        category="Packaging",
        unit_of_measure="Box",
        base_price_cents=1250,
        min_order_quantity=5,
        package_increment=5,
    )
    db_session.add(product)
    db_session.commit()

    saved = db_session.query(CatalogProduct).filter_by(sku="SKU-BOX-100").one()
    assert saved.base_price_cents == 1250
    assert saved.min_order_quantity == 5
    assert saved.package_increment == 5


def test_catalog_product_negative_base_price_rejected(db_session):
    """Constraint check: base_price_cents < 0 must be rejected."""
    product = CatalogProduct(
        sku="SKU-NEG-PRICE",
        name="Invalid Product",
        category="Packaging",
        unit_of_measure="Each",
        base_price_cents=-1,
        min_order_quantity=1,
        package_increment=1,
    )
    db_session.add(product)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_catalog_product_min_order_quantity_below_one_rejected(db_session):
    """Constraint check: min_order_quantity < 1 must be rejected."""
    product = CatalogProduct(
        sku="SKU-MOQ-0",
        name="Zero MOQ Product",
        category="Packaging",
        unit_of_measure="Each",
        base_price_cents=100,
        min_order_quantity=0,
        package_increment=1,
    )
    db_session.add(product)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_catalog_product_package_increment_below_one_rejected(db_session):
    """Constraint check: package_increment < 1 must be rejected."""
    product = CatalogProduct(
        sku="SKU-PKG-0",
        name="Zero Increment Product",
        category="Packaging",
        unit_of_measure="Each",
        base_price_cents=100,
        min_order_quantity=1,
        package_increment=0,
    )
    db_session.add(product)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_contract_price_tier_min_quantity_below_one_rejected(db_session):
    """Constraint check: tier min_quantity < 1 must be rejected."""
    contract = CustomerContract(
        customer_id="CUST-001",
        customer_name="Acme Corp",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )
    product = CatalogProduct(
        sku="SKU-001",
        name="Test Item",
        category="Test",
        unit_of_measure="Case",
        base_price_cents=1000,
        min_order_quantity=1,
        package_increment=1,
    )
    db_session.add_all([contract, product])
    db_session.commit()

    tier = ContractPriceTier(
        contract_id=contract.id,
        sku=product.sku,
        min_quantity=0,
        tier_price_cents=900,
    )
    db_session.add(tier)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_contract_price_tier_negative_price_rejected(db_session):
    """Constraint check: negative tier_price_cents must be rejected."""
    contract = CustomerContract(
        customer_id="CUST-002",
        customer_name="Apex Logistics",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )
    product = CatalogProduct(
        sku="SKU-002",
        name="Test Item 2",
        category="Test",
        unit_of_measure="Case",
        base_price_cents=1000,
        min_order_quantity=1,
        package_increment=1,
    )
    db_session.add_all([contract, product])
    db_session.commit()

    tier = ContractPriceTier(
        contract_id=contract.id,
        sku=product.sku,
        min_quantity=10,
        tier_price_cents=-50,
    )
    db_session.add(tier)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_foreign_key_enforcement_rejects_orphan_record(db_session):
    """Constraint check: invalid FK must be rejected by PRAGMA foreign_keys = ON."""
    tier = ContractPriceTier(
        contract_id="non-existent-contract-uuid",
        sku="non-existent-sku",
        min_quantity=10,
        tier_price_cents=500,
    )
    db_session.add(tier)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_verified_order_duplicate_draft_id_rejected(db_session):
    """Constraint check: duplicate draft_id on VerifiedOrderRecord must be rejected."""
    doc = PurchaseOrderDocument(
        filename="po.txt",
        content_type="text/plain",
        raw_text="Customer: Acme Corp",
        status="Ingested",
    )
    db_session.add(doc)
    db_session.commit()

    draft = OrderDraft(
        document_id=doc.id,
        customer_id="CUST-001",
        status="Ready for Approval",
        calculated_subtotal_cents=5000,
    )
    db_session.add(draft)
    db_session.commit()

    order1 = VerifiedOrderRecord(
        draft_id=draft.id,
        order_number="VO-2026-0001",
        customer_id="CUST-001",
        po_number="PO-9999",
        grand_total_cents=5000,
        approved_by="Operator A",
        is_replay_mode=False,
        line_items_snapshot_json="[]",
    )
    db_session.add(order1)
    db_session.commit()

    order2 = VerifiedOrderRecord(
        draft_id=draft.id,
        order_number="VO-2026-0002",
        customer_id="CUST-001",
        po_number="PO-9999",
        grand_total_cents=5000,
        approved_by="Operator B",
        is_replay_mode=False,
        line_items_snapshot_json="[]",
    )
    db_session.add(order2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_verified_order_duplicate_order_number_rejected(db_session):
    """Constraint check: duplicate order_number on VerifiedOrderRecord must be rejected."""
    doc = PurchaseOrderDocument(
        filename="po2.txt",
        content_type="text/plain",
        raw_text="Customer: Acme Corp",
        status="Ingested",
    )
    doc2 = PurchaseOrderDocument(
        filename="po3.txt",
        content_type="text/plain",
        raw_text="Customer: Acme Corp",
        status="Ingested",
    )
    db_session.add_all([doc, doc2])
    db_session.commit()

    draft1 = OrderDraft(
        document_id=doc.id,
        customer_id="CUST-001",
        status="Ready for Approval",
    )
    draft2 = OrderDraft(
        document_id=doc2.id,
        customer_id="CUST-001",
        status="Ready for Approval",
    )
    db_session.add_all([draft1, draft2])
    db_session.commit()

    order1 = VerifiedOrderRecord(
        draft_id=draft1.id,
        order_number="VO-2026-0001",
        customer_id="CUST-001",
        po_number="PO-1",
        grand_total_cents=1000,
        approved_by="Operator A",
        is_replay_mode=False,
        line_items_snapshot_json="[]",
    )
    order2 = VerifiedOrderRecord(
        draft_id=draft2.id,
        order_number="VO-2026-0001",
        customer_id="CUST-001",
        po_number="PO-2",
        grand_total_cents=2000,
        approved_by="Operator B",
        is_replay_mode=False,
        line_items_snapshot_json="[]",
    )
    db_session.add(order1)
    db_session.commit()

    db_session.add(order2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_reconciliation_domain_full_graph_navigation(db_session):
    """Verify creation and bidirectional navigation across all domain entities."""
    # 1. Product & Contract
    product = CatalogProduct(
        sku="SKU-WRAP-18",
        name="Industrial Stretch Wrap 18in",
        category="Packaging Supplies",
        unit_of_measure="Roll",
        base_price_cents=2450,
        min_order_quantity=4,
        package_increment=4,
    )
    contract = CustomerContract(
        customer_id="CUST-ACME",
        customer_name="Acme Packaging Solutions",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )
    db_session.add_all([product, contract])
    db_session.commit()

    tier = ContractPriceTier(
        contract_id=contract.id,
        sku=product.sku,
        min_quantity=10,
        tier_price_cents=2100,
    )
    db_session.add(tier)
    db_session.commit()

    assert len(product.tiers) == 1
    assert product.tiers[0].tier_price_cents == 2100
    assert len(contract.tiers) == 1
    assert contract.tiers[0].product.sku == "SKU-WRAP-18"

    # 2. Document & Draft
    doc = PurchaseOrderDocument(
        filename="PO_ACME_10928.txt",
        content_type="text/plain",
        raw_text="Customer: Acme Packaging Solutions\nPO Number: PO-10928\nItem: 18 inch wrap, Qty: 20, Price: 21.00",
        status="Ingested",
    )
    db_session.add(doc)
    db_session.commit()

    draft = OrderDraft(
        document_id=doc.id,
        customer_id="CUST-ACME",
        customer_name_extracted="Acme Packaging Solutions",
        po_number_extracted="PO-10928",
        status="Ready for Approval",
        calculated_subtotal_cents=42000,
        is_replay_mode=False,
    )
    db_session.add(draft)
    db_session.commit()

    assert doc.draft.id == draft.id
    assert draft.document.id == doc.id

    # 3. Line Item
    line = DraftLineItem(
        draft_id=draft.id,
        line_number=1,
        customer_description="18 inch wrap",
        extracted_quantity=20,
        extracted_unit_price_cents=2100,
        extracted_line_total_cents=42000,
        matched_sku=product.sku,
        sku_confidence="High",
        sku_resolution_source="AI_HIGH_CONFIDENCE",
        contract_price_cents=2100,
        calculated_line_total_cents=42000,
        status="Active",
    )
    db_session.add(line)
    db_session.commit()

    assert len(draft.line_items) == 1
    assert draft.line_items[0].product.sku == "SKU-WRAP-18"

    # 4. Provenance
    prov_header = FieldProvenance(
        draft_id=draft.id,
        field_name="po_number",
        verbatim_snippet="PO-10928",
        location_type="txt",
        location_data_json='{"type": "txt", "line_number": 2, "char_offset": 11}',
    )
    prov_line = FieldProvenance(
        draft_id=draft.id,
        line_item_id=line.id,
        field_name="extracted_quantity",
        verbatim_snippet="20",
        location_type="txt",
        location_data_json='{"type": "txt", "line_number": 3, "char_offset": 26}',
    )
    db_session.add_all([prov_header, prov_line])
    db_session.commit()

    assert len(draft.provenance) == 2
    assert len(line.provenance) == 1

    # 5. Discrepancy Flag
    flag = DiscrepancyFlag(
        draft_id=draft.id,
        line_item_id=line.id,
        discrepancy_type="PriceMismatch",
        severity="Blocking",
        expected_value="$21.00",
        requested_value="$21.00",
        explanation="Pricing matches contract tier",
        resolution_state="ResolvedByCorrection",
    )
    db_session.add(flag)
    db_session.commit()

    assert len(draft.flags) == 1
    assert len(line.flags) == 1

    # 6. Verified Order & Audit Event
    verified = VerifiedOrderRecord(
        draft_id=draft.id,
        order_number="VO-2026-0001",
        customer_id="CUST-ACME",
        po_number="PO-10928",
        grand_total_cents=42000,
        approved_by="Operator Alice",
        is_replay_mode=False,
        line_items_snapshot_json='[{"sku": "SKU-WRAP-18", "qty": 20, "price_cents": 2100}]',
    )
    audit = AuditEvent(
        draft_id=draft.id,
        event_type="OrderApproved",
        actor="Operator Alice",
        details_json='{"status": "Approved"}',
    )
    db_session.add_all([verified, audit])
    db_session.commit()

    assert draft.verified_record.order_number == "VO-2026-0001"
    assert len(draft.audit) == 1
    assert draft.audit[0].event_type == "OrderApproved"


PERSISTENCE_DOMAINS = [
    (PurchaseOrderDocument, "content_type", ("text/plain", "application/pdf"), "image/jpeg"),
    (PurchaseOrderDocument, "status", ("Ingested", "Failed"), "BANANA"),
    (OrderDraft, "status", ("Ingested", "Needs Review", "Ready for Approval", "Approved", "Rejected"), "BANANA"),
    (DraftLineItem, "sku_confidence", ("High", "Ambiguous", "Unrecognized", None), "Certain"),
    (DraftLineItem, "sku_resolution_source", ("NONE", "AI_HIGH_CONFIDENCE", "OPERATOR_SELECTED"), "MAGIC"),
    (DraftLineItem, "status", ("Active", "Removed"), "DELETED"),
    (FieldProvenance, "location_type", ("txt", "pdf"), "jpeg"),
    (DiscrepancyFlag, "discrepancy_type", ("PriceMismatch", "QuantityOrPackagingBreach", "ArithmeticMismatch", "CatalogMatchingMismatch"), "CreditLimit"),
    (DiscrepancyFlag, "severity", ("Blocking", "Warning"), "Info"),
    (DiscrepancyFlag, "resolution_state", ("Unresolved", "ResolvedByCorrection", "ResolvedByLineRemoval"), "DELETED"),
]


@pytest.fixture
def valid_domain_records():
    """Minimal valid records whose related parents are persisted with each target."""
    document = PurchaseOrderDocument(
        filename="po.txt", content_type="text/plain", raw_text="PO-1", status="Ingested",
    )
    draft = OrderDraft(document=document)
    return {
        PurchaseOrderDocument: document,
        OrderDraft: draft,
        DraftLineItem: DraftLineItem(draft=draft, line_number=1, customer_description="Box"),
        FieldProvenance: FieldProvenance(
            draft=draft, field_name="po_number", verbatim_snippet="PO-1",
            location_type="txt", location_data_json='{"type": "txt", "line_number": 1, "char_offset": 0}',
        ),
        DiscrepancyFlag: DiscrepancyFlag(
            draft=draft, discrepancy_type="PriceMismatch", expected_value="100",
            requested_value="200", explanation="Price differs",
        ),
    }


@pytest.mark.parametrize(
    "model,field,value",
    [pytest.param(model, field, invalid, id=f"{model.__name__}.{field}")
     for model, field, valid, invalid in PERSISTENCE_DOMAINS],
)
def test_invalid_persistence_domain_rejected(db_session, valid_domain_records, model, field, value):
    record = valid_domain_records[model]
    setattr(record, field, value)
    db_session.add(record)
    with pytest.raises(IntegrityError, match="CHECK constraint failed"):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize(
    "model,field,value",
    [pytest.param(model, field, value, id=f"{model.__name__}.{field}={value}")
     for model, field, valid, invalid in PERSISTENCE_DOMAINS for value in valid],
)
def test_valid_persistence_domain_allowed(db_session, valid_domain_records, model, field, value):
    record = valid_domain_records[model]
    setattr(record, field, value)
    db_session.add(record)
    db_session.commit()
    db_session.refresh(record)
    assert getattr(record, field) == value


def test_document_has_at_most_one_draft(db_session):
    document = PurchaseOrderDocument(
        filename="po.txt", content_type="text/plain", raw_text="PO-1", status="Ingested",
    )
    db_session.add(document)
    db_session.commit()
    assert document.draft is None

    draft = OrderDraft(document=document)
    db_session.add(draft)
    db_session.commit()
    db_session.expire_all()
    assert document.draft is draft
    assert draft.document is document
    assert draft.is_replay_mode is False

    # Use the FK directly so ORM reassignment cannot detach the original draft.
    db_session.add(OrderDraft(document_id=document.id))
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed: order_drafts.document_id"):
        db_session.commit()
    db_session.rollback()
    assert db_session.query(OrderDraft).count() == 1
    assert document.draft is draft


@pytest.mark.parametrize("replay_mode", [None, False, True], ids=["omitted", "live", "replay"])
def test_verified_order_requires_explicit_replay_mode(db_session, replay_mode):
    document = PurchaseOrderDocument(
        filename="po.txt", content_type="text/plain", raw_text="PO-1", status="Ingested",
    )
    draft = OrderDraft(document=document)
    db_session.add(draft)
    db_session.commit()

    replay_fields = {} if replay_mode is None else {"is_replay_mode": replay_mode}
    order = VerifiedOrderRecord(
        draft_id=draft.id, order_number="VO-2026-0001", customer_id="CUST-001",
        po_number="PO-1", grand_total_cents=100, approved_by="Operator",
        line_items_snapshot_json="[]", **replay_fields,
    )
    db_session.add(order)
    if replay_mode is None:
        with pytest.raises(IntegrityError, match="NOT NULL constraint failed: verified_order_records.is_replay_mode"):
            db_session.commit()
        db_session.rollback()
        assert db_session.query(VerifiedOrderRecord).count() == 0
    else:
        db_session.commit()
        db_session.refresh(order)
        assert order.is_replay_mode is replay_mode
