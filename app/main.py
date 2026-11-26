import os, uuid, shutil, re
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, Response
from urllib.parse import quote
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, inspect, text
from .db import Base, engine, get_db, Session as SessionLocal
from .models import *
from .security import hash_pw, check_pw, make_token, current_user, require
from .seed import seed

UPLOADS = os.getenv("UPLOAD_DIR") or os.path.join(os.path.dirname(__file__), "..", "private_uploads")
os.makedirs(UPLOADS, exist_ok=True)
Base.metadata.create_all(engine)
# Add the seller county field to existing databases without dropping records.
with engine.begin() as conn:
    seller_columns = {column["name"] for column in inspect(engine).get_columns("seller_profiles")}
    if "county" not in seller_columns:
        conn.execute(text("ALTER TABLE seller_profiles ADD COLUMN county VARCHAR(80) DEFAULT ''"))
    user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
    if "farm_name" not in user_columns:
        conn.execute(text("ALTER TABLE users ADD COLUMN farm_name VARCHAR(200) DEFAULT ''"))
with SessionLocal() as _s: seed(_s)
app = FastAPI(title="SHESA FIRM ENTERPRISES")

# resource -> (model, roles allowed, writable fields, creatable)
R = {
 "users": (User, ["support"], ["status", "role", "name", "phone", "country"], False),
 "providers": (ProviderProfile, ["verification"], ["verification_status", "verification_note", "service_area"], False),
 "documents": (VerificationDoc, ["verification"], ["expires_on"], False),
 "categories": (Category, ["catalogue"], ["name", "active", "parent_id"], True),
 "services": (Service, ["catalogue"], ["name","description","provider_type","booking_method","location_type","price_type","price",
              "currency","verification_required","required_documents","cancellation_rules","commission_pct","capacity","countries",
              "active","category_id"], True),
 "bookings": (Booking, ["operations"], ["status", "provider_id", "scheduled_at", "notes"], False),
 "payments": (Payment, ["finance"], ["status"], False),
 "reviews": (Review, ["support"], ["hidden"], False),
 "complaints": (Complaint, ["support"], ["status", "resolution"], False),
 "escrow": (Escrow, ["finance"], [], False),
 "audit": (AuditLog, [], [], False),
 "activity": (ActivityLog, ["analytics", "operations", "finance", "support", "verification", "catalogue"], [], False),
 "products": (Product, ["catalogue"], ["active", "price", "stock", "title", "category", "description", "unit", "county"], False),
 "product_orders": (ProductOrder, ["operations", "finance", "support"], ["status"], False),
 "product_order_items": (ProductOrderItem, ["finance", "operations"], [], False),
 "seller_profiles": (SellerProfile, ["finance", "operations", "analytics"], [], False),
 "settings": (Setting, [], ["value"], True),
}
def row(o): return {c.name: getattr(o, c.name) for c in o.__table__.columns if c.name != "password_hash"}
def audit(db, u, action, res, tid=None, detail=""):
    db.add(AuditLog(admin_id=u.id, action=action, resource=res, target_id=tid, detail=detail))
    db.add(ActivityLog(actor_id=u.id, actor_email=u.email, action=f"admin_{action}", resource=res, target_id=tid, detail=detail))
    db.commit()
def guard(res, u):
    if u.role != "super" and u.role not in R[res][1] and not (res == "payments" and u.role == "finance"):
        raise HTTPException(403, "Insufficient permissions")
    if u.role in ("customer", "provider"): raise HTTPException(403, "Admins only")

class Login(BaseModel): email: str; password: str
class Register(BaseModel):
    email: str
    password: str
    name: str
    phone: str = ""
    role: str = "customer"
    business_name: str = ""
    payout_phone: str = ""
    county: str = ""

@app.post("/api/auth/login")
def login(b: Login, db: Session = Depends(get_db)):
    u = db.query(User).filter_by(email=b.email.lower()).first()
    if not u or not check_pw(b.password, u.password_hash): raise HTTPException(401, "Wrong email or password")
    if u.status != "active": raise HTTPException(403, "Account " + u.status)
    db.add(ActivityLog(actor_id=u.id, actor_email=u.email, action="login", resource="auth", detail="Account signed in"))
    db.commit()
    return {"token": make_token(u), "role": u.role, "name": u.name}

