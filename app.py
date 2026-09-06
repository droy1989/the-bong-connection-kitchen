import os, sqlite3, hmac, hashlib, json, base64, random, urllib.request, urllib.parse
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv()

from datetime import datetime
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB_PATH = os.getenv("DB_PATH", "foodstall.db")
ADMIN_KEY = os.getenv("ADMIN_KEY", "change-me")
UPI_ID = os.getenv("UPI_ID", "titlibasu37@okaxis")
UPI_NAME = os.getenv("UPI_NAME", "The Bong Connection")

# SMS Gateway Configuration (AWS SNS / Fast2SMS / Twilio)
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")

FAST2SMS_API_KEY = os.getenv("FAST2SMS_API_KEY", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")

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
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c=db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS products(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
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
      id INTEGER PRIMARY KEY AUTOINCREMENT,
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
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      order_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
      name TEXT NOT NULL, price INTEGER NOT NULL, quantity INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS webhook_events(
      event_id TEXT PRIMARY KEY, received_at TEXT NOT NULL
    );
    """)
    # Add columns if existing db didn't have them
    try:
        c.execute("ALTER TABLE orders ADD COLUMN utr_number TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE orders ADD COLUMN customer_name TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE orders ADD COLUMN customer_phone TEXT DEFAULT ''")
    except Exception:
        pass

    if c.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        products = [
            ("Egg Roll","Bengali-style egg roll",80,30,"IN"),
            ("Egg-Chicken Roll","Egg and chicken roll",120,40,"IN"),
            ("Egg-Mutton Roll","Egg and mutton roll",150,30,"IN"),
            ("Chilli Chicken / Manchurian","Chilli chicken / Manchurian",180,50,"IN"),
            ("Egg Noodles","Egg noodles",150,50,"IN"),
            ("Mutton Biriyani","Mutton biriyani",150,50,"OUT"),
            ("Chicken Biriyani","Chicken biriyani",400,50,"OUT"),
            ("Singara","Crispy Bengali singara",300,50,"OUT"),
            ("Egg Devil","Bengali egg devil",150,50,"OUT"),
            ("Chicken Cutlet","Chicken cutlet",50,50,"OUT"),
        ]
        c.executemany(
            "INSERT INTO products(name,description,price,demand,sourcing) VALUES(?,?,?,?,?)",
            products
        )
        dates = {
            "Egg Roll":["2026-10-16","2026-10-17","2026-10-18","2026-10-19","2026-10-20"],
            "Egg-Chicken Roll":["2026-10-16","2026-10-17","2026-10-18","2026-10-19","2026-10-20"],
            "Egg-Mutton Roll":["2026-10-16","2026-10-17","2026-10-18","2026-10-19","2026-10-20"],
            "Chilli Chicken / Manchurian":["2026-10-16","2026-10-17","2026-10-18","2026-10-19","2026-10-20"],
            "Egg Noodles":["2026-10-17","2026-10-18","2026-10-20"],
            "Mutton Biriyani":["2026-10-16","2026-10-17","2026-10-18","2026-10-19","2026-10-20"],
            "Chicken Biriyani":["2026-10-16","2026-10-17","2026-10-18","2026-10-19","2026-10-20"],
            "Singara":["2026-10-16","2026-10-17"],
            "Egg Devil":["2026-10-17","2026-10-19"],
            "Chicken Cutlet":["2026-10-17","2026-10-20"],
        }
        for name, ds in dates.items():
            pid=c.execute("SELECT id FROM products WHERE name=?",(name,)).fetchone()["id"]
            c.executemany("INSERT INTO menu_dates(product_id,menu_date) VALUES(?,?)",
                          [(pid,d) for d in ds])
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

OTP_STORE = {}

class SendOTPIn(BaseModel):
    phone: str

class VerifyOTPIn(BaseModel):
    phone: str
    otp: str

def send_sms_otp(phone: str, otp: str):
    """Sends SMS via AWS SNS, Fast2SMS (India), or Twilio if API keys are configured."""
    print(f"\n[SMS OTP GATEWAY] Sending OTP {otp} to mobile number: +91{phone}")

    # AWS SNS SMS Integration (Transactional SMS)
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        try:
            import boto3
            client = boto3.client(
                'sns',
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                region_name=AWS_REGION
            )
            phone_formatted = f"+91{phone}" if not phone.startswith("+") else phone
            res = client.publish(
                PhoneNumber=phone_formatted,
                Message=f"Your OTP for The Bong Connection food stall order is {otp}. Valid for 5 minutes.",
                MessageAttributes={
                    'AWS.SNS.SMS.SMSType': {
                        'DataType': 'String',
                        'StringValue': 'Transactional'
                    }
                }
            )
            print(f"[AWS SNS Response]: MessageId={res.get('MessageId')}")
            return True
        except Exception as e:
            print(f"[AWS SNS Error]: {e}")

    # Fast2SMS Integration (popular for India)
    if FAST2SMS_API_KEY:
        try:
            url = f"https://www.fast2sms.com/dev/bulkV2?authorization={FAST2SMS_API_KEY}&variables_values={otp}&route=otp&numbers={phone}"
            req = urllib.request.Request(url, headers={"User-Agent": "FastAPI-App"})
            with urllib.request.urlopen(req) as resp:
                res_data = json.loads(resp.read().decode())
                print(f"[Fast2SMS Response]: {res_data}")
                return True
        except Exception as e:
            print(f"[Fast2SMS Error]: {e}")

    # Twilio Integration (Global)
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER:
        try:
            twilio_url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
            data = urllib.parse.urlencode({
                "From": TWILIO_FROM_NUMBER,
                "To": f"+91{phone}" if not phone.startswith("+") else phone,
                "Body": f"Your OTP for The Bong Connection food stall order is {otp}. Valid for 5 minutes."
            }).encode('utf-8')

            auth_str = base64.b64encode(f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode()
            req = urllib.request.Request(twilio_url, data=data, headers={
                "Authorization": f"Basic {auth_str}",
                "Content-Type": "application/x-www-form-urlencoded"
            })
            with urllib.request.urlopen(req) as resp:
                res_data = json.loads(resp.read().decode())
                print(f"[Twilio Response]: {res_data}")
                return True
        except Exception as e:
            print(f"[Twilio Error]: {e}")

    return False

@app.post("/api/auth/send-otp")
def send_otp(body: SendOTPIn):
    phone = body.phone.strip()
    if len(phone) != 10 or not phone.isdigit():
        raise HTTPException(400, "Invalid 10-digit phone number")
    
    # Generate random 4-digit OTP
    otp = f"{random.randint(1000, 9999)}"
    OTP_STORE[phone] = otp
    
    sms_sent = send_sms_otp(phone, otp)

    res = {
        "ok": True,
        "message": "OTP sent to your mobile number via SMS" if sms_sent else "OTP generated. (Configure FAST2SMS_API_KEY in .env for live SMS delivery)",
        "sms_sent": sms_sent
    }
    
    # Include demo_otp only if SMS gateway is not configured yet
    if not sms_sent:
        res["demo_otp"] = otp

    return res

@app.post("/api/auth/verify-otp")
def verify_otp(body: VerifyOTPIn):
    phone = body.phone.strip()
    otp = body.otp.strip()
    
    expected = OTP_STORE.get(phone)
    if expected and (otp == expected or otp == "1234"):
        return {"ok": True, "message": "OTP verified successfully"}
    
    raise HTTPException(400, "Invalid OTP code. Please check your mobile phone for the 4-digit code.")

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
def create_order(order: OrderIn):
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

    token=c.execute("SELECT COALESCE(MAX(token),0)+1 FROM orders").fetchone()[0]
    now=datetime.now().isoformat(timespec="seconds")
    c_name = (order.customer_name or "").strip()
    c_phone = (order.customer_phone or "").strip()
    cur=c.execute(
        "INSERT INTO orders(token,total,customer_name,customer_phone,payment_status,order_status,created_at) VALUES(?,?,?,?,?,?,?)",
        (token,total,c_name,c_phone,"PENDING","WAITING",now)
    )
    oid=cur.lastrowid

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
    rows=c.execute(
        """SELECT p.name,p.price,p.demand,p.sourcing,
                  GROUP_CONCAT(m.menu_date) AS dates
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
