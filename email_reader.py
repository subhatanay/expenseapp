import os
import base64
import re
import requests
import logging
from datetime import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from email import message_from_bytes
from bs4 import BeautifulSoup
from google.auth.transport.requests import Request

# === Logging Setup ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
API_BASE = API_BASE = os.getenv("API_BASE_URL", "https://expenseapp-git-main-subhajits-projects-82cd4a28.vercel.app")


def sanitize_text(text):
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text)

def authenticate_gmail(token_str):
    try:
        with open('token.json', 'w') as f:
            f.write(token_str)
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise Exception("Invalid Gmail token or no refresh token.")
        return creds
    except Exception as e:
        logger.error("Authentication failed: %s", e)
        raise

def extract_text_from_payload(payload):
    parts = payload.get('parts')
    data = None
    mime_type = None

    if parts:
        for part in parts:
            if part.get('mimeType') == 'text/plain':
                data = part['body'].get('data')
                mime_type = 'text/plain'
                break
        if not data:
            for part in parts:
                if part.get('mimeType') == 'text/html':
                    data = part['body'].get('data')
                    mime_type = 'text/html'
                    break
    else:
        data = payload['body'].get('data')
        mime_type = payload.get('mimeType')

    if data:
        try:
            decoded_bytes = base64.urlsafe_b64decode(data)
            decoded_text = decoded_bytes.decode('utf-8', errors='ignore')
            if mime_type == 'text/html':
                soup = BeautifulSoup(decoded_text, 'html.parser')
                return soup.get_text(separator='\n')
            return decoded_text
        except Exception as e:
            logger.warning("Failed to decode email body: %s", e)
    return None

def parse_transaction_details(body, regex_pattern, pattern_type):
    body = body.replace('\n', '')
    pattern = re.compile(regex_pattern, re.DOTALL)
    match = pattern.search(body)
    if match:
        data = match.groupdict()
        try:
            if "amount" in data:
                data["amount"] = float(data["amount"].replace(",", ""))
            data["transaction_ref"] = data.get("ref")
            data["merchant"] = data.get("merchant", "UNKNOWN")
            data["action"] = "CREDIT" if "CREDIT" in pattern_type.upper() else "DEBIT"
            
            try:
                # Convert from "dd-mm-yy" to "yyyy-mm-dd"
                parsed_date = datetime.strptime(data.get("date"), "%d-%m-%y")
                data["transaction_date"] = parsed_date.strftime("%Y-%m-%d")
            except Exception as e:
                logger.warning("Invalid date format '%s', using today's date. Error: %s", data.get("date"), e)
                data["transaction_date"] = datetime.today().strftime("%Y-%m-%d")
            return data
        except Exception as e:
            logger.error("Error parsing transaction details: %s", e)
    return None

def get_sender_by_pattern_type(pattern_type):
    if pattern_type in ["UPI_DEBIT", "UPI_CREDIT", "BANK_CREDIT", "UPI_CREDIT"]:
        return "alerts@hdfcbank.net"
    return "alerts@hdfcbank.net"   # Default fallback

