-- ========================================================
-- SlipScan Database Schema (PostgreSQL / Supabase)
-- รัน: paste ใน Supabase SQL Editor หรือ psql
-- ========================================================

-- ── Table: users ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    email       VARCHAR(255) UNIQUE NOT NULL,
    password    VARCHAR(255) NOT NULL,
    role        VARCHAR(10)  NOT NULL DEFAULT 'user'
                    CHECK (role IN ('admin', 'user')),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── Table: slips ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS slips (
    id            SERIAL PRIMARY KEY,
    user_id       INT           NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    image_path    VARCHAR(500)  NOT NULL,
    sender_name   VARCHAR(255)  DEFAULT NULL,
    bank_name     VARCHAR(100)  DEFAULT NULL,
    amount        NUMERIC(15,2) DEFAULT NULL,
    slip_date     DATE          DEFAULT NULL,
    slip_time     TIME          DEFAULT NULL,
    ref_no        VARCHAR(100)  DEFAULT NULL,
    receiver_name VARCHAR(255)  DEFAULT NULL,
    receiver_acct VARCHAR(50)   DEFAULT NULL,
    raw_ocr       JSONB         DEFAULT NULL,
    is_fake       BOOLEAN       NOT NULL DEFAULT FALSE,
    is_duplicate  BOOLEAN       NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ── Table: slip_hashes (Duplicate Detection) ─────────────
CREATE TABLE IF NOT EXISTS slip_hashes (
    id          SERIAL PRIMARY KEY,
    slip_id     INT          NOT NULL REFERENCES slips(id) ON DELETE CASCADE,
    hash        VARCHAR(64)  UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── Indexes ──────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_slips_user_id  ON slips(user_id);
CREATE INDEX IF NOT EXISTS idx_slips_ref_no   ON slips(ref_no);
CREATE INDEX IF NOT EXISTS idx_slips_created  ON slips(created_at DESC);
