import os, json, base64, secrets, time
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv()

from datetime import datetime
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from db import get_db, IS_POSTGRES

ADMIN_KEY = os.getenv("ADMIN_KEY", "change-me")
UPI_ID = os.getenv("UPI_ID", "titlibasu37@okaxis")
UPI_NAME = os.getenv("UPI_NAME", "The Bong Connection")

# A customer browser session stores the supplied name and phone number. It is not
# identity verification; it simply keeps simultaneous customer orders separate.
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60

# Optional PhonePe Business PG credentials
PHONEPE_MERCHANT_ID = os.getenv("PHONEPE_MERCHANT_ID", "")
PHONEPE_SALT_KEY = os.getenv("PHONEPE_SALT_KEY", "")
PHONEPE_SALT_INDEX = os.getenv("PHONEPE_SALT_INDEX", "1")
PHONEPE_ENV = os.getenv("PHONEPE_ENV", "UAT")

app = FastAPI(title="Food Stall Queue Ordering")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "bong-connection"}

def db():
    return get_db()

# id column syntax differs: SQLite uses AUTOINCREMENT, Postgres uses SERIAL.
_PK = "SERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"

def init_db():
    c=db()
    c.executescript(f"""
    CREATE TABLE IF NOT EXISTS products(
      id {_PK},
      name TEXT NOT NULL, description TEXT DEFAULT '',
      price INTEGER NOT NULL, demand INTEGER DEFAULT 0,
      sourcing TEXT DEFAULT 'OUT', available INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS menu_dates(
      product_id INTEGER NOT NULL,
      menu_date TEXT NOT NULL,
      PRIMARY KEY(product_id, menu_date)
    );
    CREATE TABLE IF NOT EXISTS orders(
      id {_PK},
      token INTEGER UNIQUE NOT NULL,
      total INTEGER NOT NULL,
      customer_name TEXT DEFAULT '',
      customer_phone TEXT DEFAULT '',
      payment_status TEXT NOT NULL DEFAULT 'PENDING',
      order_status TEXT NOT NULL DEFAULT 'WAITING',
      utr_number TEXT DEFAULT '',
      razorpay_order_id TEXT DEFAULT '',
      razorpay_payment_id TEXT DEFAULT '',
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS order_items(
      id {_PK},
      order_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
      name TEXT NOT NULL, price INTEGER NOT NULL, quantity INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS webhook_events(
      event_id TEXT PRIMARY KEY, received_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS customer_sessions(
      session_id TEXT PRIMARY KEY,
      customer_name TEXT NOT NULL,
      customer_phone TEXT NOT NULL,
      expires_at INTEGER NOT NULL,
      created_at TEXT NOT NULL
    );
    """)
    c.commit()

    # Add columns if an existing (older) db didn't have them yet.
    if IS_POSTGRES:
        for stmt in [
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS utr_number TEXT DEFAULT ''",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_name TEXT DEFAULT ''",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_phone TEXT DEFAULT ''",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_session_id TEXT DEFAULT ''",
        ]:
            c.execute(stmt)
        c.commit()
    else:
        for stmt in [
            "ALTER TABLE orders ADD COLUMN utr_number TEXT DEFAULT ''",
            "ALTER TABLE orders ADD COLUMN customer_name TEXT DEFAULT ''",
            "ALTER TABLE orders ADD COLUMN customer_phone TEXT DEFAULT ''",
            "ALTER TABLE orders ADD COLUMN customer_session_id TEXT DEFAULT ''",
        ]:
            try:
                c.execute(stmt)
            except Exception:
                pass
        c.commit()

    # Migration: Rename or merge Egg Noodles -> Egg Chowmein
    noodle_row = c.execute("SELECT id FROM products WHERE name='Egg Noodles'").fetchone()
    chowmein_row = c.execute("SELECT id FROM products WHERE name='Egg Chowmein'").fetchone()
    if noodle_row:
        if chowmein_row:
            existing_dates = [r["menu_date"] for r in c.execute("SELECT menu_date FROM menu_dates WHERE product_id=?", (chowmein_row["id"],)).fetchall()]
            for ed in existing_dates:
                c.execute("DELETE FROM menu_dates WHERE product_id=? AND menu_date=?", (noodle_row["id"], ed))
            c.execute("UPDATE menu_dates SET product_id=? WHERE product_id=?", (chowmein_row["id"], noodle_row["id"]))
            try:
                c.execute("UPDATE order_items SET product_id=? WHERE product_id=?", (chowmein_row["id"], noodle_row["id"]))
            except Exception:
                pass
            c.execute("DELETE FROM products WHERE id=?", (noodle_row["id"],))
        else:
            c.execute("UPDATE products SET name='Egg Chowmein', price=130, description='Bengali-style egg chowmein' WHERE id=?", (noodle_row["id"],))

    # Aliases
    aliases = {
        "Egg-Mutton Roll": ["Egg Mutton Roll"],
        "Gandhoraj Fish Fry": ["Gandhoraj Fish Fry (Single Serving)"],
        "Chilled Mishti Doi": ["Chilled Misto Doi", "Misto Doi", "Mishti Doi"],
        "Luchi + Mutton Curry": ["Luchi + Mutton Curry (4 pcs Luchi, 2 pcs mutton)", "Luchi + Mutton Curry Combo", "Luchi + Mutton Curry (4 Pcs Luchi, 2 Pcs Mutton)", "Combo: Luchi + Mutton Curry"],
        "Basanti Polao + Mutton Curry": ["Basanti Polao + Mutton Curry Combo", "Basanti Polao + Mutton Curry(2 peice mutton)", "Basanti Polao + Mutton Curry (2 Pcs Mutton)", "Combo: Basanti Polao + Mutton Curry"],
        "Mutton Curry (3 Pcs Mutton)": ["Mutton Curry", "Mutton Curry (3 Pcs)", "Mutton Curry( 3 peice Mutton)", "Mutton Curry (3 pcs Mutton)"],
        "Veg Chop": ["Veg Chop (2 pcs)", "Veg Chop (2 peice)", "Veg Chop (2 Pcs)"],
        "Baked Rasgulla Cups": ["Baked Rasgulla Cup", "Baked Rasgulla Cups (1 pc)", "Baked Rasgulla Cups (1 peice)"],
    }

    for canonical_name, alias_list in aliases.items():
        can_row = c.execute("SELECT id FROM products WHERE name=?", (canonical_name,)).fetchone()
        for alias in alias_list:
            alias_row = c.execute("SELECT id FROM products WHERE name=?", (alias,)).fetchone()
            if alias_row:
                if can_row:
                    if alias_row["id"] != can_row["id"]:
                        existing_dates = [r["menu_date"] for r in c.execute("SELECT menu_date FROM menu_dates WHERE product_id=?", (can_row["id"],)).fetchall()]
                        for ed in existing_dates:
                            c.execute("DELETE FROM menu_dates WHERE product_id=? AND menu_date=?", (alias_row["id"], ed))
                        c.execute("UPDATE menu_dates SET product_id=? WHERE product_id=?", (can_row["id"], alias_row["id"]))
                        try:
                            c.execute("UPDATE order_items SET product_id=? WHERE product_id=?", (can_row["id"], alias_row["id"]))
                        except Exception:
                            pass
                        c.execute("DELETE FROM products WHERE id=?", (alias_row["id"],))
                else:
                    c.execute("UPDATE products SET name=? WHERE id=?", (canonical_name, alias_row["id"]))
                    can_row = alias_row

    products = [
        ("Egg Roll", "Bengali-style egg roll", 79, 30, "IN"),
        ("Egg-Chicken Roll", "Egg and chicken roll", 120, 40, "IN"),
        ("Egg-Mutton Roll", "Egg and mutton roll", 175, 30, "IN"),
        ("Chilli Chicken / Manchurian", "Chilli chicken / Manchurian", 180, 50, "IN"),
        ("Egg Chowmein", "Bengali-style egg chowmein", 130, 50, "IN"),
        ("Egg Chicken Chowmein", "Bengali-style egg chicken chowmein", 150, 50, "IN"),
        ("Mutton Biriyani", "Mutton biriyani", 450, 50, "OUT"),
        ("Chicken Biriyani", "Chicken biriyani", 300, 50, "OUT"),
        ("Singara", "Crispy Bengali singara", 80, 50, "OUT"),
        ("Egg Devil", "Bengali egg devil", 120, 50, "OUT"),
        ("Chicken Cutlet", "Crispy Bengali chicken cutlet", 150, 50, "OUT"),
        ("Gandhoraj Fish Fry", "Crispy Gandhoraj fish fry (single serving)", 150, 50, "OUT"),
        ("Bhetki Fish Fry", "Crispy Bengali Bhetki fish fry (single serving)", 150, 50, "OUT"),
        ("Luchi + Mutton Curry", "Combo: 4 pcs Luchi, 2 pcs Mutton", 280, 50, "OUT"),
        ("Mutton Curry (3 Pcs Mutton)", "Rich Bengali mutton curry (3 pcs Mutton)", 300, 50, "OUT"),
        ("Basanti Polao + Mutton Curry", "Combo: Basanti Polao with 2 pcs Mutton", 450, 50, "OUT"),
        ("Veg Chop", "Crispy Kolkata-style vegetable chop (2 pcs)", 100, 50, "OUT"),
        ("Egg Fried Rice", "Kolkata-style egg fried rice", 150, 50, "IN"),
        ("Egg Chicken Fried Rice", "Kolkata-style egg chicken fried rice", 175, 50, "IN"),
        ("Mixed Fried Rice", "Special mixed fried rice with egg, chicken and prawns", 250, 50, "IN"),
        ("Baked Rasgulla Cups", "Decadent baked rasgulla in a cup (1 pc)", 100, 50, "OUT"),
        ("Chilled Mishti Doi", "Classic Bengali sweet curd, served chilled", 80, 50, "OUT"),
    ]
    dates = {
        "Egg Roll": ["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20"],
        "Egg-Chicken Roll": ["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20"],
        "Egg-Mutton Roll": ["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20"],
        "Chilli Chicken / Manchurian": ["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20"],
        "Egg Chowmein": ["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20"],
        "Egg Chicken Chowmein": ["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20"],
        "Mutton Biriyani": ["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20"],
        "Chicken Biriyani": ["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20"],
        "Singara": ["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20"],
        "Egg Devil": ["2026-10-17", "2026-10-19"],
        "Chicken Cutlet": ["2026-10-17", "2026-10-19", "2026-10-20"],
        "Gandhoraj Fish Fry": ["2026-10-16", "2026-10-19", "2026-10-20"],
        "Bhetki Fish Fry": ["2026-10-18"],
        "Luchi + Mutton Curry": ["2026-10-18", "2026-10-19", "2026-10-20"],
        "Mutton Curry (3 Pcs Mutton)": ["2026-10-18", "2026-10-19", "2026-10-20"],
        "Basanti Polao + Mutton Curry": ["2026-10-18", "2026-10-19", "2026-10-20"],
        "Veg Chop": ["2026-10-19"],
        "Egg Fried Rice": ["2026-10-19"],
        "Egg Chicken Fried Rice": ["2026-10-19"],
        "Mixed Fried Rice": ["2026-10-19"],
        "Baked Rasgulla Cups": ["2026-10-19"],
        "Chilled Mishti Doi": ["2026-10-19", "2026-10-20"],
    }

    for name, desc, price, demand, sourcing in products:
        row = c.execute("SELECT id FROM products WHERE name=?", (name,)).fetchone()
        if row:
            c.execute(
                "UPDATE products SET price=?, description=?, demand=?, sourcing=?, available=1 WHERE id=?",
                (price, desc, demand, sourcing, row["id"])
            )
            pid = row["id"]
        else:
            c.execute(
                "INSERT INTO products(name, description, price, demand, sourcing, available) VALUES(?,?,?,?,?,1)",
                (name, desc, price, demand, sourcing)
            )
            pid = c.execute("SELECT id FROM products WHERE name=?", (name,)).fetchone()["id"]

        for d in dates.get(name, []):
            exists = c.execute(
                "SELECT 1 FROM menu_dates WHERE product_id=? AND menu_date=?",
                (pid, d)
            ).fetchone()
            if not exists:
                c.execute(
                    "INSERT INTO menu_dates(product_id, menu_date) VALUES(?,?)",
                    (pid, d)
                )
    c.commit(); c.close()

