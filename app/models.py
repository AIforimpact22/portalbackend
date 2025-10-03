# app/models.py
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from sqlalchemy import (
    Column, Integer, BigInteger, String, Date, Numeric, ForeignKey, Text,
    UniqueConstraint, text, cast
)
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import relationship
from .core import Base


class TextDecimal(TypeDecorator):
    """Store Decimal values in legacy text columns while exposing them as decimals."""

    impl = Text
    cache_ok = True

    def __init__(self, precision: int = 12, scale: int = 2, **kwargs):
        super().__init__(**kwargs)
        self.precision = precision
        self.scale = scale
        self._numeric = Numeric(precision, scale)
        self._quant = Decimal(1).scaleb(-scale)

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(Text())

    @property
    def python_type(self):  # type: ignore[override]
        return Decimal

    def _coerce_decimal(self, value):
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        if isinstance(value, (int, float)):
            return Decimal(str(value))
        if isinstance(value, str):
            if value.strip() == "":
                return None
            try:
                return Decimal(value.strip())
            except (InvalidOperation, ValueError):
                return None
        try:
            return Decimal(value)  # type: ignore[arg-type]
        except (InvalidOperation, ValueError, TypeError):
            return None

    def process_bind_param(self, value, dialect):  # type: ignore[override]
        dec_value = self._coerce_decimal(value)
        if dec_value is None:
            return None
        try:
            quantized = dec_value.quantize(self._quant)
        except (InvalidOperation, ValueError):
            quantized = Decimal("0").quantize(self._quant)
        return format(quantized, f".{self.scale}f")

    def process_result_value(self, value, dialect):  # type: ignore[override]
        dec_value = self._coerce_decimal(value)
        if dec_value is None:
            return None
        try:
            return dec_value.quantize(self._quant)
        except (InvalidOperation, ValueError):
            return Decimal("0").quantize(self._quant)

    def column_expression(self, column):  # type: ignore[override]
        return cast(column, self._numeric)

    def coerce_compared_value(self, op, value):  # type: ignore[override]
        return self._numeric


