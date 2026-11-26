import os, uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session
from .db import get_db
from .models import *
from .security import current_user, require

router = APIRouter()
UPLOADS = os.getenv("UPLOAD_DIR") or os.path.join(os.path.dirname(__file__), "..", "private_uploads")
os.makedirs(UPLOADS, exist_ok=True)
DOC_TYPES = ["National ID", "Passport", "Selfie with ID", "Business registration", "Professional licence", "Other"]
ALLOWED, MAXB = {".pdf", ".jpg", ".jpeg", ".png"}, 8 * 1024 * 1024
def row(o): return {c.name: getattr(o, c.name) for c in o.__table__.columns if c.name != "password_hash"}
def log(db, e, u, event, note=""): db.add(EscrowEvent(escrow_id=e.id, actor_id=u.id, event=event, note=note))
def audit(db, u, action, tid, detail=""): db.add(AuditLog(admin_id=u.id, action=action, resource="escrow", target_id=tid, detail=detail))
def prov(db, u):
    p = db.query(ProviderProfile).filter_by(user_id=u.id).first()
    if not p: raise HTTPException(403, "Providers only")
    return p
def view(db, b):
    e = db.query(Escrow).filter_by(booking_id=b.id).first(); s = db.get(Service, b.service_id)
    p = db.get(ProviderProfile, b.provider_id) if b.provider_id else None
    return {"booking": row(b), "escrow": row(e) if e else None, "service": s.name if s else "", "provider": p.business_name if p else ""}

@router.get("/api/me")
def me(u: User = Depends(current_user)): return {"id": u.id, "name": u.name, "email": u.email, "phone": u.phone or "", "farm_name": u.farm_name or "", "role": u.role}
@router.get("/api/services")
def services(db: Session = Depends(get_db)): return [row(s) for s in db.query(Service).filter_by(active=True)]
@router.get("/api/config")
def config(db: Session = Depends(get_db)):
    keys = ("whatsapp_number", "facebook_page"); out = {k: "" for k in keys}
    for s in db.query(Setting).filter(Setting.key.in_(keys)): out[s.key] = s.value
    return out
@router.get("/api/escrow/instructions")
def instructions(db: Session = Depends(get_db)):
    s = db.query(Setting).filter_by(key="escrow_instructions").first(); return s.value if s else ""

# ---- Provider
@router.get("/api/provider/me")
def provider_me(u: User = Depends(current_user), db: Session = Depends(get_db)):
    p = prov(db, u)
    docs = db.query(VerificationDoc).filter_by(provider_id=p.id).all()
    jobs = db.query(Booking).filter_by(provider_id=p.id).order_by(Booking.id.desc()).all()
    return {"profile": row(p), "doc_types": DOC_TYPES, "jobs": [view(db, b) for b in jobs],
            "docs": [{"id": d.id, "doc_type": d.doc_type, "filename": d.filename, "created_at": str(d.created_at)} for d in docs]}
class ProfileIn(BaseModel):
    business_name: str = ""; address: str = ""; service_area: str = ""; payout_info: str = ""; whatsapp: str = ""; service_ids: list[int] = []
