"""
backend_flask/db/seed.py
สร้าง test users ใน Supabase Postgres
รัน: python db/seed.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bcrypt
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))

from config import get_db

USERS = [
    {"email": "admin@slipscan.test", "password": "admin1234", "role": "admin"},
    {"email": "user@slipscan.test",  "password": "user1234",  "role": "user"},
]

print("🌱 Seeding users...")
conn = get_db()
with conn.cursor() as cur:
    for u in USERS:
        hashed = bcrypt.hashpw(u["password"].encode(), bcrypt.gensalt(12)).decode()
        cur.execute(
            """INSERT INTO users (email, password, role)
               VALUES (%s, %s, %s)
               ON CONFLICT (email) DO NOTHING""",
            (u["email"], hashed, u["role"]),
        )
        print(f"  ✅ {u['email']} (password: {u['password']}, role: {u['role']})")
    conn.commit()

print("\nDone! ทดสอบ login ด้วย:")
print("  Email:    admin@slipscan.test")
print("  Password: admin1234")
