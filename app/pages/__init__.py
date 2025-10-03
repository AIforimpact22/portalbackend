# app/pages/__init__.py
from . import health, auth, dashboard, settings, invoices, expenses, income, files, freelancers, customers, students

def register_routes(app):
    health.register(app)
    auth.register(app)
    dashboard.register(app)
    settings.register(app)
    invoices.register(app)
    expenses.register(app)
    income.register(app)
    files.register(app)
    freelancers.register(app)
    customers.register(app)   # <-- NEW
    students.register(app)     # <-- add this line
