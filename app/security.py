import os, bcrypt, jwt
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session
from .db import get_db
from .models import User
SECRET = os.getenv("SECRET_KEY", "dev-only-change-me")
bearer = HTTPBearer(auto_error=False)
def hash_pw(p): return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()
def check_pw(p, h): return bcrypt.checkpw(p.encode(), h.encode())
def make_token(u, hours=8):
    return jwt.encode({"sub": str(u.id), "role": u.role, "exp": datetime.utcnow()+timedelta(hours=hours)}, SECRET, "HS256")
def current_user(cred=Depends(bearer), db: Session = Depends(get_db)):
    if not cred: raise HTTPException(401, "Not authenticated")
    try: data = jwt.decode(cred.credentials, SECRET, ["HS256"])
    except Exception: raise HTTPException(401, "Invalid or expired token")
    u = db.get(User, int(data["sub"]))
    if not u or u.status != "active": raise HTTPException(403, "Account not active")
    return u
def require(*roles):
    def dep(u=Depends(current_user)):
        if u.role != "super" and u.role not in roles: raise HTTPException(403, "Insufficient permissions")
        return u
    return dep
