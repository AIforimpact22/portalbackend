import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from datetime import date
from unittest.mock import patch

# Configure database before importing application modules
_db_fd, _db_path = tempfile.mkstemp()
os.close(_db_fd)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_path}")

# Ensure SQLite returns string values for DATE columns to match production behaviour
sqlite3.register_converter("DATE", lambda value: value.decode() if isinstance(value, (bytes, bytearray)) else value)
sqlite3.register_converter("DATETIME", lambda value: value.decode() if isinstance(value, (bytes, bytearray)) else value)

# Ensure the application package is importable when tests are run directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app import create_app
from app.core import get_db
from app.models import Expense, Payment


def test_dashboard_handles_blank_amounts():
    app = create_app()

    with app.app_context():
        db = get_db()
        # Seed records using the ORM so required defaults are applied
        legacy_date = date(1999, 1, 1)
        expense = Expense(
            id=1,
            date=legacy_date,
            vendor="Legacy Vendor",
            category="Software",
            description="Test expense",
            currency="EUR",
        )
        payment = Payment(
            id=1,
            date=legacy_date,
            amount="0",
            method="bank",
            reference="legacy",
        )
        db.add(expense)
        db.add(payment)
        expense_id = expense.id
        payment_id = payment.id
        db.commit()

        # Simulate legacy/invalid values persisted as empty strings
        db.execute(
            text("UPDATE expenses SET amount_gross='' WHERE id=:id"),
            {"id": expense_id},
        )
        db.execute(
            text("UPDATE payments SET amount='' WHERE id=:id"),
            {"id": payment_id},
        )
        db.commit()

    client = app.test_client()
    with client.session_transaction() as session:
        session["auth_ok"] = True

    text_date_type = type(Expense.date.type)
    with patch.object(text_date_type, "column_expression", lambda self, column: column):
        response = client.get("/")
    assert response.status_code == 200
    assert b"Recent Payments" in response.data
