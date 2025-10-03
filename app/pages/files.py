from flask import send_from_directory

def register(app):
    @app.route("/uploads/<path:filename>", endpoint="uploaded_file")
    def uploaded_file(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=False)
