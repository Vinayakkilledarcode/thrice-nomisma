# setup_angelone.py
import pyotp
import httpx
import os

print("\n==================================================")
print("       ANGEL ONE SMARTAPI AUTO-SETUP UTILITY       ")
print("==================================================\n")

api_key = input("1. Enter your SmartAPI App API Key: ").strip()
client_code = input("2. Enter your Angel One Client ID (e.g., AACI642195): ").strip().upper()
mpin = input("3. Enter your 4-Digit Numeric MPIN (e.g., 1234): ").strip()
totp_key = input("4. Enter your TOTP QR Secret Key (Alphanumeric): ").strip().upper()

print("\nAttempting connection test to Angel One REST API...")

try:
    totp_code = pyotp.TOTP(totp_key).now()
    payload = {
        "clientcode": client_code,
        "password": mpin,
        "totp": totp_code
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-PrivateKey": api_key,
        "X-UserType": "USER",
        "X-SourceID": "WEB",
        "X-ClientLocalIP": "127.0.0.1",
        "X-ClientPublicIP": "127.0.0.1",
        "X-MACAddress": "00:00:00:00:00:00"
    }
    
    url = "https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword"
    res = httpx.post(url, json=payload, headers=headers, timeout=10.0)
    
    if res.status_code == 200:
        data = res.json()
        if data.get("status"):
            print("\n[SUCCESS] Authentication verified! Connection established successfully.")
            
            # Write credentials to .env file
            with open(".env", "a+") as f:
                f.write(f'\nANGEL_ONE_API_KEY="{api_key}"')
                f.write(f'\nANGEL_ONE_CLIENT_CODE="{client_code}"')
                f.write(f'\nANGEL_ONE_PASSWORD="{mpin}"')
                f.write(f'\nANGEL_ONE_TOTP_KEY="{totp_key}"\n')
                
            print("\nConfiguration written successfully to '.env'")
        else:
            print(f"\n[FAILED] Login rejected by broker: {data.get('message')}")
    else:
        print(f"\n[ERROR] Connection returned HTTP Status {res.status_code}: {res.text}")

except Exception as e:
    print(f"\n[CRITICAL ERROR] Handshake failed: {e}")