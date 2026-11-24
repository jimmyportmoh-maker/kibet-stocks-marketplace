import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request as APIRequest, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .db import get_db
from .models import ActivityLog, Product, ProductOrder, ProductOrderItem, SellerProfile, User
from .security import current_user, require

router = APIRouter()
PRODUCT_IMAGES = Path(os.getenv("UPLOAD_DIR") or Path(__file__).parent.parent / "private_uploads") / "products"
PRODUCT_IMAGES.mkdir(parents=True, exist_ok=True)
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _activity(db, actor, action, resource, target_id=None, detail=""):
    db.add(ActivityLog(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else "",
        action=action,
        resource=resource,
        target_id=target_id,
        detail=detail[:1000],
    ))


def _product_row(product):
    seller = product.seller
    return {
        "id": product.id,
        "seller_id": product.seller_id,
        "seller_name": seller.name if seller else "Seller",
        "title": product.title,
        "category": product.category,
        "description": product.description,
        "price": product.price,
        "unit": product.unit,
        "stock": product.stock,
        "county": product.county,
        "active": product.active,
        "image_url": f"/api/products/{product.id}/image" if product.image_path else "",
        "created_at": product.created_at.isoformat() if product.created_at else "",
    }


def _order_row(db, order, seller_id=None):
    items = db.query(ProductOrderItem).filter_by(order_id=order.id).all()
    customer = db.get(User, order.customer_id)
    return {
        "id": order.id,
        "status": order.status,
        "payment_method": order.payment_method,
        "payment_status": order.payment_status,
        "payment_reference": order.payment_reference,
        "destination": order.destination,
        "county": order.county,
        "latitude": order.latitude,
        "longitude": order.longitude,
        "total": order.total,
        "created_at": order.created_at.isoformat() if order.created_at else "",
        "customer_name": customer.name if customer else "",
        "customer_email": customer.email if customer else "",
        "items": [{
            "product_id": item.product_id,
            "name": item.product_name,
            "seller_name": item.seller_name,
            "unit": item.unit,
            "unit_price": item.unit_price,
            "quantity": item.quantity,
        } for item in items if seller_id is None or item.seller_id == seller_id],
    }


def _image_extension(upload: UploadFile):
    extension = ALLOWED_IMAGE_TYPES.get(upload.content_type or "")
    if not extension:
        raise HTTPException(400, "Upload a JPG, PNG, or WebP image")
    return extension


def _save_image(upload: UploadFile):
    extension = _image_extension(upload)
    content = upload.file.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(400, "Product image must be 5 MB or smaller")
    if not content:
        raise HTTPException(400, "The selected image is empty")
    valid_image = (
        extension == ".jpg" and content.startswith(b"\xff\xd8\xff")
        or extension == ".png" and content.startswith(b"\x89PNG\r\n\x1a\n")
        or extension == ".webp" and len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    )
    if not valid_image:
        raise HTTPException(400, "The uploaded file does not match its image type")
    name = f"{uuid.uuid4().hex}{extension}"
    (PRODUCT_IMAGES / name).write_bytes(content)
    return name


def _require_seller(user):
    if user.role not in ("seller", "provider"):
        raise HTTPException(403, "Create a seller account to manage product listings")


def _seller_profile(db, user):
    profile = db.query(SellerProfile).filter_by(user_id=user.id).first()
    if not profile or not profile.payout_phone:
        raise HTTPException(400, "Add your M-Pesa number for receiving seller payouts before listing products")
    return profile


def _payment_readiness():
    callback = os.getenv("MPESA_CALLBACK_URL", "").strip()
    return {
        "mpesa_ready": all((
            os.getenv("MPESA_CONSUMER_KEY"),
            os.getenv("MPESA_CONSUMER_SECRET"),
            os.getenv("MPESA_SHORTCODE"),
            os.getenv("MPESA_PASSKEY"),
            os.getenv("MPESA_CALLBACK_TOKEN"),
            callback.startswith("https://"),
        )),
        "card_ready": bool(os.getenv("STRIPE_SECRET_KEY") and os.getenv("STRIPE_WEBHOOK_SECRET")),
    }


@router.get("/api/payments/config")
def payment_config():
    return _payment_readiness()


def _normalize_ke_phone(value):
    phone = re.sub(r"\D", "", value)
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    elif value.strip().startswith("+"):
        phone = phone[1:]
    if not re.fullmatch(r"254[17]\d{8}", phone):
        raise HTTPException(400, "Enter a valid Kenyan M-Pesa number, such as 0712345678")
    return phone


