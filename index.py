from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import urllib.parse
import psycopg2
import datetime
import os
import logging
import json

app = Flask(__name__)

# Setup logging
logging.basicConfig(level=logging.INFO)

# Neon DB connection URL from environment variable
DATABASE_URL = os.getenv("DATABASE_URL")

# DB connection
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode='require')


@app.route('/', methods=['GET'])
def hello():
    return "Hello! Welcome to the WhatsApp Expense App", 200, {'Content-Type': 'text/plain'}

@app.route('/api/email-configs', methods=['GET'])
def get_email_configs():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        u.id as user_id, u.name, u.phone_number,
                        ue.email, ue.provider, ue.token, ue.id as email_config_id,
                        ep.type, ep.pattern_text, ep.source
                    FROM users u
                    JOIN user_email_configs ue ON u.id = ue.user_id
                    JOIN user_email_patterns uep ON uep.user_email_config_id = ue.id AND uep.active = TRUE
                    JOIN email_patterns ep ON ep.id = uep.email_pattern_id
                """)
                rows = cur.fetchall()

                result_map = {}
                for row in rows:
                    user_id = row[0]
                    if user_id not in result_map:
                        result_map[user_id] = {
                            "user_id": user_id,
                            "name": row[1],
                            "phone_number": row[2],
                            "email": row[3],
                            "provider": row[4],
                            "token": row[5],
                            "patterns": []
                        }
                    result_map[user_id]["patterns"].append({
                        "type": row[7],
                        "pattern_text": row[8],
                        "source": row[9]
                    })

                return jsonify(list(result_map.values()))

    except Exception as e:
        logging.exception("Error fetching email config data")
        return jsonify({"error": "Internal server error"}), 500
        

@app.route('/', methods=['POST'])
def twilio_webhook():
    body_str = request.get_data(as_text=True)
    data = urllib.parse.parse_qs(body_str)
    incoming_msg = data.get('Body', [''])[0].strip().lower()
    phone_number = data.get('From', [''])[0]
    phone_number = phone_number.replace('whatsapp:', '')

    resp = MessagingResponse()
    msg = resp.message()

    logging.info(f"Received message from {phone_number}: {incoming_msg}")
    try:
        with get_conn() as conn:
            with conn.cursor() as c:
                user_info = get_user_by_phonenumber(phone_number, c)
                if not user_info:
                    msg.body("❌ User not found. Please contact administrator.")
                    return str(resp), 200, {'Content-Type': 'application/xml'}

                user_id = user_info['user_id']

                user_settings = get_user_settings(c, user_id)
                current_event_id = user_settings.get("current_event_id")
                pending_add = user_settings.get("pending_add", False)
                add_buffer = user_settings.get("add_buffer", [])

                if incoming_msg.startswith("create "):
                    event_name = incoming_msg.split("create ", 1)[1].strip()
                    try:
                        c.execute("INSERT INTO events (event_name, user_id) VALUES (%s, %s)", (event_name, user_id,))
                        msg.body(f"✅ Event '{event_name}' created.")
                    except psycopg2.Error as e:
                        if e.pgcode == '23505':  # UniqueViolation
                            conn.rollback()
                            msg.body(f"⚠️ Event '{event_name}' already exists.")
                        else:
                            logging.exception("Error creating event")
                            msg.body("❌ Failed to create event. Please try again.")

                elif incoming_msg == "list":
                    c.execute("SELECT event_name FROM events WHERE user_id = %s", (user_id,))
                    rows = c.fetchall()
                    if rows:
                        event_list = "\n".join([f"🔹 {row[0]}" for row in rows])
                        msg.body(f"📋 Your Events:\n{event_list}")
                    else:
                        msg.body("⚠️ No events found. Create one using `create <event_name>`.")

                elif incoming_msg.startswith("switch "):
                    event_name = incoming_msg.split("switch ", 1)[1].strip()
                    c.execute("SELECT event_id FROM events WHERE event_name = %s AND user_id = %s", (event_name, user_id,))
                    row = c.fetchone()
                    if row:
                        current_event_id = row[0]
                        set_user_setting(c, user_id, "current_event_id", current_event_id)
                        msg.body(f"🔄 Switched to event: {event_name}")
                    else:
                        msg.body("⚠️ Event not found. Please create it first.")

                elif incoming_msg.startswith("add"):
                    parts = incoming_msg.split()
                    if not current_event_id:
                        msg.body("⚠️ Please switch to an event first using `switch <event_name>`")
                    elif len(parts) == 1:
                        set_multiple_settings(c, user_id, {"pending_add": True, "add_buffer": []})
                        msg.body("📝 Add mode started. Send item and amount like:\n`tea 10`\nWhen done, type `done`.")
                    elif len(parts) >= 3:
                        item = parts[1]
                        try:
                            amount = float(parts[2])
                            date = str(datetime.date.today())
                            c.execute("INSERT INTO transactions (event_id, date, action, item, amount, user_id) VALUES (%s, %s, %s, %s, %s, %s)",
                                      (current_event_id, date, 'add', item, amount, user_id))
                            msg.body(f"💸 Added: {item} - ₹{amount}")
                        except Exception:
                            logging.exception("Failed to add transaction")
                            msg.body("❌ Amount should be a number. Try again.")
                    else:
                        msg.body("❌ Usage: add <item> <amount>")

                elif pending_add:
                    if incoming_msg == "done":
                        if not add_buffer:
                            msg.body("⚠️ No entries added.")
                        else:
                            date = str(datetime.date.today())
                            try:
                                for item, amount in add_buffer:
                                    c.execute("INSERT INTO transactions (event_id, date, action, item, amount, user_id) VALUES (%s, %s, %s, %s, %s, %s)",
                                              (current_event_id, date, 'add', item, amount, user_id))
                                msg.body(f"✅ {len(add_buffer)} items added.\n🛑 Exiting add mode.")
                            except Exception:
                                logging.exception("Error inserting buffered transactions")
                                msg.body("❌ Failed to save items. Try again later.")
                        set_multiple_settings(c, user_id, {"pending_add": False, "add_buffer": []})
                    else:
                        parts = incoming_msg.split()
                        if len(parts) != 2:
                            msg.body("❌ Format should be: `item amount`\nOr type `done` to finish.")
                        else:
                            item = parts[0]
                            try:
                                amount = float(parts[1])
                                add_buffer.append((item, amount))
                                set_user_setting(c, user_id, "add_buffer", add_buffer)
                                msg.body(f"➕ Staged: {item} ₹{amount}")
                            except Exception:
                                logging.exception("Failed to parse buffer item")
                                msg.body("❌ Amount should be a number. Try again.")

                elif incoming_msg.startswith("summary"):
                    # Summary command handling unchanged
                    pass  # Fill in with previous logic

                elif incoming_msg.startswith("show"):
                    # Show command handling unchanged
                    pass  # Fill in with previous logic

                else:
                    msg.body(
    "🤖 I didn't understand that.\n\n"
    "Try:\n"
    "• create <event>\n"
    "• list\n"
    "• switch <event>\n"
    "• add <item> <amount>\n"
    "• add (then items... then `done`)\n"
    "• summary\n"
    "• show"
)

    except Exception:
        logging.exception("Exception in Twilio webhook handler")
        msg.body("❌ Something went wrong. Please try again later.")

    return str(resp), 200, {'Content-Type': 'application/xml'}


# ---------- User Settings Utilities ----------
def get_user_setting(cur, user_id, key, default=None):
    try:
        cur.execute("SELECT value FROM user_settings WHERE user_id = %s AND key = %s", (user_id, key))
        row = cur.fetchone()
        return json.loads(row[0]) if row else default
    except Exception:
        logging.exception("Error fetching user setting")
        return default

def get_user_settings(cur, user_id):
    try:
        cur.execute("SELECT key, value FROM user_settings WHERE user_id = %s", (user_id,))
        rows = cur.fetchall()
        result = {}
        for key, value in rows:
            try:
                result[key] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                result[key] = value
        return result
    except Exception:
        logging.exception("Error fetching user settings")
        return {}

def set_user_setting(cur, user_id, key, value):
    try:
        value_str = json.dumps(value)
        cur.execute("""
            INSERT INTO user_settings (user_id, key, value)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value
        """, (user_id, key, value_str))
    except Exception:
        logging.exception("Error setting user setting")

def set_multiple_settings(cur, user_id, settings_dict):
    for key, value in settings_dict.items():
        set_user_setting(cur, user_id, key, value)

def get_user_by_phonenumber(phone_number, cur):
    try:
        cur.execute("SELECT id FROM users WHERE phone_number = %s", (phone_number,))
        row = cur.fetchone()
        return {"user_id": row[0]} if row else None
    except Exception:
        logging.error("Error fetching user info", exc_info=True)
        return None