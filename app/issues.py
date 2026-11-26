import os
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from .db import get_db
from .models import User, FarmIssue, FarmIssueAttachment, FarmIssueMessage
from .security import current_user

router = APIRouter()
MAX_FILE = 8 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
VOICE_TYPES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav", "audio/x-wav"}

def customer(user):
    if user.role != "customer":
        raise HTTPException(403, "Customer accounts only")

def issue_row(db, issue):
    customer_user = db.get(User, issue.customer_id)
    photos = db.query(FarmIssueAttachment).filter_by(issue_id=issue.id).order_by(FarmIssueAttachment.id).all()
    messages = db.query(FarmIssueMessage).filter_by(issue_id=issue.id).order_by(FarmIssueMessage.id).all()
    return {
        "id": issue.id, "crop": issue.crop, "issue_type": issue.issue_type,
        "urgency": issue.urgency, "description": issue.description,
        "latitude": issue.latitude, "longitude": issue.longitude,
        "status": issue.status, "rating": issue.rating,
        "created_at": issue.created_at.isoformat() if issue.created_at else "",
        "updated_at": issue.updated_at.isoformat() if issue.updated_at else "",
        "customer_name": customer_user.name if customer_user else "",
        "attachments": [{"id": a.id, "filename": a.filename, "media_type": a.media_type,
                         "created_at": a.created_at.isoformat() if a.created_at else ""} for a in photos],
        "messages": [{"id": m.id, "sender_id": m.sender_id, "message": m.message,
                      "created_at": m.created_at.isoformat() if m.created_at else ""} for m in messages]
    }

def owned_issue(db, issue_id, user):
    customer(user)
    issue = db.get(FarmIssue, issue_id)
    if not issue or issue.customer_id != user.id:
        raise HTTPException(404, "Issue not found")
    return issue

def save_attachment(db, issue, user, upload, kind):
    if not upload or not upload.filename:
        return
    media = (upload.content_type or "").lower()
    allowed = IMAGE_TYPES if kind == "image" else VOICE_TYPES
    if media not in allowed:
        raise HTTPException(400, "Unsupported " + kind + " format")
    data = upload.file.read(MAX_FILE + 1)
    if not data:
        raise HTTPException(400, "The selected file is empty")
    if len(data) > MAX_FILE:
        raise HTTPException(400, "Files must be 8 MB or smaller")
    db.add(FarmIssueAttachment(issue_id=issue.id, uploaded_by=user.id,
        filename=os.path.basename(upload.filename)[:255], media_type=media, content=data))

@router.post("/api/issues")
def create_issue(crop: str = Form(...), issue_type: str = Form(...),
                 urgency: str = Form("medium"), description: str = Form(""),
                 latitude: float | None = Form(None), longitude: float | None = Form(None),
                 photo: UploadFile | None = File(None), voice: UploadFile | None = File(None),
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    customer(user)
    crop, issue_type = crop.strip(), issue_type.strip()
    if not crop or not issue_type:
        raise HTTPException(400, "Select a crop and issue type")
    if urgency not in ("low", "medium", "high"):
        raise HTTPException(400, "Choose a valid urgency")
    issue = FarmIssue(customer_id=user.id, crop=crop[:100], issue_type=issue_type[:80],
        urgency=urgency, description=description.strip()[:5000],
        latitude=latitude, longitude=longitude)
    db.add(issue); db.flush()
    save_attachment(db, issue, user, photo, "image")
    save_attachment(db, issue, user, voice, "voice")
    db.commit(); db.refresh(issue)
    return issue_row(db, issue)

@router.get("/api/issues/mine")
def my_issues(user: User = Depends(current_user), db: Session = Depends(get_db)):
    customer(user)
    return [issue_row(db, item) for item in db.query(FarmIssue).filter_by(customer_id=user.id).order_by(FarmIssue.id.desc()).all()]

@router.get("/api/issues/{issue_id}")
def issue_details(issue_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return issue_row(db, owned_issue(db, issue_id, user))

@router.get("/api/issues/{issue_id}/attachments/{attachment_id}")
def issue_attachment(issue_id: int, attachment_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owned_issue(db, issue_id, user)
    item = db.query(FarmIssueAttachment).filter_by(id=attachment_id, issue_id=issue_id).first()
    if not item: raise HTTPException(404, "Attachment not found")
    from fastapi.responses import Response
    return Response(content=bytes(item.content), media_type=item.media_type,
        headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})

@router.post("/api/issues/{issue_id}/photos")
def add_issue_photo(issue_id: int, photo: UploadFile = File(...),
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    issue = owned_issue(db, issue_id, user)
    if issue.status in ("resolved", "cancelled"):
        raise HTTPException(400, "Re-open this issue before adding another photo")
    save_attachment(db, issue, user, photo, "image")
    db.commit()
    return issue_row(db, issue)

class MessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=3000)

@router.post("/api/issues/{issue_id}/messages")
def send_issue_message(issue_id: int, body: MessageIn,
                       user: User = Depends(current_user), db: Session = Depends(get_db)):
    issue = owned_issue(db, issue_id, user)
    if issue.status == "cancelled": raise HTTPException(400, "This report was cancelled")
    db.add(FarmIssueMessage(issue_id=issue.id, sender_id=user.id, message=body.message.strip()))
    db.commit()
    return issue_row(db, issue)

@router.post("/api/issues/{issue_id}/{action}")
def issue_action(issue_id: int, action: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    issue = owned_issue(db, issue_id, user)
    if action == "resolve" and issue.status not in ("resolved", "cancelled"):
        issue.status = "resolved"
    elif action == "reopen" and issue.status == "resolved":
        issue.status = "open"
    elif action == "cancel" and issue.status in ("open", "investigating"):
        issue.status = "cancelled"
    else:
        raise HTTPException(400, "That action is not available for this report")
    issue.updated_at = datetime.utcnow()
    db.commit()
    return issue_row(db, issue)

class RatingIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=1000)

@router.post("/api/issues/{issue_id}/rating")
def rate_issue(issue_id: int, body: RatingIn,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    issue = owned_issue(db, issue_id, user)
    if issue.status != "resolved": raise HTTPException(400, "Resolve the issue before rating the service")
    issue.rating, issue.rating_comment = body.rating, body.comment
    db.commit()
    return issue_row(db, issue)


class FarmProfileIn(BaseModel):
    farm_name: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=40)

@router.patch("/api/issues/profile")
def update_farm_profile(body: FarmProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    customer(user)
    user.farm_name = body.farm_name.strip()
    user.phone = body.phone.strip()
    db.commit()
    return {"name": user.name, "phone": user.phone or "", "farm_name": user.farm_name or "", "email": user.email}
