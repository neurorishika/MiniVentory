import csv
import io
import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from datetime import datetime, timedelta
from functools import wraps
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    send_file,
    jsonify,
)
from pymongo import MongoClient, ASCENDING, DESCENDING
from dotenv import load_dotenv

# --- Config & DB ---
load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "lab_inventory")
ADMIN_PIN = os.environ.get("ADMIN_PIN", "")

# Email settings
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_USE_SSL = str(os.environ.get("SMTP_USE_SSL", "false")).lower() == "true"
SMTP_FROM = os.environ.get("SMTP_FROM", "stockroom@example.org")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")

CRON_TOKEN = os.environ.get("CRON_TOKEN", "")

SUMMARY_DEFAULT_FREQUENCY = os.environ.get("SUMMARY_DEFAULT_FREQUENCY", "daily").lower()
SUMMARY_DEFAULT_HOUR_UTC = int(os.environ.get("SUMMARY_DEFAULT_HOUR_UTC", "22") or 22)
SUMMARY_DEFAULT_WEEKDAY = int(os.environ.get("SUMMARY_DEFAULT_WEEKDAY", "4") or 4)

client = MongoClient(MONGO_URI)
db = client[MONGO_DB]
items_col = db["items"]  # { name, sku?, stock:int, low_stock_threshold:int, unit:str }
users_col = db["users"]  # { name, tag?, is_active:bool }
logs_col = db["logs"]  # { time, user, item, qty:int, note:str, before:int, after:int }
alerts_col = db[
    "alerts"
]  # { item, last_sent: datetime }  # to rate-limit low-stock emails
settings_col = db[
    "settings"
]  # single doc: { _id:"app", summary_frequency, summary_hour_utc, summary_weekday, last_summary_sent_utc }

# helpful indexes
items_col.create_index([("name", ASCENDING)], unique=True)
users_col.create_index([("name", ASCENDING)], unique=True)
logs_col.create_index([("time", DESCENDING)])
alerts_col.create_index([("item", ASCENDING)], unique=True)


# ---------- Email helpers ----------
def _send_email(subject: str, body: str, to_addr: str):
    """Best-effort email sender with retries; never raises."""
    if not (SMTP_HOST and to_addr):
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_addr
    msg.set_content(body)

    attempts, delay = 3, 1.0
    for _ in range(attempts):
        try:
            if SMTP_USE_SSL:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(
                    SMTP_HOST, SMTP_PORT, context=context, timeout=10
                ) as s:
                    if SMTP_USERNAME:
                        s.login(SMTP_USERNAME, SMTP_PASSWORD)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
                    s.ehlo()
                    try:
                        s.starttls(context=ssl.create_default_context())
                    except smtplib.SMTPException:
                        pass
                    if SMTP_USERNAME:
                        s.login(SMTP_USERNAME, SMTP_PASSWORD)
                    s.send_message(msg)
            return
        except Exception:
            time.sleep(delay)
            delay *= 2


def send_low_stock_email(item_name: str, after_stock: int, threshold: int):
    """Rate-limited (1/hr per item) low-stock notice."""
    if not (SMTP_HOST and ADMIN_EMAIL):
        return
    now = datetime.utcnow()
    last = alerts_col.find_one({"item": item_name})
    if last and (now - last.get("last_sent", now)) < timedelta(hours=1):
        return
    subj = f"[Stockroom] LOW STOCK: {item_name} ({after_stock} ≤ {threshold})"
    body = (
        f"Item: {item_name}\nRemaining: {after_stock}\nThreshold: {threshold}\n"
        f"Time (UTC): {now:%Y-%m-%d %H:%M:%S}\n\n— Local Stockroom"
    )
    _send_email(subj, body, ADMIN_EMAIL)
    alerts_col.update_one(
        {"item": item_name}, {"$set": {"last_sent": now}}, upsert=True
    )


