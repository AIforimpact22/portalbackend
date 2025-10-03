# app/pages/customers.py
import math
from typing import Dict, Set

from flask import request, redirect, url_for, flash
from sqlalchemy import select, or_, func

from ..core import get_customers_db, get_db, csrf_token, render, require_csrf
from ..accounting import ensure_company
from ..models_customers import Registration
from ..models import Invoice  # main DB invoices

# Invoice statuses that count as "closed" for enrollment auto-accept
CLOSED_INVOICE_STATUSES = {"PAID", "CLOSED"}

# Allowed manual enrollment statuses
ALLOWED_ENROLLMENT = {"pending", "accepted", "rejected", "waitlist"}


def _effective_status_map(reg_rows, closed_ids: Set[int]) -> Dict[int, dict]:
    """
    Build a map: reg_id -> {effective: 'accepted'|'pending'|'rejected'|'waitlist', via_invoice: bool}
    A reg is "accepted via invoice" if reg.id is in closed_ids AND its stored status isn't 'accepted'.
    """
    out = {}
    for r in reg_rows:
        base = (getattr(r, "enrollment_status", None) or "pending").lower()
        has_closed_invoice = r.id in closed_ids
        if base == "accepted" or has_closed_invoice:
            out[r.id] = {
                "effective": "accepted",
                "via_invoice": (has_closed_invoice and base != "accepted")
            }
        else:
            out[r.id] = {"effective": base, "via_invoice": False}
    return out


def _find_all_closed_customer_ids() -> Set[int]:
    """Return registration IDs that have >=1 PAID/CLOSED invoice."""
    db_main = get_db()
    rows = db_main.execute(
        select(Invoice.customer_registration_id)
        .where(Invoice.customer_registration_id.is_not(None))
        .where(Invoice.status.in_(list(CLOSED_INVOICE_STATUSES)))
        .group_by(Invoice.customer_registration_id)
    ).all()
    return {r[0] for r in rows if r and r[0] is not None}


def _sync_enrollment_acceptance(cdb) -> int:
    """
    Persist rule: if a customer has a closed invoice, set enrollment_status='accepted'.
    Idempotent. Returns #rows updated.
    """
    closed_ids = _find_all_closed_customer_ids()
    if not closed_ids:
        return 0

    updated = 0
    ids_list = list(closed_ids)
    CHUNK = 500
    for i in range(0, len(ids_list), CHUNK):
        chunk = ids_list[i:i+CHUNK]
        res = cdb.execute(
            Registration.__table__.update()
            .where(Registration.id.in_(chunk))
            .where(Registration.enrollment_status != 'accepted')
            .values(enrollment_status='accepted')
        )
        updated += getattr(res, "rowcount", 0) or 0
    try:
        if updated:
            cdb.commit()
    except Exception:
        cdb.rollback()
    return updated


def _find_closed_invoice_ids_for_regs(reg_ids: list[int]) -> Set[int]:
    """Return reg IDs (from the given subset) that have >=1 PAID/CLOSED invoice."""
    if not reg_ids:
        return set()
    db_main = get_db()
    rows = db_main.execute(
        select(Invoice.customer_registration_id)
        .where(Invoice.customer_registration_id.in_(reg_ids))
        .where(Invoice.status.in_(list(CLOSED_INVOICE_STATUSES)))
    ).all()
    return {r[0] for r in rows if r and r[0] is not None}


