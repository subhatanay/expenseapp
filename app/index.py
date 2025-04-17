import sqlite3
import datetime
from urllib.parse import parse_qs
from twilio.twiml.messaging_response import MessagingResponse

def init_db():
    conn = sqlite3.connect('/tmp/expenses.db')  # use /tmp for Vercel serverless
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS expenses
                 (id INTEGER PRIMARY KEY, date TEXT, item TEXT, amount REAL)''')
    conn.commit()
    conn.close()

init_db()

def handler(environ, start_response):
    method = environ['REQUEST_METHOD']
    path = environ.get('PATH_INFO', '/')
    status = '200 OK'
    headers = [('Content-type', 'text/xml')]

    if method == 'POST' and path == '/':
        try:
            # Get POST data
            request_body_size = int(environ.get('CONTENT_LENGTH', 0))
            request_body = environ['wsgi.input'].read(request_body_size)
            data = parse_qs(request_body.decode())

            incoming_msg = data.get('Body', [''])[0].strip().lower()

            resp = MessagingResponse()
            msg = resp.message()

            if incoming_msg.startswith("add "):
                parts = incoming_msg.split()
                item = parts[1]
                amount = float(parts[2])
                date = str(datetime.date.today())

                conn = sqlite3.connect('/tmp/expenses.db')
                c = conn.cursor()
                c.execute("INSERT INTO expenses (date, item, amount) VALUES (?, ?, ?)", (date, item, amount))
                conn.commit()
                conn.close()

                msg.body(f"✅ Added expense: {item} - ₹{amount}")

            elif incoming_msg == "summary":
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

            start_response(status, headers)
            return [str(resp).encode()]

        except Exception as e:
            start_response('500 INTERNAL SERVER ERROR', headers)
            return [f"<Response><Message>Error: {str(e)}</Message></Response>".encode()]
    
    elif method == 'GET' and path == '/':
        headers = [('Content-type', 'text/plain')]
        start_response(status, headers)
        return [b"Hello!!! Welcome to expense app"]
    
    else:
        start_response('404 NOT FOUND', [('Content-type', 'text/plain')])
        return [b'404 Not Found']