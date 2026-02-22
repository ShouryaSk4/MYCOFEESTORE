"""
5 A.M. Assembly — FastAPI Backend
==================================
Run with:
    pip install fastapi uvicorn razorpay python-dotenv
    uvicorn main:app --reload --port 8000

Set your keys in .env (copy from ..env)
"""

import sqlite3
import hashlib
import hmac
import os
from datetime import datetime
from contextlib import asynccontextmanager

import razorpay
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# ─── Config ────────────────────────────────────────────────
KEY_ID     = os.getenv("RAZORPAY_KEY_ID", "")
KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
DB_PATH    = os.getenv("DB_PATH", "orders.db")

rzp_client = razorpay.Client(auth=(KEY_ID, KEY_SECRET))

# ─── DB Setup ──────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                razorpay_order_id   TEXT UNIQUE NOT NULL,
                razorpay_payment_id TEXT,
                razorpay_signature  TEXT,
                product         TEXT NOT NULL,
                qty             INTEGER NOT NULL,
                amount_paise    INTEGER NOT NULL,
                customer_name   TEXT NOT NULL,
                customer_email  TEXT NOT NULL,
                customer_phone  TEXT NOT NULL,
                delivery_address TEXT NOT NULL,
                status          TEXT DEFAULT 'created',
                created_at      TEXT DEFAULT (datetime('now')),
                verified_at     TEXT
            )
        """)
        conn.commit()
    print("✅  Database ready →", DB_PATH)

# ─── Lifespan ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print(f"🔑  Razorpay Key: {KEY_ID[:12]}..." if KEY_ID else "⚠️  No Razorpay key set!")
    yield

app = FastAPI(title="5 A.M. Assembly API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # Tighten to your domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the frontend folder as static files
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ─── Pydantic Models ───────────────────────────────────────
class CreateOrderRequest(BaseModel):
    product: str
    qty: int
    amount_paise: int          # amount in paise (₹1 = 100 paise)
    customer_name: str
    customer_email: str
    customer_phone: str
    delivery_address: str

class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


# ─── Routes ────────────────────────────────────────────────

@app.get("/")
def root():
    # Serve frontend index if it exists
    index = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"status": "5 A.M. Assembly API running"}


@app.get("/api/config")
def get_config():
    """Return public Razorpay key to the frontend."""
    if not KEY_ID:
        raise HTTPException(500, "Razorpay key not configured")
    return {"key_id": KEY_ID}


@app.post("/api/create-order")
def create_order(req: CreateOrderRequest):
    """
    Step 1 — Create a Razorpay order server-side.
    Returns order_id that the frontend passes to Razorpay Checkout.
    """
    if not KEY_ID or not KEY_SECRET:
        raise HTTPException(500, "Razorpay credentials missing in .env")

    if req.qty < 1 or req.qty > 20:
        raise HTTPException(400, "Quantity must be between 1 and 20")

    if req.amount_paise <= 0:
        raise HTTPException(400, "Invalid amount")

    # Create order via Razorpay Orders API
    try:
        rzp_order = rzp_client.order.create({
            "amount":   req.amount_paise,
            "currency": "INR",
            "receipt":  f"5am_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "notes": {
                "product": req.product,
                "qty":     str(req.qty),
                "customer": req.customer_name,
            }
        })
    except Exception as e:
        raise HTTPException(502, f"Razorpay order creation failed: {str(e)}")

    rzp_order_id = rzp_order["id"]

    # Persist to SQLite with status = 'created'
    with get_db() as conn:
        conn.execute("""
            INSERT INTO orders
              (razorpay_order_id, product, qty, amount_paise,
               customer_name, customer_email, customer_phone, delivery_address)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            rzp_order_id,
            req.product,
            req.qty,
            req.amount_paise,
            req.customer_name,
            req.customer_email,
            req.customer_phone,
            req.delivery_address,
        ))
        conn.commit()

    print(f"📦  Order created: {rzp_order_id} | {req.product} ×{req.qty} | ₹{req.amount_paise//100}")

    return {
        "order_id":     rzp_order_id,
        "amount_paise": req.amount_paise,
        "currency":     "INR",
        "key_id":       KEY_ID,
    }


@app.post("/api/verify-payment")
def verify_payment(req: VerifyPaymentRequest):
    """
    Step 4 — Verify Razorpay signature using HMAC SHA256.
    Only marks order as 'paid' if signature is valid.
    """
    # Build the string Razorpay signs
    payload = f"{req.razorpay_order_id}|{req.razorpay_payment_id}"

    expected_sig = hmac.new(
        KEY_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, req.razorpay_signature):
        # Signature mismatch — possible tampering
        print(f"⚠️  Signature MISMATCH for order {req.razorpay_order_id}")
        raise HTTPException(400, "Payment verification failed — invalid signature")

    # Signature valid → update DB
    now = datetime.now().isoformat()
    with get_db() as conn:
        conn.execute("""
            UPDATE orders SET
                razorpay_payment_id = ?,
                razorpay_signature  = ?,
                status              = 'paid',
                verified_at         = ?
            WHERE razorpay_order_id = ?
        """, (req.razorpay_payment_id, req.razorpay_signature, now, req.razorpay_order_id))
        conn.commit()

    print(f"✅  Payment VERIFIED: {req.razorpay_payment_id} for order {req.razorpay_order_id}")

    return {"status": "verified", "payment_id": req.razorpay_payment_id}


@app.get("/api/orders")
def list_orders(limit: int = 50):
    """View all orders (protect this with auth in production!)"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/orders/{order_id}")
def get_order(order_id: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE razorpay_order_id = ?", (order_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Order not found")
    return dict(row)