def poll_and_process():
    try:
        email_configs = requests.get(f"{API_BASE}/api/email-configs").json()
    except Exception as e:
        logger.error("Failed to fetch email configs: %s", e)
        return

    for config in email_configs:
        credit_count = 0
        debit_count = 0
        logger.info(f"\n======== starting trasactional fetch for user {config['user_id']} ======\n")
        user_id = config['user_id']
        email_config_id = config['email_config_id']
        token = config['token']
        last_fetch_time = config.get('last_email_fetch_time')
        last_fetch_id = config.get('last_fetched_email_id')
        patterns = config.get('patterns', [])

        if not token:
            logger.warning("No token available for user_id=%s", user_id)
            continue

        try:
            creds = authenticate_gmail(token)
            service = build('gmail', 'v1', credentials=creds)

            # Build unified Gmail query for all patterns
            senders = set(get_sender_by_pattern_type(p['type']) for p in patterns)
            query = " OR ".join([f"from:{sender}" for sender in senders])

            if last_fetch_time:
                try:
                    after_ts = int(datetime.strptime(last_fetch_time, "%a, %d %b %Y %H:%M:%S %Z").timestamp())
                    query += f" after:{after_ts}"
                except Exception as e:
                    logger.warning("Could not parse last_email_fetch_time: %s", e)

            logger.info("Fetching messages for user_id=%s with query=%s", user_id, query)
            messages_result = service.users().messages().list(
                userId='me', q=query, maxResults=50
            ).execute()

            messages = messages_result.get('messages', [])
            logger.info("Found %d emails for user_id=%s", len(messages), user_id)

            for msg in messages:
                msg_id = msg['id']

                if msg_id == last_fetch_id:
                    logger.info("Reached last fetched email. Skipping further.")
                    break

                try:
                    msg_data = service.users().messages().get(
                        userId='me', id=msg_id, format='full'
                    ).execute()

                    payload = msg_data.get('payload', {})
                    email_text = extract_text_from_payload(payload)
                    email_text = sanitize_text(email_text)

                    if not email_text:
                        logger.warning("Empty email for message ID: %s", msg_id)
                        continue

                    matched = False
                    for pattern in patterns:
                        regex = pattern["pattern_text"]
                        pattern_type = pattern["type"]
                        parsed = parse_transaction_details(email_text, regex, pattern_type)
                        if parsed:
                            
                            parsed["user_id"] = user_id
                            matched = True
                            print(parsed)

                            response = requests.post(
                                f"{API_BASE}/api/staged-transactions",
                                json={
                                    "transaction_date": parsed["transaction_date"],
                                    "action": parsed["action"],
                                    "amount": parsed["amount"],
                                    "user_id": parsed["user_id"],
                                    "merchant": parsed.get("merchant"),
                                    "transaction_ref": parsed.get("transaction_ref")
                                }
                            )

                            if response.status_code == 201:
                                logger.info("Transaction saved for user_id=%s", user_id)
                                if parsed["action"].lower() == "credit":
                                    credit_count += 1
                                elif parsed["action"].lower() == "debit":
                                    debit_count += 1

                                update_data = {
                                    "last_fetched_email_id": msg_id,
                                    "last_email_fetch_time": datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
                                }
                                requests.put(
                                    f"{API_BASE}/api/users/{user_id}/email-configs/{email_config_id}",
                                    json=update_data
                                )
                            else:
                                logger.error("Failed to save transaction: %s", response.text)
                            break  # Stop trying patterns after a successful match

                    if not matched:
                        print(email_text)
                        logger.info("No matching pattern found for message ID: %s", msg_id)

                except Exception as e:
                    logger.error("Error processing message ID %s: %s", msg_id, e)

        except Exception as e:
            logger.error("Error processing user_id %s: %s", user_id, e)

        
        alert_user_for_transaction(user_id, credit_count, debit_count)
        logger.info(f"\n======== completed trasactional fetch for user {config['user_id']} ======\n")


def alert_user_for_transaction(user_id, credit_count, debit_count):
    if credit_count > 0 or debit_count > 0:
        try:
            notify_payload = {
                "message": f"💰 You received {credit_count} credit(s) and {debit_count} debit(s) added to your account.\nType 'show pending' to review them.",
                 
            }

            notify_resp = requests.post(
                f"{API_BASE}/api/users/{user_id}/notify-whatsapp",
                json=notify_payload
            )

            if notify_resp.status_code == 200:
                logger.info("Notification sent to user_id=%s", user_id)
            else:
                logger.warning("Failed to notify user_id=%s: %s", user_id, notify_resp.text)

        except Exception as e:
            logger.error("Error sending WhatsApp notification to user_id=%s: %s", user_id, e)
            

if __name__ == '__main__':
    poll_and_process()