def send_replenish_verification_email(
    item_name: str, qty_added: int, new_stock: int, interval_desc: str
):
    """Send verification email to admin when auto-replenishment occurs."""
    if not (SMTP_HOST and ADMIN_EMAIL):
        return

    now = datetime.utcnow()
    subj = f"[Stockroom] AUTO-REPLENISH: {item_name} - Please Verify Delivery"
    body = (
        f"🔄 AUTO-REPLENISHMENT NOTIFICATION\n\n"
        f"Item: {item_name}\n"
        f"Quantity Added: +{qty_added}\n"
        f"New Stock Level: {new_stock}\n"
        f"Replenishment Schedule: {interval_desc}\n"
        f"Time (UTC): {now:%Y-%m-%d %H:%M:%S}\n\n"
        f"⚠️  ACTION REQUIRED:\n"
        f"Please verify that the delivery actually occurred and adjust stock levels if needed.\n"
        f"If the delivery did not happen, please manually adjust the stock in the admin panel.\n\n"
        f"🔗 Admin Panel: http://your-server:2152/admin/items\n\n"
        f"— MiniVentory Auto-Replenishment System"
    )
    _send_email(subj, body, ADMIN_EMAIL)


# ---------- Auto-Replenish helpers ----------
# Allowed interval types for auto-replenish
REPLENISH_INTERVAL_TYPES = ("days", "weeks", "months")


def _ensure_item_defaults(item: dict) -> dict:
    """Ensure auto-replenish keys exist with safe defaults."""
    item = dict(item)
    item.setdefault("auto_replenish_enabled", False)
    item.setdefault("auto_replenish_qty", 0)
    item.setdefault("auto_replenish_interval_type", "days")  # days|weeks|months
    item.setdefault(
        "auto_replenish_interval_value", 1
    )  # e.g., every 1 day, 2 weeks, 3 months
    item.setdefault("auto_replenish_hour_utc", 0)  # 0-23
    item.setdefault(
        "auto_replenish_next_due", None
    )  # exact datetime when next replenishment is due
    item.setdefault("auto_replenish_max_stock", None)  # optional int cap; None=no cap
    item.setdefault("last_replenished_utc", None)
    return item


def _calculate_next_due(
    base_time: datetime, interval_type: str, interval_value: int, hour_utc: int
) -> datetime:
    """Calculate the next due datetime based on interval settings."""
    if interval_type == "days":
        next_due = base_time + timedelta(days=interval_value)
    elif interval_type == "weeks":
        next_due = base_time + timedelta(weeks=interval_value)
    elif interval_type == "months":
        # Handle month arithmetic carefully
        month = base_time.month
        year = base_time.year
        month += interval_value
        while month > 12:
            month -= 12
            year += 1
        # Keep same day, but handle month-end edge cases
        day = min(base_time.day, 28)  # Cap at 28 to avoid month-end issues
        next_due = base_time.replace(year=year, month=month, day=day)
    else:
        # Fallback to daily
        next_due = base_time + timedelta(days=1)

    # Set the specific hour
    return next_due.replace(hour=hour_utc, minute=0, second=0, microsecond=0)


def _is_replenish_due(now_utc: datetime, item: dict) -> bool:
    """Simple check: replenishment is due if now >= next_due and at the right hour."""
    it = _ensure_item_defaults(item)
    if not it["auto_replenish_enabled"]:
        return False

    interval_type = it.get("auto_replenish_interval_type", "days")
    if interval_type not in REPLENISH_INTERVAL_TYPES:
        return False

    # Check if we're at the right hour
    hour = int(it.get("auto_replenish_hour_utc", 0))
    if now_utc.hour != hour:
        return False

    # Check if next_due is set and if we've reached it
    next_due = it.get("auto_replenish_next_due")
    if not next_due:
        # If no next_due is set, we need to calculate it first
        return False

    # Simple check: are we at or past the due time?
    return now_utc >= next_due