class SellerPayoutIn(BaseModel):
    payout_phone: str = Field(min_length=10, max_length=24)


@router.get("/api/seller/payout")
def get_seller_payout(user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require_seller(user)
    profile = db.query(SellerProfile).filter_by(user_id=user.id).first()
    return {"payout_phone": profile.payout_phone if profile else ""}


@router.put("/api/seller/payout")
def set_seller_payout(
    body: SellerPayoutIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _require_seller(user)
    phone = _normalize_ke_phone(body.payout_phone)
    profile = db.query(SellerProfile).filter_by(user_id=user.id).first()
    if not profile:
        profile = SellerProfile(user_id=user.id)
        db.add(profile)
    profile.payout_phone = phone
    _activity(db, user, "seller_payout_number_updated", "seller_profile", profile.id, "Seller M-Pesa payout number updated")
    db.commit()
    return {"payout_phone": profile.payout_phone}


@router.get("/api/products")
def public_products(db: Session = Depends(get_db)):
    products = db.query(Product).filter_by(active=True).order_by(Product.id.desc()).limit(500).all()
    return [_product_row(product) for product in products]


@router.get("/api/products/{product_id}/image")
def product_image(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product or not product.active or not product.image_path:
        raise HTTPException(404, "Product image not found")
    path = (PRODUCT_IMAGES / product.image_path).resolve()
    if path.parent != PRODUCT_IMAGES.resolve() or not path.is_file():
        raise HTTPException(404, "Product image not found")
    return FileResponse(path)


@router.get("/api/seller/products")
def seller_products(user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require_seller(user)
    products = db.query(Product).filter_by(seller_id=user.id).order_by(Product.id.desc()).all()
    return [_product_row(product) for product in products]


@router.post("/api/seller/products")
def create_product(
    title: str = Form(...),
    category: str = Form(...),
    description: str = Form(""),
    price: int = Form(..., gt=0),
    unit: str = Form(...),
    stock: int = Form(..., ge=0),
    county: str = Form(...),
    image: UploadFile | None = File(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _require_seller(user)
    seller_profile = _seller_profile(db, user)
    title, category, unit, county = title.strip(), category.strip(), unit.strip(), county.strip()
    if not title or not category or not unit or not county:
        raise HTTPException(400, "Product name, category, unit, and county are required")
    image_path = _save_image(image) if image and image.filename else ""
    product = Product(
        seller_id=user.id, title=title, category=category, description=description.strip(),
        price=price, unit=unit, stock=stock, county=county, image_path=image_path,
    )
    db.add(product)
    db.flush()
    _activity(db, user, "product_created", "product", product.id, f"{title}; KES {price} {unit}; stock {stock}")
    db.commit()
    return _product_row(product)


class ProductUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = None
    price: int | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    stock: int | None = Field(default=None, ge=0)
    county: str | None = Field(default=None, min_length=1, max_length=80)
    active: bool | None = None


@router.patch("/api/seller/products/{product_id}")
def update_product(
    product_id: int,
    changes: ProductUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _require_seller(user)
    _seller_profile(db, user)
    product = db.get(Product, product_id)
    if not product or product.seller_id != user.id:
        raise HTTPException(404, "Product not found")
    data = changes.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "No product changes supplied")
    for key, value in data.items():
        setattr(product, key, value)
    _activity(db, user, "product_updated", "product", product.id, ", ".join(sorted(data)))
    db.commit()
    return _product_row(product)


@router.post("/api/seller/products/{product_id}/image")
def replace_product_image(
    product_id: int,
    image: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _require_seller(user)
    _seller_profile(db, user)
    product = db.get(Product, product_id)
    if not product or product.seller_id != user.id:
        raise HTTPException(404, "Product not found")
    image_path = _save_image(image)
    old_path = product.image_path
    product.image_path = image_path
    _activity(db, user, "product_image_updated", "product", product.id)
    db.commit()
    if old_path:
        (PRODUCT_IMAGES / old_path).unlink(missing_ok=True)
    return _product_row(product)


class OrderLine(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, le=500)


class CreateOrder(BaseModel):
    items: list[OrderLine] = Field(min_length=1, max_length=50)
    destination: str = Field(min_length=4, max_length=500)
    county: str = Field(min_length=2, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    payment_method: str


@router.post("/api/orders")
def create_order(
    body: CreateOrder,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user.role != "customer":
        raise HTTPException(403, "Sign in with a customer account to place an order")
    if body.payment_method not in ("mpesa", "card"):
        raise HTTPException(400, "Choose M-Pesa or card")
    if (body.latitude is None) != (body.longitude is None):
        raise HTTPException(400, "Share both latitude and longitude, or use the written delivery address only")
    order = ProductOrder(
        customer_id=user.id,
        payment_method=body.payment_method,
        destination=body.destination.strip(),
        county=body.county.strip(),
        latitude=body.latitude,
        longitude=body.longitude,
        total=0,
    )
    db.add(order)
    db.flush()
    total = 0
    quantities = {}
    for line in body.items:
        quantities[line.product_id] = quantities.get(line.product_id, 0) + line.quantity
    for product_id, quantity in quantities.items():
        product = db.get(Product, product_id)
        if not product or not product.active:
            db.rollback()
            raise HTTPException(404, "A product in your cart is no longer available")
        if quantity > product.stock:
            db.rollback()
            raise HTTPException(409, f"Only {product.stock} units of {product.title} remain")
        product_seller = db.get(User, product.seller_id)
        if not product_seller:
            db.rollback()
            raise HTTPException(409, f"Seller for {product.title} is unavailable")
        seller_profile = _seller_profile(db, product_seller)
        total += product.price * quantity
        db.add(ProductOrderItem(
            order_id=order.id,
            product_id=product.id,
            seller_id=product.seller_id,
            product_name=product.title,
            seller_name=product_seller.name,
            seller_payout_phone=seller_profile.payout_phone,
            unit=product.unit,
            unit_price=product.price,
            quantity=quantity,
        ))
    order.total = total
    db.flush()
    _activity(db, user, "order_created", "product_order", order.id, f"{len(body.items)} line items; total KES {total}; payment {body.payment_method}")
    db.commit()
    return _order_row(db, order)


@router.get("/api/orders/mine")
def my_orders(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role != "customer":
        raise HTTPException(403, "Customers only")
    orders = db.query(ProductOrder).filter_by(customer_id=user.id).order_by(ProductOrder.id.desc()).all()
    return [_order_row(db, order) for order in orders]


@router.get("/api/seller/orders")
def seller_orders(user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require_seller(user)
    order_ids = db.query(ProductOrderItem.order_id).filter_by(seller_id=user.id).distinct().all()
    ids = [row[0] for row in order_ids]
    orders = db.query(ProductOrder).filter(ProductOrder.id.in_(ids)).order_by(ProductOrder.id.desc()).limit(200).all() if ids else []
    result = []
    for order in orders:
        result.append(_order_row(db, order, user.id))
    return result


@router.get("/api/orders/{order_id}")
def get_order(order_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    order = db.get(ProductOrder, order_id)
    if not order or order.customer_id != user.id:
        raise HTTPException(404, "Order not found")
    return _order_row(db, order)


def _post_form(url, data, headers=None):
    request = Request(url, data=urlencode(data).encode(), headers=headers or {}, method="POST")
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode())
    except HTTPError as exc:
        raise HTTPException(502, f"Payment provider rejected the request (HTTP {exc.code})") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "Could not reach the payment provider; try again") from exc


def _mpesa_token():
    key, secret = os.getenv("MPESA_CONSUMER_KEY"), os.getenv("MPESA_CONSUMER_SECRET")
    if not key or not secret:
        raise HTTPException(503, "M-Pesa is not configured. Set MPESA_CONSUMER_KEY and MPESA_CONSUMER_SECRET.")
    base = "https://sandbox.safaricom.co.ke" if os.getenv("MPESA_ENV", "sandbox").lower() != "production" else "https://api.safaricom.co.ke"
    encoded = base64.b64encode(f"{key}:{secret}".encode()).decode()
    request = Request(f"{base}/oauth/v1/generate?grant_type=client_credentials", headers={"Authorization": f"Basic {encoded}"})
    try:
        with urlopen(request, timeout=20) as response:
            return base, json.loads(response.read().decode())["access_token"]
    except (HTTPError, URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "Unable to authenticate with M-Pesa; check Daraja configuration") from exc


def _fulfil_paid_order(db, order, reference):
    if order.payment_status == "paid":
        return
    items = db.query(ProductOrderItem).filter_by(order_id=order.id).all()
    quantities = {}
    for item in items:
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity
    decremented = {}
    for product_id, quantity in quantities.items():
        changed = db.query(Product).filter(
            Product.id == product_id,
            Product.active.is_(True),
            Product.stock >= quantity,
        ).update({Product.stock: Product.stock - quantity}, synchronize_session=False)
        if not changed:
            for restored_id, restored_quantity in decremented.items():
                db.query(Product).filter_by(id=restored_id).update(
                    {Product.stock: Product.stock + restored_quantity}, synchronize_session=False
                )
            order.payment_status = "paid"
            order.payment_reference = reference
            order.status = "refund_required"
            _activity(db, None, "payment_paid_stock_conflict", "product_order", order.id, "Payment succeeded but stock was unavailable; refund review required")
            return
        decremented[product_id] = quantity
    order.payment_status = "paid"
    order.payment_reference = reference[:100]
    order.status = "paid"
    _activity(db, None, "payment_confirmed", "product_order", order.id, f"Payment confirmed; reference {reference[:40]}")


@router.post("/api/orders/{order_id}/pay/mpesa")
def pay_mpesa(
    order_id: int,
    body: dict,
    request: APIRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    order = db.get(ProductOrder, order_id)
    if not order or order.customer_id != user.id:
        raise HTTPException(404, "Order not found")
    if order.payment_status not in ("pending", "failed"):
        raise HTTPException(409, "This order already has a payment attempt")
    callback = os.getenv("MPESA_CALLBACK_URL", "").strip()
    shortcode, passkey = os.getenv("MPESA_SHORTCODE"), os.getenv("MPESA_PASSKEY")
    if not callback.startswith("https://") or not shortcode or not passkey or not os.getenv("MPESA_CALLBACK_TOKEN"):
        raise HTTPException(503, "M-Pesa is not ready. Configure the shortcode, passkey, and public HTTPS callback URL.")
    phone = re.sub(r"\D", "", str(body.get("phone", "")))
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    if phone.startswith("+"):
        phone = phone[1:]
    if not re.fullmatch(r"254[17]\d{8}", phone):
        raise HTTPException(400, "Enter a valid Kenyan mobile number, such as 0712345678")
    base, token = _mpesa_token()
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(f"{shortcode}{passkey}{timestamp}".encode()).decode()
    payload = {
        "BusinessShortCode": shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": os.getenv("MPESA_TRANSACTION_TYPE", "CustomerPayBillOnline"),
        "Amount": int(order.total),
        "PartyA": phone,
        "PartyB": shortcode,
        "PhoneNumber": phone,
        "CallBackURL": callback,
        "AccountReference": f"KS-{order.id}",
        "TransactionDesc": f"Kibet Stocks order {order.id}",
    }
    data = json.dumps(payload).encode()
    req = Request(
        f"{base}/mpesa/stkpush/v1/processrequest",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=25) as response:
            result = json.loads(response.read().decode())
    except HTTPError as exc:
        raise HTTPException(502, f"M-Pesa STK Push failed (HTTP {exc.code})") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "Could not start the M-Pesa payment; check your connection and try again") from exc
    if result.get("ResponseCode") != "0":
        raise HTTPException(502, result.get("ResponseDescription") or "M-Pesa did not accept the payment request")
    order.checkout_id = result.get("CheckoutRequestID", "")
    _activity(db, user, "mpesa_stk_started", "product_order", order.id, f"M-Pesa STK request {order.checkout_id}")
    db.commit()
    return {"order_id": order.id, "checkout_request_id": order.checkout_id, "message": result.get("CustomerMessage", "Check your phone to approve the M-Pesa prompt")}


@router.post("/api/payments/mpesa/callback/{callback_token}")
async def mpesa_callback(callback_token: str, request: APIRequest, db: Session = Depends(get_db)):
    expected_token = os.getenv("MPESA_CALLBACK_TOKEN", "")
    if not expected_token or not hmac.compare_digest(callback_token, expected_token):
        raise HTTPException(404, "Callback not found")
    payload = await request.json()
    callback = payload.get("Body", {}).get("stkCallback", {})
    checkout_id = callback.get("CheckoutRequestID")
    order = db.query(ProductOrder).filter_by(checkout_id=checkout_id).first()
    if not order:
        return {"ResultCode": 0, "ResultDesc": "Callback received"}
    if callback.get("ResultCode") == 0:
        values = {item.get("Name"): item.get("Value") for item in callback.get("CallbackMetadata", {}).get("Item", [])}
        try:
            paid_amount = int(values.get("Amount", 0))
        except (TypeError, ValueError):
            paid_amount = 0
        if paid_amount != order.total:
            order.payment_status = "paid"
            order.payment_reference = str(values.get("MpesaReceiptNumber", ""))[:100]
            order.status = "payment_review"
            _activity(db, None, "mpesa_amount_mismatch", "product_order", order.id, f"Expected KES {order.total}; callback amount KES {paid_amount}")
        else:
            _fulfil_paid_order(db, order, str(values.get("MpesaReceiptNumber", "")))
    else:
        order.payment_status = "failed"
        order.status = "awaiting_payment"
        _activity(db, None, "mpesa_payment_failed", "product_order", order.id, str(callback.get("ResultDesc", ""))[:200])
    db.commit()
    return {"ResultCode": 0, "ResultDesc": "Callback received"}


@router.post("/api/orders/{order_id}/pay/card")
def pay_card(
    order_id: int,
    request: APIRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    order = db.get(ProductOrder, order_id)
    if not order or order.customer_id != user.id:
        raise HTTPException(404, "Order not found")
    if order.payment_status not in ("pending", "failed"):
        raise HTTPException(409, "This order already has a payment attempt")
    secret = os.getenv("STRIPE_SECRET_KEY")
    if not secret or not os.getenv("STRIPE_WEBHOOK_SECRET"):
        raise HTTPException(503, "Card payments are not configured. Set STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET.")
    items = db.query(ProductOrderItem).filter_by(order_id=order.id).all()
    form = {
        "mode": "payment",
        "success_url": str(request.base_url).rstrip("/") + "/?payment=success&session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": str(request.base_url).rstrip("/") + "/?payment=cancelled",
        "client_reference_id": str(order.id),
        "metadata[order_id]": str(order.id),
        "currency": "kes",
    }
    for index, item in enumerate(items):
        form.update({
            f"line_items[{index}][price_data][currency]": "kes",
            f"line_items[{index}][price_data][unit_amount]": str(item.unit_price * 100),
            f"line_items[{index}][price_data][product_data][name]": item.product_name,
            f"line_items[{index}][quantity]": str(item.quantity),
        })
    data = _post_form("https://api.stripe.com/v1/checkout/sessions", form, {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    order.checkout_id = data["id"]
    _activity(db, user, "card_checkout_started", "product_order", order.id, f"Stripe Checkout {order.checkout_id}")
    db.commit()
    return {"order_id": order.id, "checkout_url": data["url"]}


@router.post("/api/payments/stripe/webhook")
async def stripe_webhook(request: APIRequest, db: Session = Depends(get_db)):
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(503, "Stripe webhook is not configured")
    raw = await request.body()
    signature = request.headers.get("stripe-signature", "")
    parts = dict(part.split("=", 1) for part in signature.split(",") if "=" in part)
    timestamp, provided = parts.get("t", ""), parts.get("v1", "")
    try:
        timestamp_value = int(timestamp)
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid Stripe signature timestamp")
    if not provided or abs(time.time() - timestamp_value) > 300:
        raise HTTPException(400, "Invalid or expired Stripe signature")
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(400, "Invalid Stripe signature")
    try:
        event = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "Invalid Stripe webhook body") from exc
    event_data = event.get("data", {}).get("object", {})
    if event.get("type") == "checkout.session.completed" and event_data.get("payment_status") == "paid":
        order_id = event_data.get("metadata", {}).get("order_id")
        order = db.get(ProductOrder, int(order_id)) if order_id and str(order_id).isdigit() else None
        if order and order.checkout_id == event_data.get("id"):
            reference = event_data.get("payment_intent", event_data.get("id", ""))
            if event_data.get("currency", "").lower() == "kes" and event_data.get("amount_total") == order.total * 100:
                _fulfil_paid_order(db, order, reference)
            else:
                order.payment_status = "paid"
                order.payment_reference = str(reference)[:100]
                order.status = "payment_review"
                _activity(db, None, "card_amount_mismatch", "product_order", order.id, "Stripe confirmed payment with unexpected currency or total; manual review required")
            db.commit()
    elif event.get("type") == "checkout.session.expired":
        order_id = event_data.get("metadata", {}).get("order_id")
        order = db.get(ProductOrder, int(order_id)) if order_id and str(order_id).isdigit() else None
        if order and order.payment_status == "pending":
            order.status = "cancelled"
            order.payment_status = "failed"
            _activity(db, None, "card_checkout_expired", "product_order", order.id)
            db.commit()
    return {"received": True}


@router.get("/api/admin/sellers/payouts")
def admin_seller_payouts(
    user: User = Depends(require("finance", "operations", "analytics")),
    db: Session = Depends(get_db),
):
    profiles = db.query(SellerProfile).order_by(SellerProfile.id.desc()).limit(500).all()
    result = []
    for profile in profiles:
        seller = db.get(User, profile.user_id)
        result.append({
            "id": profile.id,
            "user_id": profile.user_id,
            "seller_name": seller.name if seller else "",
            "seller_email": seller.email if seller else "",
            "payout_phone": profile.payout_phone,
            "created_at": profile.created_at.isoformat() if profile.created_at else "",
        })
    return result

