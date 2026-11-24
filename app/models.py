from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, Float, DateTime, ForeignKey, LargeBinary
from sqlalchemy.orm import relationship
from .db import Base
def pk(): return Column(Integer, primary_key=True)
def now(): return Column(DateTime, default=datetime.utcnow)
class User(Base):
    __tablename__="users"; id=pk()
    email=Column(String(200),unique=True,index=True); name=Column(String(200)); phone=Column(String(40))
    password_hash=Column(String(200))
    role=Column(String(30),default="customer")  # customer|provider|super|verification|operations|finance|support|catalogue|analytics
    status=Column(String(20),default="active")  # active|suspended|blocked
    country=Column(String(60),default="Kenya"); created_at=now()
class ProviderProfile(Base):
    __tablename__="providers"; id=pk()
    user_id=Column(Integer,ForeignKey("users.id")); business_name=Column(String(200)); address=Column(String(300))
    service_area=Column(String(300)); payout_info=Column(String(300),default=""); whatsapp=Column(String(20),default=""); service_ids=Column(String(500),default="")
    verification_status=Column(String(30),default="pending")  # pending|under_review|more_info|approved|rejected|expired|suspended|blocked
    verification_note=Column(Text,default=""); created_at=now()
class SellerProfile(Base):
    __tablename__="seller_profiles"; id=pk()
    user_id=Column(Integer,ForeignKey("users.id"),unique=True,index=True)
    payout_phone=Column(String(24),default="")
    created_at=now()
class VerificationDoc(Base):
    __tablename__="documents"; id=pk()
    provider_id=Column(Integer,ForeignKey("providers.id")); doc_type=Column(String(60)); filename=Column(String(300))
    stored_path=Column(String(400),default=""); content=Column(LargeBinary); expires_on=Column(String(20),default=""); created_at=now()
class Category(Base):  # self-referencing: industry -> category -> specialty (unlimited depth)
    __tablename__="categories"; id=pk()
    parent_id=Column(Integer,ForeignKey("categories.id"),nullable=True); name=Column(String(200)); active=Column(Boolean,default=True)
class Service(Base):
    __tablename__="services"; id=pk()
    category_id=Column(Integer,ForeignKey("categories.id")); name=Column(String(200)); description=Column(Text,default="")
    provider_type=Column(String(80),default="individual"); booking_method=Column(String(40),default="appointment")
    location_type=Column(String(40),default="physical"); price_type=Column(String(30),default="quotation")
    price=Column(Float,default=0); currency=Column(String(5),default="KES"); verification_required=Column(Boolean,default=True)
    required_documents=Column(String(300),default="National ID"); cancellation_rules=Column(String(300),default="")
    commission_pct=Column(Float,default=10); capacity=Column(Integer,default=1); countries=Column(String(300),default="KE")
    active=Column(Boolean,default=True)
class Booking(Base):
    __tablename__="bookings"; id=pk()
    customer_id=Column(Integer,ForeignKey("users.id")); provider_id=Column(Integer,ForeignKey("providers.id"),nullable=True)
    service_id=Column(Integer,ForeignKey("services.id")); scheduled_at=Column(String(40)); notes=Column(Text,default="")
    status=Column(String(20),default="new")  # new|assigned|confirmed|completed|cancelled
    synced=Column(Boolean,default=True); created_at=now()
class Payment(Base):
    __tablename__="payments"; id=pk()
    booking_id=Column(Integer,ForeignKey("bookings.id")); method=Column(String(20),default="mpesa"); amount=Column(Float)
    currency=Column(String(5),default="KES"); reference=Column(String(100)); status=Column(String(20),default="pending")  # pending|paid|failed|refunded
    created_at=now()
class Review(Base):
    __tablename__="reviews"; id=pk()
    booking_id=Column(Integer,ForeignKey("bookings.id")); author_id=Column(Integer,ForeignKey("users.id"))
    rating=Column(Integer); comment=Column(Text,default=""); hidden=Column(Boolean,default=False); created_at=now()