def _apply_replenish(item_name: str, qty: int, max_cap: int | None):
    """Atomic stock increment with optional cap. Logs a SYSTEM auto-replenish entry and calculates next due date."""
    # Read current stock and item details
    doc = items_col.find_one({"name": item_name})
    if not doc:
        return False

    doc = _ensure_item_defaults(doc)
    current = int(doc.get("stock", 0))
    add = int(qty)
    if add <= 0:
        return False

    new_stock = current + add
    if isinstance(max_cap, int):
        # enforce cap
        new_stock = min(new_stock, max_cap)

    # Calculate next due date
    now = datetime.utcnow()
    interval_type = doc.get("auto_replenish_interval_type", "days")
    interval_value = int(doc.get("auto_replenish_interval_value", 1))
    hour_utc = int(doc.get("auto_replenish_hour_utc", 0))
    next_due = _calculate_next_due(now, interval_type, interval_value, hour_utc)

    # atomic set: compare-and-set on stock value to avoid races
    res = items_col.update_one(
        {"name": item_name, "stock": current},
        {
            "$set": {
                "stock": new_stock,
                "last_replenished_utc": now,
                "auto_replenish_next_due": next_due,
            }
        },
    )
    if res.modified_count != 1:
        return False

    actual_qty_added = new_stock - current
    logs_col.insert_one(
        {
            "time": now,
            "user": "SYSTEM",
            "item": item_name,
            "qty": actual_qty_added,
            "note": "auto-replenish",
            "before": current,
            "after": new_stock,
        }
    )

    # Send verification email to admin
    interval_desc = f"every {interval_value} {interval_type}"
    try:
        send_replenish_verification_email(
            item_name, actual_qty_added, new_stock, interval_desc
        )
    except Exception:
        # Never let email failures block the replenishment process
        pass

    return True


# ---------- Settings helpers ----------
def get_settings():
    s = settings_col.find_one({"_id": "app"})
    if not s:
        s = {
            "_id": "app",
            "summary_frequency": SUMMARY_DEFAULT_FREQUENCY,  # "never" | "daily" | "weekly"
            "summary_hour_utc": SUMMARY_DEFAULT_HOUR_UTC,  # 0-23
            "summary_weekday": SUMMARY_DEFAULT_WEEKDAY,  # 0=Mon..6=Sun
            "last_summary_sent_utc": None,
        }
        settings_col.insert_one(s)
    return s


def update_settings(**kwargs):
    settings_col.update_one({"_id": "app"}, {"$set": kwargs}, upsert=True)


def _compose_summary(days_window: int):
    """Builds a text summary for email: top items/users + low stock list."""
    since = datetime.utcnow() - timedelta(days=days_window)

    by_item = list(
        logs_col.aggregate(
            [
                {"$match": {"time": {"$gte": since}}},
                {
                    "$group": {
                        "_id": "$item",
                        "total_qty": {"$sum": "$qty"},
                        "events": {"$sum": 1},
                    }
                },
                {"$project": {"item": "$_id", "_id": 0, "total_qty": 1, "events": 1}},
                {"$sort": {"total_qty": -1}},
            ]
        )
    )

    by_user = list(
        logs_col.aggregate(
            [
                {"$match": {"time": {"$gte": since}}},
                {
                    "$group": {
                        "_id": "$user",
                        "total_qty": {"$sum": "$qty"},
                        "events": {"$sum": 1},
                    }
                },
                {"$project": {"user": "$_id", "_id": 0, "total_qty": 1, "events": 1}},
                {"$sort": {"total_qty": -1}},
            ]
        )
    )

    items = list(items_col.find({}, {"_id": 0}).sort("name", ASCENDING))
    low_items = [
        i
        for i in items
        if isinstance(i.get("low_stock_threshold"), int)
        and i["stock"] <= i["low_stock_threshold"]
    ]

    lines = []
    lines.append(
        f"Usage Summary (last {days_window} day(s)) — UTC {datetime.utcnow():%Y-%m-%d %H:%M}"
    )
    lines.append("")
    lines.append("Top Items:")
    if by_item:
        for r in by_item[:25]:
            lines.append(f"- {r['item']}: qty={r['total_qty']} (events={r['events']})")
    else:
        lines.append("- (no usage)")

    lines.append("")
    lines.append("Top Users:")
    if by_user:
        for r in by_user[:25]:
            lines.append(f"- {r['user']}: qty={r['total_qty']} (events={r['events']})")
    else:
        lines.append("- (no usage)")

    lines.append("")
    lines.append("Low Stock Now:")
    if low_items:
        for i in low_items:
            lines.append(
                f"- {i['name']}: {i['stock']} {i.get('unit','pcs')} (≤ {i.get('low_stock_threshold',0)})"
            )
    else:
        lines.append("- none")

    return "\n".join(lines)