init_db()

class Item(BaseModel):
    product_id: int
    quantity: int

class OrderIn(BaseModel):
    items: list[Item]
    menu_date: str
    customer_name: str | None = ""
    customer_phone: str | None = ""

class ConfirmIn(BaseModel):
    utr_number: str | None = ""

class CustomerSessionIn(BaseModel):
    name: str
    phone: str

def normalized_phone(phone: str) -> str:
    phone = phone.strip()
    if len(phone) != 10 or not phone.isdigit():
        raise HTTPException(400, "Enter a valid 10-digit Indian mobile number")
    return phone

def current_customer(request: Request) -> dict:
    session_id = request.cookies.get("customer_session")
    if not session_id:
        raise HTTPException(401, "Please enter your name and mobile number before placing an order")
    c = db()
    customer = c.execute("SELECT * FROM customer_sessions WHERE session_id=?", (session_id,)).fetchone()
    if not customer or customer["expires_at"] < int(time.time()):
        if customer:
            c.execute("DELETE FROM customer_sessions WHERE session_id=?", (session_id,)); c.commit()
        c.close()
        raise HTTPException(401, "Please enter your name and mobile number before placing an order")
    c.close()
    return dict(customer)

@app.post("/api/customer-session")
def start_customer_session(body: CustomerSessionIn):
    name = body.name.strip()
    phone = normalized_phone(body.phone)
    if not name or len(name) > 100:
        raise HTTPException(400, "Enter a valid name")
    session_id = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    c = db()
    c.execute("INSERT INTO customer_sessions(session_id,customer_name,customer_phone,expires_at,created_at) VALUES(?,?,?,?,?)",
              (session_id, name, phone, expires_at, datetime.now().isoformat(timespec="seconds")))
    c.commit(); c.close()
    response = JSONResponse(
        {"ok": True, "name": name, "phone": phone}
    )
    response.set_cookie("customer_session", session_id, max_age=SESSION_TTL_SECONDS,
                        httponly=True, secure=AUTH_COOKIE_SECURE, samesite="lax", path="/")
    return response

