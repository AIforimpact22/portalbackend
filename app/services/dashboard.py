"""Utilities for assembling dashboard metrics in a DB-friendly way."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict

from sqlalchemy import Date, Numeric, cast, func, literal, select, case
from sqlalchemy.orm import Session

from ..accounting import dec, vat_summary
from ..models import Expense, Invoice, Payment


def _normalize_financial_entries() -> Any:
    """Return a selectable that exposes payments/expenses with consistent types."""
    payments = (
        select(
            cast(Payment.date, Date).label("entry_date"),
            cast(Payment.amount, Numeric(12, 2)).label("amount"),
            literal("income").label("kind"),
        )
    )

    expenses = (
        select(
            cast(Expense.date, Date).label("entry_date"),
            cast(Expense.amount_gross, Numeric(12, 2)).label("amount"),
            literal("expense").label("kind"),
        )
    )

    return payments.union_all(expenses).cte("financial_entries")


def load_dashboard_context(db: Session, *, today: date | None = None) -> Dict[str, Any]:
    """Collect all data required by the dashboard template.

    The production database stores monetary values and dates as legacy TEXT columns.
    To avoid `text >= date` errors (as seen on Render) we explicitly cast to
    `DATE`/`NUMERIC` for every aggregate/order-by. The combined CTE also lets us
    compute income and expense totals in a single round-trip.
    """

    today = today or date.today()
    start_of_year = date(today.year, 1, 1)

    entries = _normalize_financial_entries()

    aggregates = db.execute(
        select(
            func.coalesce(
                func.sum(
                    case((entries.c.kind == "income", entries.c.amount), else_=0)
                ),
                0,
            ).label("income"),
            func.coalesce(
                func.sum(
                    case((entries.c.kind == "expense", entries.c.amount), else_=0)
                ),
                0,
            ).label("expenses"),
        ).where(entries.c.entry_date >= start_of_year)
    ).one()

    ytd_income = dec(aggregates.income)
    ytd_expenses = dec(aggregates.expenses)

    vat = vat_summary(db, today.year, ((today.month - 1) // 3) + 1)

    recent_invoices = (
        db.execute(
            select(Invoice).order_by(Invoice.issue_date.desc(), Invoice.id.desc()).limit(6)
        )
        .scalars()
        .all()
    )

    recent_expenses = (
        db.execute(
            select(Expense)
            .order_by(cast(Expense.date, Date).desc(), Expense.id.desc())
            .limit(6)
        )
        .scalars()
        .all()
    )
    expenses_with_amounts = [(expense, dec(expense.amount_gross)) for expense in recent_expenses]

    recent_payments = (
        db.execute(
            select(Payment)
            .order_by(cast(Payment.date, Date).desc(), Payment.id.desc())
            .limit(6)
        )
        .scalars()
        .all()
    )
    payments_with_amounts = [(payment, dec(payment.amount)) for payment in recent_payments]

    return {
        "ytd_income": ytd_income,
        "ytd_expenses": ytd_expenses,
        **vat,
        "recent_invoices": recent_invoices,
        "recent_expenses": expenses_with_amounts,
        "recent_payments": payments_with_amounts,
    }
