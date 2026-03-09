"""
extensions.py — shared Flask extensions (prevents circular imports)
Import this module in both app.py and routes that need the limiter.
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,   # will be overridden in app.py via init_app
    storage_uri="memory://",
)