def _should_send_summary(now_utc: datetime, s: dict):
    """Checks if we should send now; respects last sent."""
    freq = s.get("summary_frequency", "never")
    hour = int(s.get("summary_hour_utc", SUMMARY_DEFAULT_HOUR_UTC))
    weekday = int(s.get("summary_weekday", SUMMARY_DEFAULT_WEEKDAY))
    last = s.get("last_summary_sent_utc")

    if freq == "never":
        return False, 0

    if now_utc.hour != hour:
        return False, 0

    if freq == "daily":
        # send once per calendar day at the chosen hour
        if last and last.date() == now_utc.date():
            return False, 0
        return True, 1

    if freq == "weekly":
        if now_utc.weekday() != weekday:
            return False, 0
        # send once per week at that weekday/hour
        if last and (now_utc - last) < timedelta(days=6, hours=23):
            return False, 0
        return True, 7

    return False, 0


def send_summary_email_if_due(now_utc: datetime):
    """Idempotent; safe to call hourly. Sends summary if policy/time matches."""
    if not ADMIN_EMAIL:
        return False
    s = get_settings()
    do_send, window_days = _should_send_summary(now_utc, s)
    if not do_send:
        return False

    # choose window: 1 day for daily, 7 days for weekly (fallback to 1 if unknown)
    days = window_days if window_days in (1, 7) else 1
    body = _compose_summary(days)
    subj = f"[Stockroom] {'Daily' if days==1 else 'Weekly'} Usage Summary"
    _send_email(subj, body, ADMIN_EMAIL)
    update_settings(last_summary_sent_utc=now_utc)
    return True


# --- Context processor to make datetime available in templates ---
@app.context_processor
def inject_datetime():
    return dict(datetime=datetime)


# --- Admin gate (no full login; just a session flag) ---
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("is_admin"):
            return f(*args, **kwargs)
        return redirect(url_for("admin_login", next=request.path))

    return wrapper


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        pin = request.form.get("pin", "")
        if ADMIN_PIN and pin == ADMIN_PIN:
            session["is_admin"] = True
            flash("Admin access granted.", "success")
            return redirect(request.args.get("next") or url_for("admin_home"))
        flash("Incorrect PIN.", "danger")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    flash("Admin logged out.", "info")
    return redirect(url_for("index"))


# --- Public kiosk (no login) ---
@app.route("/", methods=["GET"])
def index():
    users = list(
        users_col.find({"is_active": True}, {"_id": 0}).sort("name", ASCENDING)
    )
    items = list(items_col.find({}, {"_id": 0}).sort("name", ASCENDING))
    return render_template("index.html", users=users, items=items)


@app.route("/checkout", methods=["POST"])
def checkout():
    user = request.form.get("user")
    item_name = request.form.get("item")
    qty = request.form.get("quantity")
    note = request.form.get("note", "")

    try:
        qty = int(qty)
        if qty <= 0:
            raise ValueError
    except Exception:
        flash("Quantity must be a positive integer.", "danger")
        return redirect(url_for("index"))

    user_doc = users_col.find_one({"name": user, "is_active": True})
    item_doc = items_col.find_one({"name": item_name})
    if not user_doc:
        flash("Selected user is not valid.", "danger")
        return redirect(url_for("index"))
    if not item_doc:
        flash("Selected item is not valid.", "danger")
        return redirect(url_for("index"))

    before_stock = int(item_doc.get("stock", 0))
    after_stock = before_stock - qty
    # atomic compare-and-set
    result = items_col.update_one(
        {"name": item_name, "stock": before_stock}, {"$set": {"stock": after_stock}}
    )
    if result.modified_count != 1:
        flash("Stock changed while you were checking out. Please try again.", "warning")
        return redirect(url_for("index"))

    logs_col.insert_one(
        {
            "time": datetime.utcnow(),
            "user": user,
            "item": item_name,
            "qty": qty,
            "note": note.strip(),
            "before": before_stock,
            "after": after_stock,
        }
    )

    # Alert if low
    low = item_doc.get("low_stock_threshold", 0)
    low_alert = (after_stock <= low) if isinstance(low, int) else False
    if low_alert:
        # fire-and-forget, never blocks checkout UX
        try:
            send_low_stock_email(item_name, after_stock, low)
        except Exception:
            pass

    return render_template(
        "success.html",
        user=user,
        item=item_name,
        qty=qty,
        after_stock=after_stock,
        low_alert=low_alert,
        low_threshold=low,
        action="checkout",
    )


