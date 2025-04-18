from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import urllib.parse

app = Flask(__name__)

@app.route('/', methods=['POST'])
def twilio_webhook():
    body_str = request.get_data(as_text=True)
    data = urllib.parse.parse_qs(body_str)
    incoming_msg = data.get('Body', [''])[0].strip().lower()

    resp = MessagingResponse()
    msg = resp.message()

    if incoming_msg.startswith("hello"):
        msg.body("👋 Hello from Vercel + Twilio!")
    elif incoming_msg.startswith("add"):
        msg.body("✅ Add command received. DB operations can be plugged here.")
    else:
        msg.body("🤖 I didn't understand that. Try: hello, add <item> <amount>")

    return str(resp), 200, {'Content-Type': 'application/xml'}