class TextDate(TypeDecorator):
    """Store ISO date strings in legacy text columns while exposing them as ``date`` objects."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(Text())

    @property
    def python_type(self):  # type: ignore[override]
        return date

    def _coerce_date(self, value):
        if value is None:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            try:
                return date.fromisoformat(value)
            except ValueError:
                try:
                    return datetime.fromisoformat(value).date()
                except ValueError:
                    return None
        return None

    def process_bind_param(self, value, dialect):  # type: ignore[override]
        coerced = self._coerce_date(value)
        if coerced is None:
            return None
        return coerced.isoformat()

    def process_result_value(self, value, dialect):  # type: ignore[override]
        coerced = self._coerce_date(value)
        return coerced

    def column_expression(self, column):  # type: ignore[override]
        return cast(column, Date())

    def coerce_compared_value(self, op, value):  # type: ignore[override]
        return Date()


class CompanySettings(Base):
    __tablename__ = "company_settings"
    id = Column(Integer, primary_key=True)
    company_name = Column(String(160), nullable=False, default="")
    address = Column(Text, default="")
    kvk = Column(String(32), default="")
    rsin = Column(String(32), default="")
    vat_number = Column(String(32), default="")
    iban = Column(String(64), default="")
    bic = Column(String(32), default="")
    invoice_prefix = Column(String(16), default="INV")
    city = Column(String(80), default="")
    postcode = Column(String(24), default="")
    country = Column(String(80), default="Netherlands")


class InvoiceSequence(Base):
    __tablename__ = "invoice_sequences"
    id = Column(Integer, primary_key=True)
    year = Column(Integer, nullable=False)
    prefix = Column(String(16), nullable=False, default="INV")
    last_seq = Column(Integer, nullable=False, default=0)
    __table_args__ = (UniqueConstraint("year", "prefix", name="uq_year_prefix"),)


class Invoice(Base):
    __tablename__ = "invoices"

    # Use BigInteger to match DB (psql showed bigint)
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Legacy live-DB column; keep mapped & synced with gross_total
    legacy_amount = Column("amount", TextDecimal(12, 2), nullable=True, server_default=text("0"))

    invoice_no = Column(String(64), unique=True, nullable=False)
    issue_date = Column(Date, nullable=False, default=date.today)
    supply_date = Column(Date, nullable=False, default=date.today)
    due_date = Column(Date, nullable=False)
    currency = Column(String(8), nullable=False, default="EUR")

    client_name = Column(String(160), nullable=False)
    client_address = Column(Text, default="")
    client_vat_number = Column(String(40), default="")

    vat_scheme = Column(String(32), nullable=False, default="STANDARD")  # STANDARD/REVERSE_CHARGE_EU/ZERO_OUTSIDE_EU/EXEMPT
    notes = Column(Text, default="")
    status = Column(String(16), nullable=False, default="SENT")  # DRAFT/SENT/PARTIAL/PAID/CLOSED

    net_total = Column(TextDecimal(12, 2), nullable=False, default=0)
    vat_total = Column(TextDecimal(12, 2), nullable=False, default=0)
    gross_total = Column(TextDecimal(12, 2), nullable=False, default=0)

    # Stripe payment info
    stripe_payment_url = Column(Text)                 # final URL to send to client
    stripe_payment_link_id = Column(String(64))       # when using Payment Links
    stripe_checkout_session_id = Column(String(64))   # fallback via Checkout

    # NEW: link to customers.registrations (cross-DB, so no FK)
    customer_registration_id = Column(BigInteger, index=True, nullable=True)

    # Relationships
    lines = relationship("InvoiceLine", back_populates="invoice", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="invoice", cascade="all, delete-orphan")
    expenses = relationship("Expense", back_populates="invoice", cascade="all, delete-orphan")

    @property
    def paid_total(self) -> Decimal:
        total = Decimal("0.00")
        for p in self.payments:
            total += Decimal(p.amount)
        return total.quantize(Decimal("0.01"))

    @property
    def balance(self) -> Decimal:
        return (Decimal(self.gross_total) - self.paid_total).quantize(Decimal("0.01"))


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    invoice_id = Column(BigInteger, ForeignKey("invoices.id"), nullable=False)

    description = Column(Text, nullable=False)
    qty = Column(Numeric(12, 2), nullable=False, default=1)
    unit_price = Column(Numeric(12, 2), nullable=False, default=0)
    vat_rate = Column(Numeric(5, 2), nullable=False, default=21.00)
    line_net = Column(Numeric(12, 2), nullable=False, default=0)
    line_vat = Column(Numeric(12, 2), nullable=False, default=0)
    line_total = Column(Numeric(12, 2), nullable=False, default=0)

    invoice = relationship("Invoice", back_populates="lines")


class Payment(Base):
    __tablename__ = "payments"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    invoice_id = Column(BigInteger, ForeignKey("invoices.id"), nullable=True)
    date = Column(TextDate(), nullable=False, default=date.today)
    amount = Column(TextDecimal(12, 2), nullable=False)
    method = Column(String(32), nullable=False, default="bank")  # bank, cash, western_union, other
    reference = Column(String(128))
    note = Column(Text)

    invoice = relationship("Invoice", back_populates="payments")


class Expense(Base):
    __tablename__ = "expenses"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Legacy Render DB stores ISO strings; use TextDate wrapper for compatibility
    date = Column(TextDate(), nullable=False, default=date.today)
    vendor = Column(String(160), nullable=False)
    category = Column(String(64), nullable=False)  # Freelancer, Subcontractor, Software, Travel...
    description = Column(Text, default="")
    currency = Column(String(8), nullable=False, default="EUR")
    # Persisted as TEXT historically; wrap with TextDecimal to coerce safely
    vat_rate = Column(TextDecimal(5, 2), nullable=False, default=21.00)
    amount_net = Column(TextDecimal(12, 2), nullable=False, default=0)
    vat_amount = Column(TextDecimal(12, 2), nullable=False, default=0)
    amount_gross = Column(TextDecimal(12, 2), nullable=False, default=0)
    receipt_path = Column(String(256))

    invoice_id = Column(BigInteger, ForeignKey("invoices.id"), nullable=True)
    invoice = relationship("Invoice", back_populates="expenses")

    pay_method = Column(String(32), default="bank")
    pay_reference = Column(String(128))
