# app/core.py
import os, secrets, hmac
from dotenv import load_dotenv
from urllib.parse import urljoin, urlparse, quote_plus
from functools import wraps
from threading import local as _thread_local

from flask import request, redirect, url_for, flash, session, render_template, render_template_string
from werkzeug.security import check_password_hash

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, scoped_session, declarative_base

load_dotenv()

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
IS_SERVERLESS = bool(os.environ.get("GAE_ENV") or os.environ.get("K_SERVICE"))
DEFAULT_UPLOAD_DIR = "/tmp/uploads" if IS_SERVERLESS else os.path.join(BASE_DIR, "uploads")
os.makedirs(DEFAULT_UPLOAD_DIR, exist_ok=True)

def get_database_url():
    return os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'app.db')}")

def flask_settings():
    return {
        "SECRET_KEY": os.environ.get("SECRET_KEY", "dev-" + secrets.token_hex(16)),
        "UPLOAD_FOLDER": DEFAULT_UPLOAD_DIR,
        "MAX_CONTENT_LENGTH": 25 * 1024 * 1024,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": os.environ.get("SESSION_COOKIE_SECURE", "1") == "1",
        "TEMPLATES_AUTO_RELOAD": os.environ.get("TEMPLATES_AUTO_RELOAD", "0") == "1",
        "PROPAGATE_EXCEPTIONS": os.environ.get("PROPAGATE_EXCEPTIONS", "0") == "1",
        "APP_USERNAME": os.environ.get("APP_USERNAME", "admin"),
    }

def get_session_minutes() -> int:
    return int(os.environ.get("SESSION_MINUTES", "720"))

# ----------------------------
# Primary DB engine/session
# ----------------------------
engine = create_engine(get_database_url(), future=True, pool_pre_ping=True)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True))
Base = declarative_base()

def get_db():
    return SessionLocal()

# ----------------------------
# Secondary DB: Customers (Cloud SQL)
# ----------------------------
_customers_engine = None
CustomersSessionLocal = None

def _build_customers_url_from_env():
    # 1) Preferred: CUSTOMERS_DATABASE_URL
    url = os.environ.get("CUSTOMERS_DATABASE_URL")
    if url:
        return url

    # 2) Assemble from CUSTOMERS_* (fallback to generic DB_* and INSTANCE_CONNECTION_NAME)
    user = os.environ.get("CUSTOMERS_DB_USER") or os.environ.get("DB_USER")
    pwd  = os.environ.get("CUSTOMERS_DB_PASS") or os.environ.get("DB_PASS")
    name = os.environ.get("CUSTOMERS_DB_NAME") or os.environ.get("DB_NAME")
    host = os.environ.get("CUSTOMERS_DB_HOST") or os.environ.get("DB_HOST")
    inst = os.environ.get("CUSTOMERS_INSTANCE_CONNECTION_NAME") or os.environ.get("INSTANCE_CONNECTION_NAME")

    if user and pwd and name and host:
        return f"postgresql+psycopg2://{user}:{quote_plus(pwd)}@{host}:5432/{name}"
    if user and pwd and name and inst:
        # App Engine Unix socket
        return f"postgresql+psycopg2://{user}:{quote_plus(pwd)}@/{name}?host=/cloudsql/{inst}"
    return None

def get_customers_db():
    """Return a session to the Customers DB (Cloud SQL). Raises if not configured."""
    global _customers_engine, CustomersSessionLocal
    if _customers_engine is None:
        dsn = _build_customers_url_from_env()
        if not dsn:
            raise RuntimeError("Customers DB not configured. Set CUSTOMERS_DATABASE_URL or CUSTOMERS_* env vars.")
        _customers_engine = create_engine(dsn, future=True, pool_pre_ping=True)
        CustomersSessionLocal = scoped_session(sessionmaker(bind=_customers_engine, autoflush=False, autocommit=False, future=True))
    return CustomersSessionLocal()

def init_app(app):
    @app.teardown_appcontext
    def remove_session(_exc=None):
        SessionLocal.remove()
        try:
            if CustomersSessionLocal:
                CustomersSessionLocal.remove()
        except Exception:
            pass

