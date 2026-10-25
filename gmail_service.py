import os
import base64
import re
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_gmail_service():
    if not os.path.exists('token.json'):
        raise FileNotFoundError("token.json not found. Please authenticate first.")
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    return build('gmail', 'v1', credentials=creds)

def clean_html(raw_html: str) -> str:
    cleanr = re.compile(r'<.*?>')
    text = re.sub(cleanr, ' ', raw_html)
    return re.sub(r'\s+', ' ', text).strip()

def extract_body(payload: dict) -> str:
    body_text = ""
    if 'parts' in payload:
        for part in payload['parts']:
            mime = part.get('mimeType', '')
            data = part.get('body', {}).get('data', '')
            if mime == 'text/plain' and data:
                body_text += base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore') + " "
            elif mime == 'text/html' and data and not body_text:
                decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                body_text += clean_html(decoded) + " "
            elif 'parts' in part:
                body_text += extract_body(part) + " "
    elif 'body' in payload and 'data' in payload['body']:
        data = payload['body']['data']
        mime = payload.get('mimeType', '')
        decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
        if mime == 'text/html':
            body_text += clean_html(decoded)
        else:
            body_text += decoded
    return body_text

def fetch_live_order_emails(limit=5):
    try:
        service = get_gmail_service()
        query = '(from:mamaearth OR from:ajio OR from:amazon OR from:flipkart) (order OR delivered OR shipped OR dispatched OR tracking OR canceled OR cancelled)'
        
        results = service.users().messages().list(userId='me', q=query, maxResults=limit).execute()
        messages = results.get('messages', [])
        
        email_records = []
        for msg in messages:
            msg_data = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            payload = msg_data.get('payload', {})
            snippet = msg_data.get('snippet', '')
            
            headers = payload.get('headers', [])
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
            
            body = extract_body(payload)
            text_content = f"Subject: {subject}\nBody: {body if len(body.strip()) > 30 else snippet}"
            
            email_records.append({
                "message_id": msg['id'],
                "text": text_content[:2500]
            })
            
        return email_records
    except Exception as e:
        print(f"Gmail sync warning: {e}")
        return []