# app/pages/course.py
import os
from datetime import datetime, timezone

from flask import request, session, redirect, url_for, flash
from sqlalchemy import text

from ..core import render, engine, get_db
from ..accounting import ensure_company

# Branding / pricing from env
BRAND_NAME = os.getenv("BRAND_NAME", "Ai For Impact")
BRAND_LOGO_URL = os.getenv("BRAND_LOGO_URL", "https://i.imgur.com/STm5VaG.png")
POWERED_BY = os.getenv("POWERED_BY", "Climate Fundraising Platform B.V.")
COURSE_ACCESS_CODE = os.getenv("COURSE_ACCESS_CODE", "letmein")

BASE_PRICE_EUR = int(os.getenv("BASE_PRICE_EUR", "480"))
PROMO_CODE = os.getenv("PROMO_CODE", "IMPACT-439")
PROMO_PRICE_EUR = int(os.getenv("PROMO_PRICE_EUR", "439"))

COURSES = [{
    "code": "AML-RTD",
    "title": "Advanced Machine Learning and Real-Time Deployment",
    "price_eur": BASE_PRICE_EUR
}]

JOB_ROLES = [
    "Student","Software Engineer / Developer","Data Analyst / Data Scientist","Product Manager",
    "Researcher / Academic","Business Owner / Founder","Marketing / Growth","Operations / Supply Chain",
    "Finance / Analyst","Other",
]

GENDER_CHOICES = ["Female", "Male", "Prefer not to say"]
REFERRAL_CHOICES = [
    "Search","YouTube","TikTok/Instagram","X/Twitter","LinkedIn",
    "Friend/Colleague","Event/Conference","Partner","Newsletter","Other"
]
MAXLEN_LONG = 500

def _s(x):
    if x is None:
        return None
    x = x.strip()
    return x or None

def _clip500(x):
    x = _s(x)
    return x[:MAXLEN_LONG] if x and len(x) > MAXLEN_LONG else x

def _compute_price(promo_input):
    if promo_input and promo_input.strip().lower() == PROMO_CODE.lower():
        return PROMO_PRICE_EUR, PROMO_CODE
    return BASE_PRICE_EUR, None

