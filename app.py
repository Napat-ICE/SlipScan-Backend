"""
backend_flask/app.py
SlipScan Flask Backend API — แทน PHP backend
รัน: python app.py
"""
import os
from pathlib import Path
from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# โหลด .env จาก backend_flask/ เสมอ ไม่ว่าจะรันจาก directory ไหน
load_dotenv(Path(__file__).parent / ".env")

from routes.auth  import auth_bp
from routes.slips import slips_bp

# ── App setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app, origins="*", supports_credentials=True)
app.url_map.strict_slashes = False  # ไม่ redirect trailing slash (ป้องกัน Auth header หาย)

# ── Register blueprints ──────────────────────────────────────────────────────
app.register_blueprint(auth_bp)
app.register_blueprint(slips_bp)


# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "slipscan-backend"}), 200


# ── 404 handler ──────────────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "error": "Endpoint not found"}), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"success": False, "error": "Method not allowed"}), 405


# ── Run ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port  = int(os.environ.get("BACKEND_PORT", 8000))
    debug = os.environ.get("APP_DEBUG", "false").lower() == "true"
    print(f"🚀 SlipScan Backend starting on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