# ----------------------------
# Security helpers
# ----------------------------
APP_PASSWORD = os.environ.get("APP_PASSWORD")
APP_PASSWORD_HASH = os.environ.get("APP_PASSWORD_HASH")

def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]

def require_csrf(form_token: str):
    tok = session.get("csrf_token")
    if not tok or not form_token or not hmac.compare_digest(tok, form_token):
        raise ValueError("CSRF token mismatch")

def verify_password(password: str) -> bool:
    if APP_PASSWORD_HASH:
        try:
            return check_password_hash(APP_PASSWORD_HASH, password)
        except Exception:
            return False
    if APP_PASSWORD:
        return hmac.compare_digest(APP_PASSWORD, password)
    from flask import current_app
    return current_app.debug and password == "dev"

def is_safe_url(target: str) -> bool:
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return test.scheme in ("http", "https") and ref.netloc == test.netloc

def init_security(app):
    @app.before_request
    def enforce_login():
        # Allow health checks, warmup, login, static, probe, and uploads
        if request.endpoint in ("healthz", "warmup", "login", "static", "probe", "uploaded_file"):
            return
        if (request.path or "").startswith("/uploads/"):
            return
        if not session.get("auth_ok"):
            if request.method == "GET":
                return redirect(url_for("login", next=request.url))
            return redirect(url_for("login"))

# ----------------------------
# Safe render (cycle guard)
# ----------------------------
_render_ctx = _thread_local()
def _guarded_render(fn):
    @wraps(fn)
    def _wrap(template_name, *args, **kwargs):
        stack = getattr(_render_ctx, "stack", [])
        if template_name in stack:
            return render_template_string(
                "<h3>Template cycle detected</h3>"
                "<p>Template <code>{{name}}</code> was re-entered while rendering. "
                "Check your {% extends %}/{% include %} loops.</p>",
                name=template_name
            ), 500
        try:
            stack.append(template_name)
            _render_ctx.stack = stack
            return fn(template_name, *args, **kwargs)
        finally:
            try:
                stack.pop()
            except Exception:
                _render_ctx.stack = []
    return _wrap

render = _guarded_render(render_template)

