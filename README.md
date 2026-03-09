# SlipScan-Backend 🔒

REST API สำหรับระบบตรวจสอบสลิปโอนเงินธนาคารไทย (SlipScan) สร้างด้วย **Flask** และ **PostgreSQL (Supabase)**

## Architecture

```
SlipScan-Backend (port 8000)
    ├── /api/auth      — Register, Login, JWT Auth
    └── /api/slips     — Upload, List, Export, Dashboard
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.10 |
| Framework | Flask 3.1 |
| Auth | JWT (PyJWT) |
| Database | PostgreSQL via psycopg2 (Supabase) |
| Rate Limiting | Flask-Limiter |
| Container | Docker + Gunicorn |

## API Endpoints

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/register` | สมัครสมาชิก |
| POST | `/api/auth/login` | เข้าสู่ระบบ (รับ JWT token) |
| GET | `/api/auth/me` | ดูข้อมูล user ปัจจุบัน |

### Slips
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/slips/upload` | อัปโหลดสลิปเดี่ยว (max 20/min) |
| POST | `/api/slips/upload-batch` | อัปโหลดแบบ batch (max 5/min) |
| GET | `/api/slips/` | รายการสลิปทั้งหมด (ค้นหา/กรองได้) |
| GET | `/api/slips/<id>` | ดูสลิปรายบัญชี |
| GET | `/api/slips/export` | Export CSV |
| GET | `/api/slips/dashboard` | ข้อมูลสถิติ Dashboard |
| GET | `/health` | Health check |

## Getting Started

### 1. Clone

```bash
git clone https://github.com/Napat-ICE/SlipScan-Backend.git
cd SlipScan-Backend
```

### 2. Setup Environment

```bash
cp .env.example .env
# แก้ไข .env ใส่ค่าจริง
```

**.env** ที่ต้องกรอก:

```env
DATABASE_URL=postgresql://user:password@host:5432/dbname
JWT_SECRET=your_very_long_random_secret_key
JWT_EXPIRE=86400
THUNDER_API_KEY=your_thunder_api_key
OCR_SERVICE_URL=http://ocr_service:5000/ocr
BACKEND_PORT=8000
APP_DEBUG=false
LOG_LEVEL=INFO
```

### 3. Run with Docker

```bash
# รันเดี่ยว
docker build -t slipscan-backend .
docker run -p 8000:8000 --env-file .env slipscan-backend

# รันพร้อม docker-compose (แนะนำ)
cd ..
docker-compose up -d
```

### 4. Run Locally (Development)

```bash
pip install -r requirements.txt
python app.py
```

## Rate Limiting

| Endpoint | Limit |
|----------|-------|
| POST /api/slips/upload | 20 req/min per user |
| POST /api/slips/upload-batch | 5 req/min per user |
| ทั้งหมด | 200 req/day, 50 req/hour |

เกินจะได้รับ `429 Too Many Requests`

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | ✅ | — | PostgreSQL connection string |
| `JWT_SECRET` | ✅ | — | Secret key สำหรับ JWT |
| `THUNDER_API_KEY` | ✅ | — | Thunder Solution API Key |
| `OCR_SERVICE_URL` | ❌ | `http://localhost:5000/ocr` | URL ของ OCR Service |
| `JWT_EXPIRE` | ❌ | `86400` | Token expire time (วินาที) |
| `LOG_LEVEL` | ❌ | `INFO` | Log level (DEBUG/INFO/WARNING) |

## Project Structure

```
SlipScan-Backend/
├── app.py              — Flask app, middleware, rate limiting
├── extensions.py       — Shared Flask extensions (Limiter)
├── auth_guard.py       — JWT authentication decorator
├── config.py           — Database connection
├── routes/
│   ├── auth.py         — Authentication endpoints
│   └── slips.py        — Slip management endpoints
├── uploads/            — Uploaded slip images (gitignored)
├── requirements.txt
├── Dockerfile
└── .env.example
```

## Related Services

- [SlipScan-OCR](https://github.com/Napat-ICE/SlipScan-OCR) — OCR microservice
- [SlipScan-Frontend](https://github.com/Napat-ICE/SlipScan-Frontend) — Web interface

## Changelog

### v1.1.0 — Bug Fixes (E2E Testing)
- **fix:** `raw_ocr` JSON parse error on duplicate slip response — psycopg2 returns JSON columns as `dict`, not `str`. Fixed with `isinstance` check before calling `json.loads()`
- **feat:** `extensions.py` — centralized Flask extensions to prevent circular imports between `app.py` and `routes/slips.py`
- **feat:** Rate limiting with `Flask-Limiter` (20/min upload, 5/min batch)
- **feat:** Structured request logging via Python `logging` module (timestamp, method, path, status, duration)
- **fix:** `.env` file corrected — `OCR_SERVICE_URL` and `THUNDER_API_KEY` were concatenated on same line

### v1.0.0 — Initial Release
- Flask REST API with JWT authentication
- Single and batch slip upload with OCR integration
- Duplicate slip detection by file hash (early check before API calls)
- PostgreSQL via Supabase
- Dashboard statistics and CSV export
