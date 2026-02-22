# 5 A.M. Assembly — Setup Guide

## Project Structure
```
5am-assembly/
├── backend/
│   ├── main.py           ← FastAPI server
│   ├── requirements.txt
│   ├── .env.example      ← copy to .env and fill keys
│   └── orders.db         ← auto-created on first run
└── frontend/
    └── index.html        ← the full website
```

---

## Step 1 — Get your Razorpay API Keys

1. Go to https://dashboard.razorpay.com/app/keys
2. Use **Test Mode** keys first (start with `rzp_test_`)
3. Switch to Live keys when ready to go live

---

## Step 2 — Configure .env

```bash
cd backend
cp ..env .env
```

Edit `.env`:
```
RAZORPAY_KEY_ID=rzp_test_YOUR_KEY_ID
RAZORPAY_KEY_SECRET=YOUR_KEY_SECRET
DB_PATH=orders.db
```

---

## Step 3 — Run the Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

You should see:
```
✅  Database ready → orders.db
🔑  Razorpay Key: rzp_test_ABC...
INFO: Uvicorn running on http://localhost:8000
```

---

## Step 4 — Open the Frontend

Just open `frontend/index.html` in your browser.
The frontend talks to `http://localhost:8000` by default.

> To change the API URL, edit the `API_BASE` constant at
> the top of the `<script>` block in `index.html`.

---

## How the Payment Flow Works

```
Browser                     FastAPI Server              Razorpay
   │                              │                         │
   │── POST /api/create-order ───►│── Orders API (auth) ───►│
   │                              │◄── order_id ────────────│
   │◄── { order_id, key_id } ─────│                         │
   │                              │                         │
   │── Razorpay Checkout opens ──────────────────────────►  │
   │   (UPI / Card / Netbanking)                            │
   │◄── payment_id + signature ─────────────────────────────│
   │                              │                         │
   │── POST /api/verify-payment ─►│                         │
   │                              │ HMAC SHA-256 verify     │
   │                              │ Update SQLite DB        │
   │◄── { status: verified } ─────│                         │
   │                              │                         │
   ✅ Success screen shown        ✅ Order saved as 'paid'
```

---

## View All Orders

While the server is running:
```
http://localhost:8000/api/orders
```

---

## Deploy to Production

1. Deploy FastAPI to Railway / Render / any VPS
2. Update `API_BASE` in `frontend/index.html` to your server URL
3. Replace test Razorpay keys with live keys in `.env`
4. Add CORS restriction in `main.py` — replace `allow_origins=["*"]`
   with your actual domain

---

## Test Payments (Test Mode)

Razorpay test UPI: `success@razorpay`
Test card: `4111 1111 1111 1111`, any future date, any CVV
