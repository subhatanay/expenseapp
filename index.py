from flask import Flask, request, jsonify
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

                elif incoming_msg == "show pending":
                    # Show staged transactions (pending ones) for the user
                    c.execute("SELECT txn_id, amount, date FROM transactions WHERE user_id = %s AND event_id = %s AND iten IS NULL", 
                              (user_id, current_event_id))
                    rows = c.fetchall()
                    if rows:
                        # Store the transaction IDs and map them to numbers
                        # We'll create a mapping of the transaction number to the actual txn_id
                        txn_map = {}
                        pending_list = "\n".join([f"{idx+1}. ₹{row[2]} on {row[3]} at {row[1]} [TXN#{row[0]}]" 
                                                 for idx, row in enumerate(rows)])
                        for idx, row in enumerate(rows):
                            txn_map[idx + 1] = row[0]  # Map number to txn_id

                        # Save the mapping in the  database to use later
                        set_user_setting(c, user_id, 'pending_txn_map', json.dumps(txn_map))

                        msg.body(f"📋 Pending Transactions:\n{pending_list}\n\nReply with:\ntag <number> <category>\nExample: tag 2 groceries")
                    else:
                        msg.body("⚠️ No pending transactions found.")

                elif incoming_msg.startswith("tag"):
                    parts = incoming_msg.split()
                    if len(parts) == 3:
                        try:
                            txn_number = int(parts[1])  # Get the transaction number
                            category = parts[2]  # Get the category

                            # Fetch the transaction ID from the user settings (txn_map)
                            txn_map = get_user_setting(c, user_id, 'pending_txn_map', {})
                            if txn_map:
                                txn_map = json.loads(txn_map)
                                
                            txn_id = txn_map.get(txn_number)

                            if txn_id:
                                # Update the transaction with the category tag
                                c.execute("UPDATE transactions SET category = %s WHERE txn_id = %s", (category, txn_id))
                                msg.body(f"Tagged TXN#{txn_id} with category '{category}' ✅")
                            else:
                                msg.body("⚠️ Transaction not found. Please check the number and try again.")
                        except ValueError:
                            msg.body("❌ Invalid input. Please use the format: tag <number> <category>")
                    else:
                        msg.body("❌ Invalid format. Please use the format: tag <number> <category>")

                elif incoming_msg.startswith("show"):
                    if not current_event_id:
                        msg.body("⚠️ Please switch to an event first using `switch <event_name>`")
                    else:
                        parts = incoming_msg.split()
                        try:
                            if len(parts) == 1:
                                show_date = datetime.date.today().isoformat()
                            elif len(parts) == 3 and parts[1] == "date":
                                show_date = parts[2]
                                datetime.datetime.strptime(show_date, '%Y-%m-%d')
                            else:
                                msg.body("❌ Invalid format. Use:\n• show\n• show date YYYY-MM-DD")
                                return str(resp), 200, {'Content-Type': 'application/xml'}

                            c.execute("SELECT item, amount FROM transactions WHERE event_id = %s AND date = %s and user_id", (current_event_id, show_date, user_id))
                            rows = c.fetchall()
                            if not rows:
                                msg.body(f"ℹ️ No expenses found for {show_date}")
                            else:
                                total = sum([r[1] for r in rows])
                                item_list = "\n".join([f"• {r[0]} – ₹{r[1]}" for r in rows])
                                msg.body(f"📅 Expenses for {show_date}:\n{item_list}\n💰 Total: ₹{total}")
                        except Exception as e:
                            logging.error(f"[ERROR] Show command failed: {e}")
                            msg.body("❌ Error fetching data. Check format or try again later.")

                elif incoming_msg.startswith("summary"):
                    if not current_event_id:
                        msg.body("⚠️ Please switch to an event first using `switch <event_name>`")
                    else:
                        parts = incoming_msg.split()
                        if len(parts) == 1:
                            today = datetime.date.today().isoformat()
                            c.execute("SELECT SUM(amount) FROM transactions WHERE event_id = %s AND date = %s and user_id = %s", (current_event_id, today, user_id))
                            row = c.fetchone()
                            total = row[0] if row[0] else 0
                            msg.body(f"📅 Total spent today ({today}): ₹{total}")
                        elif len(parts) == 3 and parts[1] == "date":
                            date = parts[2]
                            c.execute("SELECT SUM(amount) FROM transactions WHERE event_id = %s AND date = %s and user_id = %s", (current_event_id, date, user_id))
                            row = c.fetchone()
                            total = row[0] if row[0] else 0
                            msg.body(f"📅 Total spent on {date}: ₹{total}")
                        elif len(parts) == 3 and parts[1] == "month":
                            month = parts[2]
                            like_pattern = month + "%"
                            c.execute("SELECT date, SUM(amount) FROM transactions WHERE event_id = %s AND date LIKE %s and user_id = %s GROUP BY date", (current_event_id, like_pattern, user_id))
                            rows = c.fetchall()
                            if rows:
                                total = sum([row[1] for row in rows])
                                lines = [f"{row[0]}: ₹{row[1]}" for row in rows]
                                msg.body(f"📆 Monthly Total for {month}: ₹{total}\n\n📊 Daily Breakdown:\n" + "\n".join(lines))
                            else:
                                msg.body(f"ℹ️ No transactions found for month {month}")
                        else:
                            msg.body("❌ Invalid summary format.\nTry:\n• summary\n• summary date YYYY-MM-DD\n• summary month YYYY-MM")

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


@app.route('/api/staged-transactions', methods=['POST'])
def add_staged_transaction():
    data = request.json
    required_fields = ["user_id", "transaction_date", "amount", "action"]

    # Validate required fields
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        event_id = 1  # Hardcoded or fetch dynamically if needed
        created_at = datetime.now()

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO transactions 
                        (event_id, date, action, item, amount, user_id, created_at, merchant, transaction_ref)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING tran_id
                """, (
                    event_id,
                    data["transaction_date"],
                    data["action"],
                    "",  # item left blank
                    data["amount"],
                    data["user_id"],
                    created_at,
                    data.get("merchant"),
                    data.get("transaction_ref")
                ))
                new_tran_id = cur.fetchone()[0]
                conn.commit()
                return jsonify({"tran_id": new_tran_id, "message": "Transaction added successfully"}), 201

    except Exception as e:
        logging.exception(e)
        return jsonify({"error": "Internal server error"}), 500

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