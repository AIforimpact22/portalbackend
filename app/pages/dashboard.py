# app/pages/dashboard.py
from datetime import date

from ..core import get_db, csrf_token, render
from ..accounting import ensure_company
from ..services.dashboard import load_dashboard_context

def register(app):
    @app.route("/", endpoint="dashboard")
    def dashboard():
        db = get_db(); company = ensure_company(db)
        context = load_dashboard_context(db, today=date.today())

        return render(
            "dashboard.html",
            csrf_token=csrf_token(),
            company=company,
            **context,
        )