@router.patch("/api/provider/me")
def patch_me(b: ProfileIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    p = prov(db, u)
    p.business_name, p.address, p.service_area, p.payout_info = b.business_name, b.address, b.service_area, b.payout_info
    p.whatsapp = "".join(c for c in b.whatsapp if c.isdigit())[:15]
    p.service_ids = ",".join(map(str, b.service_ids)); db.commit(); return row(p)
@router.post("/api/provider/documents")
def upload_doc(doc_type: str = Form(...), expires_on: str = Form(""), file: UploadFile = File(...),
               u: User = Depends(current_user), db: Session = Depends(get_db)):
    p = prov(db, u)
    if doc_type not in DOC_TYPES: raise HTTPException(400, "Unknown document type")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED: raise HTTPException(400, "Only JPG, PNG or PDF files")
    data = file.file.read(MAXB + 1)
    if len(data) > MAXB: raise HTTPException(400, "File larger than 8 MB")
    db.add(VerificationDoc(provider_id=p.id, doc_type=doc_type, filename=os.path.basename(file.filename), content=data, expires_on=expires_on))
    if p.verification_status in ("pending", "more_info"): p.verification_status = "under_review"
    db.commit(); return {"ok": True}

# ---- Customer
@router.get("/api/providers")
def providers(service_id: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    out = []
    for p in db.query(ProviderProfile).filter_by(verification_status="approved"):
        usr = db.get(User, p.user_id)
        if usr and usr.status == "active" and str(service_id) in (p.service_ids or "").split(","):
            out.append({"id": p.id, "business_name": p.business_name, "service_area": p.service_area, "whatsapp": p.whatsapp or ""})
    return out
class NewBooking(BaseModel): service_id: int; provider_id: int; scheduled_at: str; notes: str = ""; amount: float = 0
@router.post("/api/bookings")
def book(b: NewBooking, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if u.role != "customer": raise HTTPException(403, "Customers only")
    s, p = db.get(Service, b.service_id), db.get(ProviderProfile, b.provider_id)
    if not s or not s.active: raise HTTPException(404, "Service unavailable")
    if not p or p.verification_status != "approved" or str(s.id) not in (p.service_ids or "").split(","):
        raise HTTPException(400, "Provider not available for this service")
    amount = s.price if s.price > 0 else b.amount
    if amount <= 0: raise HTTPException(400, "Enter an amount")
    o = Booking(customer_id=u.id, provider_id=p.id, service_id=s.id, scheduled_at=b.scheduled_at, notes=b.notes); db.add(o); db.flush()
    com = round(amount * s.commission_pct / 100, 2)
    e = Escrow(booking_id=o.id, amount=amount, currency=s.currency, commission_pct=s.commission_pct, commission_amount=com, payout_amount=round(amount - com, 2))
    db.add(e); db.flush(); log(db, e, u, "created"); db.commit(); return view(db, o)
@router.get("/api/bookings/mine")
def mine(u: User = Depends(current_user), db: Session = Depends(get_db)):
    return [view(db, b) for b in db.query(Booking).filter_by(customer_id=u.id).order_by(Booking.id.desc())]

# ---- Escrow transitions
class Ref(BaseModel): ref: str = ""; note: str = ""
def esc_for(db, eid, u, who):
    e = db.get(Escrow, eid)
    if not e: raise HTTPException(404)
    b = db.get(Booking, e.booking_id)
    if who == "customer" and b.customer_id != u.id: raise HTTPException(403)
    if who == "provider" and b.provider_id != prov(db, u).id: raise HTTPException(403)
    return e, b
def move(db, e, u, allowed, new, event, note=""):
    if e.status not in allowed: raise HTTPException(400, f"Not allowed while status is '{e.status}'")
    e.status, e.updated_at = new, datetime.utcnow(); log(db, e, u, event, note)
@router.post("/api/escrow/{eid}/pay")
def pay(eid: int, b: Ref, u: User = Depends(current_user), db: Session = Depends(get_db)):
    ref = b.ref.strip().upper(); e, _ = esc_for(db, eid, u, "customer")
    if not ref: raise HTTPException(400, "Enter the payment reference")
    if db.query(Escrow).filter(Escrow.customer_ref == ref, Escrow.id != e.id).first(): raise HTTPException(409, "That reference was already used")
    move(db, e, u, ["awaiting_payment"], "payment_submitted", "payment_submitted", ref); e.customer_ref = ref; db.commit(); return row(e)
@router.post("/api/escrow/{eid}/deliver")
def deliver(eid: int, b: Ref, u: User = Depends(current_user), db: Session = Depends(get_db)):
    e, _ = esc_for(db, eid, u, "provider"); move(db, e, u, ["funded"], "delivered", "delivered", b.note); db.commit(); return row(e)
@router.post("/api/escrow/{eid}/approve")
def approve(eid: int, b: Ref, u: User = Depends(current_user), db: Session = Depends(get_db)):
    e, _ = esc_for(db, eid, u, "customer"); move(db, e, u, ["delivered"], "approved", "customer_approved"); db.commit(); return row(e)
@router.post("/api/escrow/{eid}/dispute")
def dispute(eid: int, b: Ref, u: User = Depends(current_user), db: Session = Depends(get_db)):
    e, bk = esc_for(db, eid, u, "customer"); move(db, e, u, ["funded", "delivered"], "disputed", "disputed", b.note)
    pu = db.get(ProviderProfile, bk.provider_id)
    db.add(Complaint(raised_by=u.id, against_user=pu.user_id if pu else None, booking_id=bk.id, subject=f"Escrow dispute #{e.id}", details=b.note))
    db.commit(); return row(e)

# ---- Finance admin (manual money handling)
FIN = Depends(require("finance"))
@router.post("/api/admin/escrow/{eid}/confirm")
def confirm(eid: int, b: Ref, u: User = FIN, db: Session = Depends(get_db)):
    e = db.get(Escrow, eid)
    if not e: raise HTTPException(404)
    move(db, e, u, ["payment_submitted"], "funded", "funds_confirmed", b.note)
    bk = db.get(Booking, e.booking_id); bk.status = "confirmed"
    db.add(Payment(booking_id=bk.id, method="manual", amount=e.amount, currency=e.currency, reference=e.customer_ref, status="paid"))
    audit(db, u, "escrow_confirm", eid, e.customer_ref); db.commit(); return row(e)
@router.post("/api/admin/escrow/{eid}/reject")
def reject(eid: int, b: Ref, u: User = FIN, db: Session = Depends(get_db)):
    e = db.get(Escrow, eid)
    if not e: raise HTTPException(404)
    move(db, e, u, ["payment_submitted"], "awaiting_payment", "payment_rejected", b.note); e.customer_ref = ""
    audit(db, u, "escrow_reject", eid, b.note); db.commit(); return row(e)
@router.post("/api/admin/escrow/{eid}/release")
def release(eid: int, b: Ref, u: User = FIN, db: Session = Depends(get_db)):
    e = db.get(Escrow, eid)
    if not e: raise HTTPException(404)
    if not b.ref.strip(): raise HTTPException(400, "Enter the payout reference (M-Pesa/bank code)")
    move(db, e, u, ["approved", "delivered", "disputed"], "released", "released_to_provider", b.ref); e.payout_ref = b.ref.strip()
    db.get(Booking, e.booking_id).status = "completed"; audit(db, u, "escrow_release", eid, b.ref); db.commit(); return row(e)
@router.post("/api/admin/escrow/{eid}/refund")
def refund_escrow(eid: int, b: Ref, u: User = FIN, db: Session = Depends(get_db)):
    e = db.get(Escrow, eid)
    if not e: raise HTTPException(404)
    if not b.ref.strip(): raise HTTPException(400, "Enter the refund reference")
    move(db, e, u, ["funded", "delivered", "disputed"], "refunded", "refunded_to_customer", b.ref); e.refund_ref = b.ref.strip()
    db.get(Booking, e.booking_id).status = "cancelled"
    p = db.query(Payment).filter_by(booking_id=e.booking_id).first()
    if p: p.status = "refunded"
    audit(db, u, "escrow_refund", eid, b.ref); db.commit(); return row(e)
@router.get("/api/admin/escrow/{eid}/events")
def events(eid: int, u: User = Depends(require("finance", "support")), db: Session = Depends(get_db)):
    return [row(x) for x in db.query(EscrowEvent).filter_by(escrow_id=eid).order_by(EscrowEvent.id)]
