"""Master catalog lookup API route (T030) for operator SKU resolution."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.entities import CatalogProduct
from app.models.schemas import cents_to_decimal

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])


class CatalogProductResponse(BaseModel):
    """Deterministic public catalog product response contract."""

    model_config = ConfigDict(extra="forbid")

    sku: str
    name: str
    category: str
    unit_of_measure: str
    base_price: str
    min_order_quantity: int
    package_increment: int


def _serialize_catalog_product(product: CatalogProduct) -> dict[str, str | int]:
    return {
        "sku": product.sku,
        "name": product.name,
        "category": product.category,
        "unit_of_measure": product.unit_of_measure,
        "base_price": format(cents_to_decimal(product.base_price_cents), ".2f"),
        "min_order_quantity": product.min_order_quantity,
        "package_increment": product.package_increment,
    }


@router.get("", response_model=list[CatalogProductResponse])
def list_catalog_products(
    query: str | None = Query(default=None, description="Keyword search against SKU or product name"),
    category: str | None = Query(default=None, description="Filter by product category"),
    db: Session = Depends(get_db),
) -> list[dict[str, str | int]]:
    """Search and list master catalog products with optional keyword and category filtering."""
    stmt = select(CatalogProduct)

    if query is not None:
        q = query.strip()
        if q:
            keyword_filter = or_(
                func.lower(CatalogProduct.sku).contains(q.lower()),
                func.lower(CatalogProduct.name).contains(q.lower()),
            )
            stmt = stmt.where(keyword_filter)

    if category is not None:
        cat = category.strip()
        if cat:
            stmt = stmt.where(func.lower(CatalogProduct.category) == cat.lower())

    stmt = stmt.order_by(CatalogProduct.sku.asc())
    products = db.scalars(stmt).all()
    return [_serialize_catalog_product(p) for p in products]
