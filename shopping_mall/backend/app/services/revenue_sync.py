"""Sync orders into revenue_entries table."""
from sqlalchemy.orm import Session

from app.core.datetime_utils import now_kst
from app.models.order import Order, OrderItem
from app.models.revenue import RevenueEntry


def _order_date(order: Order) -> str:
    return order.created_at.strftime("%Y-%m-%d") if order.created_at else now_kst().date().isoformat()


def _entry_exists(db: Session, order_id: int, category: str) -> bool:
    return (
        db.query(RevenueEntry.id)
        .filter(
            RevenueEntry.order_id == order_id,
            RevenueEntry.category == category,
        )
        .first()
        is not None
    )


def create_order_revenue_entries(db: Session, order: Order) -> int:
    """Create sales revenue entries for a newly paid order."""
    if _entry_exists(db, order.id, "sales"):
        return 0

    items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
    count = 0
    for item in items:
        db.add(
            RevenueEntry(
                date=_order_date(order),
                order_id=order.id,
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=item.price // item.quantity if item.quantity else item.price,
                total_amount=item.price,
                category="sales",
            )
        )
        count += 1
    return count


def create_refund_revenue_entries(db: Session, order: Order) -> int:
    """Create negative revenue entries once when an order is cancelled or returned."""
    if _entry_exists(db, order.id, "refund"):
        return 0

    items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
    count = 0
    for item in items:
        db.add(
            RevenueEntry(
                date=_order_date(order),
                order_id=order.id,
                product_id=item.product_id,
                quantity=-item.quantity,
                unit_price=item.price // item.quantity if item.quantity else item.price,
                total_amount=-item.price,
                category="refund",
            )
        )
        count += 1
    return count


def sync_orders_to_revenue(db: Session) -> int:
    """Find orders not yet in revenue_entries and create entries. Returns count of new entries."""
    # Get order IDs already synced
    synced_order_ids = {
        row[0]
        for row in db.query(RevenueEntry.order_id)
        .filter(RevenueEntry.order_id.isnot(None))
        .all()
    }

    # Bank-transfer orders are treated as paid immediately, so sales can be
    # recovered from any paid-or-later order that does not already have entries.
    orders = (
        db.query(Order)
        .filter(
            Order.status.in_(("paid", "preparing", "shipped", "delivered")),
            Order.id.notin_(synced_order_ids) if synced_order_ids else True,
        )
        .all()
    )

    count = 0
    for order in orders:
        if order.id in synced_order_ids:
            continue

        count += create_order_revenue_entries(db, order)

    if count > 0:
        db.commit()
    return count
