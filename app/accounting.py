from datetime import date
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import os

from sqlalchemy import Date, cast, select
from dateutil.relativedelta import relativedelta

from .models import CompanySettings, InvoiceSequence, Invoice, InvoiceLine, Payment, Expense

# Defaults to avoid blanks (Dutch BV)
DEFAULT_COMPANY = {
    "company_name": "Climate Resilience Fundraising Platform B.V.",
    "address": "Fluwelen Burgwal",
    "postcode": "2511CJ",
    "city": "Den Haag",
    "country": "Netherlands",
    "kvk": "94437289",
    "rsin": "866777398",
    "vat_number": "NL[xxxx.xxx].B01",
    "iban": "NL06 REVO 7487 2866 30",
    "bic": "REVONL22",
    "invoice_prefix": "INV",
}

def dec(x) -> Decimal:
    try:
        if x is None or (isinstance(x, str) and x.strip() == ""):
            x = "0"
        return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")

def ensure_company(db):
    row = db.get(CompanySettings, 1)
    if not row:
        row = CompanySettings(id=1)
        db.add(row); db.flush()
    for k, v in DEFAULT_COMPANY.items():
        if not getattr(row, k, None):
            setattr(row, k, v)
    db.commit()
    return row

def next_invoice_no(db, prefix: str) -> str:
    year = date.today().year
    seq = db.execute(select(InvoiceSequence).where(InvoiceSequence.year == year, InvoiceSequence.prefix == prefix)).scalar_one_or_none()
    if not seq:
        seq = InvoiceSequence(year=year, prefix=prefix, last_seq=0)
        db.add(seq); db.flush()
    seq.last_seq += 1
    db.flush()
    return f"{prefix}-{year}-{seq.last_seq:04d}"

def recalc_invoice(inv: Invoice):
    net = Decimal("0.00"); vat = Decimal("0.00"); gross = Decimal("0.00")
    for line in inv.lines:
        ln = dec(line.qty) * dec(line.unit_price)
        lr = dec(line.vat_rate)
        lv = (ln * lr / Decimal("100")).quantize(Decimal("0.01"))
        lt = (ln + lv).quantize(Decimal("0.01"))
        line.line_net = ln; line.line_vat = lv; line.line_total = lt
        net += ln; vat += lv; gross += lt
    if inv.vat_scheme in ("REVERSE_CHARGE_EU", "ZERO_OUTSIDE_EU", "EXEMPT"):
        vat = Decimal("0.00"); gross = net
    inv.net_total = net.quantize(Decimal("0.01"))
    inv.vat_total = vat.quantize(Decimal("0.01"))
    inv.gross_total = gross.quantize(Decimal("0.01"))
    inv.legacy_amount = inv.gross_total  # keep legacy column synced

def ensure_invoice_totals(inv: Invoice):
    """Normalize legacy/NULL totals and re-calc if needed."""
    needs = any(getattr(inv, f, None) is None for f in ("net_total","vat_total","gross_total"))
    if needs:
        recalc_invoice(inv)
    # Coalesce remaining None (e.g., truly empty invoices)
    inv.net_total   = dec(inv.net_total or 0)
    inv.vat_total   = dec(inv.vat_total or 0)
    inv.gross_total = dec(inv.gross_total or 0)

def update_status(inv: Invoice):
    if inv.paid_total <= Decimal("0.00"):
        inv.status = "SENT"
    elif inv.paid_total < Decimal(inv.gross_total):
        inv.status = "PARTIAL"
    else:
        inv.status = "PAID"

def quarter_bounds(d: date):
    q = (d.month - 1)//3 + 1
    start_month = 3*(q-1)+1
    start = date(d.year, start_month, 1)
    end = (start + relativedelta(months=3)) - relativedelta(days=1)
    return start, end

def vat_summary(db, year: int, quarter: int):
    start_month = 3*(quarter-1)+1
    q_start = date(year, start_month, 1)
    q_end = (q_start + relativedelta(months=3)) - relativedelta(days=1)

    sales_21 = sales_9 = sales_0 = Decimal("0.00"); vat_out = Decimal("0.00")
    rows = db.execute(select(InvoiceLine, Invoice).join(Invoice).where(Invoice.issue_date >= q_start, Invoice.issue_date <= q_end)).all()
    for line, inv in rows:
        if inv.vat_scheme == "REVERSE_CHARGE_EU":
            pass
        elif inv.vat_scheme in ("ZERO_OUTSIDE_EU", "EXEMPT"):
            sales_0 += dec(line.line_net)
        else:
            rate = dec(line.vat_rate); net = dec(line.line_net)
            if rate == dec("21"): sales_21 += net
            elif rate == dec("9"): sales_9 += net
            else: sales_0 += net
            vat_out += dec(line.line_vat)

    vat_in = Decimal("0.00")
    exps = db.execute(
        select(Expense).where(
            cast(Expense.date, Date) >= q_start,
            cast(Expense.date, Date) <= q_end,
        )
    ).scalars().all()
    for e in exps:
        vat_in += dec(e.vat_amount)

    return {
        "q_start": q_start, "q_end": q_end,
        "sales_21": sales_21.quantize(Decimal("0.01")),
        "sales_9": sales_9.quantize(Decimal("0.01")),
        "sales_0": sales_0.quantize(Decimal("0.01")),
        "vat_out": vat_out.quantize(Decimal("0.01")),
        "vat_in": vat_in.quantize(Decimal("0.01")),
        "vat_due": (vat_out - vat_in).quantize(Decimal("0.01")),
    }

