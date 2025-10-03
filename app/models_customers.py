# app/models_customers.py
"""SQLAlchemy models for the read-only customers database."""

from sqlalchemy.orm import declarative_base
from sqlalchemy import (
    Column,
    BigInteger,
    Integer,
    SmallInteger,
    String,
    Text,
    Boolean,
    DateTime,
    ARRAY,
    PrimaryKeyConstraint,
)

try:  # pragma: no cover - only available when PostgreSQL dialect installed
    from sqlalchemy.dialects.postgresql import INET
except Exception:  # Fallback for SQLite/unit tests where INET isn't available
    from sqlalchemy import String as INET  # type: ignore

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


class ActivityLog(CustomersBase):
    __tablename__ = "activity_log"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, nullable=False)
    course_id = Column(Integer, nullable=False)
    lesson_uid = Column(Text)
    a_type = Column(String(64))
    created_at = Column(DateTime(timezone=True), nullable=False)
    score_points = Column(Text)
    passed = Column(Boolean)
    payload = Column(Text)


class Course(CustomersBase):
    __tablename__ = "courses"

    id = Column(Integer, primary_key=True)
    title = Column(Text, nullable=False)
    created_by = Column(Integer)
    is_published = Column(Boolean, nullable=False, default=False)
    published_at = Column(DateTime(timezone=True))
    structure = Column(Text)
    created_at = Column(DateTime(timezone=True))
    conversation = Column(Text)


class Enrollment(CustomersBase):
    __tablename__ = "enrollments"

    user_id = Column(Integer, nullable=False)
    course_id = Column(Integer, nullable=False)
    status = Column(String(32))
    enrolled_at = Column(DateTime(timezone=True))
    progress = Column(Text)

    __table_args__ = (
        PrimaryKeyConstraint("user_id", "course_id", name="enrollments_pkey"),
    )


class ExamCache(CustomersBase):
    __tablename__ = "exam_cache"

    exam_id = Column(Text, primary_key=True)
    course_id = Column(Integer, nullable=False)
    week = Column(SmallInteger)
    content_sha256 = Column(Text)
    payload = Column(Text)
    created_at = Column(DateTime(timezone=True))


class RegistrationProgress(CustomersBase):
    __tablename__ = "registration_progress"

    id = Column(BigInteger, primary_key=True)
    course_id = Column(Integer, nullable=False)
    max_unlocked_index = Column(Integer)
    last_lesson_uid = Column(Text)
    last_seen_at = Column(DateTime(timezone=True))


class Subscription(CustomersBase):
    __tablename__ = "subscriptions"

    id = Column(BigInteger, primary_key=True)
    email = Column(String(255), nullable=False)
    plan_code = Column(String(64))
    status = Column(String(32))
    source = Column(String(64))
    double_opt_in_token = Column(Text)
    confirmed_at = Column(DateTime(timezone=True))
    unsubscribed_at = Column(DateTime(timezone=True))
    reason_unsub = Column(Text)
    consent_marketing = Column(Boolean)
    locale = Column(String(16))
    ip_signup = Column(INET)
    user_agent_signup = Column(Text)
    tags = Column(ARRAY(Text))
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class AllowedUser(CustomersBase):
    __tablename__ = "allowed_users"

    email = Column(String(255), primary_key=True)
    role = Column(String(64))
    page_access = Column(Text)
    created_at = Column(DateTime(timezone=True))


class UserModuleRule(CustomersBase):
    __tablename__ = "user_module_rules"

    email = Column(String(255), primary_key=True)
    course_id = Column(Integer, primary_key=True)
    allowed_modules = Column(Text)
    updated_at = Column(DateTime(timezone=True))

    __table_args__ = (
        PrimaryKeyConstraint("email", "course_id", name="user_module_rules_pkey"),
    )


class UserPageRule(CustomersBase):
    __tablename__ = "user_page_rules"

    email = Column(String(255), primary_key=True)
    page_access = Column(Text)
    updated_at = Column(DateTime(timezone=True))


class CustomerUser(CustomersBase):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False)
    full_name = Column(String(255))
    role = Column(String(64))
    created_at = Column(DateTime(timezone=True))
