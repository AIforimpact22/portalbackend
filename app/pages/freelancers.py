# app/pages/freelancers.py
import os
from datetime import date, datetime
from flask import request, redirect, url_for, flash
from sqlalchemy import select
from werkzeug.utils import secure_filename

from ..core import get_db, csrf_token, require_csrf, render
from ..models import Expense, Invoice
from ..accounting import ensure_company, dec

def register(app):
    @app.route("/freelancers", endpoint="freelancers_list")
    def freelancers_list():
        db = get_db(); company = ensure_company(db)
        q = select(Expense).where(Expense.category.in_(["Freelancer", "Subcontractor"])).order_by(Expense.date.desc())
        payouts = db.execute(q).scalars().all()

        inv_ids = {p.invoice_id for p in payouts if p.invoice_id}
        invoices = {}
        if inv_ids:
            rows = db.execute(select(Invoice).where(Invoice.id.in_(inv_ids))).scalars().all()
            invoices = {i.id: i for i in rows}

        return render("freelancers_list.html",
                      csrf_token=csrf_token(), company=company,
                      payouts=payouts, invoices=invoices)

    @app.route("/freelancers/new", endpoint="freelancer_new_page")
    def freelancer_new_page():
        db = get_db(); company = ensure_company(db)
        invoice_id = request.args.get("invoice_id", type=int)
        invoices = db.execute(select(Invoice).order_by(Invoice.issue_date.desc()).limit(50)).scalars().all()
        return render("freelancer_new.html",
                      csrf_token=csrf_token(), company=company,
                      invoices=invoices, invoice_id=invoice_id)

    @app.route("/freelancers/add", methods=["POST"], endpoint="add_freelancer_payout")
    def add_freelancer_payout():
        db = get_db()
        try:
            require_csrf(request.form.get("csrf_token",""))

            exp_date = datetime.strptime(request.form.get("date",""), "%Y-%m-%d").date() if request.form.get("date") else date.today()
            vendor = (request.form.get("freelancer_name","") or "").strip()
            invoice_id = request.form.get("invoice_id", type=int)
            amount_gross = dec(request.form.get("amount_gross"))
            vat_rate = dec(request.form.get("vat_rate","0"))  # typically 0% for non‑EU freelancers
            currency = (request.form.get("currency","EUR") or "EUR").strip().upper()
            pay_method = (request.form.get("method","bank") or "bank").strip()
            pay_reference = (request.form.get("reference","") or "").strip()
            notes = (request.form.get("notes","") or "").strip()

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
                date=exp_date,
                vendor=vendor,
                category="Freelancer",
                description=notes,
                currency=currency,
                vat_rate=vat_rate,
                amount_net=amount_net,
                vat_amount=vat_amount,
                amount_gross=amount_gross,
                receipt_path=receipt_path,
                invoice_id=invoice_id,
                pay_method=pay_method,
                pay_reference=pay_reference
            ))
            db.commit()
            flash("Freelancer payout saved.", "success")
            if invoice_id:
                return redirect(url_for("invoice_detail", invoice_id=invoice_id))
            return redirect(url_for("freelancers_list"))
        except Exception as e:
            db.rollback()
            flash(f"Payout error: {e}", "danger")
            if request.form.get("invoice_id"):
                return redirect(url_for("freelancer_new_page", invoice_id=request.form.get("invoice_id")))
            return redirect(url_for("freelancer_new_page"))
