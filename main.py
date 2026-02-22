"""
5 A.M. Assembly — FastAPI Backend
Vercel-compatible | No razorpay package | Direct HTTP calls
"""

import sqlite3
import hashlib
import hmac
import os
import base64
from datetime import datetime
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# ── Config (set these in Vercel environment variables) ───────
KEY_ID     = os.getenv("RAZORPAY_KEY_ID", "")
KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
DB_PATH    = "/tmp/orders.db"   # /tmp is the only writable path on Vercel


# ── DB ───────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                razorpay_order_id   TEXT UNIQUE NOT NULL,
                razorpay_payment_id TEXT,
                razorpay_signature  TEXT,
                product             TEXT NOT NULL,
                qty                 INTEGER NOT NULL,
                amount_paise        INTEGER NOT NULL,
                customer_name       TEXT NOT NULL,
                customer_email      TEXT NOT NULL,
                customer_phone      TEXT NOT NULL,
                delivery_address    TEXT NOT NULL,
                status              TEXT DEFAULT 'created',
                created_at          TEXT DEFAULT (datetime('now')),
                verified_at         TEXT
            )
        """)
        conn.commit()


# ── Lifespan ─────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


# ── App ──────────────────────────────────────────────────────
app = FastAPI(title="5 A.M. Assembly API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)


# ── Models ───────────────────────────────────────────────────
class CreateOrderRequest(BaseModel):
    product: str
    qty: int
    amount_paise: int
    customer_name: str
    customer_email: str
    customer_phone: str
    delivery_address: str

class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


# ── Routes ───────────────────────────────────────────────────
@app.get("/")
def root():
    return HTMLResponse("<h1>5 A.M. Assembly API is running</h1>")


@app.get("/api/config")
def get_config():
    """Returns public Razorpay key to the frontend."""
    if not KEY_ID:
        raise HTTPException(500, "RAZORPAY_KEY_ID not set in environment variables")
    return {"key_id": KEY_ID}


@app.post("/api/create-order")
async def create_order(req: CreateOrderRequest):
    """
    Step 1 — Create Razorpay order server-side via direct HTTP.
    Returns order_id for the frontend Razorpay Checkout.
    """
    if not KEY_ID or not KEY_SECRET:
        raise HTTPException(500, "Razorpay credentials not set in environment variables")
    if req.qty < 1 or req.qty > 20:
        raise HTTPException(400, "Quantity must be between 1 and 20")
    if req.amount_paise <= 0:
        raise HTTPException(400, "Invalid amount")

    credentials = base64.b64encode(f"{KEY_ID}:{KEY_SECRET}".encode()).decode()

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.razorpay.com/v1/orders",
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/json",
                },
                json={
                    "amount":   req.amount_paise,
                    "currency": "INR",
                    "receipt":  f"5am_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    "notes": {
                        "product":  req.product,
                        "qty":      str(req.qty),
                        "customer": req.customer_name,
                    },
                },
            )
        if response.status_code != 200:
            raise HTTPException(502, f"Razorpay error: {response.text}")
        rzp_order = response.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Order creation failed: {str(e)}")

    rzp_order_id = rzp_order["id"]

    with get_db() as conn:
        conn.execute("""
            INSERT INTO orders
              (razorpay_order_id, product, qty, amount_paise,
               customer_name, customer_email, customer_phone, delivery_address)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            rzp_order_id, req.product, req.qty, req.amount_paise,
            req.customer_name, req.customer_email,
            req.customer_phone, req.delivery_address,
        ))
        conn.commit()

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
    Only marks order as paid if signature is valid.
    """
    payload      = f"{req.razorpay_order_id}|{req.razorpay_payment_id}"
    expected_sig = hmac.new(
        KEY_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, req.razorpay_signature):
        raise HTTPException(400, "Payment verification failed — invalid signature")

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

    return {"status": "verified", "payment_id": req.razorpay_payment_id}


@app.get("/api/orders")
def list_orders(limit: int = 50):
    """View all orders."""
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
