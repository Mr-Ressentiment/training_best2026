"""Unit tests for SQLite database initialization and deterministic baseline seed CLI (T011)."""

from datetime import date
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.cli import (
    BASELINE_CONTRACTS,
    BASELINE_PRODUCTS,
    BASELINE_TIERS,
    SeedConflictError,
    SeedContract,
    SeedProduct,
    SeedTier,
    build_parser,
    init_database,
    main,
    seed_baseline,
)
from app.models.entities import (
    CatalogProduct,
    ContractPriceTier,
    CustomerContract,
    PurchaseOrderDocument,
)
from app.services.reconciliation import select_contract_price_tier


@pytest.fixture
def test_engine():
    """Create a clean in-memory SQLite engine with foreign keys enabled."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from sqlalchemy import event

    @event.listens_for(eng, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    return eng


@pytest.fixture
def test_session(test_engine) -> Session:
    """Create a database session bound to the test engine."""
    init_database(bind_engine=test_engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


# -----------------------------------------------------------------------------
# 1. Database Schema Initialization Tests
# -----------------------------------------------------------------------------

def test_init_database_creates_all_tables(test_engine):
    """init_database creates all required domain entity tables in SQLite."""
    init_database(bind_engine=test_engine)
    inspector = inspect(test_engine)
    table_names = set(inspector.get_table_names())

    expected_tables = {
        "catalog_products",
        "customer_contracts",
        "contract_price_tiers",
        "purchase_order_documents",
        "order_drafts",
        "draft_line_items",
        "field_provenances",
        "discrepancy_flags",
        "verified_order_records",
        "audit_events",
    }
    assert expected_tables.issubset(table_names), f"Missing tables: {expected_tables - table_names}"


def test_init_database_idempotent(test_engine):
    """Calling init_database repeatedly does not fail and preserves existing tables."""
    init_database(bind_engine=test_engine)
    init_database(bind_engine=test_engine)
    inspector = inspect(test_engine)
    assert "catalog_products" in inspector.get_table_names()


def test_init_db_without_seed_leaves_tables_empty(test_session: Session):
    """init-db without --seed leaves tables completely empty."""
    assert test_session.query(CatalogProduct).count() == 0
    assert test_session.query(CustomerContract).count() == 0
    assert test_session.query(ContractPriceTier).count() == 0


# -----------------------------------------------------------------------------
# 2. Baseline Dataset Specifications Tests
# -----------------------------------------------------------------------------

def test_baseline_dataset_counts():
    """Baseline dataset satisfies size boundaries: 10 <= products <= 30, 2 contracts, >= 10 tiers."""
    assert 10 <= len(BASELINE_PRODUCTS) <= 30
    assert len(BASELINE_CONTRACTS) == 2
    assert len(BASELINE_TIERS) >= 10


def test_baseline_frozen_products():
    """Products A and B have exact required frozen attributes matching API contracts."""
    products_by_sku = {p.sku: p for p in BASELINE_PRODUCTS}

    # Product A
    assert "SKU-WRAP-18" in products_by_sku
    wrap18 = products_by_sku["SKU-WRAP-18"]
    assert wrap18.name == "Industrial Stretch Film 18in 80ga"
    assert wrap18.category == "Packaging"
    assert wrap18.unit_of_measure == "Roll"
    assert wrap18.base_price_cents == 2450
    assert wrap18.min_order_quantity == 5
    assert wrap18.package_increment == 1

    # Product B
    assert "SKU-WRAP-15" in products_by_sku
    wrap15 = products_by_sku["SKU-WRAP-15"]
    assert wrap15.name == "Standard Pallet Wrap 15in 65ga"
    assert wrap15.category == "Packaging"
    assert wrap15.unit_of_measure == "Case"
    assert wrap15.base_price_cents == 2000
    assert wrap15.min_order_quantity == 5
    assert wrap15.package_increment == 1


def test_baseline_products_money_and_constraints():
    """All baseline products have exact integer cents, MOQ >= 1, package_increment >= 1."""
    for prod in BASELINE_PRODUCTS:
        assert isinstance(prod.base_price_cents, int)
        assert not isinstance(prod.base_price_cents, bool)
        assert prod.base_price_cents >= 0
        assert prod.min_order_quantity >= 1
        assert prod.package_increment >= 1
        assert len(prod.sku) > 0
        assert len(prod.name) > 0


def test_baseline_contracts_validity():
    """Baseline contracts cover committed synthetic PO date 2026-09-29."""
    committed_date = date(2026, 9, 29)
    contracts_by_cust = {c.customer_id: c for c in BASELINE_CONTRACTS}

    assert "CUST-ACME" in contracts_by_cust
    acme_contract = contracts_by_cust["CUST-ACME"]
    assert acme_contract.id == "CONTRACT-ACME-2026"
    assert acme_contract.customer_name == "Acme Industrial Supplies"
    assert acme_contract.valid_from <= committed_date <= acme_contract.valid_to

    assert "CUST-APEX" in contracts_by_cust
    apex_contract = contracts_by_cust["CUST-APEX"]
    assert apex_contract.id == "CONTRACT-APEX-2026"
    assert apex_contract.customer_name == "Apex Distribution"
    assert apex_contract.valid_from <= committed_date <= apex_contract.valid_to


def test_baseline_tiers_money_and_constraints():
    """All baseline tiers have exact integer cents, min_quantity >= 1, tier_price_cents >= 0."""
    for tier in BASELINE_TIERS:
        assert isinstance(tier.tier_price_cents, int)
        assert not isinstance(tier.tier_price_cents, bool)
        assert tier.tier_price_cents >= 0
        assert tier.min_quantity >= 1
        assert tier.id.startswith("TIER-")


# -----------------------------------------------------------------------------
# 3. Seed Execution & Deterministic Tier Pricing Tests
# -----------------------------------------------------------------------------

def test_seed_baseline_populates_database(test_session: Session):
    """seed_baseline inserts all baseline products, contracts, and tiers."""
    summary = seed_baseline(test_session)
    test_session.commit()

    assert summary.products_seeded == len(BASELINE_PRODUCTS)
    assert summary.contracts_seeded == len(BASELINE_CONTRACTS)
    assert summary.tiers_seeded == len(BASELINE_TIERS)

    assert test_session.query(CatalogProduct).count() == len(BASELINE_PRODUCTS)
    assert test_session.query(CustomerContract).count() == len(BASELINE_CONTRACTS)
    assert test_session.query(ContractPriceTier).count() == len(BASELINE_TIERS)


def test_deterministic_pricing_resolution(test_session: Session):
    """Seeded baseline pricing satisfies all required demo lookups from spec & fixtures."""
    seed_baseline(test_session)
    test_session.commit()

    # ACME clean fixture lookups
    tier_acme_18_10 = select_contract_price_tier(test_session, "CUST-ACME", "SKU-WRAP-18", 10)
    assert tier_acme_18_10 is not None
    assert tier_acme_18_10.tier_price_cents == 2500

    tier_acme_15_5 = select_contract_price_tier(test_session, "CUST-ACME", "SKU-WRAP-15", 5)
    assert tier_acme_15_5 is not None
    assert tier_acme_15_5.tier_price_cents == 2000

    # APEX discrepancy fixture lookups ($18 requested vs $22 contract)
    tier_apex_18_10 = select_contract_price_tier(test_session, "CUST-APEX", "SKU-WRAP-18", 10)
    assert tier_apex_18_10 is not None
    assert tier_apex_18_10.tier_price_cents == 2200

    # APEX ambiguous fixture lookup (Q=10 -> 2000 cents)
    tier_apex_15_10 = select_contract_price_tier(test_session, "CUST-APEX", "SKU-WRAP-15", 10)
    assert tier_apex_15_10 is not None
    assert tier_apex_15_10.tier_price_cents == 2000

    # APEX discrepancy line 2 lookup (Q=2 -> 2000 cents)
    tier_apex_15_2 = select_contract_price_tier(test_session, "CUST-APEX", "SKU-WRAP-15", 2)
    assert tier_apex_15_2 is not None
    assert tier_apex_15_2.tier_price_cents == 2000


# -----------------------------------------------------------------------------
# 4. Critical Idempotency & Repeatability Tests
# -----------------------------------------------------------------------------

def test_seed_baseline_is_strictly_idempotent(test_session: Session):
    """Running seed_baseline twice produces identical row counts and no duplicated entities."""
    summary1 = seed_baseline(test_session)
    test_session.commit()

    summary2 = seed_baseline(test_session)
    test_session.commit()

    assert summary1 == summary2

    # Row counts must be identical, not doubled
    assert test_session.query(CatalogProduct).count() == len(BASELINE_PRODUCTS)
    assert test_session.query(CustomerContract).count() == len(BASELINE_CONTRACTS)
    assert test_session.query(ContractPriceTier).count() == len(BASELINE_TIERS)

    # T010 lookups still succeed without multi-contract ambiguity
    tier = select_contract_price_tier(test_session, "CUST-ACME", "SKU-WRAP-18", 10)
    assert tier is not None
    assert tier.tier_price_cents == 2500


def test_seed_preserves_unrelated_application_data(test_session: Session):
    """seed_baseline preserves existing products, contracts, and documents."""
    # Insert unrelated application data
    unrelated_product = CatalogProduct(
        sku="SKU-CUSTOM-99",
        name="Custom Client Part",
        category="Custom",
        unit_of_measure="Each",
        base_price_cents=9999,
        min_order_quantity=1,
        package_increment=1,
    )
    unrelated_contract = CustomerContract(
        id="CONTRACT-CUSTOM-BETA",
        customer_id="CUST-BETA",
        customer_name="Beta Logistics",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )
    unrelated_doc = PurchaseOrderDocument(
        id="DOC-USER-1",
        filename="custom_po.txt",
        content_type="text/plain",
        raw_text="Customer PO text",
        status="Ingested",
    )
    test_session.add_all([unrelated_product, unrelated_contract, unrelated_doc])
    test_session.commit()

    # Run seed
    seed_baseline(test_session)
    test_session.commit()

    # Verify unrelated data remains untouched
    assert test_session.get(CatalogProduct, "SKU-CUSTOM-99") is not None
    assert test_session.get(CustomerContract, "CONTRACT-CUSTOM-BETA") is not None
    assert test_session.get(PurchaseOrderDocument, "DOC-USER-1") is not None

    # Total counts are seed counts + 1
    assert test_session.query(CatalogProduct).count() == len(BASELINE_PRODUCTS) + 1
    assert test_session.query(CustomerContract).count() == len(BASELINE_CONTRACTS) + 1


# -----------------------------------------------------------------------------
# 5. Fail-Closed Conflict Detection Tests
# -----------------------------------------------------------------------------

def test_seed_fails_closed_on_conflicting_demo_customer_contract(test_session: Session):
    """If an existing non-seed contract exists for CUST-ACME, seed fails explicitly without modifying DB."""
    # Pre-existing non-seed contract for CUST-ACME
    conflicting_contract = CustomerContract(
        id="CONTRACT-ACME-LEGACY",
        customer_id="CUST-ACME",
        customer_name="Acme Old Entity",
        valid_from=date(2025, 1, 1),
        valid_to=date(2025, 12, 31),
    )
    test_session.add(conflicting_contract)
    test_session.commit()

    with pytest.raises(SeedConflictError, match="Conflicting contract.*CUST-ACME"):
        seed_baseline(test_session)

    test_session.rollback()

    # Conflicting contract must still exist and no seed contract was inserted
    assert test_session.get(CustomerContract, "CONTRACT-ACME-LEGACY") is not None
    assert test_session.get(CustomerContract, "CONTRACT-ACME-2026") is None


def test_seed_fails_closed_when_conflicting_contract_added_between_seeds(test_session: Session):
    """Seed, manually insert conflicting second contract for CUST-ACME, rerun seed fails explicitly."""
    # Initial clean seed
    seed_baseline(test_session)
    test_session.commit()

    # Manually insert unrelated second contract for CUST-ACME
    manual_contract = CustomerContract(
        id="CONTRACT-ACME-MANUAL",
        customer_id="CUST-ACME",
        customer_name="Acme Manual Branch",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )
    test_session.add(manual_contract)
    test_session.commit()

    # Second seed must fail closed
    with pytest.raises(SeedConflictError, match="Conflicting contract.*CUST-ACME"):
        seed_baseline(test_session)

    test_session.rollback()

    # Manual contract is preserved
    assert test_session.get(CustomerContract, "CONTRACT-ACME-MANUAL") is not None


def test_seed_fails_closed_on_conflicting_tier_threshold(test_session: Session):
    """If an existing tier on the contract has the same threshold under a different ID, seed fails closed."""
    # Create products and contracts
    init_database(bind_engine=test_session.bind)
    seed_baseline(test_session)
    test_session.commit()

    # Manually add conflicting tier at min_quantity=10 with different ID
    conflicting_tier = ContractPriceTier(
        id="TIER-MANUAL-WRAP18-Q10",
        contract_id="CONTRACT-ACME-2026",
        sku="SKU-WRAP-18",
        min_quantity=10,
        tier_price_cents=9999,
    )
    test_session.add(conflicting_tier)
    test_session.commit()

    # Rerun seed should detect conflicting tier
    with pytest.raises(SeedConflictError, match="Conflicting pricing tier"):
        seed_baseline(test_session)

    test_session.rollback()


# -----------------------------------------------------------------------------
# 6. CLI Entrypoint & Parser Tests
# -----------------------------------------------------------------------------

def test_build_parser_options():
    """CLI parser accepts init-db and --seed flags."""
    parser = build_parser()

    args_no_seed = parser.parse_args(["init-db"])
    assert args_no_seed.command == "init-db"
    assert args_no_seed.seed is False

    args_with_seed = parser.parse_args(["init-db", "--seed"])
    assert args_with_seed.command == "init-db"
    assert args_with_seed.seed is True


def test_main_cli_execution_with_custom_env(tmp_path):
    """Execute python -m app.cli in a clean subprocess with temporary DATABASE_URL."""
    import os

    db_file = tmp_path / "test_cli_smoke.db"
    db_url = f"sqlite:///{db_file}"
    sub_env = {**os.environ, "DATABASE_URL": db_url, "PYTHONPATH": "."}

    # 1. Run init-db without --seed
    res1 = subprocess.run(
        [sys.executable, "-m", "app.cli", "init-db"],
        env=sub_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res1.returncode == 0
    assert "Initialized database schema successfully." in res1.stdout

    # Verify tables exist but empty
    eng = create_engine(db_url)
    with Session(eng) as s:
        assert s.query(CatalogProduct).count() == 0
    eng.dispose()

    # 2. Run init-db --seed
    res2 = subprocess.run(
        [sys.executable, "-m", "app.cli", "init-db", "--seed"],
        env=sub_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res2.returncode == 0
    assert "Seeded baseline dataset" in res2.stdout

    # Verify rows seeded
    eng = create_engine(db_url)
    with Session(eng) as s:
        assert s.query(CatalogProduct).count() == len(BASELINE_PRODUCTS)
        assert s.query(CustomerContract).count() == len(BASELINE_CONTRACTS)
        assert s.query(ContractPriceTier).count() == len(BASELINE_TIERS)
    eng.dispose()

    # 3. Rerun init-db --seed (Idempotency)
    res3 = subprocess.run(
        [sys.executable, "-m", "app.cli", "init-db", "--seed"],
        env=sub_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res3.returncode == 0

    # Counts remain unchanged
    eng = create_engine(db_url)
    with Session(eng) as s:
        assert s.query(CatalogProduct).count() == len(BASELINE_PRODUCTS)
        assert s.query(CustomerContract).count() == len(BASELINE_CONTRACTS)
        assert s.query(ContractPriceTier).count() == len(BASELINE_TIERS)

        # Verify ACME and APEX lookups
        t_acme = select_contract_price_tier(s, "CUST-ACME", "SKU-WRAP-18", 10)
        assert t_acme.tier_price_cents == 2500
        t_apex = select_contract_price_tier(s, "CUST-APEX", "SKU-WRAP-18", 10)
        assert t_apex.tier_price_cents == 2200

        # Now insert conflicting contract and test subprocess failure
        s.add(CustomerContract(
            id="CONTRACT-ACME-MANUAL-SUBPROCESS",
            customer_id="CUST-ACME",
            customer_name="Acme Duplicate",
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
        ))
        s.commit()
    eng.dispose()

    # 4. Rerun init-db --seed with conflicting contract
    res4 = subprocess.run(
        [sys.executable, "-m", "app.cli", "init-db", "--seed"],
        env=sub_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res4.returncode == 1
    assert "Seed conflict error" in res4.stderr
