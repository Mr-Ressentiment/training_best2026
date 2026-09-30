"""Command-line interface for OrderShield database management and seeding.

Provides deterministic database schema initialization and baseline catalog / customer
contract seeding commands conforming to T011:
- python -m app.cli init-db
- python -m app.cli init-db --seed
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import sys
from typing import Sequence

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine
import app.models.entities  # noqa: F401 - register all ORM models with Base.metadata
from app.models.entities import CatalogProduct, ContractPriceTier, CustomerContract


class SeedError(Exception):
    """Base exception for database seeding operations."""


class SeedConflictError(SeedError):
    """Raised when existing database state conflicts with baseline seed definitions."""


@dataclass(frozen=True)
class SeedProduct:
    """Immutable definition of a baseline catalog product."""

    sku: str
    name: str
    category: str
    unit_of_measure: str
    base_price_cents: int
    min_order_quantity: int
    package_increment: int


@dataclass(frozen=True)
class SeedContract:
    """Immutable definition of a baseline customer contract."""

    id: str
    customer_id: str
    customer_name: str
    valid_from: date
    valid_to: date


@dataclass(frozen=True)
class SeedTier:
    """Immutable definition of a quantity-tiered contract pricing rule."""

    id: str
    contract_id: str
    sku: str
    min_quantity: int
    tier_price_cents: int


@dataclass(frozen=True)
class SeedSummary:
    """Summary counts for baseline seeding operations."""

    products_seeded: int
    contracts_seeded: int
    tiers_seeded: int


# -----------------------------------------------------------------------------
# Deterministic Baseline Datasets
# -----------------------------------------------------------------------------

BASELINE_PRODUCTS: tuple[SeedProduct, ...] = (
    # Product A - frozen core attributes required by API contracts & fixtures
    SeedProduct(
        sku="SKU-WRAP-18",
        name="Industrial Stretch Film 18in 80ga",
        category="Packaging",
        unit_of_measure="Roll",
        base_price_cents=2450,
        min_order_quantity=5,
        package_increment=1,
    ),
    # Product B - frozen core attributes required by API contracts & fixtures
    SeedProduct(
        sku="SKU-WRAP-15",
        name="Standard Pallet Wrap 15in 65ga",
        category="Packaging",
        unit_of_measure="Case",
        base_price_cents=2000,
        min_order_quantity=5,
        package_increment=1,
    ),
    # Additional representative wholesale products
    SeedProduct(
        sku="SKU-TAPE-02",
        name="Heavy Duty Packaging Tape 2in x 110yd",
        category="Packaging",
        unit_of_measure="Roll",
        base_price_cents=350,
        min_order_quantity=6,
        package_increment=6,
    ),
    SeedProduct(
        sku="SKU-TAPE-03",
        name="Industrial Filament Strapping Tape 3in",
        category="Packaging",
        unit_of_measure="Roll",
        base_price_cents=850,
        min_order_quantity=4,
        package_increment=2,
    ),
    SeedProduct(
        sku="SKU-BOX-MED",
        name="Corrugated Shipping Box 16x12x12in",
        category="Shipping",
        unit_of_measure="Bundle",
        base_price_cents=3200,
        min_order_quantity=1,
        package_increment=1,
    ),
    SeedProduct(
        sku="SKU-BOX-LRG",
        name="Heavy Duty Corrugated Box 24x18x18in",
        category="Shipping",
        unit_of_measure="Bundle",
        base_price_cents=4800,
        min_order_quantity=1,
        package_increment=1,
    ),
    SeedProduct(
        sku="SKU-LBL-THERM",
        name="Direct Thermal Shipping Labels 4x6in",
        category="Shipping",
        unit_of_measure="Roll",
        base_price_cents=1450,
        min_order_quantity=2,
        package_increment=2,
    ),
    SeedProduct(
        sku="SKU-STRAP-POLY",
        name="Polypropylene Strapping Roll 1/2in x 9000ft",
        category="Warehouse",
        unit_of_measure="Coil",
        base_price_cents=6500,
        min_order_quantity=1,
        package_increment=1,
    ),
    SeedProduct(
        sku="SKU-GLOVE-NIT",
        name="Heavy Duty Nitrile Work Gloves Large",
        category="Safety",
        unit_of_measure="Box",
        base_price_cents=1200,
        min_order_quantity=10,
        package_increment=5,
    ),
    SeedProduct(
        sku="SKU-VEST-HI",
        name="High Visibility Safety Vest ANSI Class 2",
        category="Safety",
        unit_of_measure="Each",
        base_price_cents=950,
        min_order_quantity=5,
        package_increment=1,
    ),
    SeedProduct(
        sku="SKU-CUTTER-IND",
        name="Industrial Safety Box Cutter with Holster",
        category="Warehouse",
        unit_of_measure="Each",
        base_price_cents=725,
        min_order_quantity=2,
        package_increment=1,
    ),
    SeedProduct(
        sku="SKU-BUBBLE-MED",
        name="Perforated Bubble Cushioning Wrap 12in x 175ft",
        category="Packaging",
        unit_of_measure="Roll",
        base_price_cents=2800,
        min_order_quantity=1,
        package_increment=1,
    ),
)

BASELINE_CONTRACTS: tuple[SeedContract, ...] = (
    SeedContract(
        id="CONTRACT-ACME-2026",
        customer_id="CUST-ACME",
        customer_name="Acme Industrial Supplies",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    ),
    SeedContract(
        id="CONTRACT-APEX-2026",
        customer_id="CUST-APEX",
        customer_name="Apex Distribution",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    ),
)

BASELINE_TIERS: tuple[SeedTier, ...] = (
    # -------------------------------------------------------------------------
    # ACME Contract Tiers (CONTRACT-ACME-2026)
    # -------------------------------------------------------------------------
    # SKU-WRAP-18: Q=10 -> 2500 cents ($25.00) clean fixture match
    SeedTier(
        id="TIER-ACME-WRAP18-Q1",
        contract_id="CONTRACT-ACME-2026",
        sku="SKU-WRAP-18",
        min_quantity=1,
        tier_price_cents=2600,
    ),
    SeedTier(
        id="TIER-ACME-WRAP18-Q10",
        contract_id="CONTRACT-ACME-2026",
        sku="SKU-WRAP-18",
        min_quantity=10,
        tier_price_cents=2500,
    ),
    SeedTier(
        id="TIER-ACME-WRAP18-Q50",
        contract_id="CONTRACT-ACME-2026",
        sku="SKU-WRAP-18",
        min_quantity=50,
        tier_price_cents=2300,
    ),
    # SKU-WRAP-15: Q=5 -> 2000 cents ($20.00) clean fixture match
    SeedTier(
        id="TIER-ACME-WRAP15-Q1",
        contract_id="CONTRACT-ACME-2026",
        sku="SKU-WRAP-15",
        min_quantity=1,
        tier_price_cents=2100,
    ),
    SeedTier(
        id="TIER-ACME-WRAP15-Q5",
        contract_id="CONTRACT-ACME-2026",
        sku="SKU-WRAP-15",
        min_quantity=5,
        tier_price_cents=2000,
    ),
    SeedTier(
        id="TIER-ACME-WRAP15-Q25",
        contract_id="CONTRACT-ACME-2026",
        sku="SKU-WRAP-15",
        min_quantity=25,
        tier_price_cents=1850,
    ),
    # -------------------------------------------------------------------------
    # APEX Contract Tiers (CONTRACT-APEX-2026)
    # -------------------------------------------------------------------------
    # SKU-WRAP-18: Q=10 -> 2200 cents ($22.00) discrepancy fixture match ($18 requested vs $22 contract)
    SeedTier(
        id="TIER-APEX-WRAP18-Q1",
        contract_id="CONTRACT-APEX-2026",
        sku="SKU-WRAP-18",
        min_quantity=1,
        tier_price_cents=2400,
    ),
    SeedTier(
        id="TIER-APEX-WRAP18-Q10",
        contract_id="CONTRACT-APEX-2026",
        sku="SKU-WRAP-18",
        min_quantity=10,
        tier_price_cents=2200,
    ),
    SeedTier(
        id="TIER-APEX-WRAP18-Q50",
        contract_id="CONTRACT-APEX-2026",
        sku="SKU-WRAP-18",
        min_quantity=50,
        tier_price_cents=2050,
    ),
    # SKU-WRAP-15: Q=10 -> 2000 cents ($20.00) ambiguous fixture match; Q=2 -> 2000 cents discrepancy fixture
    SeedTier(
        id="TIER-APEX-WRAP15-Q1",
        contract_id="CONTRACT-APEX-2026",
        sku="SKU-WRAP-15",
        min_quantity=1,
        tier_price_cents=2000,
    ),
    SeedTier(
        id="TIER-APEX-WRAP15-Q20",
        contract_id="CONTRACT-APEX-2026",
        sku="SKU-WRAP-15",
        min_quantity=20,
        tier_price_cents=1850,
    ),
)


# -----------------------------------------------------------------------------
# Schema Initialization & Baseline Seeding
# -----------------------------------------------------------------------------

def init_database(bind_engine: Engine | None = None) -> None:
    """Initialize database tables using SQLAlchemy metadata.

    Imports domain models to ensure all tables are registered with Base.metadata,
    then executes create_all. Does not destroy or re-create existing tables or data.
    """
    target_engine = bind_engine if bind_engine is not None else engine
    Base.metadata.create_all(bind=target_engine)


def seed_baseline(
    session: Session,
    *,
    products: Sequence[SeedProduct] = BASELINE_PRODUCTS,
    contracts: Sequence[SeedContract] = BASELINE_CONTRACTS,
    tiers: Sequence[SeedTier] = BASELINE_TIERS,
) -> SeedSummary:
    """Atomically upsert the deterministic baseline seed dataset.

    Idempotent and safe to run repeatedly:
    - Products, contracts, and tiers use deterministic primary keys.
    - Missing seed rows are inserted.
    - Existing seed rows are updated/retained with deterministic baseline attributes.
    - Unrelated pre-existing application rows are preserved.
    - If another non-seed contract exists for a seeded demo customer, fails explicitly
      by raising SeedConflictError without modifying existing rows.

    Note: The caller is responsible for transaction commitment or rollback.
    """
    if not isinstance(session, Session):
        raise TypeError(f"session must be a sqlalchemy.orm.Session, got {type(session).__name__}")

    # 1. Validate customer contract conflicts before making changes
    for contract_def in contracts:
        conflicting_contracts = (
            session.query(CustomerContract)
            .filter(
                CustomerContract.customer_id == contract_def.customer_id,
                CustomerContract.id != contract_def.id,
            )
            .all()
        )
        if conflicting_contracts:
            conflicting_ids = ", ".join(sorted(c.id for c in conflicting_contracts))
            raise SeedConflictError(
                f"Conflicting contract(s) [{conflicting_ids}] already exist for customer "
                f"'{contract_def.customer_id}'. Seeding aborted to prevent multi-contract "
                f"ambiguity with seed contract '{contract_def.id}'."
            )

    # 2. Upsert deterministic catalog products
    for prod_def in products:
        existing_prod = session.get(CatalogProduct, prod_def.sku)
        if existing_prod is None:
            new_prod = CatalogProduct(
                sku=prod_def.sku,
                name=prod_def.name,
                category=prod_def.category,
                unit_of_measure=prod_def.unit_of_measure,
                base_price_cents=prod_def.base_price_cents,
                min_order_quantity=prod_def.min_order_quantity,
                package_increment=prod_def.package_increment,
            )
            session.add(new_prod)
        else:
            existing_prod.name = prod_def.name
            existing_prod.category = prod_def.category
            existing_prod.unit_of_measure = prod_def.unit_of_measure
            existing_prod.base_price_cents = prod_def.base_price_cents
            existing_prod.min_order_quantity = prod_def.min_order_quantity
            existing_prod.package_increment = prod_def.package_increment

    # Flush products so foreign key constraints on tiers will be satisfied
    session.flush()

    # 3. Upsert deterministic demo customer contracts
    for contract_def in contracts:
        existing_contract = session.get(CustomerContract, contract_def.id)
        if existing_contract is None:
            new_contract = CustomerContract(
                id=contract_def.id,
                customer_id=contract_def.customer_id,
                customer_name=contract_def.customer_name,
                valid_from=contract_def.valid_from,
                valid_to=contract_def.valid_to,
            )
            session.add(new_contract)
        else:
            existing_contract.customer_id = contract_def.customer_id
            existing_contract.customer_name = contract_def.customer_name
            existing_contract.valid_from = contract_def.valid_from
            existing_contract.valid_to = contract_def.valid_to

    # Flush contracts so foreign key constraints on tiers will be satisfied
    session.flush()

    # 4. Upsert deterministic contract pricing tiers
    for tier_def in tiers:
        # Check for conflicting tiers at the same threshold with a different ID
        conflicting_tier = (
            session.query(ContractPriceTier)
            .filter(
                ContractPriceTier.contract_id == tier_def.contract_id,
                ContractPriceTier.sku == tier_def.sku,
                ContractPriceTier.min_quantity == tier_def.min_quantity,
                ContractPriceTier.id != tier_def.id,
            )
            .first()
        )
        if conflicting_tier:
            raise SeedConflictError(
                f"Conflicting pricing tier '{conflicting_tier.id}' exists for contract "
                f"'{tier_def.contract_id}', sku '{tier_def.sku}', min_quantity={tier_def.min_quantity}. "
                f"Seeding aborted to prevent tier ambiguity with seed tier '{tier_def.id}'."
            )

        existing_tier = session.get(ContractPriceTier, tier_def.id)
        if existing_tier is None:
            new_tier = ContractPriceTier(
                id=tier_def.id,
                contract_id=tier_def.contract_id,
                sku=tier_def.sku,
                min_quantity=tier_def.min_quantity,
                tier_price_cents=tier_def.tier_price_cents,
            )
            session.add(new_tier)
        else:
            existing_tier.contract_id = tier_def.contract_id
            existing_tier.sku = tier_def.sku
            existing_tier.min_quantity = tier_def.min_quantity
            existing_tier.tier_price_cents = tier_def.tier_price_cents

    session.flush()

    return SeedSummary(
        products_seeded=len(products),
        contracts_seeded=len(contracts),
        tiers_seeded=len(tiers),
    )


# -----------------------------------------------------------------------------
# CLI Parser & Main Entrypoint
# -----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser for app.cli."""
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="OrderShield CLI tools.",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        help="Sub-command to execute",
    )

    init_db_parser = subparsers.add_parser(
        "init-db",
        help="Initialize the SQLite database schema and optionally seed baseline data.",
    )
    init_db_parser.add_argument(
        "--seed",
        action="store_true",
        help="Seed deterministic baseline catalog, contracts, and pricing tiers.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI execution handler.

    Returns:
        0 on success, non-zero on failure or conflict.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init-db":
        try:
            init_database()
        except Exception as exc:
            print(f"Error initializing database schema: {exc}", file=sys.stderr)
            return 1

        if args.seed:
            session = SessionLocal()
            try:
                summary = seed_baseline(session)
                session.commit()
                print("Initialized database schema successfully.")
                print(
                    f"Seeded baseline dataset: {summary.products_seeded} products, "
                    f"{summary.contracts_seeded} customer contracts, {summary.tiers_seeded} pricing tiers."
                )
            except SeedConflictError as exc:
                session.rollback()
                print(f"Seed conflict error: {exc}", file=sys.stderr)
                return 1
            except Exception as exc:
                session.rollback()
                print(f"Error seeding database: {exc}", file=sys.stderr)
                return 1
            finally:
                session.close()
        else:
            print("Initialized database schema successfully.")

        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
