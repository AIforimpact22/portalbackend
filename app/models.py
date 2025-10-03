# app/models.py
from datetime import date
from decimal import Decimal
from sqlalchemy import (
    Column, Integer, BigInteger, String, Date, Numeric, ForeignKey, Text,
    UniqueConstraint, text
)
from sqlalchemy.orm import relationship
from .core import Base


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
    legacy_amount = Column("amount", Numeric(12, 2), nullable=True, server_default=text("0"))

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

    net_total = Column(Numeric(12, 2), nullable=False, default=0)
    vat_total = Column(Numeric(12, 2), nullable=False, default=0)
    gross_total = Column(Numeric(12, 2), nullable=False, default=0)

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
    date = Column(Date, nullable=False, default=date.today)
    amount = Column(Numeric(12, 2), nullable=False)
    method = Column(String(32), nullable=False, default="bank")  # bank, cash, western_union, other
    reference = Column(String(128))
    note = Column(Text)

    invoice = relationship("Invoice", back_populates="payments")


class Expense(Base):
    __tablename__ = "expenses"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, default=date.today)
    vendor = Column(String(160), nullable=False)
    category = Column(String(64), nullable=False)  # Freelancer, Subcontractor, Software, Travel...
    description = Column(Text, default="")
    currency = Column(String(8), nullable=False, default="EUR")
    vat_rate = Column(Numeric(5, 2), nullable=False, default=21.00)
    amount_net = Column(Numeric(12, 2), nullable=False, default=0)
    vat_amount = Column(Numeric(12, 2), nullable=False, default=0)
    amount_gross = Column(Numeric(12, 2), nullable=False, default=0)
    receipt_path = Column(String(256))

    invoice_id = Column(BigInteger, ForeignKey("invoices.id"), nullable=True)
    invoice = relationship("Invoice", back_populates="expenses")

    pay_method = Column(String(32), default="bank")
    pay_reference = Column(String(128))
