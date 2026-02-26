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


def migrate_media_type():
    """Add media_type column to photos and post_images if not present."""
    with app.app_context():
        db = get_db()
        for table in ("photos", "post_images"):
            cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
            if "media_type" not in cols:
                db.execute(f"ALTER TABLE {table} ADD COLUMN media_type TEXT NOT NULL DEFAULT 'image'")
        db.commit()


def migrate_post_images():
    """Populate post_images for any existing photos that don't have entries yet."""
    with app.app_context():
        db = get_db()
        orphans = db.execute(
            """SELECT p.id, p.cloudinary_url, p.cloudinary_public_id
               FROM photos p
               LEFT JOIN post_images pi ON p.id = pi.photo_id
               WHERE pi.id IS NULL"""
        ).fetchall()
        for p in orphans:
            db.execute(
                "INSERT INTO post_images (photo_id, cloudinary_url, cloudinary_public_id, display_order) VALUES (?, ?, ?, 0)",
                (p["id"], p["cloudinary_url"], p["cloudinary_public_id"]),
            )
        db.commit()


def detect_media_type(file):
    ct = (file.content_type or "").lower()
    if ct.startswith("video/"):
        return "video"
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext in {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}:
        return "video"
    return "image"


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
        """SELECT p.id, p.cloudinary_url, p.caption, p.created_at, p.media_type,
                  COUNT(pi.id) AS image_count
           FROM photos p
           LEFT JOIN post_images pi ON p.id = pi.photo_id
           GROUP BY p.id
           ORDER BY p.created_at DESC"""
    ).fetchall()
    return render_template("index.html", photos=photos)


@app.route("/photo/<int:photo_id>")
def photo(photo_id):
    db = get_db()
    p = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if p is None:
        return render_template("404.html"), 404
    images = db.execute(
        "SELECT cloudinary_url, media_type FROM post_images WHERE photo_id = ? ORDER BY display_order ASC",
        (photo_id,),
    ).fetchall()
    if not images:
        images = [{"cloudinary_url": p["cloudinary_url"], "media_type": p["media_type"]}]
    comments = db.execute(
        "SELECT * FROM comments WHERE photo_id = ? ORDER BY created_at ASC",
        (photo_id,),
    ).fetchall()
    return render_template("photo.html", photo=p, images=images, comments=comments)


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
        files = [f for f in request.files.getlist("photos") if f and f.filename != ""]
        caption = request.form.get("caption", "").strip()
        if not files:
            flash("Please select at least one file to upload.")
            return redirect(url_for("upload"))
        uploaded = []
        for f in files:
            mtype = detect_media_type(f)
            result = cloudinary.uploader.upload(f, folder="sharayunet", resource_type="auto")
            uploaded.append((result["secure_url"], result["public_id"], mtype))
        db = get_db()
        first_url, first_public_id, first_mtype = uploaded[0]
        cursor = db.execute(
            "INSERT INTO photos (cloudinary_url, cloudinary_public_id, media_type, caption) VALUES (?, ?, ?, ?)",
            (first_url, first_public_id, first_mtype, caption or None),
        )
        photo_id = cursor.lastrowid
        for i, (url, public_id, mtype) in enumerate(uploaded):
            db.execute(
                "INSERT INTO post_images (photo_id, cloudinary_url, cloudinary_public_id, media_type, display_order) VALUES (?, ?, ?, ?, ?)",
                (photo_id, url, public_id, mtype, i),
            )
        db.commit()
        flash("Uploaded successfully!")
        return redirect(url_for("index"))
    return render_template("upload.html")


@app.route("/delete/<int:photo_id>", methods=["POST"])
@login_required
def delete_photo(photo_id):
    db = get_db()
    p = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if p is None:
        return render_template("404.html"), 404
    imgs = db.execute(
        "SELECT cloudinary_public_id, media_type FROM post_images WHERE photo_id = ?", (photo_id,)
    ).fetchall()
    if imgs:
        for img in imgs:
            rtype = "video" if img["media_type"] == "video" else "image"
            cloudinary.uploader.destroy(img["cloudinary_public_id"], resource_type=rtype)
    else:
        rtype = "video" if p["media_type"] == "video" else "image"
        cloudinary.uploader.destroy(p["cloudinary_public_id"], resource_type=rtype)
    db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
    db.commit()
    flash("Deleted.")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

with app.app_context():
    init_db()
    migrate_media_type()
    migrate_post_images()

if __name__ == "__main__":
    app.run(debug=True)
