"""
backend_flask/routes/slips.py
Endpoints: upload, upload-batch, get by id, list/search, export CSV, dashboard stats
"""
import os
import io
import csv
import uuid
import json
import hashlib
import requests
from pathlib import Path
from datetime import datetime, timedelta
import logging
from flask import Blueprint, request, jsonify, send_from_directory, Response
from config import get_db
from auth_guard import require_auth
from extensions import limiter

logger = logging.getLogger("slipscan.slips")

slips_bp = Blueprint("slips", __name__, url_prefix="/api/slips")

UPLOAD_DIR    = Path(__file__).parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
ALLOWED_EXT   = {".jpg", ".jpeg", ".png", ".webp"}
MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_BATCH      = 20
OCR_URL        = os.environ.get("OCR_SERVICE_URL", "http://localhost:5000/ocr")


def _safe_parse_raw_ocr(value):
    """Safely parse raw_ocr from DB — never raises."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return s  # return raw string if not valid JSON
    return None


# ── GET /api/slips/uploads/<filename>  (serve uploaded images) ───────────────
@slips_bp.get("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(str(UPLOAD_DIR), filename)


# ── POST /api/slips/upload ───────────────────────────────────────────────────
@slips_bp.post("/upload")
@require_auth
@limiter.limit("20 per minute")
def upload():
    user_id = int(request.current_user["sub"])

    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file uploaded (field: 'file')"}), 400

    file  = request.files["file"]
    error = _validate_file(file)
    if error:
        return jsonify({"success": False, "message": error}), 400

    # Compute hash before saving
    file_bytes = file.read()
    file_hash  = hashlib.sha256(file_bytes).hexdigest()
    file.seek(0)

    # Check duplicate BY HASH early
    is_dup, dup_slip_id = _check_duplicate(file_hash, user_id)
    
    if is_dup:
        # It's a duplicate -- still save to DB so it appears in history
        existing_slip = _get_slip_by_id(dup_slip_id, user_id)

        # Save the duplicate image
        filename = f"slip_{uuid.uuid4().hex}{Path(file.filename).suffix.lower()}"
        dest     = UPLOAD_DIR / filename
        with open(str(dest), "wb") as f:
            f.write(file_bytes)

        # Build data from existing slip for the new record
        dup_data = {}
        if existing_slip:
            dup_data = {
                "sender_name":      existing_slip.get("sender_name"),
                "bank_name":        existing_slip.get("bank_name"),
                "amount":           existing_slip.get("amount"),
                "slip_date":        str(existing_slip.get("slip_date")) if existing_slip.get("slip_date") else None,
                "slip_time":        str(existing_slip.get("slip_time")) if existing_slip.get("slip_time") else None,
                "ref_no":           existing_slip.get("ref_no"),
                "receiver_name":    existing_slip.get("receiver_name"),
                "receiver_account": existing_slip.get("receiver_acct"),
                "raw_ocr":          existing_slip.get("raw_ocr"),
            }

        # Save as duplicate slip in DB
        new_slip_id = _save_slip(user_id, f"uploads/{filename}", dup_data, is_duplicate=True, file_hash=None)

        resp_data = {
            "success":  True,
            "slip_id":  new_slip_id,
            "is_duplicate": True,
            "duplicate_of": dup_slip_id,
            "data": {
                "sender_name":   dup_data.get("sender_name"),
                "bank_name":     dup_data.get("bank_name"),
                "amount":        dup_data.get("amount"),
                "slip_date":     dup_data.get("slip_date"),
                "slip_time":     dup_data.get("slip_time"),
                "ref_no":        dup_data.get("ref_no"),
                "receiver_name": dup_data.get("receiver_name"),
                "receiver_acct": dup_data.get("receiver_account"),
                "is_fake":       existing_slip.get("is_fake", False) if existing_slip else False,
            },
            "raw_ocr":       _safe_parse_raw_ocr(dup_data.get("raw_ocr")),
            "warnings":      [f"\u26a0\ufe0f \u0e2a\u0e25\u0e34\u0e1b\u0e0b\u0e49\u0e33\u0e01\u0e31\u0e1a\u0e23\u0e32\u0e22\u0e01\u0e32\u0e23 #{dup_slip_id}"],
            "notifications": [{"type": "warning", "message": f"\u0e2a\u0e25\u0e34\u0e1b\u0e0b\u0e49\u0e33\u0e01\u0e31\u0e1a\u0e23\u0e32\u0e22\u0e01\u0e32\u0e23 #{dup_slip_id}"}],
        }
        return jsonify(resp_data), 200

    # Save file temporarily for OCR
    filename = f"slip_{uuid.uuid4().hex}{Path(file.filename).suffix.lower()}"
    dest     = UPLOAD_DIR / filename
    with open(str(dest), "wb") as f:
        f.write(file_bytes)

    ocr_data, warnings = _call_ocr(str(dest))
    if ocr_data is None:
        warnings.append("OCR service unavailable or failed")
        ocr_data = {}

    # Check duplicate again by Bank & Ref No (if OCR extracted them)
    is_dup_by_ref, dup_slip_id_by_ref = False, None
    if not is_dup:
         is_dup_by_ref, dup_slip_id_by_ref = _check_duplicate(
            None, user_id, 
            ref_no=ocr_data.get('ref_no'), 
            bank_name=ocr_data.get('bank_name')
        )
    
    final_is_dup = is_dup or is_dup_by_ref
    final_dup_id = dup_slip_id or dup_slip_id_by_ref

    # ตรวจสอบสลิปปลอมผ่าน Thunder Solution (เทียบกับข้อมูล OCR)
    is_fake, fake_reason = _call_thunder_verify(
        str(dest), 
        ocr_amount=ocr_data.get("amount"), 
        ocr_ref=ocr_data.get("ref_no")
    )
    if is_fake:
        ocr_data["is_fake"] = True
        warnings.append(f"สลิปปลอม/ตรวจสอบไม่ผ่าน: {fake_reason}")

    if final_is_dup:
        warnings.append(f"⚠️ สลิปซ้ำกับรายการ #{final_dup_id}")

    slip_id = _save_slip(user_id, f"uploads/{filename}", ocr_data, final_is_dup, file_hash)

    # Notification: แจ้งเตือนถ้าสลิปปลอมหรืออ่านข้อมูลไม่ครบ
    notifications = []
    if ocr_data.get("is_fake"):
        notifications.append({"type": "danger", "message": "⚠️ ตรวจพบสลิปปลอม!"})
    missing = [f for f in ["amount", "bank_name", "slip_date"] if not ocr_data.get(f)]
    if missing:
        notifications.append({"type": "warning", "message": f"อ่านข้อมูลไม่ครบ: {', '.join(missing)}"})
    if final_is_dup:
        notifications.append({"type": "warning", "message": f"สลิปซ้ำกับรายการ #{final_dup_id}"})

    return jsonify({
        "success":  True,
        "slip_id":  slip_id,
        "is_duplicate": final_is_dup,
        "duplicate_of": final_dup_id,
        "data": {
            "sender_name":   ocr_data.get("sender_name"),
            "bank_name":     ocr_data.get("bank_name"),
            "amount":        ocr_data.get("amount"),
            "slip_date":     ocr_data.get("slip_date"),
            "slip_time":     ocr_data.get("slip_time"),
            "ref_no":        ocr_data.get("ref_no"),
            "receiver_name": ocr_data.get("receiver_name"),
            "receiver_acct": ocr_data.get("receiver_account"),
            "is_fake":       ocr_data.get("is_fake", False),
        },
        "raw_ocr":       ocr_data.get("raw_ocr"),
        "warnings":      warnings,
        "notifications": notifications,
    }), 200


# ── POST /api/slips/upload-batch ─────────────────────────────────────────────
@slips_bp.post("/upload-batch")
@require_auth
@limiter.limit("5 per minute")
def upload_batch():
    user_id = int(request.current_user["sub"])
    files   = request.files.getlist("files")

    if not files:
        return jsonify({"success": False, "message": "No files uploaded (field: 'files[]')"}), 400

    if len(files) > MAX_BATCH:
        return jsonify({"success": False, "message": f"Maximum {MAX_BATCH} files per batch"}), 400

    results      = []
    success_count = 0
    failed_count  = 0

    for i, file in enumerate(files):
        error = _validate_file(file)
        if error:
            results.append({"index": i, "success": False, "error": error})
            failed_count += 1
            continue

        file_bytes = file.read()
        file_hash  = hashlib.sha256(file_bytes).hexdigest()
        file.seek(0)

        # Check duplicate BY HASH early
        is_dup, dup_slip_id = _check_duplicate(file_hash, user_id)

        if is_dup:
            existing_slip = _get_slip_by_id(dup_slip_id, user_id)
            if existing_slip:
                results.append({
                    "index":    i,
                    "success":  True,
                    "slip_id":  dup_slip_id,
                    "filename": file.filename,
                    "is_duplicate": True,
                    "duplicate_of": dup_slip_id,
                    "data": {
                        "sender_name":   existing_slip.get("sender_name"),
                        "bank_name":     existing_slip.get("bank_name"),
                        "amount":        existing_slip.get("amount"),
                        "slip_date":     str(existing_slip.get("slip_date")) if existing_slip.get("slip_date") else None,
                        "slip_time":     str(existing_slip.get("slip_time")) if existing_slip.get("slip_time") else None,
                        "ref_no":        existing_slip.get("ref_no"),
                        "receiver_name": existing_slip.get("receiver_name"),
                        "receiver_acct": existing_slip.get("receiver_acct"),
                        "is_fake":       existing_slip.get("is_fake", False),
                    },
                    "raw_ocr":       _safe_parse_raw_ocr(existing_slip.get("raw_ocr")),
                    "warnings": [f"⚠️ สลิปซ้ำกับรายการ #{dup_slip_id}"]
                })
                success_count += 1
                continue

        filename = f"slip_{uuid.uuid4().hex}{Path(file.filename).suffix.lower()}"
        dest     = UPLOAD_DIR / filename
        with open(str(dest), "wb") as f:
            f.write(file_bytes)

        ocr_data, warnings = _call_ocr(str(dest))
        if ocr_data is None:
            warnings.append("OCR service unavailable")
            ocr_data = {}

        is_dup_by_ref, dup_slip_id_by_ref = False, None
        if not is_dup:
            is_dup_by_ref, dup_slip_id_by_ref = _check_duplicate(
                None, user_id, 
                ref_no=ocr_data.get('ref_no'), 
                bank_name=ocr_data.get('bank_name')
            )
        
        final_is_dup = is_dup or is_dup_by_ref
        final_dup_id = dup_slip_id or dup_slip_id_by_ref

        # ตรวจสอบสลิปปลอมผ่าน Thunder Solution (เทียบกับข้อมูล OCR)
        is_fake_slip, fake_reason = _call_thunder_verify(
            str(dest),
            ocr_amount=ocr_data.get("amount"),
            ocr_ref=ocr_data.get("ref_no")
        )
        if is_fake_slip:
            ocr_data["is_fake"] = True
            warnings.append(f"สลิปปลอม/ตรวจสอบไม่ผ่าน: {fake_reason}")

        if final_is_dup:
            warnings.append(f"⚠️ สลิปซ้ำกับรายการ #{final_dup_id}")

        slip_id = _save_slip(user_id, f"uploads/{filename}", ocr_data, final_is_dup, file_hash)
        results.append({
            "index":    i,
            "success":  True,
            "slip_id":  slip_id,
            "filename": file.filename,
            "is_duplicate": final_is_dup,
            "duplicate_of": final_dup_id,
            "data": {
                "sender_name":   ocr_data.get("sender_name"),
                "bank_name":     ocr_data.get("bank_name"),
                "amount":        ocr_data.get("amount"),
                "slip_date":     ocr_data.get("slip_date"),
                "slip_time":     ocr_data.get("slip_time"),
                "ref_no":        ocr_data.get("ref_no"),
                "receiver_name": ocr_data.get("receiver_name"),
                "receiver_acct": ocr_data.get("receiver_account"),
                "is_fake":       ocr_data.get("is_fake", False),
            },
            "warnings": warnings,
        })
        success_count += 1

    return jsonify({
        "success":       True,
        "total":         len(files),
        "success_count": success_count,
        "failed_count":  failed_count,
        "items":         results,
    }), 200


# ── GET /api/slips/<id> ──────────────────────────────────────────────────────
@slips_bp.get("/<int:slip_id>")
@require_auth
def get_by_id(slip_id):
    user_id = int(request.current_user["sub"])
    conn    = get_db()

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM slips WHERE id = %s AND user_id = %s", (slip_id, user_id))
        slip = cur.fetchone()

    if not slip:
        return jsonify({"success": False, "message": "Slip not found"}), 404

    slip = dict(slip)
    if slip.get("created_at"):
        slip["created_at"] = str(slip["created_at"])

    return jsonify({"success": True, "data": slip}), 200


# ── GET /api/slips  — list + search/filter ───────────────────────────────────
@slips_bp.get("/", strict_slashes=False)
@slips_bp.get("/list", strict_slashes=False)
@require_auth
def list_all():
    user_id  = int(request.current_user["sub"])
    page     = max(1, int(request.args.get("page", 1)))
    per_page = min(50, max(1, int(request.args.get("per_page", 20))))
    offset   = (page - 1) * per_page

    # ── Search / Filter params ──
    q          = request.args.get("q", "").strip()          # ค้นหา sender_name / receiver_name
    bank       = request.args.get("bank", "").strip()       # กรองธนาคาร
    ref_no     = request.args.get("ref_no", "").strip()     # กรอง Ref No.
    date_from  = request.args.get("date_from", "").strip()  # YYYY-MM-DD
    date_to    = request.args.get("date_to", "").strip()
    min_amount = request.args.get("min_amount", "").strip()
    max_amount = request.args.get("max_amount", "").strip()
    is_fake    = request.args.get("is_fake", "").strip()    # "true" | "false"
    is_dup     = request.args.get("is_duplicate", "").strip()

    conditions = ["user_id = %s"]
    params     = [user_id]

    if q:
        conditions.append("(sender_name ILIKE %s OR receiver_name ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    if bank:
        conditions.append("bank_name ILIKE %s")
        params.append(f"%{bank}%")
    if ref_no:
        conditions.append("ref_no ILIKE %s")
        params.append(f"%{ref_no}%")
    if date_from:
        conditions.append("slip_date >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("slip_date <= %s")
        params.append(date_to)
    if min_amount:
        conditions.append("amount >= %s")
        params.append(float(min_amount))
    if max_amount:
        conditions.append("amount <= %s")
        params.append(float(max_amount))
    if is_fake in ("true", "false"):
        conditions.append("is_fake = %s")
        params.append(is_fake == "true")
    if is_dup in ("true", "false"):
        conditions.append("is_duplicate = %s")
        params.append(is_dup == "true")

    where = " AND ".join(conditions)

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS cnt FROM slips WHERE {where}", params)
        total = cur.fetchone()["cnt"]

        cur.execute(
            f"""SELECT id, image_path, sender_name, bank_name, amount, slip_date, slip_time,
                      ref_no, receiver_name, receiver_acct, is_fake, is_duplicate, created_at,
                      ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY created_at ASC) as user_sequence
               FROM slips WHERE {where}
               ORDER BY created_at DESC
               LIMIT %s OFFSET %s""",
            params + [per_page, offset],
        )
        slips = [dict(r) for r in cur.fetchall()]

    for s in slips:
        if s.get("created_at"):
            s["created_at"] = str(s["created_at"])
        if s.get("slip_date"):
            s["slip_date"] = str(s["slip_date"])
        if s.get("slip_time"):
            s["slip_time"] = str(s["slip_time"])
        if s.get("amount") is not None:
            s["amount"] = float(s["amount"])

    return jsonify({
        "success":  True,
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "data":     slips,
    }), 200


# ── GET /api/slips/export  — Export CSV ─────────────────────────────────────
@slips_bp.get("/export")
@require_auth
def export_csv():
    user_id = int(request.current_user["sub"])

    # Accept same filter params as list_all
    q          = request.args.get("q", "").strip()
    bank       = request.args.get("bank", "").strip()
    ref_no     = request.args.get("ref_no", "").strip()
    date_from  = request.args.get("date_from", "").strip()
    date_to    = request.args.get("date_to", "").strip()
    min_amount = request.args.get("min_amount", "").strip()
    max_amount = request.args.get("max_amount", "").strip()
    is_fake    = request.args.get("is_fake", "").strip()
    is_dup     = request.args.get("is_duplicate", "").strip()
    fmt        = request.args.get("format", "csv").lower()  # "csv" only (excel = csv w/ BOM)

    conditions = ["user_id = %s"]
    params     = [user_id]

    if q:
        conditions.append("(sender_name ILIKE %s OR receiver_name ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    if bank:
        conditions.append("bank_name ILIKE %s")
        params.append(f"%{bank}%")
    if ref_no:
        conditions.append("ref_no ILIKE %s")
        params.append(f"%{ref_no}%")
    if date_from:
        conditions.append("slip_date >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("slip_date <= %s")
        params.append(date_to)
    if min_amount:
        conditions.append("amount >= %s")
        params.append(float(min_amount))
    if max_amount:
        conditions.append("amount <= %s")
        params.append(float(max_amount))
    if is_fake in ("true", "false"):
        conditions.append("is_fake = %s")
        params.append(is_fake == "true")
    if is_dup in ("true", "false"):
        conditions.append("is_duplicate = %s")
        params.append(is_dup == "true")

    where = " AND ".join(conditions)

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT id, sender_name, bank_name, amount, slip_date, slip_time,
                      ref_no, receiver_name, receiver_acct, is_fake, is_duplicate, created_at
               FROM slips WHERE {where}
               ORDER BY created_at DESC""",
            params,
        )
        rows = cur.fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "ผู้โอน", "ธนาคาร", "จำนวนเงิน", "วันที่สลิป", "เวลา",
        "Ref No.", "ผู้รับ", "บัญชีรับ", "สลิปปลอม", "สลิปซ้ำ", "วันที่อัพโหลด"
    ])
    for r in rows:
        writer.writerow([
            r["id"],
            r["sender_name"] or "",
            r["bank_name"] or "",
            r["amount"] if r["amount"] is not None else "",
            str(r["slip_date"]) if r["slip_date"] else "",
            str(r["slip_time"]) if r["slip_time"] else "",
            r["ref_no"] or "",
            r["receiver_name"] or "",
            r["receiver_acct"] or "",
            "ใช่" if r["is_fake"] else "ไม่",
            "ใช่" if r["is_duplicate"] else "ไม่",
            str(r["created_at"]),
        ])

    # UTF-8 BOM for Excel compatibility
    bom = "\ufeff"
    csv_content = bom + output.getvalue()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"slips_{ts}.csv"

    return Response(
        csv_content.encode("utf-8-sig"),
        mimetype="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── GET /api/slips/dashboard  — Dashboard stats ─────────────────────────────
@slips_bp.get("/dashboard")
@require_auth
def dashboard():
    user_id = int(request.current_user["sub"])
    conn    = get_db()

    with conn.cursor() as cur:
        # ── Summary totals ──
        cur.execute(
            """SELECT
                COUNT(*) FILTER (WHERE NOT is_fake AND NOT is_duplicate) AS total_slips,
                COALESCE(SUM(amount) FILTER (WHERE NOT is_fake AND NOT is_duplicate), 0) AS total_amount,
                COALESCE(AVG(amount) FILTER (WHERE NOT is_fake AND NOT is_duplicate), 0) AS avg_amount,
                COUNT(*) FILTER (WHERE is_fake)     AS fake_count,
                COUNT(*) FILTER (WHERE is_duplicate AND NOT is_fake) AS dup_count
               FROM slips WHERE user_id = %s""",
            (user_id,),
        )
        summary = dict(cur.fetchone())

        # ── Bank ranking ──
        cur.execute(
            """SELECT bank_name,
                      COUNT(*)                 AS slip_count,
                      COALESCE(SUM(amount), 0) AS total_amount
                FROM slips
               WHERE user_id = %s AND bank_name IS NOT NULL AND NOT is_fake AND NOT is_duplicate
               GROUP BY bank_name
               ORDER BY total_amount DESC
               LIMIT 10""",
            (user_id,),
        )
        bank_ranking = [dict(r) for r in cur.fetchall()]

        # ── Daily trend (last 30 days) ──
        cur.execute(
            """SELECT slip_date::text AS date,
                      COUNT(*)        AS slip_count,
                      COALESCE(SUM(amount), 0) AS total_amount
               FROM slips
               WHERE user_id = %s
                 AND slip_date >= CURRENT_DATE - INTERVAL '30 days'
                 AND slip_date IS NOT NULL
                 AND NOT is_fake AND NOT is_duplicate
               GROUP BY slip_date
               ORDER BY slip_date""",
            (user_id,),
        )
        daily_trend = [dict(r) for r in cur.fetchall()]

        # ── Weekly summary (last 8 weeks) ──
        cur.execute(
            """SELECT
                DATE_TRUNC('week', slip_date)::date::text AS week_start,
                COUNT(*)                                  AS slip_count,
                COALESCE(SUM(amount), 0)                  AS total_amount
               FROM slips
               WHERE user_id = %s
                 AND slip_date >= CURRENT_DATE - INTERVAL '8 weeks'
                 AND slip_date IS NOT NULL
                 AND NOT is_fake AND NOT is_duplicate
               GROUP BY DATE_TRUNC('week', slip_date)
               ORDER BY week_start""",
            (user_id,),
        )
        weekly_summary = [dict(r) for r in cur.fetchall()]

        # ── Monthly summary (last 12 months) ──
        cur.execute(
            """SELECT
                TO_CHAR(DATE_TRUNC('month', slip_date), 'YYYY-MM') AS month,
                COUNT(*)                                           AS slip_count,
                COALESCE(SUM(amount), 0)                           AS total_amount
               FROM slips
               WHERE user_id = %s
                 AND slip_date >= CURRENT_DATE - INTERVAL '12 months'
                 AND slip_date IS NOT NULL
                 AND NOT is_fake AND NOT is_duplicate
               GROUP BY DATE_TRUNC('month', slip_date)
               ORDER BY month""",
            (user_id,),
        )
        monthly_summary = [dict(r) for r in cur.fetchall()]

        # ── Recent slips (last 10) ──
        cur.execute(
            """SELECT id, sender_name, bank_name, amount, slip_date, ref_no,
                      is_fake, is_duplicate, created_at,
                      ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY created_at ASC) as user_sequence
               FROM slips WHERE user_id = %s
               ORDER BY created_at DESC LIMIT 10""",
            (user_id,),
        )
        recent = [dict(r) for r in cur.fetchall()]

    # Serialise
    for r in recent:
        r["created_at"] = str(r["created_at"])
        r["amount"] = float(r["amount"]) if r["amount"] is not None else None

    for r in bank_ranking:
        r["total_amount"] = float(r["total_amount"])

    for r in daily_trend + weekly_summary + monthly_summary:
        r["total_amount"] = float(r["total_amount"])

    summary["total_amount"] = float(summary["total_amount"])
    summary["avg_amount"]   = float(summary["avg_amount"])

    return jsonify({
        "success":         True,
        "summary":         summary,
        "bank_ranking":    bank_ranking,
        "daily_trend":     daily_trend,
        "weekly_summary":  weekly_summary,
        "monthly_summary": monthly_summary,
        "recent_slips":    recent,
    }), 200


# ── Helpers ──────────────────────────────────────────────────────────────────

def _validate_file(file) -> str | None:
    if not file or not file.filename:
        return "Empty file"
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        return f"Unsupported file type '{ext}'. Allowed: jpg, png, webp"
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > MAX_SIZE_BYTES:
        return "File too large (max 10MB)"
    return None


def _call_ocr(file_path: str) -> tuple[dict | None, list[str]]:
    warnings = []
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(OCR_URL, files={"file": f}, timeout=60)
        if resp.status_code != 200:
            warnings.append(f"OCR service returned HTTP {resp.status_code}")
            return None, warnings
        body = resp.json()
        if not body.get("success"):
            warnings.append(body.get("error", "OCR failed"))
            return None, warnings
        warnings.extend(body.get("warnings", []))
        return body["data"], warnings
    except Exception as e:
        warnings.append(f"OCR service error: {e}")
        return None, warnings


def _call_thunder_verify(file_path: str, ocr_amount: float | None = None, ocr_ref: str | None = None) -> tuple[bool, str | None]:
    """
    ตรวจสอบสลิปปลอมด้วย API ของ Thunder Solution (v2) และนำมาเทียบกับ OCR
    Returns: (is_fake, fake_reason)
    """
    # Get API key from environment variables
    api_key = os.environ.get("THUNDER_API_KEY") or os.environ.get("Thunder_key")
    if not api_key:
        # ถ้าไม่มี API key ถือว่าไม่สามารถตรวจสอบได้ - ไม่ใช่ return False (ไม่ใช่ปลอม)
        return True, "ไม่สามารถตรวจสอบสลิปได้: ไม่ได้ตั้งค่า THUNDER_API_KEY"

    try:
        with open(file_path, "rb") as image_file:
            response = requests.post(
                'https://api.thunder.in.th/v2/verify/bank',
                headers={
                    'Authorization': f'Bearer {api_key}'
                },
                data={
                    'checkDuplicate': 'true'
                },
                files={
                    'image': image_file
                },
                timeout=15
            )

        result = response.json()

        # กรณีตรวจสอบไม่ผ่านจาก Thunder เอง (QR ใช้งานไม่ได้ หรือ API พัง)
        if not result.get('success'):
            error_data = result.get('error', {})
            reason = error_data.get('message') or error_data.get('code') or 'สลิปปลอม/ตรวจสอบไม่ได้'
            return True, reason

        # ดึง Payload Data จาก Thunder API ที่อ่านจากระบบธนาคารมาเช็ค
        api_data = result.get('data', {}).get('rawSlip', {})
        
        # 1. เทียบยอดเงิน (Amount)
        if ocr_amount is not None:
            # ยอดใน API ซ่อนอยู่ใน data.rawSlip.amount.amount
            api_amount = api_data.get('amount', {}).get('amount')
            if api_amount is not None:
                # แปลง float เป็น string เพื่อกันทศนิยมคลาดเคลื่อน หรือเปรียบเทียบตรงๆ
                if abs(float(api_amount) - float(ocr_amount)) > 0.01:
                    return True, f"ยอดเงินในภาพ ({ocr_amount}) ไม่ตรงกับข้อมูลจริง ({api_amount})"
                
        # 2. เทียบรหัสอ้างอิง (Ref No / TransRef) อย่างน้อยบางส่วน
        if ocr_ref:
            # ดึง transRef หรือ ref1 หรือ ref2 มาเทียบ (API บางธนาคารออก transRef บางที่ออก ref1)
            api_trans_ref = _normalize_ref(str(api_data.get('transRef') or ''))
            api_ref1 = _normalize_ref(str(api_data.get('ref1') or ''))
            api_ref2 = _normalize_ref(str(api_data.get('ref2') or ''))
            
            ocr_ref_norm = _normalize_ref(str(ocr_ref))
            
            # เช็คว่า ocr_ref เป็นส่วนหนึ่งของ api_ref ต่างๆ หรือไม่ 
            if not (ocr_ref_norm in api_trans_ref or ocr_ref_norm in api_ref1 or ocr_ref_norm in api_ref2):
                # ถ้าไม่ตรงเลย
                return True, f"รหัสอ้างอิงไม่ตรงกับข้อมูลจริง (ภาพ: {ocr_ref})"

        # ถ้าตรวจสอบผ่านทั้งหมด
        return False, None

    except requests.exceptions.RequestException as e:
        # API connection error - ถือว่าไม่สามารถตรวจสอบได้ (ไม่ใช่ return False)
        return True, f"ไม่สามารถเชื่อมต่อ Thunder API: {str(e)}"
    except Exception as e:
        return True, str(e)


def _normalize_ref(ref: str) -> str:
    """Normalize ref string for comparison: lowercase + ambiguous char translation."""
    if not ref:
        return ''
    import re as _re
    # ลบตัวอักษรพิเศษต่างๆ ออก
    cleaned = _re.sub(r'[^A-Za-z0-9]', '', ref).lower()
    
    # แทนที่ตัวอักษรที่มักจะทำให้เกิดปัญหา OCR / API เทียบกันไม่ผ่าน
    # ถือว่า o, 0, O เป็นตัวเดียวกันเวลาเทียบความคล้าย
    cleaned = cleaned.replace('o', '0')
    # ถือว่า i, l, I, t, 1 เป็นตัวเดียวกัน
    cleaned = cleaned.replace('i', '1')
    cleaned = cleaned.replace('l', '1')
    cleaned = cleaned.replace('t', '1')
    
    return cleaned


def _check_duplicate(file_hash: str | None, user_id: int, ref_no: str = None, bank_name: str = None) -> tuple[bool, int | None]:
    """Return (is_duplicate, original_slip_id)."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Check by hash first
            if file_hash:
                cur.execute(
                    """SELECT sh.slip_id FROM slip_hashes sh
                       JOIN slips s ON s.id = sh.slip_id
                       WHERE sh.hash = %s AND s.user_id = %s
                       LIMIT 1""",
                    (file_hash, user_id),
                )
                row = cur.fetchone()
                if row:
                    return True, row["slip_id"]
            
            # Check by ref_no alone (same ref_no for same user = duplicate)
            if ref_no:
                cur.execute(
                    """SELECT id FROM slips
                       WHERE ref_no = %s AND user_id = %s
                       LIMIT 1""",
                    (ref_no, user_id),
                )
                row2 = cur.fetchone()
                if row2:
                    return True, row2["id"]
                    
    except Exception:
        pass
    return False, None

def _get_slip_by_id(slip_id: int, user_id: int):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM slips WHERE id = %s AND user_id = %s", (slip_id, user_id))
            row = cur.fetchone()
            if row:
                return dict(row)
    except Exception:
        pass
    return None


def _save_slip(user_id: int, image_path: str, data: dict,
               is_duplicate: bool = False, file_hash: str | None = None) -> int:
    conn    = get_db()
    raw_ocr = json.dumps(data.get("raw_ocr"), ensure_ascii=False) if data.get("raw_ocr") else None

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO slips
               (user_id, image_path, sender_name, bank_name, amount,
                slip_date, slip_time, ref_no, receiver_name, receiver_acct,
                raw_ocr, is_fake, is_duplicate)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                user_id,
                image_path,
                data.get("sender_name"),
                data.get("bank_name"),
                data.get("amount"),
                data.get("slip_date") or None,
                data.get("slip_time") or None,
                data.get("ref_no"),
                data.get("receiver_name"),
                data.get("receiver_account"),
                raw_ocr,
                bool(data.get("is_fake", False)),
                is_duplicate,
            ),
        )
        slip_id = cur.fetchone()["id"]

        # Save hash for future duplicate detection
        if file_hash:
            try:
                cur.execute(
                    "INSERT INTO slip_hashes (slip_id, hash) VALUES (%s, %s) ON CONFLICT (hash) DO NOTHING",
                    (slip_id, file_hash),
                )
            except Exception:
                pass  # hash conflict is safe to ignore

        conn.commit()
    return slip_id
