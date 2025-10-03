# app/pages/settings.py
from flask import request, redirect, url_for, flash

from ..core import get_db, csrf_token, require_csrf, render
from ..accounting import ensure_company

def register(app):
    @app.route("/settings", endpoint="settings_page")
    def settings_page():
        db = get_db(); company = ensure_company(db)
        return render("settings.html", csrf_token=csrf_token(), company=company)

    @app.route("/settings/save", methods=["POST"], endpoint="save_settings")
    def save_settings():
        try:
            require_csrf(request.form.get("csrf_token",""))
            db = get_db(); company = ensure_company(db)
            company.company_name = request.form.get("company_name","").strip()
            company.kvk = request.form.get("kvk","").strip()
            company.rsin = request.form.get("rsin","").strip()
            company.vat_number = request.form.get("vat_number","").strip()
            company.invoice_prefix = request.form.get("invoice_prefix","INV").strip() or "INV"
            company.iban = request.form.get("iban","").strip()
            company.bic = request.form.get("bic","").strip()
            company.address = request.form.get("address","").strip()
            company.postcode = request.form.get("postcode","").strip()
            company.city = request.form.get("city","").strip()
            company.country = request.form.get("country","Netherlands").strip()
            db.commit()
            flash("Company settings saved.", "success")
        except Exception as e:
            get_db().rollback(); flash(f"Settings error: {e}", "danger")
        return redirect(url_for("settings_page"))
