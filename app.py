import os
import sqlite3
from datetime import datetime

from flask import Flask, jsonify, render_template, request, session

try:
    import psycopg
    from psycopg.errors import UniqueViolation
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    UniqueViolation = None
    dict_row = None

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "student-feedback-secret")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, "feedback.db")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DEFAULT_ADMIN_NAME = "Administrator"
DEFAULT_ADMIN_EMAIL = "admin@gmail.com"
DEFAULT_ADMIN_PASSWORD = "Admin@123"
IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))
INTEGRITY_ERRORS = (sqlite3.IntegrityError,) if UniqueViolation is None else (
    sqlite3.IntegrityError,
    UniqueViolation,
)


def get_connection():
    if IS_POSTGRES:
        if psycopg is None:
            raise RuntimeError(
                "PostgreSQL support requires psycopg. Install dependencies from requirements.txt."
            )
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def db_engine():
    return "postgresql" if IS_POSTGRES else "sqlite"


def adapt_query(query):
    return query.replace("?", "%s") if IS_POSTGRES else query


def row_to_dict(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return dict(row)


def execute_fetchone(connection, query, params=()):
    return connection.execute(adapt_query(query), params).fetchone()


def execute_fetchall(connection, query, params=()):
    return connection.execute(adapt_query(query), params).fetchall()


def execute_command(connection, query, params=()):
    return connection.execute(adapt_query(query), params)


def init_db():
    with get_connection() as connection:
        if IS_POSTGRES:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    password TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    user_id INTEGER,
                    student_name TEXT NOT NULL,
                    student_email TEXT NOT NULL,
                    course_name TEXT NOT NULL,
                    instructor_name TEXT NOT NULL,
                    semester TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    comments TEXT NOT NULL,
                    recommend INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )
            connection.execute(
                "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS user_id INTEGER"
            )
        else:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    password TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    student_name TEXT NOT NULL,
                    student_email TEXT NOT NULL,
                    course_name TEXT NOT NULL,
                    instructor_name TEXT NOT NULL,
                    semester TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    comments TEXT NOT NULL,
                    recommend INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )

            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(feedback)").fetchall()
            }
            if "user_id" not in columns:
                connection.execute("ALTER TABLE feedback ADD COLUMN user_id INTEGER")

        created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        if IS_POSTGRES:
            connection.execute(
                """
                INSERT INTO users (full_name, email, password, created_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (email) DO NOTHING
                """,
                (
                    DEFAULT_ADMIN_NAME,
                    DEFAULT_ADMIN_EMAIL,
                    DEFAULT_ADMIN_PASSWORD,
                    created_at,
                ),
            )
            connection.execute(
                """
                UPDATE users
                SET full_name = %s, password = %s
                WHERE email = %s
                """,
                (DEFAULT_ADMIN_NAME, DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_EMAIL),
            )
        else:
            connection.execute(
                """
                INSERT OR IGNORE INTO users (full_name, email, password, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    DEFAULT_ADMIN_NAME,
                    DEFAULT_ADMIN_EMAIL,
                    DEFAULT_ADMIN_PASSWORD,
                    created_at,
                ),
            )
            connection.execute(
                """
                UPDATE users
                SET full_name = ?, password = ?
                WHERE email = ?
                """,
                (DEFAULT_ADMIN_NAME, DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_EMAIL),
            )

        connection.commit()


def serialize_feedback(row):
    return {
        "id": row["id"],
        "student_name": row["student_name"],
        "student_email": row["student_email"],
        "course_name": row["course_name"],
        "instructor_name": row["instructor_name"],
        "semester": row["semester"],
        "rating": row["rating"],
        "category": row["category"],
        "comments": row["comments"],
        "recommend": bool(row["recommend"]),
        "created_at": row["created_at"],
    }


def current_user():
    if "user_id" not in session:
        return None

    with get_connection() as connection:
        row = execute_fetchone(
            connection,
            "SELECT id, full_name, email FROM users WHERE id = ?",
            (session["user_id"],),
        )

    return row_to_dict(row)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    try:
        with get_connection() as connection:
            execute_fetchone(connection, "SELECT 1")
        return jsonify({"status": "ok", "database": db_engine()}), 200
    except Exception as error:
        return (
            jsonify(
                {
                    "status": "error",
                    "database": db_engine(),
                    "error": str(error),
                }
            ),
            503,
        )


@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    full_name = str(data.get("full_name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", "")).strip()

    if not full_name or not email or not password:
        return jsonify({"error": "Full name, email, and password are required"}), 400

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        with get_connection() as connection:
            if IS_POSTGRES:
                execute_command(
                    connection,
                    """
                    INSERT INTO users (full_name, email, password, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (full_name, email, password, created_at),
                )
            else:
                execute_command(
                    connection,
                    """
                    INSERT INTO users (full_name, email, password, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (full_name, email, password, created_at),
                )
            connection.commit()
            user = execute_fetchone(
                connection,
                "SELECT id, full_name, email FROM users WHERE email = ?",
                (email,),
            )
    except INTEGRITY_ERRORS:
        return jsonify({"error": "An account with this email already exists"}), 400

    session["user_id"] = user["id"]
    session["user_name"] = user["full_name"]
    return jsonify({"message": "Signup successful", "user": row_to_dict(user)}), 201


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", "")).strip()

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    with get_connection() as connection:
        user = execute_fetchone(
            connection,
            "SELECT id, full_name, email FROM users WHERE email = ? AND password = ?",
            (email, password),
        )

    if not user:
        return jsonify({"error": "Invalid email or password"}), 401

    session["user_id"] = user["id"]
    session["user_name"] = user["full_name"]
    return jsonify({"message": "Login successful", "user": row_to_dict(user)}), 200


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out"}), 200


@app.route("/api/me", methods=["GET"])
def me():
    user = current_user()
    if not user:
        return jsonify({"authenticated": False}), 401
    return jsonify({"authenticated": True, "user": user}), 200


@app.route("/api/feedback", methods=["GET"])
def list_feedback():
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    with get_connection() as connection:
        rows = execute_fetchall(
            connection,
            """
            SELECT id, student_name, student_email, course_name, instructor_name,
                   semester, rating, category, comments, recommend, created_at
            FROM feedback
            ORDER BY id DESC
            LIMIT 12
            """
        )

    return jsonify([serialize_feedback(row) for row in rows])


@app.route("/api/feedback", methods=["POST"])
def submit_feedback():
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    required_fields = [
        "student_name",
        "student_email",
        "course_name",
        "instructor_name",
        "semester",
        "rating",
        "category",
        "comments",
    ]

    missing = [field for field in required_fields if not str(data.get(field, "")).strip()]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    try:
        rating = int(data["rating"])
    except (TypeError, ValueError):
        return jsonify({"error": "Rating must be a number between 1 and 5"}), 400

    if rating < 1 or rating > 5:
        return jsonify({"error": "Rating must be between 1 and 5"}), 400

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    feedback_record = {
        "user_id": session["user_id"],
        "student_name": data["student_name"].strip(),
        "student_email": data["student_email"].strip(),
        "course_name": data["course_name"].strip(),
        "instructor_name": data["instructor_name"].strip(),
        "semester": data["semester"].strip(),
        "rating": rating,
        "category": data["category"].strip(),
        "comments": data["comments"].strip(),
        "recommend": 1 if data.get("recommend") else 0,
        "created_at": created_at,
    }

    with get_connection() as connection:
        execute_command(
            connection,
            """
            INSERT INTO feedback (
                user_id, student_name, student_email, course_name, instructor_name,
                semester, rating, category, comments, recommend, created_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                feedback_record["user_id"],
                feedback_record["student_name"],
                feedback_record["student_email"],
                feedback_record["course_name"],
                feedback_record["instructor_name"],
                feedback_record["semester"],
                feedback_record["rating"],
                feedback_record["category"],
                feedback_record["comments"],
                feedback_record["recommend"],
                feedback_record["created_at"],
            ),
        )
        connection.commit()
        row = execute_fetchone(
            connection,
            """
            SELECT id, student_name, student_email, course_name, instructor_name,
                   semester, rating, category, comments, recommend, created_at
            FROM feedback
            WHERE user_id = ? AND student_email = ? AND created_at = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                feedback_record["user_id"],
                feedback_record["student_email"],
                feedback_record["created_at"],
            ),
        )

    return jsonify({"message": "Feedback submitted successfully", "feedback": serialize_feedback(row)}), 201

init_db()


if __name__ == "__main__":
    app.run(debug=False, use_reloader=False)