# ----------------------------
# Idempotent schema upgrades
# ----------------------------
def run_schema_upgrades(engine_ = None):
    eng = engine_ or engine
    insp = inspect(eng)

    # company_settings: add rsin
    if insp.has_table("company_settings"):
        cols = {c['name'] for c in insp.get_columns("company_settings")}
        if "rsin" not in cols:
            with eng.begin() as conn:
                conn.execute(text("ALTER TABLE company_settings ADD COLUMN rsin VARCHAR(32) DEFAULT ''"))

    # expenses: VAT columns + payout linkage
    if insp.has_table("expenses"):
        cols = {c['name'] for c in insp.get_columns("expenses")}
        stmts = []
        if "amount_gross" not in cols:  stmts.append("ALTER TABLE expenses ADD COLUMN amount_gross NUMERIC(12,2) DEFAULT 0")
        if "amount_net"   not in cols:  stmts.append("ALTER TABLE expenses ADD COLUMN amount_net NUMERIC(12,2) DEFAULT 0")
        if "vat_amount"   not in cols:  stmts.append("ALTER TABLE expenses ADD COLUMN vat_amount NUMERIC(12,2) DEFAULT 0")
        if "vat_rate"     not in cols:  stmts.append("ALTER TABLE expenses ADD COLUMN vat_rate NUMERIC(5,2) DEFAULT 0")
        if "invoice_id"   not in cols:  stmts.append("ALTER TABLE expenses ADD COLUMN invoice_id INTEGER")
        if "pay_method"   not in cols:  stmts.append("ALTER TABLE expenses ADD COLUMN pay_method VARCHAR(32) DEFAULT 'bank'")
        if "pay_reference" not in cols:  stmts.append("ALTER TABLE expenses ADD COLUMN pay_reference VARCHAR(128)")
        if stmts:
            with eng.begin() as conn:
                for s in stmts:
                    conn.execute(text(s))
                if "amount" in cols:
                    conn.execute(text("""
                        UPDATE expenses
                           SET amount_gross = COALESCE(amount_gross,0) + COALESCE(amount,0) * CASE WHEN COALESCE(amount_gross,0)=0 AND COALESCE(amount_net,0)=0 THEN 1 ELSE 0 END,
                               amount_net   = COALESCE(amount_net,0)   + COALESCE(amount,0) * CASE WHEN COALESCE(amount_gross,0)=0 AND COALESCE(amount_net,0)=0 THEN 1 ELSE 0 END,
                               vat_amount   = COALESCE(vat_amount,0),
                               vat_rate     = COALESCE(vat_rate,0)
                     """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_expenses_invoice_id ON expenses(invoice_id)"))

    # invoices: totals, compliance, and Stripe columns
    if insp.has_table("invoices"):
        cols = {c['name'] for c in insp.get_columns("invoices")}
        adds = []
        if "supply_date"      not in cols: adds.append("ALTER TABLE invoices ADD COLUMN supply_date DATE")
        if "vat_scheme"       not in cols: adds.append("ALTER TABLE invoices ADD COLUMN vat_scheme VARCHAR(32) DEFAULT 'STANDARD'")
        if "client_address"   not in cols: adds.append("ALTER TABLE invoices ADD COLUMN client_address TEXT")
        if "client_vat_number"not in cols: adds.append("ALTER TABLE invoices ADD COLUMN client_vat_number VARCHAR(40)")
        if "notes"            not in cols: adds.append("ALTER TABLE invoices ADD COLUMN notes TEXT")
        if "status"           not in cols: adds.append("ALTER TABLE invoices ADD COLUMN status VARCHAR(16) DEFAULT 'SENT'")
        if "net_total"        not in cols: adds.append("ALTER TABLE invoices ADD COLUMN net_total NUMERIC(12,2) DEFAULT 0")
        if "vat_total"        not in cols: adds.append("ALTER TABLE invoices ADD COLUMN vat_total NUMERIC(12,2) DEFAULT 0")
        if "gross_total"      not in cols: adds.append("ALTER TABLE invoices ADD COLUMN gross_total NUMERIC(12,2) DEFAULT 0")
        if "stripe_payment_url" not in cols:         adds.append("ALTER TABLE invoices ADD COLUMN stripe_payment_url TEXT")
        if "stripe_payment_link_id" not in cols:     adds.append("ALTER TABLE invoices ADD COLUMN stripe_payment_link_id VARCHAR(64)")
        if "stripe_checkout_session_id" not in cols: adds.append("ALTER TABLE invoices ADD COLUMN stripe_checkout_session_id VARCHAR(64)")
        with eng.begin() as conn:
            for s in adds:
                conn.execute(text(s))
            if "amount" in cols:
                conn.execute(text("ALTER TABLE invoices ALTER COLUMN amount SET DEFAULT 0"))
                conn.execute(text("UPDATE invoices SET amount = 0 WHERE amount IS NULL"))
            cols_after = {c['name'] for c in insp.get_columns("invoices")}
            if "supply_date" not in cols and "issue_date" in cols_after:
                conn.execute(text("UPDATE invoices SET supply_date = issue_date WHERE supply_date IS NULL"))
            if "amount" in cols_after:
                conn.execute(text("""
                    UPDATE invoices
                       SET gross_total = COALESCE(gross_total,0),
                           net_total   = CASE WHEN COALESCE(net_total,0)=0 THEN COALESCE(amount,0) END,
                           vat_total   = COALESCE(vat_total,0)
                 """))

    # invoice_lines: totals & VAT rate
    if insp.has_table("invoice_lines"):
        cols = {c['name'] for c in insp.get_columns("invoice_lines")}
        with eng.begin() as conn:
            if "line_net"   not in cols: conn.execute(text("ALTER TABLE invoice_lines ADD COLUMN line_net NUMERIC(12,2) DEFAULT 0"))
            if "line_vat"   not in cols: conn.execute(text("ALTER TABLE invoice_lines ADD COLUMN line_vat NUMERIC(12,2) DEFAULT 0"))
            if "line_total" not in cols: conn.execute(text("ALTER TABLE invoice_lines ADD COLUMN line_total NUMERIC(12,2) DEFAULT 0"))
            if "vat_rate"   not in cols: conn.execute(text("ALTER TABLE invoice_lines ADD COLUMN vat_rate NUMERIC(5,2) DEFAULT 0"))
