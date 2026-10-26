import os
import json
import sqlite3
import base64
from typing import List, Optional, Any
from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth
from pydantic import BaseModel
from dotenv import load_dotenv
from groq import Groq
from gmail_service import fetch_live_order_emails_for_token

load_dotenv()

api_key = os.getenv("GROQ_API_KEY", "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").strip()
client = Groq(api_key=api_key)

ACTIVE_MODEL = "openai/gpt-oss-20b"
VISION_MODEL = "qwen/qwen3.8-27b"

app = FastAPI()

# Session Middleware to store each user's login state securely in browser cookies
SESSION_SECRET = os.getenv("SESSION_SECRET", "aura-super-secret-key-prod-2026")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

# Google OAuth Credentials Fallback

GOOGLE_CLIENT_ID = "786796474890-b1loroghl9a7dqmvh6dn9s7i74r9mhcl.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"

# Google OAuth Setup
oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={
        "scope": "openid email profile https://www.googleapis.com/auth/gmail.readonly",
        "prompt": "consent"
    }
)


def get_db_connection():
    conn = sqlite3.connect("aura_records.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            user_email TEXT,
            platform TEXT,
            item_name TEXT,
            carrier TEXT,
            tracking_id TEXT,
            status TEXT,
            eta TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT,
            filename TEXT,
            content TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ai_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT,
            fact TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_user_db_data(user_email: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        rows = cursor.execute("""
            SELECT order_id, platform, item_name, carrier, tracking_id, status, eta
            FROM orders
            WHERE user_email = ?
            GROUP BY order_id
            LIMIT 5
        """, (user_email,)).fetchall()
        orders = [dict(r) for r in rows]
        
        up_rows = cursor.execute(
            "SELECT filename, content FROM uploads WHERE user_email = ? ORDER BY id DESC LIMIT 5",
            (user_email,)
        ).fetchall()
        uploads = [dict(r) for r in up_rows]
        
        mem_rows = cursor.execute(
            "SELECT fact FROM ai_memory WHERE user_email = ? ORDER BY id DESC LIMIT 15",
            (user_email,)
        ).fetchall()
        memories = [r["fact"] for r in mem_rows]
        
        conn.close()
        return orders, uploads, memories
    except Exception as e:
        print(f"DB Error: {e}")
        return [], [], []

def save_user_dynamic_memory(user_email: str, user_msg: str):
    msg = user_msg.strip()
    if not msg or msg.endswith("?"):
        return
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO ai_memory (user_email, fact) VALUES (?, ?)", (user_email, msg))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Memory Save Error: {e}")

class ChatInput(BaseModel):
    message: str
    history: Optional[List[Any]] = []

# --- Verification & Policy Routes ---
@app.get("/googled312b44987ddae3a.html")
async def google_verification():
    if os.path.exists("googled312b44987ddae3a.html"):
        return FileResponse("googled312b44987ddae3a.html")
    return HTMLResponse("google-site-verification: googled312b44987ddae3a.html")

@app.get("/privacy", response_class=HTMLResponse)
def privacy_policy():
    if os.path.exists("privacy.html"):
        with open("privacy.html", "r", encoding="utf-8") as file:
            return file.read()
    return HTMLResponse("<h1>AURA Universal Sync Privacy Policy</h1><p>Contact: dadaiahpalliishika@gmail.com</p>")

# --- OAuth Routes ---
@app.get("/auth/login")
async def login(request: Request):
    redirect_uri = "https://aura-vz7f.onrender.com/auth/callback"
    print(f"DEBUG AUTH: Using client_id={GOOGLE_CLIENT_ID[:15]}... and redirect_uri={redirect_uri}")
    return await oauth.google.authorize_redirect(request, redirect_uri, access_type="offline")

@app.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get("userinfo")
        if not user_info:
            user_info = await oauth.google.userinfo(token=token)
        
        request.session["user"] = {
            "email": user_info.get("email"),
            "name": user_info.get("name"),
            "access_token": token.get("access_token")
        }
        return RedirectResponse(url="/")
    except Exception as e:
        return HTMLResponse(f"<h3>Authentication error: {e}</h3><br><a href='/'>Go Back</a>")

@app.get("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")

@app.get("/api/user-status")
def user_status(request: Request):
    user = request.session.get("user")
    if user:
        return {"logged_in": True, "name": user.get("name"), "email": user.get("email")}
    return {"logged_in": False, "name": "Guest", "email": None}

@app.get("/", response_class=HTMLResponse)
def home():
    with open("index.html", "r", encoding="utf-8") as file:
        return file.read()

@app.post("/api/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    user = request.session.get("user")
    user_email = user.get("email") if user else "guest"
    
    try:
        contents = await file.read()
        filename_lower = file.filename.lower()
        text_content = ""
        
        if filename_lower.endswith(('.png', '.jpg', '.jpeg', '.webp', '.heic')):
            base64_image = base64.b64encode(contents).decode('utf-8')
            mime_type = "image/png" if filename_lower.endswith('.png') else "image/jpeg"
            
            vision_completion = client.chat.completions.create(
                model=VISION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Extract all text, titles, names, certificate details, exam schedules, or timings from this image precisely."},
                            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
                        ]
                    }
                ],
                max_tokens=400
            )
            text_content = vision_completion.choices[0].message.content.strip()
        else:
            text_content = contents.decode("utf-8", errors="ignore")
        
        safe_text_content = text_content[:400]

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO uploads (user_email, filename, content) VALUES (?, ?, ?)", (user_email, file.filename, safe_text_content))
        conn.commit()
        conn.close()
        
        return {
            "status": "success", 
            "filename": file.filename, 
            "extracted_text": safe_text_content,
            "message": f"Successfully stored '{file.filename}' for your account!"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

def detect_ui_metadata(user_msg: str, ai_reply: str):
    combined = (user_msg + " " + ai_reply).lower()
    if any(k in combined for k in ["unauthorized", "hacked", "stolen", "fraud", "scam"]):
        priority = "High Priority 🚨"
    elif any(k in combined for k in ["failed", "deducted", "unpaid", "dispute", "error"]):
        priority = "Important ⚠️"
    else:
        priority = "Normal ✅"

    if "bank" in combined and ("college" in combined or "fee" in combined or "portal" in combined):
        return f"Cross-Domain [Banking + Education] ({priority})", ["Verify RRN", "Exam Results", "Show My Orders"]
    if any(k in combined for k in ["order id", "mamaearth", "ajio", "amazon", "flipkart", "tracking", "awb", "shipment"]):
        return f"E-Commerce Desk [Live] ({priority})", ["Track Courier", "View Invoice", "Delivery Updates"]
    if any(k in combined for k in ["rrn", "refund", "upi", "chargeback", "bank"]):
        return f"Banking & Finance Desk [Verified] ({priority})", ["Verify RRN", "Banking & Refunds", "Show My Orders"]
    if any(k in combined for k in ["sgpa", "pin", "diploma", "exam", "schedule", "timing", "result", "certificate"]):
        return f"Academic Desk [Verified] ({priority})", ["Exam Results", "Show My Orders", "Banking & Refunds"]
    
    return f"AURA Intelligence [Online] ({priority})", ["Show My Orders", "Banking & Refunds", "Exam Results"]

@app.post("/api/chat")
def handle_chat(payload: ChatInput, request: Request):
    user = request.session.get("user")
    user_email = user.get("email") if user else "guest"
    user_name = user.get("name") if user else "User"
    
    user_msg = payload.message.strip()
    save_user_dynamic_memory(user_email, user_msg)
    
    db_orders, db_uploads, db_memories = get_user_db_data(user_email)

    live_emails = []
    if user and user.get("access_token"):
        live_emails = fetch_live_order_emails_for_token(user.get("access_token"), limit=3)

    user_context = f"User Name: {user_name} | Email: {user_email}"
    if not user:
        user_context += " (Guest user - not signed in with Google yet. Advise them they can connect their Gmail for live order sync)."

    sys_prompt = f"""You are AURA, an intelligent universal personal AI support platform assisting {user_name}.
You manage 3 primary desks: E-Commerce Logistics, Banking/UPI Refunds, and Academic Records.

[CURRENT USER]:
{user_context}

[USER ISOLATED CONVERSATIONAL MEMORY]:
{json.dumps(db_memories)}

[USER ORDERS & DATABASE RECORDS]:
{json.dumps(db_orders)}

[USER UPLOADED FILES]:
{json.dumps(db_uploads)}

[USER LIVE GMAIL INBOX EMAILS]:
{json.dumps(live_emails)}

Guidelines:
- Only reference the data belonging to THIS user ({user_name}). Never share or assume other users' personal info.
- If the user asks about orders, check [USER LIVE GMAIL INBOX EMAILS] and [USER ORDERS & DATABASE RECORDS]. If none exist, state clearly that no orders were found for their account and provide helpful guidance.
- When displaying orders, format them into a clean Markdown table.
"""

    messages = [{"role": "system", "content": sys_prompt}]
    if payload.history:
        for item in payload.history[-4:]:
            if isinstance(item, dict) and item.get("role") in ["user", "assistant"]:
                messages.append({"role": item["role"], "content": str(item["content"])[:300]})

    messages.append({"role": "user", "content": user_msg})

    try:
        completion = client.chat.completions.create(
            messages=messages,
            model=ACTIVE_MODEL,
            temperature=0.6,
            max_tokens=500
        )
        ai_reply = completion.choices[0].message.content.strip()
        domain, actions = detect_ui_metadata(user_msg, ai_reply)
        return {
            "reply": ai_reply,
            "domain": domain,
            "actions": actions,
            "orders": db_orders
        }
    except Exception as e:
        return {
            "reply": f"Error detail: {str(e)}",
            "domain": "System Notice",
            "actions": ["Show My Orders", "Banking & Refunds", "Exam Results"],
            "orders": db_orders
        }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)