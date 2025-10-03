def register(app):
    @app.route("/healthz")
    def healthz():
        return "ok", 200

    @app.route("/_probe")
    def probe():
        return "OK", 200
