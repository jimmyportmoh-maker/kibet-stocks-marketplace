"""Change a Mobi Hub staff password. Run from the mobihub folder: python setpw.py"""
import getpass
from app.db import Session
from app.models import User
from app.security import hash_pw

email = input("Admin email [admin@fambeermconn.local]: ").strip().lower() or "admin@fambeermconn.local"
pw = getpass.getpass("New password (8+ characters, typing is hidden): ")
if len(pw) < 8:
    raise SystemExit("Password must be at least 8 characters. Nothing changed.")
if pw != getpass.getpass("Repeat the password: "):
    raise SystemExit("Passwords did not match. Nothing changed.")

s = Session()
u = s.query(User).filter_by(email=email).first()
if not u or u.role in ("customer", "provider"):
    raise SystemExit("No staff account with that email. Nothing changed.")
u.password_hash = hash_pw(pw)
s.commit()
print("Done. Password changed for", email)