@app.get("/api/customer-session")
def customer_session(request: Request):
    customer = current_customer(request)
    return {"ok": True, "name": customer["customer_name"], "phone": customer["customer_phone"]}

def require_admin(key):
    if key != ADMIN_KEY:
        raise HTTPException(401, "Invalid admin key")

@app.get("/")
def home():
    return FileResponse("static/menu.html")

@app.get("/checkout")
def checkout_page():
    return FileResponse("static/checkout.html")

@app.get("/admin")
def admin():
    return FileResponse("static/admin.html")

@app.get("/display")
def display():
    return FileResponse("static/display.html")

@app.get("/api/config")
def config():
    return {"upi_id": UPI_ID, "upi_name": UPI_NAME}

@app.get("/api/products")
def products(menu_date: str | None = None):
    c=db()
    if menu_date:
        rows=c.execute(
            """SELECT p.* FROM products p
               JOIN menu_dates m ON m.product_id=p.id
               WHERE m.menu_date=? AND p.available=1
               ORDER BY p.id""",(menu_date,)
        ).fetchall()
    else:
        rows=c.execute("SELECT * FROM products WHERE available=1 ORDER BY id").fetchall()
    c.close()
    return [dict(r) for r in rows]

@app.post("/api/orders")
def create_order(order: OrderIn, request: Request):
    customer = current_customer(request)
    if not order.items:
        raise HTTPException(400, "Cart is empty")

    c=db()
    ids=[x.product_id for x in order.items]
    marks=",".join("?" for _ in ids)
    rows=c.execute(
        f"""SELECT p.* FROM products p
            JOIN menu_dates m ON m.product_id=p.id
            WHERE p.id IN ({marks}) AND p.available=1 AND m.menu_date=?""",
        ids+[order.menu_date]
    ).fetchall()
    by_id={r["id"]:r for r in rows}

    total=0
    clean=[]
    for x in order.items:
        if x.quantity < 1 or x.product_id not in by_id:
            raise HTTPException(400, "Invalid or unavailable item")
        r=by_id[x.product_id]
        total += r["price"] * x.quantity
        clean.append((r["id"], r["name"], r["price"], x.quantity))

    token=c.execute("SELECT COALESCE(MAX(token),0)+1 AS next_token FROM orders").fetchone()["next_token"]
    now=datetime.now().isoformat(timespec="seconds")
    c_name = customer["customer_name"]
    c_phone = customer["customer_phone"]
    insert_sql = """INSERT INTO orders(token,total,customer_name,customer_phone,customer_session_id,payment_status,order_status,created_at)
           VALUES(?,?,?,?,?,?,?,?)"""
    insert_params = (token,total,c_name,c_phone,customer["session_id"],"PENDING","WAITING",now)
    if c.is_postgres:
        cur = c.execute(insert_sql + " RETURNING id", insert_params)
        oid = cur.fetchone()["id"]
    else:
        cur = c.execute(insert_sql, insert_params)
        oid = cur.lastrowid

    c.executemany(
        "INSERT INTO order_items(order_id,product_id,name,price,quantity) VALUES(?,?,?,?,?)",
        [(oid,*x) for x in clean]
    )

    c.commit(); c.close()

    note = f"Food Order Token #{token}"
    ref = f"FOOD-{oid}"

    upi_link = f"upi://pay?pa={UPI_ID}&pn={quote(UPI_NAME)}&am={total:.2f}&cu=INR&tn={quote(note)}&tr={ref}"
    gpay_link = f"tez://upi/pay?pa={UPI_ID}&pn={quote(UPI_NAME)}&am={total:.2f}&cu=INR&tn={quote(note)}&tr={ref}"
    phonepe_link = f"phonepe://pay?pa={UPI_ID}&pn={quote(UPI_NAME)}&am={total:.2f}&cu=INR&tn={quote(note)}&tr={ref}"
    paytm_link = f"paytmmp://pay?pa={UPI_ID}&pn={quote(UPI_NAME)}&am={total:.2f}&cu=INR&tn={quote(note)}&tr={ref}"

    return {
        "order_id": oid,
        "token": token,
        "total": total,
        "upi_id": UPI_ID,
        "upi_name": UPI_NAME,
        "upi_link": upi_link,
        "gpay_link": gpay_link,
        "phonepe_link": phonepe_link,
        "paytm_link": paytm_link
    }