def compliance_warnings(company: CompanySettings, inv: Invoice) -> list[str]:
    warns = []
    if not company.company_name or not company.address or not company.city or not company.postcode:
        warns.append("Company name/address/postcode/city missing in Company Settings.")
    if not company.kvk: warns.append("KVK number missing in Company Settings.")
    if not company.rsin: warns.append("RSIN missing in Company Settings.")
    if inv.vat_scheme != "EXEMPT":
        if not company.vat_number:
            warns.append("Supplier VAT number missing in Company Settings.")
        elif "[" in (company.vat_number or ""):
            warns.append("Supplier VAT number looks like a placeholder. Replace it with your real VAT number.")
    if not company.iban or not company.bic:
        warns.append("IBAN/BIC missing in Company Settings.")
    if not inv.client_name or not inv.client_address:
        warns.append("Customer name and address are required.")
    if inv.vat_scheme == "REVERSE_CHARGE_EU" and not inv.client_vat_number:
        warns.append("Customer VAT number required for reverse charge (BTW verlegd).")
    if inv.supply_date is None:
        warns.append("Supply/performance date is required.")
    if not inv.lines:
        warns.append("Invoice must contain at least one line.")
    return warns

# ----------------------------
# Stripe helpers
# ----------------------------
_ZERO_DECIMAL = {"BIF","CLP","DJF","GNF","JPY","KMF","KRW","MGA","PYG","RWF","UGX","VND","VUV","XAF","XOF","XPF","HUF"}

def _to_minor_units(amount: Decimal, currency: str) -> int:
    c = (currency or "EUR").upper()
    if c in _ZERO_DECIMAL:
        return int(Decimal(amount).quantize(Decimal("1")))
    return int((Decimal(amount) * Decimal("100")).quantize(Decimal("1")))

def _get_stripe_secret_key() -> str | None:
    # Accept your existing env var names
    return (
        os.environ.get("STRIPE_API_KEY")
        or os.environ.get("STRIPE_SECRET_KEY")
        or os.environ.get("STRIPE_SECRET")
        or os.environ.get("STRIPE_SK")
    )

def create_or_get_stripe_payment_link(inv: Invoice, *, success_url: str | None = None) -> str | None:
    """
    Creates and attaches a Stripe Payment Link to the invoice (preferred),
    or falls back to a Checkout Session. Raises RuntimeError with a clear message
    if Stripe is not configured/installed or amount is invalid.
    Does not commit; caller should commit after setting fields on inv.
    """
    api_key = _get_stripe_secret_key()
    if not api_key:
        raise RuntimeError("Stripe not configured: set STRIPE_SECRET_KEY (or STRIPE_API_KEY).")

    try:
        import stripe
    except ImportError:
        raise RuntimeError("Stripe library not installed. Add 'stripe>=7' to requirements.txt.")

    stripe.api_key = api_key

    # Reuse if already created
    if getattr(inv, "stripe_payment_url", None):
        return inv.stripe_payment_url

    ensure_invoice_totals(inv)
    if inv.gross_total is None or Decimal(inv.gross_total) <= Decimal("0"):
        raise RuntimeError("Invoice amount must be greater than zero to create a payment link.")

    currency = (inv.currency or "EUR").lower()
    amount_minor = _to_minor_units(inv.gross_total, inv.currency)
    if amount_minor <= 0:
        raise RuntimeError("Invoice amount is below Stripe minimum for this currency.")

    product_name = f"Invoice {inv.invoice_no}"

    success_default = os.environ.get("STRIPE_SUCCESS_URL") or ""
    cancel_default = os.environ.get("STRIPE_CANCEL_URL") or success_default
    success_url = success_url or success_default or "https://example.com/thanks"

    # Prefer Payment Links (persistent)
    try:
        pl = stripe.PaymentLink.create(
            line_items=[{
                "price_data": {
                    "currency": currency,
                    "product_data": {"name": product_name},
                    "unit_amount": amount_minor,
                },
                "quantity": 1,
            }],
            metadata={"invoice_id": str(inv.id), "invoice_no": inv.invoice_no},
            after_completion={"type": "redirect", "redirect": {"url": success_url}},
            allow_promotion_codes=False,
        )
        inv.stripe_payment_link_id = pl.get("id")
        inv.stripe_payment_url = pl.get("url")
        return inv.stripe_payment_url
    except Exception as e:
        # Fallback to Checkout Session (time-limited)
        try:
            session = stripe.checkout.Session.create(
                mode="payment",
                success_url=success_url,
                cancel_url=cancel_default or success_url,
                line_items=[{
                    "price_data": {
                        "currency": currency,
                        "product_data": {"name": product_name},
                        "unit_amount": amount_minor,
                    },
                    "quantity": 1,
                }],
                metadata={"invoice_id": str(inv.id), "invoice_no": inv.invoice_no},
            )
            inv.stripe_checkout_session_id = session.get("id")
            inv.stripe_payment_url = session.get("url")
            return inv.stripe_payment_url
        except Exception as e2:
            raise RuntimeError(f"Stripe error: {e2}")
