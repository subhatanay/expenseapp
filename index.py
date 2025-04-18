from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs
from twilio.twiml.messaging_response import MessagingResponse
import sqlite3
import datetime

# Database setup
def init_db():
    conn = sqlite3.connect('/tmp/expenses.db')  # /tmp works on Vercel
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS expenses
                 (id INTEGER PRIMARY KEY, date TEXT, item TEXT, amount REAL)''')
    conn.commit()
    conn.close()

init_db()

class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type','text/plain')
        self.end_headers()
        self.wfile.write('Hello, world!'.encode('utf-8'))
        return


    def do_POST(self):
        print("Got request to add expense")
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length).decode()
        data = parse_qs(post_data)
        body = data.get('Body', [''])[0].strip().lower()

        resp = MessagingResponse()
        msg = resp.message()

        print("----- Got data from request")
        try:
            if body.startswith("add "):
                # Format: add coffee 30
                parts = body.split()
                if len(parts) < 3:
                    msg.body("❌ Usage: add <item> <amount>")
                else:
                    item = parts[1]
                    amount = float(parts[2])
                    date = str(datetime.date.today())
                    conn = sqlite3.connect('/tmp/expenses.db')
                    c = conn.cursor()
                    c.execute("INSERT INTO expenses (date, item, amount) VALUES (?, ?, ?)", (date, item, amount))
                    conn.commit()
                    conn.close()
                    msg.body(f"✅ Added: {item} - ₹{amount}")
            elif body == "summary":
                conn = sqlite3.connect('/tmp/expenses.db')
                c = conn.cursor()
                c.execute("SELECT item, amount FROM expenses WHERE date=?", (str(datetime.date.today()),))
                rows = c.fetchall()
                total = sum([row[1] for row in rows])
                summary = "\n".join([f"{row[0]}: ₹{row[1]}" for row in rows])
                msg.body(f"📊 Today's Expenses:\n{summary}\n\nTotal: ₹{total}")
                conn.close()
            else:
                msg.body("👋 Welcome to Expense Bot!\nUse:\n• add <item> <amount>\n• summary")
        except Exception as e:
            msg.body(f"❌ Error: {str(e)}")

        self.send_response(200)
        self.send_header('Content-type', 'application/xml')
        self.end_headers()
        self.wfile.write(str(resp).encode('utf-8'))