def _ensure_schema():
    """Idempotently ensure enums, table, trigger, and indexes exist in the public schema."""
    with engine.begin() as conn:
        # --- Enums (schema-qualified, idempotent) ---
        conn.execute(text("""
            CREATE TYPE IF NOT EXISTS public.gender_enum AS ENUM
            ('Female','Male','Prefer not to say');
        """))
        # Add values if the enum was created earlier with fewer labels
        conn.execute(text("ALTER TYPE public.gender_enum ADD VALUE IF NOT EXISTS 'Female';"))
        conn.execute(text("ALTER TYPE public.gender_enum ADD VALUE IF NOT EXISTS 'Male';"))
        conn.execute(text("ALTER TYPE public.gender_enum ADD VALUE IF NOT EXISTS 'Prefer not to say';"))

        conn.execute(text("""
            CREATE TYPE IF NOT EXISTS public.referral_source_enum AS ENUM
            ('Search','YouTube','TikTok/Instagram','X/Twitter','LinkedIn',
             'Friend/Colleague','Event/Conference','Partner','Newsletter','Other');
        """))
        conn.execute(text("ALTER TYPE public.referral_source_enum ADD VALUE IF NOT EXISTS 'Search';"))
        conn.execute(text("ALTER TYPE public.referral_source_enum ADD VALUE IF NOT EXISTS 'YouTube';"))
        conn.execute(text("ALTER TYPE public.referral_source_enum ADD VALUE IF NOT EXISTS 'TikTok/Instagram';"))
        conn.execute(text("ALTER TYPE public.referral_source_enum ADD VALUE IF NOT EXISTS 'X/Twitter';"))
        conn.execute(text("ALTER TYPE public.referral_source_enum ADD VALUE IF NOT EXISTS 'LinkedIn';"))
        conn.execute(text("ALTER TYPE public.referral_source_enum ADD VALUE IF NOT EXISTS 'Friend/Colleague';"))
        conn.execute(text("ALTER TYPE public.referral_source_enum ADD VALUE IF NOT EXISTS 'Event/Conference';"))
        conn.execute(text("ALTER TYPE public.referral_source_enum ADD VALUE IF NOT EXISTS 'Partner';"))
        conn.execute(text("ALTER TYPE public.referral_source_enum ADD VALUE IF NOT EXISTS 'Newsletter';"))
        conn.execute(text("ALTER TYPE public.referral_source_enum ADD VALUE IF NOT EXISTS 'Other';"))

        # --- Table (schema-qualified) ---
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS public.registrations (
          id BIGSERIAL PRIMARY KEY,
          created_at TIMESTAMPTZ NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL,
          user_email VARCHAR(255),
          first_name VARCHAR(100) NOT NULL,
          middle_name VARCHAR(100),
          last_name VARCHAR(100) NOT NULL,
          age INTEGER,
          gender public.gender_enum,
          gender_other_note VARCHAR(120),
          phone VARCHAR(50),
          address_line1 VARCHAR(255),
          address_line2 VARCHAR(255),
          city VARCHAR(100),
          state VARCHAR(100),
          postal_code VARCHAR(40),
          country VARCHAR(100),
          job_title VARCHAR(150),
          company VARCHAR(150),
          ai_current_involvement TEXT,
          ai_goals_wish_to_achieve TEXT,
          ai_datasets_available TEXT,
          referral_source public.referral_source_enum,
          referral_details VARCHAR(255),
          reason_choose_us TEXT,
          invoice_name VARCHAR(150),
          invoice_company VARCHAR(150),
          invoice_vat_id VARCHAR(64),
          invoice_email VARCHAR(255),
          invoice_phone VARCHAR(50),
          invoice_addr_line1 VARCHAR(255),
          invoice_addr_line2 VARCHAR(255),
          invoice_city VARCHAR(100),
          invoice_state VARCHAR(100),
          invoice_postal_code VARCHAR(40),
          invoice_country VARCHAR(100),
          course_session_code VARCHAR(80),
          notes TEXT,
          consent_contact_ok BOOLEAN NOT NULL DEFAULT TRUE,
          consent_marketing_ok BOOLEAN NOT NULL DEFAULT FALSE,
          data_processing_ok BOOLEAN NOT NULL DEFAULT FALSE
        );"""))

        # --- Trigger function & trigger (schema-qualified) ---
        conn.execute(text("""
        CREATE OR REPLACE FUNCTION public.touch_updated_at() RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
        """))
        conn.execute(text("DROP TRIGGER IF EXISTS trg_registrations_touch ON public.registrations;"))
        conn.execute(text("""
        CREATE TRIGGER trg_registrations_touch
        BEFORE UPDATE ON public.registrations
        FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
        """))

        # --- Indexes ---
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_reg_email   ON public.registrations(user_email);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_reg_created ON public.registrations(created_at);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_reg_course  ON public.registrations(course_session_code);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_reg_ref     ON public.registrations(referral_source);"))

def register(app):
    _ensure_schema()  # safe to call repeatedly

    @app.get("/course")
    def course_index():
        db = get_db()
        company = ensure_company(db)
        submitted = request.args.get("submitted") == "1"
        return render("course.html",
                      company=company,
                      brand_name=BRAND_NAME, brand_logo_url=BRAND_LOGO_URL, powered_by=POWERED_BY,
                      signed_in=session.get("course_signed_in", False),
                      user_email=session.get("course_email"),
                      genders=GENDER_CHOICES, referrals=REFERRAL_CHOICES, courses=COURSES, job_roles=JOB_ROLES,
                      base_price_eur=BASE_PRICE_EUR, promo_code=PROMO_CODE, promo_price_eur=PROMO_PRICE_EUR,
                      submitted=submitted, errors=[])

    @app.post("/course/signin")
    def course_signin():
        code = _s(request.form.get("access_code"))
        email = _s(request.form.get("user_email"))
        if code == COURSE_ACCESS_CODE:
            session["course_signed_in"] = True
            session["course_email"] = email
            flash("Signed in. Please complete your registration.", "success")
        else:
            flash("Invalid course access code.", "error")
        return redirect(url_for("course_index"))

    @app.get("/course/logout")
    def course_logout():
        session.pop("course_signed_in", None)
        session.pop("course_email", None)
        flash("Signed out.", "success")
        return redirect(url_for("course_index"))

    @app.post("/course/register")
    def course_register():
        if not session.get("course_signed_in"):
            flash("Please sign in with the course access code.", "error")
            return redirect(url_for("course_index"))

        errors = []
        first = _s(request.form.get("first_name"))
        last  = _s(request.form.get("last_name"))
        if not first: errors.append("First name is required.")
        if not last:  errors.append("Last name is required.")

        age = None
        ar = _s(request.form.get("age"))
        if ar:
            try:
                age = int(ar)
                if not (10 <= age <= 120): errors.append("Age must be between 10 and 120.")
            except ValueError:
                errors.append("Age must be a whole number.")

        gender = _s(request.form.get("gender"))
        if gender not in GENDER_CHOICES:
            gender = None

        course_session_code = _s(request.form.get("course_session_code"))
        if course_session_code not in {c["code"] for c in COURSES}:
            errors.append("Please select a valid course.")

        promo_input = _s(request.form.get("promo_code"))
        final_price_eur, applied_promo = _compute_price(promo_input)

        data_processing_ok = bool(request.form.get("data_processing_ok"))
        if not data_processing_ok:
            errors.append("You must consent to data processing to register.")

        job_title = _s(request.form.get("job_title"))
        if job_title not in JOB_ROLES:
            job_title = "Other"

        ai_current_involvement   = _clip500(request.form.get("ai_current_involvement"))
        ai_goals_wish_to_achieve = _clip500(request.form.get("ai_goals_wish_to_achieve"))
        ai_datasets_available    = _clip500(request.form.get("ai_datasets_available"))
        reason_choose_us         = _clip500(request.form.get("reason_choose_us"))
        notes                    = _clip500(request.form.get("notes"))

        # Parse checkboxes explicitly (HTML checkboxes send a value like 'on' when checked)
        consent_contact_ok   = request.form.get("consent_contact_ok")   is not None
        consent_marketing_ok = request.form.get("consent_marketing_ok") is not None

        vals = {
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "user_email": _s(request.form.get("user_email")) or session.get("course_email"),
            "first_name": first,
            "middle_name": _s(request.form.get("middle_name")),
            "last_name": last,
            "age": age,
            "gender": gender,  # cast below
            "gender_other_note": None,
            "phone": _s(request.form.get("phone")),
            "address_line1": _s(request.form.get("address_line1")),
            "address_line2": _s(request.form.get("address_line2")),
            "city": _s(request.form.get("city")),
            "state": _s(request.form.get("state")),
            "postal_code": _s(request.form.get("postal_code")),
            "country": _s(request.form.get("country")),
            "job_title": job_title,
            "company": _s(request.form.get("company")),
            "ai_current_involvement": ai_current_involvement,
            "ai_goals_wish_to_achieve": ai_goals_wish_to_achieve,
            "ai_datasets_available": ai_datasets_available,
            "referral_source": _s(request.form.get("referral_source")),  # cast below
            "referral_details": (f"PROMO:{applied_promo};PRICE_EUR:{final_price_eur}"
                                 if applied_promo else f"PRICE_EUR:{final_price_eur}"),
            "reason_choose_us": reason_choose_us,
            "invoice_name": _s(request.form.get("invoice_name")),
            "invoice_company": _s(request.form.get("invoice_company")),
            "invoice_vat_id": _s(request.form.get("invoice_vat_id")),
            "invoice_email": _s(request.form.get("invoice_email")),
            "invoice_phone": _s(request.form.get("invoice_phone")),
            "invoice_addr_line1": _s(request.form.get("invoice_addr_line1")),
            "invoice_addr_line2": _s(request.form.get("invoice_addr_line2")),
            "invoice_city": _s(request.form.get("invoice_city")),
            "invoice_state": _s(request.form.get("invoice_state")),
            "invoice_postal_code": _s(request.form.get("invoice_postal_code")),
            "invoice_country": _s(request.form.get("invoice_country")),
            "course_session_code": course_session_code,
            "notes": notes,
            "consent_contact_ok": consent_contact_ok,
            "consent_marketing_ok": consent_marketing_ok,
            "data_processing_ok": data_processing_ok,
        }

        if request.form.get("billing_same_as_personal") is not None:
            fullname = " ".join([v for v in [vals["first_name"], vals["last_name"]] if v])
            vals["invoice_name"]        = vals["invoice_name"]        or fullname
            vals["invoice_email"]       = vals["invoice_email"]       or vals["user_email"]
            vals["invoice_phone"]       = vals["invoice_phone"]       or vals["phone"]
            vals["invoice_addr_line1"]  = vals["invoice_addr_line1"]  or vals["address_line1"]
            vals["invoice_addr_line2"]  = vals["invoice_addr_line2"]  or vals["address_line2"]
            vals["invoice_city"]        = vals["invoice_city"]        or vals["city"]
            vals["invoice_state"]       = vals["invoice_state"]       or vals["state"]
            vals["invoice_postal_code"] = vals["invoice_postal_code"] or vals["postal_code"]
            vals["invoice_country"]     = vals["invoice_country"]     or vals["country"]

        if errors:
            db = get_db()
            company = ensure_company(db)
            return render("course.html",
                          company=company,
                          brand_name=BRAND_NAME, brand_logo_url=BRAND_LOGO_URL, powered_by=POWERED_BY,
                          signed_in=True, user_email=session.get("course_email"),
                          genders=GENDER_CHOICES, referrals=REFERRAL_CHOICES, courses=COURSES, job_roles=JOB_ROLES,
                          base_price_eur=BASE_PRICE_EUR, promo_code=PROMO_CODE, promo_price_eur=PROMO_PRICE_EUR,
                          submitted=False, errors=errors, form_data=request.form), 400

        sql = text("""
        INSERT INTO public.registrations (
          created_at, updated_at,
          user_email, first_name, middle_name, last_name, age,
          gender, gender_other_note, phone,
          address_line1, address_line2, city, state, postal_code, country,
          job_title, company,
          ai_current_involvement, ai_goals_wish_to_achieve, ai_datasets_available,
          referral_source, referral_details, reason_choose_us,
          invoice_name, invoice_company, invoice_vat_id, invoice_email, invoice_phone,
          invoice_addr_line1, invoice_addr_line2, invoice_city, invoice_state, invoice_postal_code, invoice_country,
          course_session_code, notes,
          consent_contact_ok, consent_marketing_ok, data_processing_ok
        ) VALUES (
          :created_at, :updated_at,
          :user_email, :first_name, :middle_name, :last_name, :age,
          CAST(:gender AS public.gender_enum), :gender_other_note, :phone,
          :address_line1, :address_line2, :city, :state, :postal_code, :country,
          :job_title, :company,
          :ai_current_involvement, :ai_goals_wish_to_achieve, :ai_datasets_available,
          CAST(:referral_source AS public.referral_source_enum), :referral_details, :reason_choose_us,
          :invoice_name, :invoice_company, :invoice_vat_id, :invoice_email, :invoice_phone,
          :invoice_addr_line1, :invoice_addr_line2, :invoice_city, :invoice_state, :invoice_postal_code, :invoice_country,
          :course_session_code, :notes,
          :consent_contact_ok, :consent_marketing_ok, :data_processing_ok
        )
        """)

        try:
            _ensure_schema()
            with engine.begin() as conn:
                conn.execute(sql, vals)
            flash(f"Thank you! Your registration has been recorded. Final price: €{final_price_eur}"
                  + (f" (promo {applied_promo})" if applied_promo else ""), "success")
            return redirect(url_for("course_index") + "?submitted=1")
        except Exception as e:
            # Minimal visibility in logs to help debugging if anything goes wrong
            print("course_register error:", repr(e))
            flash("Sorry, something went wrong saving your registration.", "error")
            return redirect(url_for("course_index"))