@app.post("/api/orders/{oid}/confirm")
def confirm_payment(oid: int, body: ConfirmIn | None = None):
    c=db()
    order=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
    if not order:
        c.close()
        raise HTTPException(404, "Order not found")

    utr = body.utr_number.strip() if (body and body.utr_number) else ""
    # Set payment status to PAID, and record utr_number
    c.execute(
        "UPDATE orders SET payment_status='PAID', utr_number=? WHERE id=?",
        (utr, oid)
    )
    c.commit(); c.close()
    return {"ok": True, "order_id": oid, "token": order["token"], "status":"PAID", "utr_number": utr}

@app.post("/api/orders/{oid}/approve-payment")
def approve_payment(oid: int, x_admin_key: str | None = Header(default=None)):
    require_admin(x_admin_key)
    c=db()
    order=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
    if not order:
        c.close()
        raise HTTPException(404, "Order not found")

    c.execute("UPDATE orders SET payment_status='PAID' WHERE id=?",(oid,))
    c.commit(); c.close()
    return {"ok": True, "order_id": oid, "token": order["token"], "status":"PAID"}

@app.post("/api/webhooks/phonepe")
async def phonepe_webhook(request: Request):
    """Automated server webhook listener for PhonePe Business PG payments."""
    try:
        body = await request.json()
        response_b64 = body.get("response", "")
        if response_b64:
            decoded = json.loads(base64.b64decode(response_b64).decode("utf-8"))
            data = decoded.get("data", {})
            code = decoded.get("code", "")
            merchant_trx_id = data.get("merchantTransactionId", "")

            # If PhonePe transaction succeeded (code == PAYMENT_SUCCESS)
            if code == "PAYMENT_SUCCESS" and merchant_trx_id.startswith("FOOD-"):
                oid = int(merchant_trx_id.replace("FOOD-", ""))
                c = db()
                c.execute("UPDATE orders SET payment_status='PAID' WHERE id=?", (oid,))
                c.commit(); c.close()
                return {"ok": True, "status": "SUCCESS"}
    except Exception as e:
        print("PhonePe Webhook Error:", e)
    return {"ok": True}

