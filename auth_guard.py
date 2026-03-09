"""
backend_flask/auth_guard.py
JWT middleware — ดึง Authorization header แล้ว decode token
"""
import os
import jwt
from functools import wraps
from flask import request, jsonify


def require_auth(f):
    """Decorator: ป้องกัน endpoint ที่ต้องการ login"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"success": False, "message": "Missing or invalid Authorization header"}), 401

        token = auth_header[7:]
        try:
            secret = os.environ.get("JWT_SECRET", "slipscan_secret")
            payload = jwt.decode(token, secret, algorithms=["HS256"])
            request.current_user = payload
        except jwt.ExpiredSignatureError:
            return jsonify({"success": False, "message": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"success": False, "message": "Invalid token"}), 401

        return f(*args, **kwargs)
    return decorated