@app.post("/api/auth/register")
def register(b: Register, db: Session = Depends(get_db)):
    if b.role not in ("customer", "provider", "seller"): raise HTTPException(400, "Role must be customer, seller, or provider")
    if len(b.password) < 8: raise HTTPException(400, "Password must be 8+ characters")
    if db.query(User).filter_by(email=b.email.lower()).first(): raise HTTPException(409, "Email already registered")
    if b.role == "seller":
        if not b.county.strip(): raise HTTPException(400, "Select your seller county")
        payout = "".join(c for c in b.payout_phone if c.isdigit())
        if payout.startswith("0"): payout = "254" + payout[1:]
        if not re.fullmatch(r"254[17]\d{8}", payout):
            raise HTTPException(400, "Enter a valid Kenyan M-Pesa payout number, such as 0712345678")
    else:
        payout = ""
    u = User(email=b.email.lower(), name=b.name, phone=b.phone, role=b.role, password_hash=hash_pw(b.password)); db.add(u); db.flush()
    if b.role == "provider": db.add(ProviderProfile(user_id=u.id, business_name=b.business_name))
    if b.role == "seller": db.add(SellerProfile(user_id=u.id, payout_phone=payout, county=b.county.strip()))
    db.add(ActivityLog(actor_id=u.id, actor_email=u.email, action="account_registered", resource="auth", detail=f"New {b.role} account"))
    db.commit(); return {"token": make_token(u), "role": u.role}

# ---- Admin
@app.get("/api/admin/overview")
def overview(u: User = Depends(require("analytics", "operations", "finance", "support", "verification", "catalogue")), db: Session = Depends(get_db)):
    c = lambda m, **f: db.query(m).filter_by(**f).count()
    return {"users": c(User), "active_users": c(User, status="active"), "providers": c(ProviderProfile),
            "verified_providers": c(ProviderProfile, verification_status="approved"),
            "pending_verification": db.query(ProviderProfile).filter(ProviderProfile.verification_status.in_(["pending", "under_review"])).count(),
            "services": c(Service), "bookings": c(Booking), "new_bookings": c(Booking, status="new"),
            "payments_paid": c(Payment, status="paid"), "payments_failed": c(Payment, status="failed"),
            "revenue_kes": db.query(func.coalesce(func.sum(Payment.amount), 0)).filter_by(status="paid", currency="KES").scalar(),
            "open_complaints": c(Complaint, status="open"), "reviews": c(Review),
            "escrow_held_kes": db.query(func.coalesce(func.sum(Escrow.amount), 0)).filter(Escrow.status.in_(["funded","delivered","approved","disputed"]), Escrow.currency=="KES").scalar(),
            "escrow_to_confirm": c(Escrow, status="payment_submitted"), "payouts_due": c(Escrow, status="approved"),
            "commission_earned_kes": db.query(func.coalesce(func.sum(Escrow.commission_amount), 0)).filter_by(status="released", currency="KES").scalar(),
            "products": c(Product, active=True), "product_orders": c(ProductOrder),
            "orders_awaiting_payment": c(ProductOrder, payment_status="pending"),
            "product_orders_paid": c(ProductOrder, payment_status="paid"),
            "mpesa_checkout_configured": bool(os.getenv("MPESA_CONSUMER_KEY") and os.getenv("MPESA_CONSUMER_SECRET")
                and os.getenv("MPESA_SHORTCODE") and os.getenv("MPESA_PASSKEY") and os.getenv("MPESA_CALLBACK_TOKEN")
                and os.getenv("MPESA_CALLBACK_URL", "").startswith("https://")),
            "card_checkout_configured": bool(os.getenv("STRIPE_SECRET_KEY") and os.getenv("STRIPE_WEBHOOK_SECRET"))}

