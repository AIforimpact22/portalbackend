# app/pages/invoice.py
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import request, redirect, url_for, flash, jsonify
from sqlalchemy import select, or_, func

from ..core import get_db, csrf_token, require_csrf, render, get_customers_db
from ..models import Invoice, InvoiceLine, Payment, Expense
from ..accounting import (
    ensure_company, dec, next_invoice_no,
    recalc_invoice, update_status, compliance_warnings,
    ensure_invoice_totals, create_or_get_stripe_payment_link,
)
from ..models_customers import Registration


# ------------------------------ Helpers ------------------------------

def _compose_addr_from(reg: Registration, use_invoice: bool = True) -> str:
    """Render a multi-line address string from Registration. Fallback to personal address if invoice address empty."""
    def join_addr(prefix: str):
        l1 = getattr(reg, f"{prefix}_addr_line1", None)
        l2 = getattr(reg, f"{prefix}_addr_line2", None)
        city = getattr(reg, f"{prefix}_city", None)
        state = getattr(reg, f"{prefix}_state", None)
        pc = getattr(reg, f"{prefix}_postal_code", None)
        country = getattr(reg, f"{prefix}_country", None)
        parts = []
        if l1: parts.append(l1)
        if l2: parts.append(l2)
        line_city = " ".join([p for p in [city, state] if p]) or None
        if line_city: parts.append(line_city)
        if pc: parts.append(pc)
        if country: parts.append(country)
        return "\n".join(parts)

    if use_invoice:
        addr = join_addr("invoice")
        if addr.strip():
            return addr

    # Fallback: personal address
    personal = []
    if getattr(reg, "address_line1", None): personal.append(reg.address_line1)
    if getattr(reg, "address_line2", None): personal.append(reg.address_line2)
    line_city = " ".join([p for p in [getattr(reg, "city", None), getattr(reg, "state", None)] if p]) or None
    if line_city: personal.append(line_city)
    if getattr(reg, "postal_code", None): personal.append(reg.postal_code)
    if getattr(reg, "country", None): personal.append(reg.country)
    return "\n".join([p for p in personal if p])


def _prefill_from_registration(reg: Registration) -> dict:
    """Build initial values for invoice header from a Registration row."""
    client_name = (getattr(reg, "invoice_company", None)
                   or getattr(reg, "invoice_name", None)
                   or getattr(reg, "full_name", None)
                   or "").strip()
    client_vat = (getattr(reg, "invoice_vat_id", None) or "").strip()
    client_addr = _compose_addr_from(reg, use_invoice=True) or _compose_addr_from(reg, use_invoice=False)
    return {
        "client_name": client_name,
        "client_vat_number": client_vat,
        "client_address": client_addr,
        "customer_registration_id": reg.id,
    }


# ------------------------------ Routes ------------------------------

