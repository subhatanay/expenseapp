from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import urllib.parse
import sqlite3
import datetime
import os

app = Flask(__name__)

# Set DB path to Vercel’s writable directory
DB_PATH = "/tmp/expenses.db"

def init_db():
    if not os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS expenses
                     (id INTEGER PRIMARY KEY, date TEXT, item TEXT, amount REAL)''')
        conn.commit()
        conn.close()

init_db()

@app.route('/', methods=['GET'])
def health_check():
    return 'Hello, Welcome to Expense bot!', 200, {'Content-Type': 'text/plain'}

@app.route('/', methods=['POST'])
def twilio_webhook():
    body_str = request.get_data(as_text=True)
    data = urllib.parse.parse_qs(body_str)
    incoming_msg = data.get('Body', [''])[0].strip().lower()

    resp = MessagingResponse()
    msg = resp.message()

    try:
        if incoming_msg.startswith("add "):
            # Example: add tea 20
            parts = incoming_msg.split()
            if len(parts) < 3:
                msg.body("❌ Usage: add <item> <amount>")
            else:
                item = parts[1]
                amount = float(parts[2])
                date = str(datetime.date.today())

                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("INSERT INTO expenses (date, item, amount) VALUES (?, ?, ?)", (date, item, amount))
                conn.commit()
                conn.close()

                msg.body(f"✅ Added: {item} - ₹{amount}")

        elif incoming_msg == "summary":
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT item, amount FROM expenses WHERE date=?", (str(datetime.date.today()),))
            rows = c.fetchall()
            conn.close()

            if rows:
                total = sum([r[1] for r in rows])
                summary = "\n".join([f"{r[0]}: ₹{r[1]}" for r in rows])
                msg.body(f"📊 Today's Expenses:\n{summary}\n\nTotal: ₹{total}")
            else:
                msg.body("📭 No expenses recorded today.")

        else:
            msg.body("👋 Welcome to Expense Bot!\nTry:\n• add <item> <amount>\n• summary")

    except Exception as e:
        msg.body(f"❌ Error: {str(e)}")

    return str(resp), 200, {'Content-Type': 'application/xml'}