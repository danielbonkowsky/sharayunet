import os
import sqlite3
from functools import wraps

import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv
from flask import (Flask, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]

cloudinary.config(
    cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
    api_key=os.environ["CLOUDINARY_API_KEY"],
    api_secret=os.environ["CLOUDINARY_API_SECRET"],
)

DATABASE = os.environ.get("DATABASE", "photos.db")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.environ["ADMIN_PASSWORD_HASH"]


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db():
    with app.app_context():
        db = get_db()
        with app.open_resource("schema.sql", mode="r") as f:
            db.cursor().executescript(f.read())
        db.commit()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            flash("Please log in to access that page.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    db = get_db()
    photos = db.execute(
        "SELECT id, cloudinary_url, caption, created_at FROM photos ORDER BY created_at DESC"
    ).fetchall()
    return render_template("index.html", photos=photos)


@app.route("/photo/<int:photo_id>")
def photo(photo_id):
    db = get_db()
    p = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if p is None:
        return render_template("404.html"), 404
    comments = db.execute(
        "SELECT * FROM comments WHERE photo_id = ? ORDER BY created_at ASC",
        (photo_id,),
    ).fetchall()
    return render_template("photo.html", photo=p, comments=comments)


@app.route("/photo/<int:photo_id>/comment", methods=["POST"])
def add_comment(photo_id):
    name = request.form.get("name", "").strip()
    body = request.form.get("body", "").strip()
    if not name or not body:
        flash("Both name and comment are required.")
        return redirect(url_for("photo", photo_id=photo_id))
    db = get_db()
    # Make sure photo exists
    if db.execute("SELECT id FROM photos WHERE id = ?", (photo_id,)).fetchone() is None:
        return render_template("404.html"), 404
    db.execute(
        "INSERT INTO comments (photo_id, name, body) VALUES (?, ?, ?)",
        (photo_id, name, body),
    )
    db.commit()
    return redirect(url_for("photo", photo_id=photo_id))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("upload"))
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session["logged_in"] = True
            flash("Welcome back!")
            return redirect(url_for("upload"))
        flash("Invalid username or password.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    flash("You have been logged out.")
    return redirect(url_for("index"))


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        file = request.files.get("photo")
        caption = request.form.get("caption", "").strip()
        if not file or file.filename == "":
            flash("Please select a photo to upload.")
            return redirect(url_for("upload"))
        result = cloudinary.uploader.upload(
            file,
            folder="sharayunet",
            resource_type="image",
        )
        db = get_db()
        db.execute(
            "INSERT INTO photos (cloudinary_url, cloudinary_public_id, caption) VALUES (?, ?, ?)",
            (result["secure_url"], result["public_id"], caption or None),
        )
        db.commit()
        flash("Photo uploaded successfully!")
        return redirect(url_for("index"))
    return render_template("upload.html")


@app.route("/delete/<int:photo_id>", methods=["POST"])
@login_required
def delete_photo(photo_id):
    db = get_db()
    p = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if p is None:
        return render_template("404.html"), 404
    cloudinary.uploader.destroy(p["cloudinary_public_id"])
    db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
    db.commit()
    flash("Photo deleted.")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(debug=True)
