from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import urllib.parse
import sqlite3
import datetime

app = Flask(__name__)

# DB Setup
conn = sqlite3.connect("/tmp/expense.db", check_same_thread=False)
c = conn.cursor()

# Create Tables
c.execute('''CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_name TEXT UNIQUE
)''')

c.execute('''CREATE TABLE IF NOT EXISTS transactions (
    tran_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    date TEXT,
    action TEXT,
    item TEXT,
    amount REAL
)''')

conn.commit()

# App state (temporary for one-user test setup)
current_event_id = None
pending_add = False
add_buffer = []  # stores list of (item, amount)


@app.route('/', methods=['GET'])
def hello():
    return "Hello! Welcome to the WhatsApp Expense App", 200, {'Content-Type': 'text/plain'}


@app.route('/', methods=['POST'])
def twilio_webhook():
    global current_event_id, pending_add, add_buffer

    body_str = request.get_data(as_text=True)
    data = urllib.parse.parse_qs(body_str)
    incoming_msg = data.get('Body', [''])[0].strip().lower()

    resp = MessagingResponse()
    msg = resp.message()

    if incoming_msg.startswith("create "):
        event_name = incoming_msg.split("create ", 1)[1].strip()
        try:
            c.execute("INSERT INTO events (event_name) VALUES (?)", (event_name,))
            conn.commit()
            msg.body(f"✅ Event '{event_name}' created.")
        except sqlite3.IntegrityError:
            msg.body(f"⚠️ Event '{event_name}' already exists.")

    elif incoming_msg == "list":
        c.execute("SELECT event_name FROM events")
        rows = c.fetchall()
        if rows:
            event_list = "\n".join([f"🔹 {row[0]}" for row in rows])
            msg.body(f"📋 Your Events:\n{event_list}")
        else:
            msg.body("⚠️ No events found. Create one using `create <event_name>`.")

    elif incoming_msg.startswith("switch "):
        event_name = incoming_msg.split("switch ", 1)[1].strip()
        c.execute("SELECT event_id FROM events WHERE event_name = ?", (event_name,))
        row = c.fetchone()
        if row:
            current_event_id = row[0]
            msg.body(f"🔄 Switched to event: {event_name}")
        else:
            msg.body("⚠️ Event not found. Please create it first.")

    elif incoming_msg.startswith("add"):
        if not current_event_id:
            msg.body("⚠️ Please switch to an event first using `switch <event_name>`")
        elif len(incoming_msg.split()) == 1:
            # Just "add" – start multi-step entry mode
            pending_add = True
            add_buffer = []
            msg.body("📝 Add mode started. Send item and amount like:\n`tea 10`\nWhen done, type `done`.")
        else:
            # Single-line add
            parts = incoming_msg.split()
            if len(parts) < 3:
                msg.body("❌ Usage: add <item> <amount>")
            else:
                item = parts[1]
                try:
                    amount = float(parts[2])
                    date = str(datetime.date.today())
                    c.execute("INSERT INTO transactions (event_id, date, action, item, amount) VALUES (?, ?, ?, ?, ?)",
                              (current_event_id, date, 'add', item, amount))
                    conn.commit()
                    msg.body(f"💸 Added: {item} - ₹{amount}")
                except ValueError:
                    msg.body("❌ Amount should be a number. Try again.")

    elif pending_add:
        if incoming_msg == "done":
            if not add_buffer:
                msg.body("⚠️ No entries added.")
            else:
                date = str(datetime.date.today())
                for item, amount in add_buffer:
                    c.execute("INSERT INTO transactions (event_id, date, action, item, amount) VALUES (?, ?, ?, ?, ?)",
                              (current_event_id, date, 'add', item, amount))
                conn.commit()
                msg.body(f"✅ {len(add_buffer)} items added.\n🛑 Exiting add mode.")
            pending_add = False
            add_buffer = []
        else:
            parts = incoming_msg.split()
            if len(parts) != 2:
                msg.body("❌ Format should be: `item amount`\nOr type `done` to finish.")
            else:
                item = parts[0]
                try:
                    amount = float(parts[1])
                    add_buffer.append((item, amount))
                    msg.body(f"➕ Staged: {item} ₹{amount}")
                except ValueError:
                    msg.body("❌ Amount should be a number. Try again.")

    elif incoming_msg.startswith("summary"):
        if not current_event_id:
            msg.body("⚠️ Please switch to an event first using `switch <event_name>`")
        else:
            parts = incoming_msg.split()

            # Case 1: "summary"
            if len(parts) == 1:
                today = datetime.date.today().isoformat()
                c.execute("SELECT SUM(amount) FROM transactions WHERE event_id = ? AND date = ?", (current_event_id, today))
                row = c.fetchone()
                total = row[0] if row[0] else 0
                msg.body(f"📅 Total spent today ({today}): ₹{total}")

            # Case 2: "summary date YYYY-MM-DD"
            elif len(parts) == 3 and parts[1] == "date":
                try:
                    date = parts[2]
                    c.execute("SELECT SUM(amount) FROM transactions WHERE event_id = ? AND date = ?", (current_event_id, date))
                    row = c.fetchone()
                    total = row[0] if row[0] else 0
                    msg.body(f"📅 Total spent on {date}: ₹{total}")
                except Exception:
                    msg.body("❌ Invalid format. Use: summary date YYYY-MM-DD")

            # Case 3: "summary month YYYY-MM"
            elif len(parts) == 3 and parts[1] == "month":
                try:
                    month = parts[2]
                    like_pattern = month + "%"
                    c.execute("SELECT date, SUM(amount) FROM transactions WHERE event_id = ? AND date LIKE ? GROUP BY date", (current_event_id, like_pattern))
                    rows = c.fetchall()
                    if rows:
                        total = sum([row[1] for row in rows])
                        lines = [f"{row[0]}: ₹{row[1]}" for row in rows]
                        msg.body(f"📆 Monthly Total for {month}: ₹{total}\n\n📊 Daily Breakdown:\n" + "\n".join(lines))
                    else:
                        msg.body(f"ℹ️ No transactions found for month {month}")
                except Exception:
                    msg.body("❌ Invalid format. Use: summary month YYYY-MM")

            else:
                msg.body("❌ Invalid summary format.\nTry:\n• summary\n• summary date YYYY-MM-DD\n• summary month YYYY-MM")

    else:
        msg.body("🤖 I didn't understand that.\nTry:\n• create <event>\n• list\n• switch <event>\n• add <item> <amount>\n• add (then items... then `done`)\n• summary")

    return str(resp), 200, {'Content-Type': 'application/xml'}