@app.route("/dropoff", methods=["POST"])
def dropoff():
    user = request.form.get("user")
    item_name = request.form.get("item")
    qty = request.form.get("quantity")
    note = request.form.get("note", "")

    try:
        qty = int(qty)
        if qty <= 0:
            raise ValueError
    except Exception:
        flash("Quantity must be a positive integer.", "danger")
        return redirect(url_for("index"))

    user_doc = users_col.find_one({"name": user, "is_active": True})
    item_doc = items_col.find_one({"name": item_name})
    if not user_doc:
        flash("Selected user is not valid.", "danger")
        return redirect(url_for("index"))
    if not item_doc:
        flash("Selected item is not valid.", "danger")
        return redirect(url_for("index"))

    before_stock = int(item_doc.get("stock", 0))
    after_stock = before_stock + qty
    # atomic compare-and-set
    result = items_col.update_one(
        {"name": item_name, "stock": before_stock}, {"$set": {"stock": after_stock}}
    )
    if result.modified_count != 1:
        flash("Stock changed while you were dropping off. Please try again.", "warning")
        return redirect(url_for("index"))

    logs_col.insert_one(
        {
            "time": datetime.utcnow(),
            "user": user,
            "item": item_name,
            "qty": qty,
            "note": note.strip(),
            "before": before_stock,
            "after": after_stock,
        }
    )

    return render_template(
        "success.html",
        user=user,
        item=item_name,
        qty=qty,
        after_stock=after_stock,
        low_alert=False,
        low_threshold=0,
        action="dropoff",
    )


# --- Admin: home/dashboard ---
@app.route("/admin", methods=["GET"])
@admin_required
def admin_home():
    items = list(items_col.find({}, {"_id": 0}).sort("name", ASCENDING))
    low_items = [
        i
        for i in items
        if isinstance(i.get("low_stock_threshold"), int)
        and i["stock"] <= i["low_stock_threshold"]
    ]
    recent = list(logs_col.find({}, {"_id": 0}).sort("time", DESCENDING).limit(10))
    return render_template(
        "admin_home.html", items=items, low_items=low_items, recent=recent
    )


# --- Admin: items ---
@app.route("/admin/items", methods=["GET", "POST"])
@admin_required
def admin_items():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            name = request.form.get("name", "").strip()
            unit = request.form.get("unit", "").strip() or "pcs"
            stock = int(request.form.get("stock", "0") or 0)
            low = int(request.form.get("low_stock_threshold", "0") or 0)
            if not name:
                flash("Item name required.", "danger")
            else:
                try:
                    items_col.insert_one(
                        {
                            "name": name,
                            "unit": unit,
                            "stock": stock,
                            "low_stock_threshold": low,
                        }
                    )
                    flash(f"Item '{name}' created.", "success")
                except Exception as e:
                    flash(
                        f"Could not create item (maybe duplicate name). {e}", "danger"
                    )

        elif action == "adjust":
            name = request.form.get("name")
            delta = int(request.form.get("delta", "0"))
            res = items_col.update_one({"name": name}, {"$inc": {"stock": delta}})
            if res.modified_count == 1:
                flash(f"Stock for '{name}' adjusted by {delta}.", "success")
            else:
                flash("Adjust failed; item not found.", "danger")

        elif action == "update_threshold":
            name = request.form.get("name")
            low = int(request.form.get("low_stock_threshold", "0") or 0)
            res = items_col.update_one(
                {"name": name}, {"$set": {"low_stock_threshold": low}}
            )
            if res.modified_count == 1:
                flash(f"Low-stock threshold updated for '{name}'.", "success")
            else:
                flash("Update failed; item not found.", "danger")

        elif action == "update_replenish":
            name = request.form.get("name")
            enabled = request.form.get("enabled") == "on"
            qty = int(request.form.get("qty", "0") or 0)
            interval_type = (
                request.form.get("interval_type", "days") or "days"
            ).lower()
            interval_value = int(request.form.get("interval_value", "1") or 1)
            hour = int(request.form.get("hour", "9") or 9)
            max_cap_raw = request.form.get("max_cap", "").strip()
            max_cap = int(max_cap_raw) if max_cap_raw not in ("", None) else None

            if interval_type not in REPLENISH_INTERVAL_TYPES:
                flash(
                    "Invalid interval type. Must be days, weeks, or months.", "danger"
                )
            elif interval_value < 1:
                flash("Interval value must be at least 1.", "danger")
            elif not (0 <= hour <= 23):
                flash("Hour must be 0–23 (UTC).", "danger")
            else:
                # Calculate initial next_due if enabling replenishment
                next_due = None
                if enabled:
                    now = datetime.utcnow()
                    next_due = _calculate_next_due(
                        now, interval_type, interval_value, hour
                    )

                res = items_col.update_one(
                    {"name": name},
                    {
                        "$set": {
                            "auto_replenish_enabled": enabled,
                            "auto_replenish_qty": qty,
                            "auto_replenish_interval_type": interval_type,
                            "auto_replenish_interval_value": interval_value,
                            "auto_replenish_hour_utc": hour,
                            "auto_replenish_max_stock": max_cap,
                            "auto_replenish_next_due": next_due,
                        }
                    },
                )
                if res.matched_count == 1:
                    if enabled:
                        flash(
                            f"Auto-replenish enabled for '{name}': {qty} every {interval_value} {interval_type} at {hour}:00 UTC",
                            "success",
                        )
                    else:
                        flash(f"Auto-replenish disabled for '{name}'.", "success")
                else:
                    flash("Item not found.", "danger")

        elif action == "delete":
            name = request.form.get("name")
            res = items_col.delete_one({"name": name})
            if res.deleted_count == 1:
                flash(f"Item '{name}' deleted.", "info")
            else:
                flash("Delete failed; item not found.", "danger")

    items = list(items_col.find({}, {"_id": 0}).sort("name", ASCENDING))
    return render_template("admin_items.html", items=items)