@app.get("/api/orders/{oid}")
def get_order(oid:int):
    c=db()
    r=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
    if not r:
        c.close()
        raise HTTPException(404,"Order not found")
    items=c.execute(
        "SELECT name,price,quantity FROM order_items WHERE order_id=?",(oid,)
    ).fetchall()
    c.close()
    return {**dict(r),"items":[dict(x) for x in items]}

@app.get("/api/orders")
def all_orders(x_admin_key: str | None = Header(default=None)):
    require_admin(x_admin_key)
    c=db()
    rows=c.execute(
        "SELECT * FROM orders WHERE payment_status='PAID' ORDER BY token"
    ).fetchall()
    out=[]
    for r in rows:
        items=c.execute(
            "SELECT name,price,quantity FROM order_items WHERE order_id=?",(r["id"],)
        ).fetchall()
        out.append({**dict(r),"items":[dict(x) for x in items]})
    c.close()
    return out

@app.patch("/api/orders/{oid}/status")
def update_status(oid:int,new_status:str,x_admin_key: str | None = Header(default=None)):
    require_admin(x_admin_key)
    if new_status not in {"WAITING","PREPARING","READY","DELIVERED"}:
        raise HTTPException(400,"Invalid status")
    c=db()
    c.execute("UPDATE orders SET order_status=? WHERE id=?",(new_status,oid))
    c.commit(); c.close()
    return {"ok":True}


