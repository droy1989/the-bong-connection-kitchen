# Food Stall Queue App — Real Razorpay UPI Payment

This version replaces the demo payment with Razorpay Standard Checkout.

Flow:
QR -> menu -> cart -> server creates Razorpay order -> Razorpay Checkout/UPI -> server verifies signature + captured payment -> PAID -> queue token -> kitchen.

## 1. Create Razorpay keys

Use Razorpay Test Mode first. Generate API keys from the Razorpay Dashboard and keep the secret only on the server.

Set:
- RAZORPAY_KEY_ID
- RAZORPAY_KEY_SECRET
- RAZORPAY_WEBHOOK_SECRET
- ADMIN_KEY

Copy `.env.example` to `.env`.

## 2. Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

Open:
- Customer: http://localhost:8000
- Kitchen: http://localhost:8000/admin
- Queue display: http://localhost:8000/display

For Razorpay Checkout, the browser needs access to the internet because checkout.js is loaded from Razorpay.

## 3. Test payment

In Razorpay Test Mode, use the documented test UPI ID:
success@razorpay

Do not use this test flow for real customer payments.

## 4. Webhook

For production, expose:

POST https://YOUR-DOMAIN/api/webhooks/razorpay

Configure the webhook in Razorpay Dashboard and subscribe to:
- payment.captured
- order.paid

Use the SAME webhook secret in Razorpay and `.env`.

The webhook validates the raw request body using HMAC-SHA256 and handles duplicate event IDs.

## 5. Important production rules

- Never put RAZORPAY_KEY_SECRET in frontend JavaScript.
- Never trust a browser-only "payment successful" flag.
- Verify the Checkout signature on the server.
- Fetch the payment and require `captured`.
- Verify the amount matches the order.
- Use webhooks as the server-side payment notification mechanism.
- Use HTTPS in production.
- Change ADMIN_KEY before deployment.

## 6. QR

After deployment, print one QR code pointing to:

https://YOUR-DOMAIN/

All customers use the same QR. Each successful paid order gets the next queue token automatically.

## 7. Current sample menu

Edit the seed products in `app.py` or add a product-management screen later.

The sample items are:
Burger 120
Cheese Burger 150
French Fries 80
Sandwich 100
Coke 40
Fresh Lime 50


## The Bong Connection menu

The app is preloaded with the festive 16–20 October 2026 Durga Puja menu and prices:

**All-Day Staples (16–20 Oct):**
- Egg Roll — ₹79 — demand 30 — sourcing IN
- Egg-Chicken Roll — ₹120 — demand 40 — sourcing IN
- Egg-Mutton Roll — ₹175 — demand 30 — sourcing IN
- Chilli Chicken / Manchurian — ₹180 — demand 50 — sourcing IN
- Egg Chowmein — ₹130 — demand 50 — sourcing IN (renamed from Egg Noodles)
- Egg Chicken Chowmein — ₹150 — demand 50 — sourcing IN
- Mutton Biriyani — ₹450 — demand 50 — sourcing OUT
- Chicken Biriyani — ₹300 — demand 50 — sourcing OUT
- Singara — ₹80 — demand 50 — sourcing OUT

**Festival Specials & Featured Offerings:**
- Egg Devil — ₹120 — demand 50 — sourcing OUT (17 Oct Saptami, 19 Oct Ashtami)
- Chicken Cutlet — ₹150 — demand 50 — sourcing OUT (17 Oct Saptami, 20 Oct Navami)
- Gandhoraj Fish Fry — ₹150 (single serving) — demand 50 — sourcing OUT (16 Oct Sasti, 19 Oct Ashtami, 20 Oct Navami)
- Bhetki Fish Fry — ₹150 (single serving) — demand 50 — sourcing OUT (18 Oct Saptami)
- Luchi + Mutton Curry — ₹280 (Combo: 4 pcs Luchi, 2 pcs Mutton) — demand 50 — sourcing OUT (18, 20 Oct)
- Mutton Curry (3 Pcs Mutton) — ₹300 — demand 50 — sourcing OUT (18, 20 Oct)
- Basanti Polao + Mutton Curry — ₹450 (Combo: Basanti Polao with 2 pcs Mutton) — demand 50 — sourcing OUT (18, 20 Oct)
- Veg Chop — ₹100 (2 pcs) — demand 50 — sourcing OUT (19 Oct)
- Egg Fried Rice — ₹150 — demand 50 — sourcing IN (19 Oct)
- Egg Chicken Fried Rice — ₹175 — demand 50 — sourcing IN (19 Oct)
- Mixed Fried Rice — ₹250 — demand 50 — sourcing IN (19 Oct)
- Baked Rasgulla Cups — ₹100 (1 pc) — demand 50 — sourcing OUT (19 Oct)
- Chilled Mishti Doi — ₹80 — demand 50 — sourcing OUT (19, 20 Oct)

Note: the supplied "REVENUE" values were used as item prices because DEMAND × REVENUE equals the supplied total for every row.


## Updated kitchen workflow

Kitchen uses `/admin`: PAID -> START -> PREPARING -> READY -> DELIVERED. The `/display` page auto-refreshes every 3 seconds, announces READY tokens as NOW SERVING, and lists WAITING/PREPARING tokens as GETTING READY. Customers can monitor `/order.html?id=...`.


### Added Puja Day — 21 Oct (Dasami)
- Luchi + Mutton Ghugni — ₹200
- Baked Rasgulla Cups — ₹100
- Makha Sandesh — ₹80
- Mihidana Rabri — ₹100 (1 piece)
