# app/models_customers.py
from sqlalchemy.orm import declarative_base
from sqlalchemy import (
    Column, BigInteger, String, Integer, Text, Boolean, DateTime
)

# Separate Base so create_all for your main DB won't touch the customers schema
CustomersBase = declarative_base()


class Registration(CustomersBase):
    __tablename__ = "registrations"

    # Identity / audit
    id                 = Column(BigInteger, primary_key=True)
    created_at         = Column(DateTime(timezone=True))
    updated_at         = Column(DateTime(timezone=True))

    # Contact / basic profile
    user_email         = Column(String(255))
    first_name         = Column(String(100), nullable=False)
    middle_name        = Column(String(100))
    last_name          = Column(String(100), nullable=False)
    age                = Column(Integer)
    gender             = Column(String(64))       # stored as postgres enum, map as string for read
    gender_other_note  = Column(String(120))
    phone              = Column(String(50))

    # Address
    address_line1      = Column(String(255))
    address_line2      = Column(String(255))
    city               = Column(String(100))
    state              = Column(String(100))
    postal_code        = Column(String(40))
    country            = Column(String(100))

    # Work / role
    job_title          = Column(String(150))
    company            = Column(String(150))

    # AI-focused intake
    ai_current_involvement    = Column(Text)
    ai_goals_wish_to_achieve  = Column(Text)
    ai_datasets_available     = Column(Text)

    # Marketing & fit
    referral_source     = Column(String(64))      # postgres enum -> map as string
    referral_details    = Column(String(255))
    reason_choose_us    = Column(Text)

    # Invoice / billing
    invoice_name        = Column(String(150))
    invoice_company     = Column(String(150))
    invoice_vat_id      = Column(String(64))
    invoice_email       = Column(String(255))
    invoice_phone       = Column(String(50))
    invoice_addr_line1  = Column(String(255))
    invoice_addr_line2  = Column(String(255))
    invoice_city        = Column(String(100))
    invoice_state       = Column(String(100))
    invoice_postal_code = Column(String(40))
    invoice_country     = Column(String(100))

    # Cohort / admin
    course_session_code = Column(String(80))
    notes               = Column(Text)

    # Consents
    consent_contact_ok   = Column(Boolean, default=True)
    consent_marketing_ok = Column(Boolean, default=False)
    data_processing_ok   = Column(Boolean, default=True)

    # Enrollment status
    enrollment_status   = Column(String(32), default="pending", nullable=False)
    # DB enum is ("pending", "accepted", "rejected", "waitlist")

    # Helpers
    @property
    def full_name(self) -> str:
        if self.middle_name:
            return f"{self.first_name} {self.middle_name} {self.last_name}".strip()
        return f"{self.first_name} {self.last_name}".strip()