def register(app):
    # ---------- Invoices list ----------
    @app.route("/invoices", endpoint="invoices_list")
    def invoices_list():
        db = get_db()
        company = ensure_company(db)

        status = (request.args.get("status") or "").strip() or None
        q = select(Invoice).order_by(Invoice.issue_date.desc())
        if status:
            q = q.where(Invoice.status == status)

        invoices = db.execute(q).scalars().all()
        return render("invoices_list.html",
                      csrf_token=csrf_token(), company=company,
                      invoices=invoices, status=status)

    # ---------- New invoice page (with optional preselected customer) ----------
    @app.route("/invoices/new", endpoint="invoice_new_page")
    def invoice_new_page():
        db = get_db()
        company = ensure_company(db)
        prefill = {}

        customer_id = request.args.get("customer_id", type=int)
        if customer_id:
            try:
                cdb = get_customers_db()
                reg = cdb.get(Registration, customer_id)
                if reg:
                    prefill = _prefill_from_registration(reg)
            except Exception as e:
                flash(f"Customers DB not available for preload: {e}", "warning")

        return render("invoice_new.html",
                      csrf_token=csrf_token(), company=company, prefill=prefill)

    # ---------- Customer lookup API for the selector ----------
    @app.route("/api/customers/lookup", methods=["GET"], endpoint="customers_lookup_api")
    def customers_lookup_api():
        q = (request.args.get("q", "") or "").strip()
        if len(q) < 2:
            return jsonify(results=[])

        try:
            cdb = get_customers_db()
        except Exception as e:
            return jsonify(results=[], error=f"Customers DB not configured: {e}")

        like = f"%{q.lower()}%"
        stmt = (
            select(Registration)
            .where(or_(
                func.lower(Registration.first_name).like(like),
                func.lower(Registration.middle_name).like(like),
                func.lower(Registration.last_name).like(like),
                func.lower(Registration.user_email).like(like),
                func.lower(Registration.company).like(like),
            ))
            .order_by(Registration.created_at.desc())
            .limit(15)
        )
        rows = cdb.execute(stmt).scalars().all()

        results = []
        for r in rows:
            label_parts = [getattr(r, "full_name", "") or ""]
            if getattr(r, "company", None):
                label_parts.append(f"({r.company})")
            if getattr(r, "user_email", None):
                label_parts.append(f"· {r.user_email}")
            label = " ".join([p for p in label_parts if p]).strip()

            pf = _prefill_from_registration(r)
            results.append({
                "id": r.id,
                "label": label,
                "email": r.user_email,
                "company": r.company,
                # Prefill fields:
                "client_name": pf["client_name"],
                "client_vat_number": pf["client_vat_number"],
                "client_address": pf["client_address"],
            })
        return jsonify(results=results)

    # ---------- Invoice detail ----------
    @app.route("/invoice/<int:invoice_id>", endpoint="invoice_detail")
    def invoice_detail(invoice_id: int):
        """
        Self-heals legacy totals and, if keys are present, attaches a Stripe payment link when missing.
        """
        db = get_db()
        company = ensure_company(db)

        inv = db.get(Invoice, invoice_id)
        if not inv:
            flash("Invoice not found.", "warning")
            return redirect(url_for("invoices_list"))

        # Normalize totals first (prevents template crashes)
        try:
            ensure_invoice_totals(inv)
            db.flush()
        except Exception:
            pass

        # Create Stripe link if missing (non-fatal on failure)
        if not getattr(inv, "stripe_payment_url", None):
            try:
                success_url = url_for("invoice_detail", invoice_id=inv.id, _external=True)
                url = create_or_get_stripe_payment_link(inv, success_url=success_url)
                if url:
                    db.commit()
                    flash("Stripe payment link attached to invoice.", "success")
            except Exception as e:
                db.rollback()
                flash(f"Stripe: {e}", "warning")

        # Link back to customer (column now exists in DB + model)
        customer_link = url_for("customer_detail", reg_id=inv.customer_registration_id) \
            if getattr(inv, "customer_registration_id", None) else None

        warns = compliance_warnings(company, inv)

        # Linked freelancers summary
        linked_expenses = db.execute(
            select(Expense)
            .where(Expense.invoice_id == inv.id)
            .where(Expense.category.in_(["Freelancer", "Subcontractor"]))
            .order_by(Expense.date.desc())
        ).scalars().all()

        linked_total = sum(
            (Decimal(e.amount_gross) for e in linked_expenses),
            Decimal("0.00")
        ).quantize(Decimal("0.01"))
        margin_after_freelancers = (Decimal(inv.gross_total or 0) - linked_total).quantize(Decimal("0.01"))

        # Deletion safety counts
        payments_count = db.execute(
            select(func.count()).select_from(Payment).where(Payment.invoice_id == inv.id)
        ).scalar() or 0
        all_expenses_count = db.execute(
            select(func.count()).select_from(Expense).where(Expense.invoice_id == inv.id)
        ).scalar() or 0
        can_delete = (payments_count == 0 and all_expenses_count == 0)

        return render("invoice_detail.html",
                      csrf_token=csrf_token(), company=company, inv=inv, warns=warns,
                      linked_expenses=linked_expenses,
                      linked_total=linked_total,
                      margin_after_freelancers=margin_after_freelancers,
                      customer_link=customer_link,
                      payments_count=payments_count,
                      all_expenses_count=all_expenses_count,
                      can_delete=can_delete)

    # ---------- Create invoice ----------
    @app.route("/invoice/create", methods=["POST"], endpoint="create_invoice")
    def create_invoice():
        db = get_db()
        try:
            require_csrf(request.form.get("csrf_token", ""))

            company = ensure_company(db)
            invoice_no = next_invoice_no(db, company.invoice_prefix)

            def parse_date(field, default=None):
                val = request.form.get(field, "")
                try:
                    return datetime.strptime(val, "%Y-%m-%d").date()
                except Exception:
                    return default or date.today()

            issue_date = parse_date("issue_date")
            supply_date = parse_date("supply_date", issue_date)
            due_date = parse_date("due_date", issue_date + timedelta(days=14))

            currency = (request.form.get("currency", "EUR") or "EUR").strip().upper()
            vat_scheme = request.form.get("vat_scheme", "STANDARD")
            client_name = (request.form.get("client_name", "") or "").strip()
            client_address = (request.form.get("client_address", "") or "").strip()
            client_vat_number = (request.form.get("client_vat_number", "") or "").strip()
            notes = (request.form.get("notes", "") or "").strip()

            inv = Invoice(
                invoice_no=invoice_no,
                issue_date=issue_date,
                supply_date=supply_date,
                due_date=due_date,
                currency=currency,
                vat_scheme=vat_scheme,
                client_name=client_name,
                client_address=client_address,
                client_vat_number=client_vat_number,
                notes=notes,
                status="SENT",
                legacy_amount=dec("0.00"),
            )

            # NEW: set the selected customer (column exists)
            selected_cust_id = request.form.get("customer_registration_id")
            if selected_cust_id:
                try:
                    inv.customer_registration_id = int(selected_cust_id)
                except Exception:
                    pass

            db.add(inv)
            db.flush()

            # Lines
            descs = request.form.getlist("line_desc")
            qtys = request.form.getlist("line_qty")
            prices = request.form.getlist("line_price")
            vats = request.form.getlist("line_vat")

            max_len = max(len(descs), len(qtys), len(prices), len(vats)) if any([descs, qtys, prices, vats]) else 0

            def get_or_empty(lst, i):
                try:
                    return lst[i]
                except Exception:
                    return ""

            for i in range(max_len):
                d = (get_or_empty(descs, i) or "").strip()
                qv = dec(get_or_empty(qtys, i))
                pv = dec(get_or_empty(prices, i))
                rv = dec(get_or_empty(vats, i))

                if d == "" and qv == dec("0") and pv == dec("0"):
                    continue
                if not d:
                    d = "Item"
                if qv <= dec("0"):
                    qv = dec("1")
                if pv < dec("0"):
                    pv = dec("0")
                if rv < dec("0"):
                    rv = dec("0")

                db.add(InvoiceLine(invoice_id=inv.id, description=d, qty=qv, unit_price=pv, vat_rate=rv))

            db.flush()
            recalc_invoice(inv)
            ensure_invoice_totals(inv)
            inv.legacy_amount = inv.gross_total
            update_status(inv)

            # Try to create Stripe link now (optional). If it fails, detail page will retry.
            try:
                success_url = url_for("invoice_detail", invoice_id=inv.id, _external=True)
                url = create_or_get_stripe_payment_link(inv, success_url=success_url)
                if url:
                    flash("Stripe payment link attached to invoice.", "success")
            except Exception as e:
                flash(f"Stripe: {e}", "warning")

            for w in compliance_warnings(company, inv):
                flash("Invoice warning: " + w, "warning")

            db.commit()
            flash(f"Invoice {inv.invoice_no} created.", "success")
            return redirect(url_for("invoice_detail", invoice_id=inv.id))

        except Exception as e:
            db.rollback()
            flash(f"Create invoice error: {e}", "danger")
            return redirect(url_for("invoice_new_page"))

    # ---------- Record payment ----------
    @app.route("/invoice/<int:invoice_id>/pay", methods=["POST"], endpoint="pay_invoice")
    def pay_invoice(invoice_id: int):
        db = get_db()
        try:
            require_csrf(request.form.get("csrf_token", ""))

            inv = db.get(Invoice, invoice_id)
            if not inv:
                flash("Invoice not found.", "warning")
                return redirect(url_for("invoices_list"))

            amount = dec(request.form.get("amount"))
            pay_date = datetime.strptime(request.form.get("date", ""), "%Y-%m-%d").date() if request.form.get("date") else date.today()
            method = request.form.get("method", "bank")
            reference = (request.form.get("reference", "") or "").strip()
            note = (request.form.get("note", "") or "").strip()

            db.add(Payment(invoice_id=invoice_id, date=pay_date, amount=amount, method=method, reference=reference, note=note))
            db.flush()
            update_status(inv)
            db.commit()

            flash(f"Payment €{amount} recorded for {inv.invoice_no}.", "success")
        except Exception as e:
            db.rollback()
            flash(f"Payment error: {e}", "danger")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))

    # ---------- Delete invoice ----------
    @app.route("/invoice/<int:invoice_id>/delete", methods=["POST"], endpoint="delete_invoice")
    def delete_invoice(invoice_id: int):
        db = get_db()
        try:
            require_csrf(request.form.get("csrf_token", ""))

            inv = db.get(Invoice, invoice_id)
            if not inv:
                flash("Invoice not found.", "warning")
                return redirect(url_for("invoices_list"))

            # Safety checks: no payments & no linked expenses
            payments_count = db.execute(
                select(func.count()).select_from(Payment).where(Payment.invoice_id == inv.id)
            ).scalar() or 0
            all_expenses_count = db.execute(
                select(func.count()).select_from(Expense).where(Expense.invoice_id == inv.id)
            ).scalar() or 0

            if payments_count > 0 or all_expenses_count > 0:
                msg = []
                if payments_count > 0:
                    msg.append(f"{payments_count} payment(s)")
                if all_expenses_count > 0:
                    msg.append(f"{all_expenses_count} linked expense(s)")
                flash("Cannot delete invoice with " + " and ".join(msg) + ".", "danger")
                return redirect(url_for("invoice_detail", invoice_id=invoice_id))

            # Delete lines explicitly (belt-and-braces if cascade changes)
            try:
                for ln in list(inv.lines or []):
                    db.delete(ln)
            except Exception:
                pass

            inv_no = inv.invoice_no
            db.delete(inv)
            db.commit()
            flash(f"Invoice {inv_no or invoice_id} deleted.", "success")
            return redirect(url_for("invoices_list"))

        except Exception as e:
            db.rollback()
            flash(f"Delete invoice error: {e}", "danger")
            return redirect(url_for("invoice_detail", invoice_id=invoice_id))
