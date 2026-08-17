# routers/auth.py
import os
import sqlite3
import time
import random
import urllib.parse
from fastapi import APIRouter, HTTPException, Query, Header, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr
from jose import jwt
import httpx
from passlib.context import CryptContext
from twilio.rest import Client

router = APIRouter(prefix="/api/auth", tags=["auth"])

# --- Security & Config ---
SECRET_KEY = os.getenv("JWT_SECRET", "THRICE_NOMISMA_SECRET_SECURE_KEY_99812")
ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- Integrated Twilio Credentials ---
TWILIO_ACCOUNT_SID = "ACfb99a7cce18381b8b8c173e3fd94f543"
TWILIO_AUTH_TOKEN = "06f7443377c3315bcf54a4dc5b6e420e"
TWILIO_PHONE_NUMBER = "+16503790451"

# --- Google OAuth Credentials ---
GOOGLE_CLIENT_ID = "758156257674-reo3in6uv1c6v5q0iokf1svg2f8io351.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"
GOOGLE_REDIRECT_URI = "http://localhost:8000/api/auth/google/callback"

# --- SQLite Persistent Database ---
DB_PATH = "nomisma.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT UNIQUE,
            name TEXT,
            hashed_password TEXT,
            avatar TEXT,
            clearance_level TEXT DEFAULT 'LEVEL 1 OPERATOR',
            created_at REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS otps (
            target TEXT PRIMARY KEY,
            code TEXT,
            expires_at REAL
        )
    """)
    
    # Dynamic Database Migration for Phone Column
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN phone TEXT UNIQUE")
    except sqlite3.OperationalError:
        pass  # Column already exists

    conn.commit()
    conn.close()

init_db()

# --- Database Operations Helpers ---
def db_get_user_by_email(email: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def db_create_user(email: str = None, name: str = None, hashed_password: str = None, avatar: str = None, clearance_level: str = "LEVEL 1 OPERATOR"):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (email, name, hashed_password, avatar, clearance_level, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (email, name, hashed_password, avatar, clearance_level, time.time())
    )
    conn.commit()
    conn.close()

def db_update_user_profile(email: str, name: str, avatar: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET name = ?, avatar = ? WHERE email = ?",
        (name, avatar, email)
    )
    conn.commit()
    conn.close()

def db_register_or_update_google_user(email: str, name: str, avatar: str):
    user = db_get_user_by_email(email)
    if user:
        db_update_user_profile(email, name, avatar)
    else:
        db_create_user(email=email, name=name, avatar=avatar, clearance_level="LEVEL 1 OPERATOR")

def db_save_otp(target: str, code: str, expires_in_secs: int = 300):
    conn = get_db_connection()
    cursor = conn.cursor()
    expires_at = time.time() + expires_in_secs
    cursor.execute(
        "INSERT OR REPLACE INTO otps (target, code, expires_at) VALUES (?, ?, ?)",
        (target, code, expires_at)
    )
    conn.commit()
    conn.close()

def db_verify_otp(target: str, code: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT code, expires_at FROM otps WHERE target = ?", (target,))
    row = cursor.fetchone()
    conn.close()
    if row:
        stored_code = row["code"]
        expires_at = row["expires_at"]
        if stored_code == code and time.time() < expires_at:
            conn_del = get_db_connection()
            cursor_del = conn_del.cursor()
            cursor_del.execute("DELETE FROM otps WHERE target = ?", (target,))
            conn_del.commit()
            conn_del.close()
            return True
    return False

def get_current_user_from_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Session validation token is missing")
    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Decryption payload validation failed")
        user = db_get_user_by_email(sub)
        if not user:
            raise HTTPException(status_code=401, detail="Secure system core profile not found")
        return user
    except Exception:
        raise HTTPException(status_code=401, detail="Session signature key expired or altered")

# --- Schemas ---
class SendLinkOTPRequest(BaseModel):
    phone: str

class VerifyLinkOTPRequest(BaseModel):
    phone: str
    code: str

def create_access_token(data: dict):
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)

# --- Endpoints ---

@router.get("/me")
def get_me(user = Depends(get_current_user_from_token)):
    return {
        "email": user["email"],
        "phone": user.get("phone") or "",
        "name": user["name"],
        "avatar": user["avatar"] or "",
        "clearance_level": user["clearance_level"],
        "created_at": user["created_at"]
    }

@router.get("/google/dev-token")
def google_dev_token():
    if not GOOGLE_CLIENT_ID:
        mock_token = create_access_token({"sub": "dev.user@thricenomisma.com", "name": "Dev Nomisma", "avatar": ""})
        db_register_or_update_google_user("dev.user@thricenomisma.com", "Dev Nomisma", "")
        return {
            "token": mock_token,
            "email": "dev.user@thricenomisma.com",
            "name": "Dev Nomisma",
            "avatar": ""
        }
    else:
        raise HTTPException(status_code=400, detail="Sandbox dev bypass disabled")

@router.get("/google")
def google_auth(platform: str = Query("electron")):
    if not GOOGLE_CLIENT_ID: 
        mock_token = create_access_token({"sub": "dev.user@thricenomisma.com", "name": "Dev Nomisma", "avatar": ""})
        db_register_or_update_google_user("dev.user@thricenomisma.com", "Dev Nomisma", "")
        return RedirectResponse(url=f"thrice-nomisma://login-success?token={mock_token}&email=dev.user@thricenomisma.com&name=Dev%20Nomisma&avatar=")

    params = {
        "client_id": GOOGLE_CLIENT_ID, "redirect_uri": GOOGLE_REDIRECT_URI, "response_type": "code",
        "scope": "openid email profile", "access_type": "offline", "prompt": "select_account", "state": platform
    }
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=url)

@router.get("/google/callback")
async def google_callback(code: str = Query(...), state: str = Query("electron")):
    async with httpx.AsyncClient() as client:
        token_res = await client.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI, "grant_type": "authorization_code"
        })
        if token_res.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to retrieve OAuth registration code")
        
        access_token = token_res.json().get("access_token")
        userinfo_res = await client.get("https://www.googleapis.com/oauth2/v3/userinfo", headers={"Authorization": f"Bearer {access_token}"})
        if userinfo_res.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to verify profile keys")
        
        user_info = userinfo_res.json()
        email = user_info.get("email")
        name = user_info.get("name")
        avatar = user_info.get("picture", "")
        
        db_register_or_update_google_user(email, name, avatar)
        jwt_token = create_access_token({"sub": email, "name": name, "avatar": avatar})
        
        if state == "browser":
            redirect_url = (
                f"http://localhost:5173/?token={jwt_token}"
                f"&email={urllib.parse.quote(email)}&name={urllib.parse.quote(name)}&avatar={urllib.parse.quote(avatar)}"
            )
            return RedirectResponse(url=redirect_url)
        else:
            # Native In-App postMessage handshake. Sends auth credentials directly to the parent and auto-closes the popup.
            html_content = f"""
            <html>
            <head>
                <title>Authenticating...</title>
                <script>
                    window.onload = function() {{
                        const payload = {{
                            token: "{jwt_token}",
                            email: "{urllib.parse.quote(email)}",
                            name: "{urllib.parse.quote(name)}",
                            avatar: "{urllib.parse.quote(avatar)}"
                        }};
                        if (window.opener) {{
                            window.opener.postMessage({{ type: "AUTH_SUCCESS", data: payload }}, "*");
                        }}
                        // Close the window after a tiny delay to ensure message delivery
                        setTimeout(function() {{
                            window.close();
                        }}, 500);
                    }};
                </script>
            </head>
            <body style="background-color: #0c0e12; color: #8ea1b4; font-family: 'Rajdhani', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0;">
                <div style="text-align: center; padding: 40px; border: 1px solid rgba(201, 168, 76, 0.25); background-color: #13161f; border-radius: 8px;">
                    <h2 style="color: #c9a84c;">AUTHENTICATION SUCCESSFUL</h2>
                    <p>Syncing security tokens with workspace. Please wait...</p>
                </div>
            </body>
            </html>
            """
            return HTMLResponse(content=html_content)

# --- Real-time Phone Link Verification Endpoints ---

@router.post("/phone/send-link-otp")
def send_link_otp(req: SendLinkOTPRequest, current_user = Depends(get_current_user_from_token)):
    if not req.phone or not req.phone.startswith("+") or len(req.phone) < 8:
        raise HTTPException(status_code=400, detail="Invalid telephone coordinates. Format: +[CountryCode][Number]")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT email FROM users WHERE phone = ? AND email != ?", (req.phone, current_user["email"]))
    existing = cursor.fetchone()
    conn.close()
    if existing:
        raise HTTPException(status_code=400, detail="This phone number is already linked to another terminal profile.")

    otp_code = "".join([str(random.randint(0, 9)) for _ in range(6)])
    db_save_otp(req.phone, otp_code)
    
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=f"[Thrice Nomisma] Your security link verification code is: {otp_code}. Valid for 5 minutes.",
            from_=TWILIO_PHONE_NUMBER,
            to=req.phone
        )
        print(f"[TWILIO DISPATCH] Real-time SMS Sent to {req.phone}. SID: {message.sid}")
    except Exception as e:
        print(f"[TWILIO ERROR] SMS Delivery failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to dispatch carrier SMS. {str(e)}")

    return {"status": "success", "message": "Verification code dispatched"}

@router.post("/phone/verify-link-otp")
def verify_link_otp(req: VerifyLinkOTPRequest, current_user = Depends(get_current_user_from_token)):
    is_valid = db_verify_otp(req.phone, req.code)
    if not is_valid:
        raise HTTPException(status_code=400, detail="MFA verification sequence failed or code expired")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET phone = ?, clearance_level = 'LEVEL 2 OPERATOR' WHERE email = ?",
        (req.phone, current_user["email"])
    )
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": "Security clearance elevated to LEVEL 2 OPERATOR"}