def _invoice_badge_map_for_regs(reg_ids: list[int]) -> Dict[int, dict]:
    """
    For the given reg_ids, compute a compact invoice status summary:
      badge in {'closed','partial','due','sent','draft','void','none'}
      count = number of invoices for that reg
    Priority (best summary across all invoices):
      closed (if any PAID/CLOSED) >
      partial >
      due >
      sent >
      draft/void >
      none
    """
    badge_map: Dict[int, dict] = {}
    if not reg_ids:
        return badge_map

    db_main = get_db()
    rows = db_main.execute(
        select(Invoice.customer_registration_id, Invoice.status)
        .where(Invoice.customer_registration_id.in_(reg_ids))
    ).all()

    statuses_by_reg: Dict[int, set] = {}
    counts: Dict[int, int] = {}
    for rid, st in rows:
        if rid is None:
            continue
        st_up = (st or "").upper()
        statuses_by_reg.setdefault(rid, set()).add(st_up)
        counts[rid] = counts.get(rid, 0) + 1

    def summarize(s: set[str]) -> str:
        if not s:
            return "none"
        if "PAID" in s or "CLOSED" in s:
            return "closed"
        if "PARTIAL" in s:
            return "partial"
        if "DUE" in s:
            return "due"
        if "SENT" in s:
            return "sent"
        if "DRAFT" in s:
            return "draft"
        if "VOID" in s:
            return "void"
        # Fallback to any present status (lowercased)
        return next(iter(s)).lower()

    for rid in reg_ids:
        sset = statuses_by_reg.get(rid, set())
        badge_map[rid] = {
            "badge": summarize(sset),
            "count": counts.get(rid, 0),
        }
    return badge_map


