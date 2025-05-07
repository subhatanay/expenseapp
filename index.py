from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import urllib.parse
import psycopg2
import datetime
import os
import logging
import json

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

# --- User Settings Utility Functions ---
def get_user_settings(cur, user_id):
    cur.execute("SELECT key, value FROM user_settings WHERE user_id = %s", (user_id,))
    rows = cur.fetchall()
    settings = {row[0]: json.loads(row[1]) for row in rows}
    return {
        "current_event_id": settings.get("current_event_id"),
        "pending_add": settings.get("pending_add", False),
        "add_buffer": settings.get("add_buffer", [])
    }

def set_user_setting(cur, user_id, key, value):
    cur.execute("""
        INSERT INTO user_settings (user_id, key, value)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value
    """, (user_id, key, json.dumps(value)))

def set_multiple_settings(cur, user_id, settings_dict):
    for key, value in settings_dict.items():
        set_user_setting(cur, user_id, key, value)

# --- Routes ---
@app.route('/', methods=['GET'])
def hello():
    return "Hello! Welcome to the WhatsApp Expense App", 200, {'Content-Type': 'text/plain'}

@app.route('/', methods=['POST'])
def twilio_webhook():
    body_str = request.get_data(as_text=True)
    data = urllib.parse.parse_qs(body_str)
    incoming_msg = data.get('Body', [''])[0].strip().lower()
    user_id = data.get('From', [''])[0].replace("whatsapp:", "")

    resp = MessagingResponse()
    msg = resp.message()

    logging.info(f"Received message from {user_id}: {incoming_msg}")

    try:
        with get_conn() as conn:
            with conn.cursor() as c:
                user_state = get_user_settings(c, user_id)
                current_event_id = user_state["current_event_id"]
                pending_add = user_state["pending_add"]
                add_buffer = user_state["add_buffer"]

                # --- Event Creation ---
                if incoming_msg.startswith("create "):
                    event_name = incoming_msg.split("create ", 1)[1].strip()
                    try:
                        c.execute("INSERT INTO events (event_name) VALUES (%s)", (event_name,))
                        msg.body(f"✅ Event '{event_name}' created.")
                    except psycopg2.errors.UniqueViolation:
                        conn.rollback()
                        msg.body(f"⚠️ Event '{event_name}' already exists.")

                # --- List Events ---
                elif incoming_msg == "list":
                    c.execute("SELECT event_name FROM events")
                    rows = c.fetchall()
                    if rows:
                        msg.body("📋 Your Events:\n" + "\n".join([f"🔹 {r[0]}" for r in rows]))
                    else:
                        msg.body("⚠️ No events found. Use `create <event_name>` to create one.")

                # --- Switch Event ---
                elif incoming_msg.startswith("switch "):
                    event_name = incoming_msg.split("switch ", 1)[1].strip()
                    c.execute("SELECT event_id FROM events WHERE event_name = %s", (event_name,))
                    row = c.fetchone()
                    if row:
                        set_user_setting(c, user_id, "current_event_id", row[0])
                        msg.body(f"🔄 Switched to event: {event_name}")
                    else:
                        msg.body("⚠️ Event not found. Please create it first.")

                # --- Add Expenses ---
                elif incoming_msg.startswith("add"):
                    if not current_event_id:
                        msg.body("⚠️ Please switch to an event using `switch <event_name>`")
                    elif len(incoming_msg.split()) == 1:
                        set_multiple_settings(c, user_id, {"pending_add": True, "add_buffer": []})
                        msg.body("📝 Add mode started. Send items like `tea 10`. When done, type `done`.")
                    else:
                        parts = incoming_msg.split()
                        if len(parts) < 3:
                            msg.body("❌ Usage: add <item> <amount>")
                        else:
                            item = parts[1]
                            try:
                                amount = float(parts[2])
                                today = str(datetime.date.today())
                                c.execute("INSERT INTO transactions (event_id, date, action, item, amount) VALUES (%s, %s, 'add', %s, %s)",
                                          (current_event_id, today, item, amount))
                                msg.body(f"💸 Added: {item} - ₹{amount}")
                            except ValueError:
                                msg.body("❌ Amount should be a number.")

                # --- Handle Add Mode ---
                elif pending_add:
                    if incoming_msg == "done":
                        if not add_buffer:
                            msg.body("⚠️ No entries to add.")
                        else:
                            today = str(datetime.date.today())
                            for item, amount in add_buffer:
                                c.execute("INSERT INTO transactions (event_id, date, action, item, amount) VALUES (%s, %s, 'add', %s, %s)",
                                          (current_event_id, today, item, amount))
                            msg.body(f"✅ {len(add_buffer)} items added.\n🛑 Exiting add mode.")
                        set_multiple_settings(c, user_id, {"pending_add": False, "add_buffer": []})
                    else:
                        parts = incoming_msg.split()
                        if len(parts) != 2:
                            msg.body("❌ Format: `item amount` or `done` to finish.")
                        else:
                            try:
                                item = parts[0]
                                amount = float(parts[1])
                                add_buffer.append((item, amount))
                                set_user_setting(c, user_id, "add_buffer", add_buffer)
                                msg.body(f"➕ Staged: {item} ₹{amount}")
                            except ValueError:
                                msg.body("❌ Amount should be a number.")

                # --- Show Summary ---
                elif incoming_msg.startswith("summary"):
                    if not current_event_id:
                        msg.body("⚠️ Please switch to an event first.")
                    else:
                        parts = incoming_msg.split()
                        if len(parts) == 1:
                            date = datetime.date.today().isoformat()
                        elif len(parts) == 3 and parts[1] == "date":
                            date = parts[2]
                        elif len(parts) == 3 and parts[1] == "month":
                            month = parts[2]
                            like_pattern = month + "%"
                            c.execute("SELECT date, SUM(amount) FROM transactions WHERE event_id = %s AND date LIKE %s GROUP BY date",
                                      (current_event_id, like_pattern))
                            rows = c.fetchall()
                            if rows:
                                total = sum(r[1] for r in rows)
                                breakdown = "\n".join([f"{r[0]}: ₹{r[1]}" for r in rows])
                                msg.body(f"📆 Monthly Total: ₹{total}\n{breakdown}")
                            else:
                                msg.body(f"ℹ️ No data for {month}")
                            return str(resp), 200, {'Content-Type': 'application/xml'}
                        else:
                            msg.body("❌ Invalid format. Use:\n• summary\n• summary date YYYY-MM-DD\n• summary month YYYY-MM")
                            return str(resp), 200, {'Content-Type': 'application/xml'}

                        c.execute("SELECT SUM(amount) FROM transactions WHERE event_id = %s AND date = %s",
                                  (current_event_id, date))
                        row = c.fetchone()
                        total = row[0] if row[0] else 0
                        msg.body(f"📅 Total spent on {date}: ₹{total}")

                # --- Show Daily Items ---
                elif incoming_msg.startswith("show"):
                    if not current_event_id:
                        msg.body("⚠️ Please switch to an event first.")
                    else:
                        try:
                            parts = incoming_msg.split()
                            if len(parts) == 1:
                                date = datetime.date.today().isoformat()
                            elif len(parts) == 3 and parts[1] == "date":
                                date = parts[2]
                                datetime.datetime.strptime(date, "%Y-%m-%d")
                            else:
                                msg.body("❌ Format: show OR show date YYYY-MM-DD")
                                return str(resp), 200, {'Content-Type': 'application/xml'}

                            c.execute("SELECT item, amount FROM transactions WHERE event_id = %s AND date = %s",
                                      (current_event_id, date))
                            rows = c.fetchall()
                            if not rows:
                                msg.body(f"ℹ️ No expenses found for {date}")
                            else:
                                total = sum(r[1] for r in rows)
                                items = "\n".join([f"• {r[0]} – ₹{r[1]}" for r in rows])
                                msg.body(f"📅 Expenses for {date}:\n{items}\n💰 Total: ₹{total}")
                        except Exception as e:
                            logging.error(f"[ERROR] Show command failed: {e}")
                            msg.body("❌ Error fetching data. Check format or try again.")

                # --- Unknown Command ---
                else:
                    msg.body("🤖 Unknown command.\nTry:\n• create <event>\n• list\n• switch <event>\n• add tea 10\n• add (multi-mode)\n• summary\n• show")

    except Exception as e:
        logging.exception("Webhook failure ", e)
        msg.body("❌ Something went wrong. Please try again later.")

    return str(resp), 200, {'Content-Type': 'application/xml'}          