# --- Admin: users ---
@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def admin_users():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            name = request.form.get("name", "").strip()
            tag = request.form.get("tag", "").strip()
            if not name:
                flash("User name required.", "danger")
            else:
                try:
                    users_col.insert_one({"name": name, "tag": tag, "is_active": True})
                    flash(f"User '{name}' created.", "success")
                except Exception as e:
                    flash(f"Could not create user (maybe duplicate). {e}", "danger")

        elif action == "toggle":
            name = request.form.get("name")
            user = users_col.find_one({"name": name})
            if user:
                new_state = not user.get("is_active", True)
                users_col.update_one({"name": name}, {"$set": {"is_active": new_state}})
                flash(f"'{name}' active={new_state}", "success")
            else:
                flash("User not found.", "danger")

        elif action == "delete":
            name = request.form.get("name")
            res = users_col.delete_one({"name": name})
            if res.deleted_count == 1:
                flash(f"User '{name}' deleted.", "info")
            else:
                flash("Delete failed; user not found.", "danger")

    users = list(users_col.find({}, {"_id": 0}).sort("name", ASCENDING))
    return render_template("admin_users.html", users=users)


# --- Admin: logs & exports ---
@app.route("/admin/logs", methods=["GET"])
@admin_required
def admin_logs():
    q_user = request.args.get("user", "").strip()
    q_item = request.args.get("item", "").strip()

    query = {}
    if q_user:
        query["user"] = q_user
    if q_item:
        query["item"] = q_item

    logs = list(logs_col.find(query, {"_id": 0}).sort("time", DESCENDING).limit(1000))
    users = list(users_col.find({}, {"_id": 0}).sort("name", ASCENDING))
    items = list(items_col.find({}, {"_id": 0}).sort("name", ASCENDING))
    return render_template(
        "admin_logs.html",
        logs=logs,
        users=users,
        items=items,
        q_user=q_user,
        q_item=q_item,
    )


@app.route("/admin/logs/export", methods=["GET"])
@admin_required
def export_logs_csv():
    q_user = request.args.get("user", "").strip()
    q_item = request.args.get("item", "").strip()

    query = {}
    if q_user:
        query["user"] = q_user
    if q_item:
        query["item"] = q_item

    logs = list(logs_col.find(query, {"_id": 0}).sort("time", DESCENDING))
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["time_utc", "user", "item", "qty", "note", "before", "after"])
    for L in logs:
        cw.writerow(
            [
                L["time"].strftime("%Y-%m-%d %H:%M:%S"),
                L.get("user", ""),
                L.get("item", ""),
                L.get("qty", 0),
                L.get("note", ""),
                L.get("before", ""),
                L.get("after", ""),
            ]
        )
    mem = io.BytesIO()
    mem.write(si.getvalue().encode("utf-8"))
    mem.seek(0)
    return send_file(
        mem, mimetype="text/csv", as_attachment=True, download_name="usage_logs.csv"
    )


