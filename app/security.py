import os, bcrypt, jwt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session
from .db import get_db
from .models import User

# Keep SECRET_KEY fixed in Railway Variables. Changing it invalidates existing tokens.
SECRET = os.getenv("SECRET_KEY", "dev-only-change-me")
bearer = HTTPBearer(auto_error=False)

def hash_pw(p):
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()

def check_pw(p, h):
    return bcrypt.checkpw(p.encode(), h.encode())

def make_token(u, hours=24):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(u.id),
        "role": u.role,
        "iat": now,
        "exp": now + timedelta(hours=hours),
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")

def current_user(cred=Depends(bearer), db: Session = Depends(get_db)):
    if not cred:
        raise HTTPException(401, "Not authenticated. Please sign in again.")
    try:
        data = jwt.decode(
            cred.credentials,
            SECRET,
            algorithms=["HS256"],
            leeway=120,
        )
        user_id = int(data["sub"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired. Please sign in again.")
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(401, "Invalid session. Please sign in again.")

    u = db.get(User, user_id)
    if not u or u.status != "active":
        raise HTTPException(403, "Account not active")
    return u

def require(*roles):
    def dep(u=Depends(current_user)):
        if u.role != "super" and u.role not in roles:
            raise HTTPException(403, "Insufficient permissions")
        return u
    return dep
