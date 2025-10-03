# app/pages/auth.py
from flask import request, redirect, url_for, flash, session

from ..core import csrf_token, require_csrf, verify_password, is_safe_url, render

def register(app):
    @app.route("/login", methods=["GET", "POST"], endpoint="login")
    def login():
        if request.method == "POST":
            try:
                require_csrf(request.form.get("csrf_token", ""))
            except Exception:
                flash("Security check failed. Please try again.", "danger")
                return redirect(url_for("login", next=request.form.get("next", "")))

            pwd = request.form.get("password", "")
            if verify_password(pwd):
                session["auth_ok"] = True
                session["auth_user"] = app.config.get("APP_USERNAME", "admin")
                session.permanent = True
                flash("Welcome back.", "success")
                nxt = request.form.get("next") or url_for("dashboard")
                return redirect(nxt if is_safe_url(nxt) else url_for("dashboard"))
            else:
                flash("Invalid password.", "danger")
                return redirect(url_for("login", next=request.form.get("next", "")))
        return render("login.html", csrf_token=csrf_token(), next=request.args.get("next", ""))

    @app.route("/logout", methods=["POST"], endpoint="logout")
    def logout():
        try:
            require_csrf(request.form.get("csrf_token", ""))
        except Exception:
            pass
        session.clear()
        flash("Logged out.", "success")
        return redirect(url_for("login"))
