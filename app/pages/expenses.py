# app/pages/expenses.py
import os
from datetime import date, datetime
from flask import request, redirect, url_for, flash
from sqlalchemy import select
from werkzeug.utils import secure_filename

from ..core import get_db, csrf_token, require_csrf, render
from ..models import Expense
from ..accounting import ensure_company, dec

def register(app):
    @app.route("/expenses", endpoint="expenses_list")
    def expenses_list():
        db = get_db(); company = ensure_company(db)
        expenses = db.execute(select(Expense).order_by(Expense.date.desc())).scalars().all()
        return render("expenses_list.html", csrf_token=csrf_token(), company=company, expenses=expenses)

    @app.route("/expenses/new", endpoint="expense_new_page")
    def expense_new_page():
        db = get_db(); company = ensure_company(db)
        return render("expense_new.html", csrf_token=csrf_token(), company=company)

    @app.route("/expense/add", methods=["POST"], endpoint="add_expense")
    def add_expense():
        db = get_db()
        try:
            require_csrf(request.form.get("csrf_token",""))
            exp_date = datetime.strptime(request.form.get("date",""), "%Y-%m-%d").date() if request.form.get("date") else date.today()
            vendor = (request.form.get("vendor","") or "").strip()
            category = (request.form.get("category","General") or "General").strip()
            description = (request.form.get("description","") or "").strip()
            currency = (request.form.get("currency","EUR") or "EUR").strip().upper()
            amount_gross = dec(request.form.get("amount_gross"))
            vat_rate = dec(request.form.get("vat_rate","21"))

            if vat_rate <= dec("0"):
                amount_net = amount_gross; vat_amount = dec("0")
            else:
                amount_net = (amount_gross / (dec("1") + vat_rate/dec("100"))).quantize(dec("0.01"))
                vat_amount = (amount_gross - amount_net).quantize(dec("0.01"))

            receipt_path = None
            file = request.files.get("receipt")
            if file and file.filename:
                fname = secure_filename(file.filename)
                ts = datetime.now().strftime("%Y%m%d%H%M%S")
                base, ext = os.path.splitext(fname)
                fname = f"{base}_{ts}{ext}".replace(" ", "_")
                file.save(os.path.join(app.config["UPLOAD_FOLDER"], fname))
                receipt_path = fname

            db.add(Expense(
                date=exp_date, vendor=vendor, category=category, description=description, currency=currency,
                vat_rate=vat_rate, amount_net=amount_net, vat_amount=vat_amount, amount_gross=amount_gross,
                receipt_path=receipt_path
            ))
            db.commit(); flash("Expense saved.", "success")
        except Exception as e:
            db.rollback(); flash(f"Expense error: {e}", "danger")
        return redirect(url_for("expense_new_page"))
