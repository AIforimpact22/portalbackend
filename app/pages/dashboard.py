# app/pages/dashboard.py
from datetime import date
from sqlalchemy import select, func

from ..core import get_db, csrf_token, render
from ..models import Payment, Expense, Invoice
from ..accounting import ensure_company, dec, vat_summary

def register(app):
    @app.route("/", endpoint="dashboard")
    def dashboard():
        db = get_db(); company = ensure_company(db)
        today = date.today()
        ytd_income = db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.date >= date(today.year,1,1))
        ).scalar_one()
        ytd_expenses = db.execute(
            select(func.coalesce(func.sum(Expense.amount_gross), 0)).where(Expense.date >= date(today.year,1,1))
        ).scalar_one()

        vat = vat_summary(db, today.year, ((today.month - 1)//3)+1)
        recent_invoices = db.execute(select(Invoice).order_by(Invoice.issue_date.desc()).limit(6)).scalars().all()
        recent_expenses = db.execute(select(Expense).order_by(Expense.date.desc()).limit(6)).scalars().all()
        recent_payments = db.execute(select(Payment).order_by(Payment.date.desc()).limit(6)).scalars().all()

        return render("dashboard.html",
            csrf_token=csrf_token(), company=company,
            ytd_income=dec(ytd_income), ytd_expenses=dec(ytd_expenses),
            **vat, recent_invoices=recent_invoices, recent_expenses=recent_expenses, recent_payments=recent_payments
        )
