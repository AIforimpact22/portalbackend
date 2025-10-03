import os
from datetime import timedelta
from flask import Flask

from .core import (
    flask_settings, get_session_minutes, init_app as init_db_session,
    Base, engine, run_schema_upgrades, render, get_db, get_customers_db
)
from .accounting import ensure_company

def create_app():
    here = os.path.abspath(os.path.dirname(__file__))
    tpl_dir = os.path.abspath(os.path.join(here, "..", "templates"))
    static_dir = os.path.abspath(os.path.join(here, "..", "static"))

    app = Flask(__name__, template_folder=tpl_dir, static_folder=static_dir)
    app.config.update(**flask_settings())
    app.permanent_session_lifetime = timedelta(minutes=get_session_minutes())

    # init teardown hooks for both DBs
    init_db_session(app)

    # Ensure main models are loaded and schema upgraded
    from . import models  # noqa: F401
    Base.metadata.create_all(engine)
    run_schema_upgrades(engine)

    # Security + routing
    from .core import init_security
    init_security(app)

    from .pages import register_routes
    register_routes(app)

    @app.errorhandler(500)
    def server_error(e):
        company = None
        # Try primary DB first
        try:
            db = get_db()
            company = ensure_company(db)
        except Exception:
            # Fall back to customers DB if available
            try:
                db = get_customers_db()
                company = ensure_company(db)
            except Exception:
                pass
        return render("error_500.html", error=e, company=company), 500

    @app.errorhandler(404)
    def not_found(_):
        company = None
        try:
            db = get_db()
            company = ensure_company(db)
        except Exception:
            try:
                db = get_customers_db()
                company = ensure_company(db)
            except Exception:
                pass
        return render("error_404.html", company=company), 404

    return app
