import base64
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

# All major shopping, grocery, pharmacy, apparel, and delivery platforms
ECOMMERCE_SENDERS = [
    # Top Marketplaces
    "amazon", "flipkart", "meesho", "myntra", "ajio", "tatacliq", "jiomart", "snapdeal", "ebay",
    # Beauty & Personal Care
    "purplle", "nykaa", "tirabeauty", "mamaearth", "sugarcosmetics", "plumgoodness",
    "buywow", "thedermaco", "aqualogica", "foxtale", "dotandkey", "beminimalist",
    "mcaffeine", "reneecosmetics", "swissbeauty", "facescanada", "healthandglow",
    "kamaayurveda", "forestessentialsindia",
    # Fashion & Apparel
    "bewakoof", "thesouledstore", "snitch", "urbanic", "zivame", "clovia", "hm",
    "zara", "reliancetrends", "westside", "marksandspencer", "fabindia", "libas",
    "maxfashion", "pantaloons", "lifestylestores", "lenskart", "titan", "bata", "redtape",
    # Electronics & Wearables
    "croma", "reliancedigital", "boat-lifestyle", "gonoise", "fireboltt", "boultaudio",
    "oneplus", "mi.com", "samsung", "apple", "vijaysales",
    # Quick Commerce & Grocery
    "blinkit", "zeptonow", "swiggy", "bigbasket", "bbdaily", "countrydelight", "dmart",
    # Food Delivery
    "zomato", "eatsure", "dominos", "pizzahut", "kfc", "mcdelivery", "burgerking",
    # Medicine & Healthcare
    "1mg", "pharmeasy", "apollopharmacy", "netmeds", "medplusmart", "truemeds",
    # Baby, Home & Gifts
    "firstcry", "hopscotch", "pepperfry", "urbanladder", "wakefit", "chumbak", "nestasia", "fnp", "igp",
    # Couriers & Logistics
    "shiprocket", "delhivery", "bluedart", "dtdc", "shadowfax", "xpressbees", "ekartlogistics", "ecomexpress"
]

def build_order_search_query() -> str:
    # 1. Join sender names
    senders_filter = " OR ".join(f"from:{name}" for name in ECOMMERCE_SENDERS)
    
    # 2. Universal order keyword triggers (catches any unnamed store or Shopify seller)
    order_keywords = (
        'subject:(order OR "confirmed" OR "dispatched" OR "shipped" OR '
        '"out for delivery" OR "delivered" OR "invoice" OR "tracking" OR "awb" OR "shipment")'
    )
    
    # Full combined Gmail search query
    return f"({senders_filter}) AND {order_keywords}"

def clean_email_body(payload: dict) -> str:
    body = ""
    try:
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        body += base64.urlsafe_b64decode(data.encode("ASCII")).decode("utf-8", errors="ignore")
                elif "parts" in part:
                    for subpart in part["parts"]:
                        if subpart.get("mimeType") == "text/plain":
                            subdata = subpart.get("body", {}).get("data", "")
                            if subdata:
                                body += base64.urlsafe_b64decode(subdata.encode("ASCII")).decode("utf-8", errors="ignore")
        else:
            data = payload.get("body", {}).get("data", "")
            if data:
                body = base64.urlsafe_b64decode(data.encode("ASCII")).decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"Error decoding email body: {e}")
    return body

def fetch_live_order_emails_for_token(access_token: str, limit: int = 5):
    try:
        creds = Credentials(token=access_token)
        service = build("gmail", "v1", credentials=creds)

        query = build_order_search_query()

        # Query up to 25 messages so older store emails are not missed
        results = service.users().messages().list(
            userId="me", 
            q=query, 
            maxResults=25
        ).execute()
        
        messages = results.get("messages", [])
        if not messages:
            # Fallback query if the combined string is too strict
            fallback_query = 'subject:(order OR "shipped" OR "delivered" OR "tracking")'
            results = service.users().messages().list(userId="me", q=fallback_query, maxResults=15).execute()
            messages = results.get("messages", [])

        extracted = []
        for msg in messages[:limit]:
            msg_data = service.users().messages().get(
                userId="me", 
                id=msg["id"], 
                format="full"
            ).execute()
            
            payload = msg_data.get("payload", {})
            headers = payload.get("headers", [])
            
            subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "No Subject")
            sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "Unknown Sender")
            date_str = next((h["value"] for h in headers if h["name"].lower() == "date"), "")
            snippet = msg_data.get("snippet", "")
            raw_body = clean_email_body(payload)

            extracted.append({
                "from": sender,
                "subject": subject,
                "date": date_str,
                "snippet": snippet,
                "body": raw_body[:1000]
            })

        return extracted
    except Exception as e:
        print(f"Error fetching Gmail order emails: {e}")
        return []