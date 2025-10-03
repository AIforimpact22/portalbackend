# app/pages/income.py
from datetime import date, datetime
from decimal import Decimal
from flask import request, redirect, url_for, flash, jsonify
from sqlalchemy import select, or_, func

from ..core import get_db, csrf_token, require_csrf, render
from ..models import Payment, Invoice
from ..accounting import ensure_company, dec


def _sum_payments(db, invoice_id: int) -> Decimal:
    total = db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.invoice_id == invoice_id)
    ).scalar() or Decimal("0.00")
    return Decimal(total).quantize(Decimal("0.01"))


def _invoice_outstanding(db, inv: Invoice) -> Decimal:
    gross = Decimal(inv.gross_total or 0).quantize(Decimal("0.01"))
    paid = _sum_payments(db, inv.id)
    out = (gross - paid).quantize(Decimal("0.01"))
    # Avoid negative due to rounding
    if out < Decimal("0.00"):
        out = Decimal("0.00")
    return out


def register(app):
    @app.route("/income/new", endpoint="income_new_page")
    def income_new_page():
        """
        Optional preload:
          /income/new?invoice_id=<id>  -> pre-select invoice and default Amount to outstanding.
        """
        db = get_db(); company = ensure_company(db)

        inv_pre = None
        amount_default = ""
        invoice_id = request.args.get("invoice_id", type=int)
        if invoice_id:
            inv = db.get(Invoice, invoice_id)
            if inv:
                outstanding = _invoice_outstanding(db, inv)
                inv_pre = {
                    "id": inv.id,
                    "invoice_no": inv.invoice_no,
                    "client_name": inv.client_name,
                    "currency": inv.currency,
                    "outstanding": f"{outstanding:.2f}",
                    "status": inv.status,
                }
                amount_default = f"{outstanding:.2f}"

        return render("income_new.html",
                      csrf_token=csrf_token(), company=company,
                      inv_pre=inv_pre, amount_default=amount_default)

    @app.route("/api/invoices/lookup", methods=["GET"], endpoint="invoices_lookup_api")
    def invoices_lookup_api():
        """
        Lightweight typeahead for invoices: searches by invoice number or client name.
        Returns only non-closed invoices by default.
        q: query string (min 2 chars)
        include_closed: '1' to include closed invoices in results (optional)
        """
        db = get_db()
        q = (request.args.get("q", "") or "").strip()
        include_closed = request.args.get("include_closed") == "1"
        if len(q) < 2:
            return jsonify(results=[])

        stmt = select(Invoice).order_by(Invoice.issue_date.desc()).limit(15)
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Invoice.invoice_no).like(like),
                func.lower(Invoice.client_name).like(like),
            )
        )
        if not include_closed:
            stmt = stmt.where(Invoice.status != "CLOSED")

        rows = db.execute(stmt).scalars().all()

        results = []
        for inv in rows:
            outstanding = _invoice_outstanding(db, inv)
            results.append({
                "id": inv.id,
                "invoice_no": inv.invoice_no,
                "client_name": inv.client_name,
                "currency": inv.currency,
                "issue_date": str(inv.issue_date) if inv.issue_date else None,
                "due_date": str(inv.due_date) if inv.due_date else None,
                "status": inv.status,
                "gross_total": float(inv.gross_total or 0),
                "outstanding": float(outstanding),
                "label": f"{inv.invoice_no} · {inv.client_name} · {inv.currency} {outstanding:.2f} due",
            })
        return jsonify(results=results)

    @app.route("/income/add", methods=["POST"], endpoint="add_income")
    def add_income():
        """
        Records income. If an invoice_id is provided, the payment is linked to that invoice.
        If 'close_invoice' is on (default), the invoice status is set to CLOSED after saving.
        """
        db = get_db()
        try:
            require_csrf(request.form.get("csrf_token",""))

            # Basic fields
            amount = dec(request.form.get("amount"))
            pay_date = datetime.strptime(request.form.get("date",""), "%Y-%m-%d").date() if request.form.get("date") else date.today()
            method = request.form.get("method","bank")
            reference = (request.form.get("reference","") or "").strip()
            note = (request.form.get("note","") or "").strip()

            # Optional invoice link
            invoice_id = request.form.get("invoice_id")
            invoice_obj = None
            if invoice_id:
                try:
                    invoice_id = int(invoice_id)
                    invoice_obj = db.get(Invoice, invoice_id)
                except Exception:
                    invoice_obj = None

            db.add(Payment(
                invoice_id=invoice_obj.id if invoice_obj else None,
                date=pay_date, amount=amount,
                method=method, reference=reference, note=note
            ))

            # If attached to an invoice, optionally close it
            if invoice_obj:
                close_flag = request.form.get("close_invoice") in ("on", "1", "true", "True")
                if close_flag:
                    invoice_obj.status = "CLOSED"
                else:
                    # fall back to normal status recalculation (if you have partials)
                    try:
                        from ..accounting import update_status as _upd
                        _upd(invoice_obj)
                    except Exception:
                        pass

            db.commit()

            if invoice_obj:
                flash(f"Income €{amount} saved and linked to invoice {invoice_obj.invoice_no}."
                     + (" Invoice CLOSED." if request.form.get("close_invoice") in ("on","1","true","True") else ""), "success")
                return redirect(url_for("invoice_detail", invoice_id=invoice_obj.id))
            else:
                flash(f"Income €{amount} saved.", "success")
                return redirect(url_for("income_new_page"))

        except Exception as e:
            db.rollback(); flash(f"Income error: {e}", "danger")
            return redirect(url_for("income_new_page"))