def register(app):
    @app.route("/customers", endpoint="customers_list")
    def customers_list():
        """
        Read-only list of registrations (customers) from Cloud SQL.
        Filters: q (name/email/company), session (course_session_code), referral (referral_source).
        Pagination: page (1-based), size (default 25).

        Enforces: anyone with a closed invoice is stored as 'accepted'.
        Displays: Enrollment chip + Invoice chip (summary) per customer.
        """
        company = ensure_company(get_db())

        # Connect to the Customers DB only for registrations
        try:
            cdb = get_customers_db()
        except Exception as e:
            flash(f"Customers DB not configured: {e}", "danger")
            return render("customers_list.html",
                          csrf_token=csrf_token(), company=company,
                          customers=[], q="", session_code="", referral="",
                          page=1, pages=1, size=25,
                          effective_status_map={}, invoice_status_map={})

        # Auto-sync acceptance (idempotent)
        try:
            _sync_enrollment_acceptance(cdb)
        except Exception as e:
            flash(f"Auto-accept sync error: {e}", "warning")

        q = (request.args.get("q", "") or "").strip()
        session_code = (request.args.get("session", "") or "").strip()
        referral = (request.args.get("referral", "") or "").strip()
        page = max(1, int(request.args.get("page", 1)))
        size = max(1, min(100, int(request.args.get("size", 25))))
        offset = (page - 1) * size

        stmt = select(Registration)
        count_stmt = select(func.count())

        filters = []
        if q:
            like = f"%{q.lower()}%"
            filters.append(or_(
                func.lower(Registration.first_name).like(like),
                func.lower(Registration.middle_name).like(like),
                func.lower(Registration.last_name).like(like),
                func.lower(Registration.user_email).like(like),
                func.lower(Registration.company).like(like),
            ))
        if session_code:
            filters.append(Registration.course_session_code == session_code)
        if referral:
            filters.append(Registration.referral_source == referral)

        if filters:
            for f in filters:
                stmt = stmt.where(f)
                count_stmt = count_stmt.where(f)

        stmt = stmt.order_by(Registration.created_at.desc()).limit(size).offset(offset)

        try:
            rows = cdb.execute(stmt).scalars().all()
            total = cdb.execute(count_stmt.select_from(Registration.__table__)).scalar()
        except Exception as e:
            flash(f"Customers DB error: {e}", "danger")
            return render("customers_list.html",
                          csrf_token=csrf_token(), company=company,
                          customers=[], q=q, session_code=session_code, referral=referral,
                          page=1, pages=1, size=size,
                          effective_status_map={}, invoice_status_map={})

        # Compute per-page effective enrollment + invoice summary badges
        reg_ids = [r.id for r in rows]
        closed_ids = _find_closed_invoice_ids_for_regs(reg_ids)
        effective_status_map = _effective_status_map(rows, closed_ids)
        invoice_status_map = _invoice_badge_map_for_regs(reg_ids)

        pages = max(1, math.ceil((total or 0) / size))
        return render("customers_list.html",
                      csrf_token=csrf_token(), company=company,
                      customers=rows, q=q, session_code=session_code, referral=referral,
                      page=page, pages=pages, size=size,
                      effective_status_map=effective_status_map,
                      invoice_status_map=invoice_status_map)

    @app.route("/customer/<int:reg_id>", endpoint="customer_detail")
    def customer_detail(reg_id: int):
        """
        Detail profile view with AI Intake integrated into the Profile column.
        Shows 'effective' enrollment and persists auto-accept (closed invoice -> accepted).
        Preserves list filters via q/session/referral/page/size query params.
        """
        company = ensure_company(get_db())
        try:
            cdb = get_customers_db()
        except Exception as e:
            flash(f"Customers DB not configured: {e}", "danger")
            return redirect(url_for("customers_list"))

        # Auto-sync acceptance (idempotent)
        try:
            _sync_enrollment_acceptance(cdb)
        except Exception as e:
            flash(f"Auto-accept sync error: {e}", "warning")

        reg = cdb.get(Registration, reg_id)
        if not reg:
            flash("Customer not found.", "warning")
            return redirect(url_for("customers_list"))

        # Preserve list context
        q = (request.args.get("q", "") or "").strip()
        session_code = (request.args.get("session", "") or "").strip()
        referral = (request.args.get("referral", "") or "").strip()
        page = max(1, int(request.args.get("page", 1)))
        size = max(1, min(100, int(request.args.get("size", 25))))

        # Initial for avatar
        name = (getattr(reg, "full_name", None) or "").strip()
        initial = (name[:1] or "?").upper()

        consents = {
            "contact_ok": bool(getattr(reg, "consent_contact_ok", False)),
            "marketing_ok": bool(getattr(reg, "consent_marketing_ok", False)),
            "data_ok": bool(getattr(reg, "data_processing_ok", False)),
        }

        # Effective status (closed invoice counts as accepted)
        closed_ids = _find_closed_invoice_ids_for_regs([reg.id])
        effective_map = _effective_status_map([reg], closed_ids).get(reg.id, {"effective": "pending", "via_invoice": False})
        effective_status = effective_map["effective"]
        accepted_via_invoice = effective_map["via_invoice"]

        return render("customer_detail.html",
                      csrf_token=csrf_token(), company=company, reg=reg,
                      q=q, session_code=session_code, referral=referral, page=page, size=size,
                      initial=initial, consents=consents,
                      effective_status=effective_status, accepted_via_invoice=accepted_via_invoice)

    # -------- Manual enrollment updates --------
    @app.route("/customer/<int:reg_id>/status", methods=["POST"], endpoint="customer_set_status")
    def customer_set_status(reg_id: int):
        """
        Manually set enrollment_status to one of:
        'pending', 'accepted', 'rejected', 'waitlist'
        """
        try:
            require_csrf(request.form.get("csrf_token", ""))
        except Exception as e:
            flash(str(e), "danger")
            return redirect(url_for("customer_detail", reg_id=reg_id))

        new_status = (request.form.get("status", "") or "").strip().lower()
        if new_status not in ALLOWED_ENROLLMENT:
            flash("Invalid enrollment status.", "danger")
            return redirect(url_for("customer_detail", reg_id=reg_id))

        try:
            cdb = get_customers_db()
        except Exception as e:
            flash(f"Customers DB not configured: {e}", "danger")
            return redirect(url_for("customers_list"))

        reg = cdb.get(Registration, reg_id)
        if not reg:
            flash("Customer not found.", "warning")
            return redirect(url_for("customers_list"))

        try:
            reg.enrollment_status = new_status
            cdb.flush()
            cdb.commit()
            flash(f"Enrollment set to {new_status}.", "success")
        except Exception as e:
            cdb.rollback()
            flash(f"Update failed: {e}", "danger")

        # Preserve context on redirect back
        q = (request.args.get("q", "") or "").strip()
        session_code = (request.args.get("session", "") or "").strip()
        referral = (request.args.get("referral", "") or "").strip()
        page = request.args.get("page", 1)
        size = request.args.get("size", 25)
        return redirect(url_for("customer_detail", reg_id=reg_id, q=q, session=session_code,
                                referral=referral, page=page, size=size))
