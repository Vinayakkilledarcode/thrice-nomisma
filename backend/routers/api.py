import os
import sqlite3
import time
import random
import urllib.parse
from fastapi import APIRouter, HTTPException, Query, Header, Depends
from pydantic import BaseModel, EmailStr
from jose import jwt
import httpx
from passlib.context import CryptContext

router = APIRouter(prefix="/api/auth", tags=["auth"])

# --- Security & Config ---
SECRET_KEY = os.getenv("JWT_SECRET", "THRICE_NOMISMA_SECRET_SECURE_KEY_99812")
ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- Google Config ---
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = "http://localhost:8000/api/auth/google/callback"

# --- SQLite Persistent Database Integration ---
DB_PATH = "nomisma.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Expanded Users table with role metrics
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT UNIQUE,
            phone TEXT UNIQUE,
            name TEXT,
            hashed_password TEXT,
            avatar TEXT,
            clearance_level TEXT DEFAULT 'LEVEL 1 OPERATOR',
            created_at REAL
        )
    """)
    # Unified OTP codes verification cache
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS otps (
            target TEXT PRIMARY KEY,
            code TEXT,
            expires_at REAL
        )
    """)
    # Secure Vault database notes
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_vault (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_identifier TEXT,
            note_title TEXT,
            note_content TEXT,
            updated_at REAL
        )
    """)
    conn.commit()
    conn.close()

# Start and migrate DB
init_db()

# --- Database Operations Helpers ---
def db_get_user_by_email(email: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def db_get_user_by_phone(phone: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE phone = ?", (phone,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def db_get_user_by_identifier(identifier: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ? OR phone = ?", (identifier, identifier))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def db_create_user(email: str = None, phone: str = None, name: str = None, hashed_password: str = None, avatar: str = None, clearance_level: str = "LEVEL 1 OPERATOR"):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (email, phone, name, hashed_password, avatar, clearance_level, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (email, phone, name, hashed_password, avatar, clearance_level, time.time())
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
        db_create_user(email=email, name=name, avatar=avatar, clearance_level="LEVEL 4 OPERATOR")

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

# --- Authentication Verification Dependency ---
def get_current_user_from_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Session validation token is missing")
    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Decryption payload validation failed")
        user = db_get_user_by_identifier(sub)
        if not user:
            raise HTTPException(status_code=401, detail="Secure system core profile not found")
        return user
    except Exception:
        raise HTTPException(status_code=401, detail="Session signature key expired or altered")

# --- Schemas ---
class LoginRequest(BaseModel):
    identifier: str
    password: str

class RegisterRequest(BaseModel):
    email: EmailStr
    name: str
    password: str

class SendOTPRequest(BaseModel):
    phone: str

class VerifyOTPRequest(BaseModel):
    phone: str
    code: str
    name: str = "Nomisma User"

class SendEmailOTPRequest(BaseModel):
    email: str

class VerifyEmailOTPRequest(BaseModel):
    email: str
    code: str
    name: str = "Nomisma User"

class SaveVaultRequest(BaseModel):
    title: str
    content: str

def create_access_token(data: dict):
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)

# --- Endpoints ---

@router.get("/me")
def get_me(user = Depends(get_current_user_from_token)):
    return {
        "email": user["email"] if user["email"] else f"{user['phone']}@nomisma.local",
        "phone": user["phone"] or "",
        "name": user["name"],
        "avatar": user["avatar"] or "",
        "clearance_level": user["clearance_level"],
        "created_at": user["created_at"]
    }

@router.post("/register")
def register(req: RegisterRequest):
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    existing_user = db_get_user_by_email(req.email)
    if existing_user:
        raise HTTPException(status_code=400, detail="Secure profile with this email already initialized")

    hashed_password = pwd_context.hash(req.password)
    db_create_user(email=req.email, name=req.name, hashed_password=hashed_password, clearance_level="LEVEL 1 OPERATOR")

    token = create_access_token({"sub": req.email, "name": req.name})
    return {
        "token": token,
        "email": req.email,
        "name": req.name
    }

@router.post("/login")
def login(req: LoginRequest):
    user = db_get_user_by_identifier(req.identifier)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid system parameters")
    
    if not user["hashed_password"]:
         raise HTTPException(status_code=400, detail="Account requires direct Social Auth or OTP portal authorization")
        
    if not pwd_context.verify(req.password, user["hashed_password"]):
        raise HTTPException(status_code=400, detail="Invalid credentials")
        
    token = create_access_token({"sub": user["email"] if user["email"] else user["phone"], "name": user["name"]})
    return {
        "token": token,
        "email": user["email"] if user["email"] else f"{user['phone']}@nomisma.local",
        "name": user["name"]
    }

@router.get("/google")
def google_auth(platform: str = Query("electron")):
    if not GOOGLE_CLIENT_ID: # Dev Fallback Mode
        mock_token = create_access_token({"sub": "dev.user@thricenomisma.com", "name": "Dev Nomisma", "avatar": ""})
        db_register_or_update_google_user("dev.user@thricenomisma.com", "Dev Nomisma", "")
        
        if platform == "browser":
            redirect_url = f"http://localhost:5173/?token={mock_token}&email=dev.user%40thricenomisma.com&name=Dev%20Nomisma&avatar="
        else:
            redirect_url = f"thrice-nomisma://login-success?token={mock_token}&email=dev.user@thricenomisma.com&name=Dev%20Nomisma&avatar="
        return RedirectResponse(url=redirect_url)

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
        else:
            redirect_url = (
                f"thrice-nomisma://login-success?token={jwt_token}"
                f"&email={urllib.parse.quote(email)}&name={urllib.parse.quote(name)}&avatar={urllib.parse.quote(avatar)}"
            )
        return RedirectResponse(url=redirect_url)

# --- Phone OTP Core ---
@router.post("/phone/send-otp")
def send_otp(req: SendOTPRequest):
    if not req.phone or len(req.phone) < 8:
        raise HTTPException(status_code=400, detail="Invalid telephone coordinates")
        
    otp_code = "".join([str(random.randint(0, 9)) for _ in range(6)])
    db_save_otp(req.phone, otp_code)
    
    print(f"\n========================================")
    print(f"  [TELEPORT CHANNELS] PHONE OTP FOR {req.phone}: {otp_code}")
    print(f"========================================\n")
    
    return {"status": "success", "message": "OTP key dispatched", "code": otp_code}

@router.post("/phone/verify-otp")
def verify_otp(req: VerifyOTPRequest):
    is_valid = db_verify_otp(req.phone, req.code)
    if not is_valid:
        raise HTTPException(status_code=400, detail="OTP validation sequence failed or key expired")
        
    user = db_get_user_by_phone(req.phone)
    if not user:
        db_create_user(phone=req.phone, name=req.name, clearance_level="LEVEL 2 OPERATOR")
        user = db_get_user_by_phone(req.phone)
        
    token = create_access_token({"sub": user["phone"], "name": user["name"]})
    return {
        "token": token,
        "email": user["email"] if user["email"] else f"{user['phone']}@nomisma.local",
        "name": user["name"],
        "avatar": user["avatar"] or ""
    }

# --- Email OTP Core ---
@router.post("/email/send-otp")
def send_email_otp(req: SendEmailOTPRequest):
    if not req.email or "@" not in req.email:
        raise HTTPException(status_code=400, detail="Invalid email identifier format")
        
    otp_code = "".join([str(random.randint(0, 9)) for _ in range(6)])
    db_save_otp(req.email, otp_code)
    
    print(f"\n========================================")
    print(f"  [TELEPORT CHANNELS] EMAIL OTP FOR {req.email}: {otp_code}")
    print(f"========================================\n")
    
    return {"status": "success", "message": "Email verification OTP dispatched", "code": otp_code}

@router.post("/email/verify-otp")
def verify_email_otp(req: VerifyEmailOTPRequest):
    is_valid = db_verify_otp(req.email, req.code)
    if not is_valid:
        raise HTTPException(status_code=400, detail="Email verification code incorrect or expired")
        
    user = db_get_user_by_email(req.email)
    if not user:
        db_create_user(email=req.email, name=req.name, clearance_level="LEVEL 3 OPERATOR")
        user = db_get_user_by_email(req.email)
        
    token = create_access_token({"sub": user["email"], "name": user["name"]})
    return {
        "token": token,
        "email": user["email"],
        "name": user["name"],
        "avatar": user["avatar"] or ""
    }

# --- Encrypted User Vault Operations ---
@router.post("/vault/save")
def save_vault_secret(req: SaveVaultRequest, user = Depends(get_current_user_from_token)):
    user_id = user["email"] if user["email"] else user["phone"]
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM user_vault WHERE user_identifier = ? AND note_title = ?", (user_id, req.title))
    row = cursor.fetchone()
    now = time.time()
    if row:
        cursor.execute("UPDATE user_vault SET note_content = ?, updated_at = ? WHERE id = ?", (req.content, now, row["id"]))
    else:
        cursor.execute("INSERT INTO user_vault (user_identifier, note_title, note_content, updated_at) VALUES (?, ?, ?, ?)", (user_id, req.title, req.content, now))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Vault node entry locked and persistent"}

@router.get("/vault/list")
def list_vault_secrets(user = Depends(get_current_user_from_token)):
    user_id = user["email"] if user["email"] else user["phone"]
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT note_title, note_content, updated_at FROM user_vault WHERE user_identifier = ? ORDER BY updated_at DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]