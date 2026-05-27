import sys
from kiteconnect import KiteConnect
from app.core.config import settings

def generate_token(request_token: str):
    print(f"🔄 Exchanging request_token for access_token...")
    
    kite = KiteConnect(api_key=settings.ZERODHA_API_KEY)
    
    try:
        data = kite.generate_session(request_token, api_secret=settings.ZERODHA_API_SECRET)
        access_token = data["access_token"]
        
        print("\n" + "="*50)
        print("✅ SUCCESS! YOUR ACCESS TOKEN:")
        print("="*50)
        print(f"\n{access_token}\n")
        print("="*50)
        print("👉 Copy this token and paste it into your .env file:")
        print("   ZERODHA_ACCESS_TOKEN=your_copied_token")
        print("="*50)
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m app.scripts.generate_zerodha_token <request_token>")
    else:
        generate_token(sys.argv[1])
