import os
import json
import sqlite3
import base64
from typing import List, Optional, Any
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from groq import Groq
from gmail_service import fetch_live_order_emails

load_dotenv()

api_key = os.getenv("GROQ_API_KEY", "").strip()
client = Groq(api_key=api_key)

ACTIVE_MODEL = "openai/gpt-oss-20b"
VISION_MODEL = "qwen/qwen3.8-27b"

app = FastAPI()

def get_db_connection():
    conn = sqlite3.connect("aura_records.db")
    conn.row_factory = sqlite3.Row
    return conn

def get_saved_db_data():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
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
                filename TEXT,
                content TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact TEXT UNIQUE
            )
        """)
        conn.commit()

        rows = cursor.execute("""
            SELECT order_id, platform, item_name, carrier, tracking_id, status, eta
            FROM orders
            GROUP BY order_id
            LIMIT 4
        """).fetchall()
        orders = [dict(r) for r in rows]
        
        up_rows = cursor.execute("SELECT filename, content FROM uploads ORDER BY id DESC LIMIT 5").fetchall()
        uploads = [dict(r) for r in up_rows]
        
        # Optimized recent memory window to completely prevent TPD (daily) and TPM (minute) rate limits
        mem_rows = cursor.execute("SELECT fact FROM ai_memory ORDER BY id DESC LIMIT 20").fetchall()
        memories = [r["fact"] for r in mem_rows]
        
        conn.close()
        return orders, uploads, memories
    except Exception as e:
        print(f"DB Error: {e}")
        return [], [], []

def save_dynamic_memory(user_msg: str):
    """Universally captures and saves any statement or detail shared by the user into SQLite."""
    msg = user_msg.strip()
    if not msg or msg.endswith("?"):
        return

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO ai_memory (fact) VALUES (?)", (msg,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Memory Save Error: {e}")

class ChatInput(BaseModel):
    message: str
    history: Optional[List[Any]] = []

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
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
                            {
                                "type": "text",
                                "text": "Extract all text, titles, names, certificate details, exam schedules, or timings from this image precisely."
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{base64_image}"
                                }
                            }
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
        cursor.execute("INSERT INTO uploads (filename, content) VALUES (?, ?)", (file.filename, safe_text_content))
        conn.commit()
        conn.close()
        
        return {
            "status": "success", 
            "filename": file.filename, 
            "extracted_text": safe_text_content,
            "message": f"Successfully read and permanently stored '{file.filename}' in database memory!"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/", response_class=HTMLResponse)
def home():
    with open("index.html", "r") as file:
        return file.read()

KNOWLEDGE_CONTEXT = """
[USER PROFILE]:
1. Academic: College: TKR Engineering College (DCSE) | PIN: 24248-CS-066 | SBTET 2nd Sem Result: 9.5 SGPA (190/200 points).
2. Banking/Refund: Mamaearth Order #2355507 | Refund: ₹371.55 via UPI on Aug 17, 2026 | Bank RRN: 103869968943.
"""

def detect_ui_metadata(user_msg: str, ai_reply: str):
    combined = (user_msg + " " + ai_reply).lower()
    
    # Smart Priority Detection
    if any(k in combined for k in ["unauthorized", "hacked", "stolen", "fraud", "scam"]):
        priority = "High Priority 🚨"
    elif any(k in combined for k in ["failed", "deducted", "unpaid", "dispute", "error"]):
        priority = "Important ⚠️"
    else:
        priority = "Normal ✅"

    # Multi-Domain Crossover & Desk Routing
    if "bank" in combined and ("college" in combined or "fee" in combined or "portal" in combined):
        return f"Cross-Domain [Banking + Education] ({priority})", ["Verify RRN", "Exam Results", "Show My Orders"]
    if any(k in combined for k in ["order id", "mamaearth", "ajio", "tracking", "awb", "shipment"]):
        return f"E-Commerce Desk [Live] ({priority})", ["Track Courier", "View Invoice", "Delivery Updates"]
    if any(k in combined for k in ["rrn", "371.55", "refund", "upi", "chargeback", "bank"]):
        return f"Banking & Finance Desk [Verified] ({priority})", ["Verify RRN", "Banking & Refunds", "Show My Orders"]
    if any(k in combined for k in ["sgpa", "24248-cs-066", "sbtet", "tkr", "diploma", "exam", "schedule", "timing", "result", "certificate"]):
        return f"Academic Desk [Verified] ({priority})", ["Exam Results", "Show My Orders", "Banking & Refunds"]
    
    return f"AURA Intelligence [Online] ({priority})", ["Show My Orders", "Banking & Refunds", "Exam Results"]

@app.post("/api/chat")
def handle_chat(payload: ChatInput):
    user_msg = payload.message.strip()
    
    # Universally save any non-question statement into SQLite
    save_dynamic_memory(user_msg)
    
    db_orders, db_uploads, db_memories = get_saved_db_data()

    sys_prompt = f"""You are AURA, an intelligent, warm personal AI support assistant for Ishika Dadaiahpalli.
You manage 3 primary desks: E-Commerce Logistics, Banking/UPI Refunds, and Academic Records. You also handle cross-domain inquiries seamlessly.

{KNOWLEDGE_CONTEXT}

[UNIVERSAL CONVERSATIONAL MEMORY (Every detail and statement shared by the user across all sessions)]:
{json.dumps(db_memories)}

[LIVE ORDERS]:
{json.dumps(db_orders)}

[PERMANENT DATABASE KNOWLEDGE BASE & UPLOADED FILES]:
{json.dumps(db_uploads)}

Guidelines:
- You have permanent cross-session memory stored in the SQLite database (including all past statements, user facts, and uploaded files shown above). Always use this stored data to answer questions across any new session.
- Converse naturally and directly like a smart, friendly assistant.
- NEVER attempt to call tools, functions, or output JSON format tags. Always output plain text and Markdown.
- When asked about orders, format them in a clean Markdown table:
| Order ID | Platform / Merchant | Product(s) | Logistics Partner | Tracking / AWB | Delivery Status | ETA / Delivery Details |
|---|---|---|---|---|---|---|
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
     import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)