@app.get("/api/menu-dates")
def menu_dates():
    c=db()
    rows=c.execute("SELECT DISTINCT menu_date FROM menu_dates ORDER BY menu_date").fetchall()
    c.close()
    return [r["menu_date"] for r in rows]

@app.get("/api/menu-plan")
def menu_plan():
    c=db()
    agg = "STRING_AGG(m.menu_date, ',')" if c.is_postgres else "GROUP_CONCAT(m.menu_date)"
    rows=c.execute(
        f"""SELECT p.id,p.name,p.price,p.demand,p.sourcing,
                  {agg} AS dates
           FROM products p JOIN menu_dates m ON m.product_id=p.id
           GROUP BY p.id ORDER BY p.id"""
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]

@app.get("/api/display")
def public_display():
    c=db()
    ready=c.execute(
        """SELECT token FROM orders
           WHERE payment_status='PAID' AND order_status='READY'
           ORDER BY token LIMIT 1"""
    ).fetchone()
    pending=c.execute(
        """SELECT token FROM orders
           WHERE payment_status='PAID' AND order_status IN ('WAITING','PREPARING')
           ORDER BY token LIMIT 8"""
    ).fetchall()
    c.close()
    return {
        "now_serving": ready["token"] if ready else None,
        "queue":[x["token"] for x in pending]
    }
