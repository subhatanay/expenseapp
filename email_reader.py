import json
import os
from io import StringIO
import os.path
import base64
import re
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from email import message_from_bytes
from bs4 import BeautifulSoup
from google.auth.transport.requests import Request


SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

patterns = [
    {
        "type": "UPI",
        "regex": re.compile(
            r"Rs\.(?P<amount>\d+\.\d{2}) has been debited from account \*\*(?P<account>\d+)"
            r" to VPA (?P<vpa>\S+) (?P<merchant>[A-Za-z ]+) on (?P<date>\d{2}-\d{2}-\d{2}).*?"
            r"reference number is (?P<ref>\d+)",
            re.DOTALL
        )
    },
    {
        "type": "BANK",
        "regex": re.compile(
            r"The amount debited/drawn is\s+INR\s+(?P<amount>[\d,]+\.\d{2}) from your account XX(?P<account>\d+)"
            r".+?on\s+(?P<date>\d{2}-[A-Z]{3}-\d{4}) on account of (?P<merchant>[A-Z0-9_]+)",
            re.DOTALL
        )
    }
]



def authenticate_gmail():
    creds = None
    token_path = 'token.json'

    # Write creds.json from GitHub Secrets
    if 'GMAIL_CREDS_JSON' in os.environ:
        with open('creds.json', 'w') as f:
            f.write(os.environ['GMAIL_CREDS_JSON'])

    # Write token.json if provided
    if 'TOKEN_JSON' in os.environ:
        with open(token_path, 'w') as f:
            f.write(os.environ['TOKEN_JSON'])

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise Exception("Token.json missing or needs manual OAuth authorization.")
    return creds

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
        decoded_bytes = base64.urlsafe_b64decode(data)
        decoded_text = decoded_bytes.decode('utf-8', errors='ignore')
        if mime_type == 'text/html':
            soup = BeautifulSoup(decoded_text, 'html.parser')
            return soup.get_text(separator='\n')
        return decoded_text
    return None

def parse_transaction_details(body):
    body = body.replace('\n', '')
    for pattern in patterns:
        match = pattern["regex"].search(body)
        if match:
            data = match.groupdict()
            if "amount" in data:
                data["amount"] = float(data["amount"].replace(",", ""))
            data["type"] = pattern["type"]
            for key in ["vpa", "ref"]:
                if key not in data:
                    data[key] = None
            return data
    return None

def get_transaction_emails(service):
    result = service.users().messages().list(
        userId='me',
        q='from:alerts@hdfcbank.net'
    ).execute()

    messages = result.get('messages', [])
    transactions = []

    for msg in messages[:10]:  # Recent 10 emails
        msg_data = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
        payload = msg_data.get('payload', {})
        email_text = extract_text_from_payload(payload)
        if email_text:
            parsed = parse_transaction_details(email_text)
            if parsed:
                transactions.append(parsed)
                print(transactions)
            
    return transactions

def read_emails_api():
    try:
        creds = authenticate_gmail()
        service = build('gmail', 'v1', credentials=creds)
        data = get_transaction_emails(service)
    except Exception as e:
        print(e)

if __name__ == '__main__':
    read_emails_api()
