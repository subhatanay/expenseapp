import requests
import pandas as pd
import json
import os
import io
import logging
from datetime import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google.auth.transport.requests import Request

# Configuration
API_BASE = "https://expenseapp-git-main-subhajits-projects-82cd4a28.vercel.app"
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly', 'https://www.googleapis.com/auth/drive']
FOLDER_NAME = "ExpenseReports"

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def get_drive_service(token_json):
    creds = Credentials.from_authorized_user_info(token_json, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise Exception("Invalid Gmail token or no refresh token.")
        
    return build('drive', 'v3', credentials=creds)

def get_or_create_folder(drive_service):
    logging.info("📁 Checking for existing folder in Drive.")
    query = f"name='{FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder'"
    response = drive_service.files().list(q=query, spaces='drive').execute()
    folders = response.get('files', [])
    if folders:
        folder_id = folders[0]['id']
        logging.info(f"✅ Folder '{FOLDER_NAME}' found: {folder_id}")
        return folder_id
    else:
        logging.info(f"📁 Creating folder '{FOLDER_NAME}' in Drive.")
        file_metadata = {'name': FOLDER_NAME, 'mimeType': 'application/vnd.google-apps.folder'}
        folder = drive_service.files().create(body=file_metadata, fields='id').execute()
        logging.info(f"✅ Folder created: {folder['id']}")
        return folder['id']

def get_transactions(user_id):
    logging.info(f"📥 Fetching transactions for user {user_id}")
    today = datetime.utcnow().strftime("%Y-%m-%d")
    page, page_size = 1, 100
    while True:
        logging.info(f"➡️ Fetching page {page}")
        response = requests.get(
            f"{API_BASE}/api/users/{user_id}/transactions",
            params={"date": today, "page": page, "limit": page_size}
        )
        if response.status_code != 200:
            logging.warning(f"⚠️ API failed with status {response.status_code}")
            break

        data = response.json()
        transactions = data.get("transactions", [])
        if not transactions:
            break

        yield pd.DataFrame(transactions)
        if len(transactions) < page_size:
            break
        page += 1

def find_or_create_excel_file(drive_service, folder_id, username):
    filename = f"transactions_{username}.xlsx"
    query = f"name='{filename}' and '{folder_id}' in parents and mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'"
    result = drive_service.files().list(q=query, spaces='drive').execute()
    files = result.get("files", [])

    if files:
        logging.info(f"✅ Excel file found: {files[0]['id']}")
        return files[0]["id"]
    else:
        logging.info("📄 Creating new Excel file.")
        df = pd.DataFrame(columns=["date", "action", "item", "amount", "merchant", "event"])
        summary_df = pd.DataFrame(columns=["month", "total_amount"])
        stream = io.BytesIO()
        with pd.ExcelWriter(stream, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name="Transactions", index=False)
            summary_df.to_excel(writer, sheet_name="MonthlySummary", index=False)
        stream.seek(0)

        file_metadata = {
            'name': filename,
            'parents': [folder_id],
            'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        }
        media = MediaIoBaseUpload(stream, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        logging.info(f"✅ Excel file created: {file['id']}")
        return file['id']

def update_excel_file(drive_service, file_id, transactions_streams):
    logging.info("⬇️ Downloading current Excel file from Drive.")
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)

    try:
        xl = pd.read_excel(fh, sheet_name=None)
        existing_df = xl.get("Transactions", pd.DataFrame())
        summary_df = xl.get("MonthlySummary", pd.DataFrame())
    except Exception as e:
        logging.error(f"❌ Failed to read Excel file: {e}")
        return

    # Process new transactions
    logging.info("📊 Appending transactions page by page.")
    for df_new in transactions_streams:
        existing_df = pd.concat([existing_df, df_new], ignore_index=True)

    # Refresh monthly summary
    logging.info("📈 Recomputing monthly summary.")
    existing_df['date'] = pd.to_datetime(existing_df['date'], errors='coerce')
    existing_df = existing_df.dropna(subset=['date'])
    existing_df['month'] = existing_df['date'].dt.to_period('M')
    summary = existing_df.groupby('month')['amount'].sum().reset_index()
    summary.columns = ["month", "total_amount"]

    # Upload updated file
    logging.info("⬆️ Uploading updated Excel file.")
    updated_fh = io.BytesIO()
    with pd.ExcelWriter(updated_fh, engine='openpyxl') as writer:
        existing_df.to_excel(writer, sheet_name="Transactions", index=False)
        summary.to_excel(writer, sheet_name="MonthlySummary", index=False)
    updated_fh.seek(0)

    media = MediaIoBaseUpload(updated_fh, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    drive_service.files().update(fileId=file_id, media_body=media).execute()
    logging.info("✅ Excel file updated and uploaded.")

def notify_user(user_id, file_id):
    try:
        file_url = f"https://drive.google.com/file/d/{file_id}/view"
        payload = {"message": f"✅ Your transaction report is ready: {file_url}"}
        logging.info(f"📤 Sending WhatsApp notification to user {user_id}.")
        response = requests.post(f"{API_BASE}/api/users/{user_id}/notify-whatsapp", json=payload)
        if response.status_code == 200:
            logging.info("✅ Notification sent.")
        else:
            logging.warning(f"⚠️ Notification failed with status {response.status_code}")
    except Exception as e:
        logging.error(f"❌ Failed to send notification: {e}")

def scheduler():
    logging.info("🔁 Starting scheduler run")
    try:
        response = requests.get(f"{API_BASE}/api/email-configs")
        email_configs = response.json()
    except Exception as e:
        logging.error(f"❌ Failed to fetch email configs: {e}")
        return

    for config in email_configs:
        user_id = config.get("user_id")
        token_json = config.get("token")
        username = config.get("username") or f"user_{user_id}"

        logging.info(f"\n\n🔄 --- Processing user {user_id} ({username}) ---")
        if not token_json:
            logging.warning(f"⚠️ Missing token for user {user_id}")
            continue
        
        token_json = json.loads(token_json)
        try:
            drive_service = get_drive_service(token_json)
            folder_id = get_or_create_folder(drive_service)
            file_id = find_or_create_excel_file(drive_service, folder_id, username)
            transaction_pages = get_transactions(user_id)
            update_excel_file(drive_service, file_id, transaction_pages)
            notify_user(user_id, file_id)
            logging.info(f"✅ Completed processing for user {user_id}")
        except Exception as e:
            logging.error(f"❌ Error processing user {user_id}: {e}")

# Run the scheduler
if __name__ == "__main__":
    scheduler()
