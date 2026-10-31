import base64
import re
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_gmail_service_from_token(access_token: str):
    """Builds a Gmail service instance dynamically using the visitor's OAuth access token."""
    creds = Credentials(token=access_token, scopes=SCOPES)
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

def fetch_live_order_emails_for_token(access_token: str, limit=10):
    """
    Fetches real-time e-commerce order confirmation, shipping, and receipt emails
    across all platforms (Purplle, Amazon, Flipkart, Nykaa, Myntra, Swiggy, Meesho, etc.).
    """
    try:
        service = get_gmail_service_from_token(access_token)
        
        # Universal query capturing transactional emails from all e-commerce platforms
        query = (
            '(subject:(order OR "order confirmed" OR "order placed" OR "shipped" OR "delivered" '
            'OR "out for delivery" OR dispatched OR "tracking" OR invoice OR receipt OR AWB OR refund) '
            'OR "order id" OR "tracking number") -category:promotions -category:social'
        )
        
        results = service.users().messages().list(userId='me', q=query, maxResults=limit).execute()
        messages = results.get('messages', [])
        
        email_records = []
        for msg in messages:
            msg_data = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            payload = msg_data.get('payload', {})
            snippet = msg_data.get('snippet', '')
            
            headers = payload.get('headers', [])
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
            sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown Sender')
            
            body = extract_body(payload)
            body_clean = body.strip() if len(body.strip()) > 30 else snippet
            
            text_content = f"Store/Sender: {sender}\nSubject: {subject}\nSnippet: {snippet}\nDetails: {body_clean[:1800]}"
            
            email_records.append({
                "message_id": msg['id'],
                "sender": sender,
                "subject": subject,
                "text": text_content
            })
            
        return email_records
    except Exception as e:
        print(f"Per-user Gmail sync notice: {e}")
        return []