# --- Admin: stock export (NEW) ---
@app.route("/admin/stock/export", methods=["GET"])
@admin_required
def export_stock_csv():
    items = list(items_col.find({}, {"_id": 0}).sort("name", ASCENDING))
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["item", "stock", "unit", "low_stock_threshold"])
    for i in items:
        cw.writerow(
            [
                i["name"],
                i.get("stock", 0),
                i.get("unit", "pcs"),
                i.get("low_stock_threshold", 0),
            ]
        )
    mem = io.BytesIO()
    mem.write(si.getvalue().encode("utf-8"))
    mem.seek(0)
    return send_file(
        mem, mimetype="text/csv", as_attachment=True, download_name="stock_snapshot.csv"
    )


# --- Admin: usage summary (NEW) ---
@app.route("/admin/summary", methods=["GET"])
@admin_required
def admin_summary():
    # optional filters
    days = int(request.args.get("days", "30") or 30)  # last N days window
    since = datetime.utcnow() - timedelta(days=days)

    # aggregate by item usage
    by_item = list(
        logs_col.aggregate(
            [
                {"$match": {"time": {"$gte": since}}},
                {
                    "$group": {
                        "_id": "$item",
                        "total_qty": {"$sum": "$qty"},
                        "events": {"$sum": 1},
                    }
                },
                {"$project": {"item": "$_id", "_id": 0, "total_qty": 1, "events": 1}},
                {"$sort": {"total_qty": -1}},
            ]
        )
    )

    # aggregate by user usage
    by_user = list(
        logs_col.aggregate(
            [
                {"$match": {"time": {"$gte": since}}},
                {
                    "$group": {
                        "_id": "$user",
                        "total_qty": {"$sum": "$qty"},
                        "events": {"$sum": 1},
                    }
                },
                {"$project": {"user": "$_id", "_id": 0, "total_qty": 1, "events": 1}},
                {"$sort": {"total_qty": -1}},
            ]
        )
    )

    # current low stock list for context
    items = list(items_col.find({}, {"_id": 0}).sort("name", ASCENDING))
    low_items = [
        i
        for i in items
        if isinstance(i.get("low_stock_threshold"), int)
        and i["stock"] <= i["low_stock_threshold"]
    ]

    return render_template(
        "admin_summary.html",
        days=days,
        by_item=by_item,
        by_user=by_user,
        low_items=low_items,
    )


# --- Small JSON helper ---
@app.route("/api/items", methods=["GET"])
def api_items():
    items = list(items_col.find({}, {"_id": 0}).sort("name", ASCENDING))
    return jsonify(items)


# --- Seed route (optional) ---
@app.route("/admin/seed", methods=["POST"])
@admin_required
def admin_seed():
    users_col.insert_many(
        [
            {"name": "Alice", "tag": "alice", "is_active": True},
            {"name": "Bob", "tag": "bob", "is_active": True},
        ]
    )
    items_col.insert_many(
        [
            {
                "name": "1.5 mL Eppendorf tubes",
                "unit": "pcs",
                "stock": 500,
                "low_stock_threshold": 100,
            },
            {
                "name": "Nitrile gloves (M)",
                "unit": "box",
                "stock": 20,
                "low_stock_threshold": 5,
            },
        ]
    )
    flash("Seed data inserted.", "success")
    return redirect(url_for("admin_home"))


# ----- Admin Settings UI -----
@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    s = get_settings()
    if request.method == "POST":
        freq = request.form.get("summary_frequency", s["summary_frequency"]).lower()
        hour = int(
            request.form.get("summary_hour_utc", s["summary_hour_utc"])
            or s["summary_hour_utc"]
        )
        weekday = int(
            request.form.get("summary_weekday", s["summary_weekday"])
            or s["summary_weekday"]
        )
        if freq not in ("never", "daily", "weekly"):
            flash("Invalid frequency.", "danger")
        elif not (0 <= hour <= 23):
            flash("Hour must be 0–23 (UTC).", "danger")
        elif not (0 <= weekday <= 6):
            flash("Weekday must be 0–6 (Mon=0).", "danger")
        else:
            update_settings(
                summary_frequency=freq, summary_hour_utc=hour, summary_weekday=weekday
            )
            flash("Settings saved.", "success")
            s = get_settings()
    return render_template("admin_settings.html", s=s)


