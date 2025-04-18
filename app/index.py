from twilio.twiml.messaging_response import MessagingResponse

def handler(request, response):
    # Safely extract the body if exists
    body = request.body.decode() if request.body else ''
    
    # Twilio sends data in `application/x-www-form-urlencoded` format
    import urllib.parse
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

    return response.send(str(resp), status=200, content_type="application/xml")
