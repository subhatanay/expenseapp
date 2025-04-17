from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import sqlite3
import datetime

app = Flask(__name__)

# # Database init
# # def init_db():
# #     conn = sqlite3.connect('expenses.db')
# #     c = conn.cursor()
# #     c.execute('''CREATE TABLE IF NOT EXISTS expenses
# #                  (id INTEGER PRIMARY KEY, date TEXT, item TEXT, amount REAL)''')
# #     conn.commit()
# #     conn.close()

# # init_db()

# @app.route('/', methods=['GET'])
# def hello():
#     return "Hello!!! welcome to expemnse app"

# @app.route('/whatsapp', methods=['POST'])
# def whatsapp():
#     incoming_msg = request.values.get('Body', '').strip().lower()
#     resp = MessagingResponse()
#     msg = resp.message()

#     try:
#         if incoming_msg.startswith("add "):
#             # Example: add coffee 45.50
#             parts = incoming_msg.split()
#             item = parts[1]
#             amount = float(parts[2])
#             date = str(datetime.date.today())
#             # conn = sqlite3.connect('expenses.db')
#             # c = conn.cursor()
#             # c.execute("INSERT INTO expenses (date, item, amount) VALUES (?, ?, ?)", (date, item, amount))
#             # conn.commit()
#             # conn.close()
#             msg.body(f"✅ Added expense: {item} - ₹{amount}")
#         elif incoming_msg == "summary":
#             # conn = sqlite3.connect('expenses.db')
#             # c = conn.cursor()
#             # c.execute("SELECT item, amount FROM expenses WHERE date=?", (str(datetime.date.today()),))
#             # rows = c.fetchall()
#             # total = sum([row[1] for row in rows])
#             # summary = "\n".join([f"{row[0]}: ₹{row[1]}" for row in rows])
#             # msg.body(f"📊 Today's Expenses:\n{summary}\n\nTotal: ₹{total}")
#             # conn.close()
#             msg.body(f"📊 Today's Expenses:")
#         else:
#             msg.body("👋 Welcome to Expense Bot!\nUse:\n• add <item> <amount>\n• summary")
#     except Exception as e:
#         msg.body(f"❌ Error: {str(e)}")

#     return str(resp)

# Export for Vercel
def handler(environ, start_response):
    status = '200 OK'
    headers = [('Content-type', 'text/plain')]
    start_response(status, headers)
    return [b"Hello!!! Welcome to expense app"]