@app.get("/api/admin/{res}")
def list_(res: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if res not in R: raise HTTPException(404)
    guard(res, u); M = R[res][0]
    return [row(o) for o in db.query(M).order_by(M.id.desc()).limit(500)]

@app.post("/api/admin/{res}")
def create(res: str, body: dict, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if res not in R or not R[res][3]: raise HTTPException(404)
    guard(res, u); M, _, fields, _ = R[res]
    data = {k: v for k, v in body.items() if k in fields or (res == "settings" and k == "key")}
    o = M(**data); db.add(o); db.commit(); audit(db, u, "create", res, o.id, str(data)); return row(o)

@app.patch("/api/admin/{res}/{rid}")
def update(res: str, rid: int, body: dict, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if res not in R: raise HTTPException(404)
    guard(res, u); M, _, fields, _ = R[res]; o = db.get(M, rid)
    if not o: raise HTTPException(404)
    if res == "users" and (o.role == "super" or o.id == u.id) and u.role != "super": raise HTTPException(403)
    if res == "users" and "role" in body and u.role != "super": raise HTTPException(403, "Only Super Admin can change roles")
    ch = {k: v for k, v in body.items() if k in fields}
    if res == "providers" and ch.get("verification_status") == "approved":
        types = {d.doc_type for d in db.query(VerificationDoc).filter_by(provider_id=rid)}
        if not types & {"National ID", "Passport"}: raise HTTPException(400, "Cannot approve: no ID document uploaded")
        if not (o.payout_info or "").strip(): raise HTTPException(400, "Cannot approve: provider has no payout details")
    for k, v in ch.items(): setattr(o, k, v)
    db.commit(); audit(db, u, "update", res, rid, str(ch)); return row(o)

@app.post("/api/admin/users")
def create_admin(body: dict, u: User = Depends(require()), db: Session = Depends(get_db)):
    roles = ("super", "verification", "operations", "finance", "support", "catalogue", "analytics")
    if body.get("role") not in roles or len(body.get("password", "")) < 8: raise HTTPException(400, "Bad role or short password")
    n = User(email=body["email"].lower(), name=body.get("name", ""), role=body["role"], password_hash=hash_pw(body["password"]))
    db.add(n); db.commit(); audit(db, u, "create_admin", "users", n.id, n.role); return row(n)

@app.get("/api/admin/documents/{rid}/file")
def doc_file(rid: int, u: User = Depends(require("verification")), db: Session = Depends(get_db)):
    d = db.get(VerificationDoc, rid)
    if not d: raise HTTPException(404)
    audit(db, u, "view_document", "documents", rid)
    data = d.content
    if data is None:  # older rows saved on disk
        with open(os.path.join(UPLOADS, d.stored_path), "rb") as f: data = f.read()
    ext = os.path.splitext(d.filename)[1].lower()
    mt = {".pdf": "application/pdf", ".png": "image/png"}.get(ext, "image/jpeg")
    return Response(content=bytes(data), media_type=mt, headers={"X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline; filename*=UTF-8''" + quote(d.filename)})

@app.post("/api/admin/payments/{rid}/refund")
def refund(rid: int, u: User = Depends(require("finance")), db: Session = Depends(get_db)):
    p = db.get(Payment, rid)
    if not p or p.status != "paid": raise HTTPException(400, "Only paid payments can be refunded")
    p.status = "refunded"; db.commit(); audit(db, u, "refund", "payments", rid); return row(p)

from .portal import router as portal_router
app.include_router(portal_router)
from .storefront import router as storefront_router
app.include_router(storefront_router)
from .issues import router as issues_router
app.include_router(issues_router)
STATIC = os.path.join(os.path.dirname(__file__), "..", "static")
@app.get("/")
def home(): return FileResponse(os.path.join(STATIC, "shop.html"))
@app.get("/admin")
def admin_page(): return FileResponse(os.path.join(STATIC, "admin.html"))
@app.get("/issues")
def issues_page(): return FileResponse(os.path.join(STATIC, "issues.html"))
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "static")), name="static")