# ----- Manual send (for testing) -----
@app.route("/admin/summary/email-now", methods=["POST"])
@admin_required
def email_summary_now():
    body = _compose_summary(1)  # default 1-day snapshot for manual sends
    _send_email("[Stockroom] Manual Usage Summary", body, ADMIN_EMAIL)
    flash("Summary email sent (check your inbox).", "success")
    return redirect(url_for("admin_summary"))


# ----- Cron ping endpoint -----
# Call hourly via cron: curl -fsS http://SERVER:5000/tasks/summary?token=CRON_TOKEN
@app.route("/tasks/summary", methods=["GET"])
def tasks_summary():
    token = request.args.get("token", "")
    if not (CRON_TOKEN and token == CRON_TOKEN):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    sent = send_summary_email_if_due(datetime.utcnow())
    return jsonify({"ok": True, "sent": bool(sent)})


# ----- Cron ping endpoint for auto-replenish -----
# Call hourly: curl -fsS "http://SERVER:5000/tasks/replenish?token=CRON_TOKEN"
@app.route("/tasks/replenish", methods=["GET"])
def tasks_replenish():
    token = request.args.get("token", "")
    if not (CRON_TOKEN and token == CRON_TOKEN):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    now = datetime.utcnow()
    changed = []
    # iterate only items that might have replenish enabled
    cursor = items_col.find({"auto_replenish_enabled": True})
    for it in cursor:
        it = _ensure_item_defaults(it)
        if _is_replenish_due(now, it):
            name = it["name"]
            qty = int(it.get("auto_replenish_qty", 0) or 0)
            cap = it.get("auto_replenish_max_stock", None)
            try:
                ok = _apply_replenish(name, qty, cap if isinstance(cap, int) else None)
                if ok:
                    changed.append(name)
            except Exception:
                # fail-closed; never crash cron
                pass

    return jsonify({"ok": True, "replenished": changed})


@app.route("/health", methods=["GET"])
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat() + "Z"}, 200


# ----- Debug endpoint for auto-replenish status -----
@app.route("/tasks/replenish/debug", methods=["GET"])
def tasks_replenish_debug():
    """Debug endpoint to check auto-replenish status (requires auth token)."""
    token = request.args.get("token", "")
    if not (CRON_TOKEN and token == CRON_TOKEN):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    now = datetime.utcnow()
    items_status = []

    cursor = items_col.find({"auto_replenish_enabled": True})
    for it in cursor:
        it = _ensure_item_defaults(it)
        is_due = _is_replenish_due(now, it)

        items_status.append(
            {
                "name": it["name"],
                "current_stock": it.get("stock", 0),
                "auto_replenish_enabled": it.get("auto_replenish_enabled", False),
                "auto_replenish_qty": it.get("auto_replenish_qty", 0),
                "auto_replenish_interval_type": it.get(
                    "auto_replenish_interval_type", "days"
                ),
                "auto_replenish_interval_value": it.get(
                    "auto_replenish_interval_value", 1
                ),
                "auto_replenish_hour_utc": it.get("auto_replenish_hour_utc", 0),
                "auto_replenish_next_due": (
                    it.get("auto_replenish_next_due").isoformat()
                    if it.get("auto_replenish_next_due")
                    else None
                ),
                "auto_replenish_max_stock": it.get("auto_replenish_max_stock"),
                "last_replenished_utc": (
                    it.get("last_replenished_utc").isoformat()
                    if it.get("last_replenished_utc")
                    else None
                ),
                "is_due_now": is_due,
                "current_hour_utc": now.hour,
            }
        )

    return jsonify(
        {
            "ok": True,
            "current_time_utc": now.isoformat(),
            "current_hour_utc": now.hour,
            "items_with_auto_replenish": items_status,
        }
    )


if __name__ == "__main__":
    # host=0.0.0.0 so other devices on LAN (tablet) can reach it
    app.run(host="0.0.0.0", port=2152, debug=True)
