"""
backend_flask/app.py
SlipScan Flask Backend API
"""
import os
import time
import logging
from pathlib import Path
from flask import Flask, jsonify, g, request
from flask_cors import CORS
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

# โหลด .env
load_dotenv(Path(__file__).parent / ".env")

from extensions import limiter
from routes.auth  import auth_bp
from routes.slips import slips_bp

# ── Logging setup ─────────────────────────────────────────────────────────────
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("slipscan.backend")

# ── App setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app, origins="*", supports_credentials=True)
app.url_map.strict_slashes = False

# ── Rate Limiting ─────────────────────────────────────────────────────────────
def _get_user_id_or_ip():
    """Key rate limit by JWT user_id when available, fallback to IP."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            import jwt as pyjwt
            secret = os.environ.get("JWT_SECRET", "slipscan_secret")
            payload = pyjwt.decode(auth[7:], secret, algorithms=["HS256"])
            return f"user:{payload.get('sub', 'anon')}"
        except Exception:
            pass
    return f"ip:{get_remote_address()}"

limiter.init_app(app)
limiter.key_func = _get_user_id_or_ip
limiter.default_limits = ["200 per day", "50 per hour"]

# ── Register blueprints ───────────────────────────────────────────────────────
app.register_blueprint(auth_bp)
app.register_blueprint(slips_bp)

# ── Request logging middleware ────────────────────────────────────────────────
@app.before_request
def _before():
    g.start_time = time.time()

@app.after_request
def _after(response):
    duration_ms = round((time.time() - g.get("start_time", time.time())) * 1000)
    logger.info(
        "%s %s → %d (%dms)",
        request.method,
        request.path,
        response.status_code,
        duration_ms,
    )
    return response

# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "slipscan-backend"}), 200

# ── Error handlers ────────────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "error": "Endpoint not found"}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"success": False, "error": "Method not allowed"}), 405

@app.errorhandler(429)
def rate_limit_exceeded(e):
    logger.warning("Rate limit exceeded: %s %s", request.method, request.path)
    return jsonify({
        "success": False,
        "error": "Too many requests — please slow down.",
        "retry_after": str(e.description),
    }), 429

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port  = int(os.environ.get("BACKEND_PORT", 8000))
    debug = os.environ.get("APP_DEBUG", "false").lower() == "true"
    logger.info("SlipScan Backend starting on http://0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, debug=debug)
