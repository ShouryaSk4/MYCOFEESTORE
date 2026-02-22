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
import httpx
import base64

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


@app.post("/api/create-order")
async def create_order(req: CreateOrderRequest):
    """
    Step 1 — Create a Razorpay order server-side.
    Returns order_id that the frontend passes to Razorpay Checkout.
    """
    if not KEY_ID or not KEY_SECRET:
        raise HTTPException(500, "Razorpay credentials missing in environment variables")
    if req.qty < 1 or req.qty > 20:
        raise HTTPException(400, "Quantity must be between 1 and 20")
    if req.amount_paise <= 0:
        raise HTTPException(400, "Invalid amount")

    # Create order via Razorpay Orders API (direct HTTP, no razorpay package)
    credentials = base64.b64encode(f"{KEY_ID}:{KEY_SECRET}".encode()).decode()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.razorpay.com/v1/orders",
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/json"
                },
                json={
                    "amount":   req.amount_paise,
                    "currency": "INR",
                    "receipt":  f"5am_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    "notes": {
                        "product":  req.product,
                        "qty":      str(req.qty),
                        "customer": req.customer_name,
                    }
                }
            )
        if response.status_code != 200:
            raise HTTPException(502, f"Razorpay error: {response.text}")
        rzp_order = response.json()
    except HTTPException:
        raise
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
