from twilio.twiml.messaging_response import MessagingResponse
import urllib.parse

def handler(request):
    # request.body is bytes, decode it safely
    body = request.body.decode() if request.body else ''
    
    # Parse form data (Twilio sends application/x-www-form-urlencoded)
    parsed_body = urllib.parse.parse_qs(body)
    incoming_msg = parsed_body.get('Body', [''])[0].strip().lower()

    resp = MessagingResponse()
    msg = resp.message()

    if incoming_msg.startswith("hello"):
        msg.body("👋 Hello from Vercel + Twilio!")
    elif incoming_msg.startswith("add"):
        msg.body("✅ Add command received. DB operations can be plugged here.")
    else:
        msg.body("🤖 I didn't understand that. Try: hello, add <item> <amount>")

    # Return response as tuple: (body, status_code, headers)
    return (str(resp), 200, {"Content-Type": "application/xml"})
