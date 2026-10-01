"""SQLAlchemy ORM domain models for OrderShield."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base


class CatalogProduct(Base):
    """Master catalog product record."""

    __tablename__ = "catalog_products"

    sku = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    unit_of_measure = Column(String, nullable=False)
    base_price_cents = Column(Integer, nullable=False)
    min_order_quantity = Column(Integer, nullable=False)
    package_increment = Column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("base_price_cents >= 0", name="ck_catalog_product_base_price_cents"),
        CheckConstraint("min_order_quantity >= 1", name="ck_catalog_product_min_order_quantity"),
        CheckConstraint("package_increment >= 1", name="ck_catalog_product_package_increment"),
    )

    tiers = relationship("ContractPriceTier", back_populates="product")
    line_items = relationship("DraftLineItem", back_populates="product")


class CustomerContract(Base):
    """Customer contract agreement with active date range."""

    __tablename__ = "customer_contracts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id = Column(String, nullable=False, index=True)
    customer_name = Column(String, nullable=False)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=False)

    tiers = relationship("ContractPriceTier", back_populates="contract")


class ContractPriceTier(Base):
    """Quantity-tiered commercial pricing rule for a customer contract."""

    __tablename__ = "contract_price_tiers"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    contract_id = Column(String, ForeignKey("customer_contracts.id"), nullable=False)
    sku = Column(String, ForeignKey("catalog_products.sku"), nullable=False)
    min_quantity = Column(Integer, nullable=False)
    tier_price_cents = Column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("min_quantity >= 1", name="ck_contract_price_tier_min_quantity"),
        CheckConstraint("tier_price_cents >= 0", name="ck_contract_price_tier_tier_price_cents"),
    )

    contract = relationship("CustomerContract", back_populates="tiers")
    product = relationship("CatalogProduct", back_populates="tiers")


class PurchaseOrderDocument(Base):
    """Incoming purchase order document file asset and canonical raw text."""

    __tablename__ = "purchase_order_documents"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    raw_text = Column(Text, nullable=False)
    status = Column(String, nullable=False)
    ingested_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "content_type IN ('text/plain', 'application/pdf')",
            name="ck_purchase_order_document_content_type",
        ),
        CheckConstraint(
            "status IN ('Ingested', 'Failed')",
            name="ck_purchase_order_document_status",
        ),
    )

    draft = relationship("OrderDraft", back_populates="document", uselist=False)


class OrderDraft(Base):
    """Reconciliation session draft for an ingested purchase order document."""

    __tablename__ = "order_drafts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String, ForeignKey("purchase_order_documents.id"), nullable=False, unique=True)
    customer_id = Column(String, nullable=True)
    customer_name_extracted = Column(String, nullable=True)
    po_number_extracted = Column(String, nullable=True)
    status = Column(String, nullable=False, default="Ingested")
    calculated_subtotal_cents = Column(Integer, nullable=False, default=0)
    extracted_order_total_cents = Column(Integer, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    is_replay_mode = Column(Boolean, nullable=False, default=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('Ingested', 'Needs Review', 'Ready for Approval', 'Approved', 'Rejected')",
            name="ck_order_draft_status",
        ),
    )

    document = relationship("PurchaseOrderDocument", back_populates="draft")
    line_items = relationship("DraftLineItem", back_populates="draft")
    provenance_records = relationship("FieldProvenance", back_populates="draft")
    discrepancy_flags = relationship("DiscrepancyFlag", back_populates="draft")
    verified_order = relationship("VerifiedOrderRecord", back_populates="draft", uselist=False)
    audit_events = relationship("AuditEvent", back_populates="draft")

    @property
    def provenance(self):
        """Alias for provenance_records navigation."""
        return self.provenance_records

    @property
    def flags(self):
        """Alias for discrepancy_flags navigation."""
        return self.discrepancy_flags

    @property
    def verified_record(self):
        """Alias for verified_order navigation."""
        return self.verified_order

    @property
    def audit(self):
        """Alias for audit_events navigation."""
        return self.audit_events


class DraftLineItem(Base):
    """Individual line item extracted from purchase order document."""

    __tablename__ = "draft_line_items"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    draft_id = Column(String, ForeignKey("order_drafts.id"), nullable=False)
    line_number = Column(Integer, nullable=False)
    customer_description = Column(Text, nullable=False)
    extracted_quantity = Column(Integer, nullable=True)
    extracted_unit_price_cents = Column(Integer, nullable=True)
    extracted_line_total_cents = Column(Integer, nullable=True)
    matched_sku = Column(String, ForeignKey("catalog_products.sku"), nullable=True)
    sku_confidence = Column(String, nullable=True)
    sku_resolution_source = Column(String, nullable=False, default="NONE")
    candidate_skus_json = Column(Text, nullable=True)
    matching_rationale = Column(Text, nullable=True)
    contract_price_cents = Column(Integer, nullable=True)
    calculated_line_total_cents = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="Active")

    __table_args__ = (
        CheckConstraint("line_number >= 1", name="ck_draft_line_item_line_number"),
        CheckConstraint(
            "sku_confidence IS NULL OR sku_confidence IN ('High', 'Ambiguous', 'Unrecognized')",
            name="ck_draft_line_item_sku_confidence",
        ),
        CheckConstraint(
            "sku_resolution_source IN ('NONE', 'AI_HIGH_CONFIDENCE', 'OPERATOR_SELECTED')",
            name="ck_draft_line_item_sku_resolution_source",
        ),
        CheckConstraint("status IN ('Active', 'Removed')", name="ck_draft_line_item_status"),
    )

    draft = relationship("OrderDraft", back_populates="line_items")
    product = relationship("CatalogProduct", back_populates="line_items")
    provenance_records = relationship("FieldProvenance", back_populates="line_item")
    discrepancy_flags = relationship("DiscrepancyFlag", back_populates="line_item")

    @property
    def provenance(self):
        """Alias for provenance_records navigation."""
        return self.provenance_records

    @property
    def flags(self):
        """Alias for discrepancy_flags navigation."""
        return self.discrepancy_flags


class FieldProvenance(Base):
    """Grounding citation linking an extracted field to canonical document raw_text."""

    __tablename__ = "field_provenances"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    draft_id = Column(String, ForeignKey("order_drafts.id"), nullable=False)
    line_item_id = Column(String, ForeignKey("draft_line_items.id"), nullable=True)
    field_name = Column(String, nullable=False)
    verbatim_snippet = Column(Text, nullable=False)
    location_type = Column(String, nullable=False)
    location_data_json = Column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("location_type IN ('txt', 'pdf')", name="ck_field_provenance_location_type"),
    )

    draft = relationship("OrderDraft", back_populates="provenance_records")
    line_item = relationship("DraftLineItem", back_populates="provenance_records")


class DiscrepancyFlag(Base):
    """Business rule discrepancy or catalog ambiguity detected during reconciliation."""

    __tablename__ = "discrepancy_flags"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    draft_id = Column(String, ForeignKey("order_drafts.id"), nullable=False)
    line_item_id = Column(String, ForeignKey("draft_line_items.id"), nullable=True)
    discrepancy_type = Column(String, nullable=False)
    severity = Column(String, nullable=False, default="Blocking")
    expected_value = Column(String, nullable=False)
    requested_value = Column(String, nullable=False)
    explanation = Column(Text, nullable=False)
    resolution_state = Column(String, nullable=False, default="Unresolved")

    __table_args__ = (
        CheckConstraint(
            "discrepancy_type IN ('PriceMismatch', 'QuantityOrPackagingBreach', 'ArithmeticMismatch', 'CatalogMatchingMismatch')",
            name="ck_discrepancy_flag_discrepancy_type",
        ),
        CheckConstraint("severity IN ('Blocking', 'Warning')", name="ck_discrepancy_flag_severity"),
        CheckConstraint(
            "resolution_state IN ('Unresolved', 'ResolvedByCorrection', 'ResolvedByLineRemoval')",
            name="ck_discrepancy_flag_resolution_state",
        ),
    )

    draft = relationship("OrderDraft", back_populates="discrepancy_flags")
    line_item = relationship("DraftLineItem", back_populates="discrepancy_flags")


class VerifiedOrderRecord(Base):
    """Immutable verified order record generated upon human approval."""

    __tablename__ = "verified_order_records"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    draft_id = Column(String, ForeignKey("order_drafts.id"), nullable=False, unique=True)
    order_number = Column(String, nullable=False, unique=True)
    customer_id = Column(String, nullable=False)
    po_number = Column(String, nullable=False)
    grand_total_cents = Column(Integer, nullable=False)
    approved_by = Column(String, nullable=False)
    approved_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    is_replay_mode = Column(Boolean, nullable=False)
    line_items_snapshot_json = Column(Text, nullable=False)

    draft = relationship("OrderDraft", back_populates="verified_order")


class AuditEvent(Base):
    """Append-only audit event recording lifecycle interactions."""

    __tablename__ = "audit_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    draft_id = Column(String, ForeignKey("order_drafts.id"), nullable=False)
    event_type = Column(String, nullable=False)
    actor = Column(String, nullable=False)
    details_json = Column(Text, nullable=False)
    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    draft = relationship("OrderDraft", back_populates="audit_events")
