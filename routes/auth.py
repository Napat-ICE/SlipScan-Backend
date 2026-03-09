"""
backend_flask/routes/auth.py
Endpoints: register, login, logout, me
"""
import os
import time
import bcrypt
import jwt
from flask import Blueprint, request, jsonify
from config import get_db
from auth_guard import require_auth

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


# ── POST /api/auth/register ──────────────────────────────────────────────────
@auth_bp.post("/register")
def register():
    body = request.get_json(silent=True) or {}
    email    = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        return jsonify({"success": False, "message": "Email and password are required"}), 400

    import re
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        return jsonify({"success": False, "message": "Invalid email format"}), 400

    if len(password) < 8:
        return jsonify({"success": False, "message": "Password must be at least 8 characters"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Check duplicate
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                return jsonify({"success": False, "message": "Email already registered"}), 409

            # Hash password (bcrypt cost 12 — same as PHP)
            hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()

            cur.execute(
                "INSERT INTO users (email, password, role) VALUES (%s, %s, %s) RETURNING id",
                (email, hashed, "user"),
            )
            user_id = cur.fetchone()["id"]
            conn.commit()

        return jsonify({
            "success": True,
            "message": "User registered successfully",
            "user": {"id": user_id, "email": email, "role": "user"},
        }), 201

    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": "Registration failed", "error": str(e)}), 500


# ── POST /api/auth/login ─────────────────────────────────────────────────────
@auth_bp.post("/login")
def login():
    body = request.get_json(silent=True) or {}
    email    = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        return jsonify({"success": False, "message": "Email and password are required"}), 400

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id, email, password, role FROM users WHERE email = %s", (email,))
        user = cur.fetchone()

    if not user or not bcrypt.checkpw(password.encode(), user["password"].encode()):
        return jsonify({"success": False, "message": "Invalid email or password"}), 401

    secret  = os.environ.get("JWT_SECRET", "slipscan_secret")
    expire  = int(os.environ.get("JWT_EXPIRE", 86400))
    now     = int(time.time())

    payload = {
        "iss":   "slipscan",
        "iat":   now,
        "exp":   now + expire,
        "sub":   str(user["id"]),   # PyJWT 2.x ต้องการ string
        "email": user["email"],
        "role":  user["role"],
    }
    token = jwt.encode(payload, secret, algorithm="HS256")

    return jsonify({
        "success": True,
        "token":   token,
        "user": {
            "id":    user["id"],
            "email": user["email"],
            "role":  user["role"],
        },
    }), 200


# ── POST /api/auth/logout ────────────────────────────────────────────────────
@auth_bp.post("/logout")
def logout():
    return jsonify({
        "success": True,
        "message": "Logged out successfully. Please remove the token on client side.",
    }), 200


# ── GET /api/auth/me ─────────────────────────────────────────────────────────
@auth_bp.get("/me")
@require_auth
def me():
    user_id = request.current_user["sub"]
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, role, created_at FROM users WHERE id = %s",
            (user_id,),
        )
        user = cur.fetchone()

    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404

    return jsonify({
        "success": True,
        "user": {
            "id":         user["id"],
            "email":      user["email"],
            "role":       user["role"],
            "created_at": str(user["created_at"]),
        },
    }), 200
