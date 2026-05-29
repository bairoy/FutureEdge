import sys
from kiteconnect import KiteConnect
from app.core.config import settings

def generate_token(request_token: str):
    print(f"🔄 Exchanging request_token for access_token...")
    
    kite = KiteConnect(api_key=settings.ZERODHA_API_KEY)
    
    try:
        data = kite.generate_session(request_token, api_secret=settings.ZERODHA_API_SECRET)
        access_token = data["access_token"]
        import redis
        from app.db.redis import KEY_ZERODHA_ACCESS_TOKEN
        from app.api.routes.zerodha_router import update_env_file

        # 1. Save token to Redis
        r = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            decode_responses=True,
        )
        r.set(KEY_ZERODHA_ACCESS_TOKEN, access_token)
        r.close()

        # 2. Save token to .env file
        update_env_file("ZERODHA_ACCESS_TOKEN", access_token)

        print("\n" + "="*50)
        print("✅ SUCCESS! YOUR ACCESS TOKEN:")
        print("="*50)
        print(f"\n{access_token}\n")
        print("="*50)
        print("👉 Updated in Redis and .env file automatically!")
        print("="*50)
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m app.scripts.generate_zerodha_token <request_token>")
    else:
        generate_token(sys.argv[1])