class Complaint(Base):
    __tablename__="complaints"; id=pk()
    raised_by=Column(Integer,ForeignKey("users.id")); against_user=Column(Integer,nullable=True); booking_id=Column(Integer,nullable=True)
    subject=Column(String(200)); details=Column(Text,default=""); status=Column(String(20),default="open")  # open|investigating|escalated|resolved
    resolution=Column(Text,default=""); created_at=now()
class AuditLog(Base):
    __tablename__="audit_log"; id=pk()
    admin_id=Column(Integer); action=Column(String(60)); resource=Column(String(60)); target_id=Column(Integer,nullable=True)
    detail=Column(Text,default=""); created_at=now()
class Setting(Base):
    __tablename__="settings"; id=pk()
    key=Column(String(100),unique=True); value=Column(Text,default="")  # countries, currencies, languages, taxes, templates...
class Escrow(Base):  # manual escrow: customer pays platform off-platform, finance admin confirms & pays out by hand
    __tablename__="escrow"; id=pk()
    booking_id=Column(Integer,ForeignKey("bookings.id"),unique=True); amount=Column(Float); currency=Column(String(5),default="KES")
    commission_pct=Column(Float,default=10); commission_amount=Column(Float,default=0); payout_amount=Column(Float,default=0)
    customer_ref=Column(String(100),default=""); payout_ref=Column(String(100),default=""); refund_ref=Column(String(100),default="")
    status=Column(String(30),default="awaiting_payment")
    # awaiting_payment|payment_submitted|funded|delivered|approved|disputed|released|refunded
    created_at=now(); updated_at=Column(DateTime,default=datetime.utcnow)
class EscrowEvent(Base):
    __tablename__="escrow_events"; id=pk()
    escrow_id=Column(Integer,ForeignKey("escrow.id")); actor_id=Column(Integer); event=Column(String(40)); note=Column(Text,default=""); created_at=now()

class Product(Base):
    __tablename__="products"; id=pk()
    seller_id=Column(Integer,ForeignKey("users.id"),index=True)
    title=Column(String(200),nullable=False); category=Column(String(80),default="Produce")
    description=Column(Text,default=""); price=Column(Integer,nullable=False); unit=Column(String(40),default="per item")
    stock=Column(Integer,default=0); county=Column(String(80),default="")
    image_path=Column(String(400),default=""); active=Column(Boolean,default=True); created_at=now()
    seller=relationship("User", foreign_keys=[seller_id])

class ProductOrder(Base):
    __tablename__="product_orders"; id=pk()
    customer_id=Column(Integer,ForeignKey("users.id"),index=True)
    status=Column(String(30),default="awaiting_payment")  # awaiting_payment|paid|payment_review|processing|shipped|delivered|cancelled|refund_required
    payment_method=Column(String(20),default="")
    payment_status=Column(String(30),default="pending")
    payment_reference=Column(String(100),default="")
    checkout_id=Column(String(150),default="",index=True)
    destination=Column(Text,nullable=False); county=Column(String(80),nullable=False)
    latitude=Column(Float,nullable=True); longitude=Column(Float,nullable=True)
    total=Column(Integer,nullable=False); created_at=now()

class ProductOrderItem(Base):
    __tablename__="product_order_items"; id=pk()
    order_id=Column(Integer,ForeignKey("product_orders.id"),index=True)
    product_id=Column(Integer,ForeignKey("products.id"),nullable=True)
    seller_id=Column(Integer,ForeignKey("users.id"),index=True)
    product_name=Column(String(200),nullable=False); seller_name=Column(String(200),default="")
    seller_payout_phone=Column(String(24),default="")
    unit=Column(String(40),default="per item"); unit_price=Column(Integer,nullable=False)
    quantity=Column(Integer,nullable=False); created_at=now()

class ActivityLog(Base):
    __tablename__="activity_log"; id=pk()
    actor_id=Column(Integer,ForeignKey("users.id"),nullable=True,index=True)
    actor_email=Column(String(200),default="")
    action=Column(String(60),nullable=False); resource=Column(String(60),nullable=False)
    target_id=Column(Integer,nullable=True); detail=Column(Text,default="")
    created_at=now()
