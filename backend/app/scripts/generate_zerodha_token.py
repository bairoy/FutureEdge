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
        from app.services.token_manager import encrypt_token

        # 1. Save token to Redis — encrypted, matching what the callback
        #    route writes and what brokers/zerodha.py expects to read.
        r = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            decode_responses=True,
        )
        r.set(KEY_ZERODHA_ACCESS_TOKEN, encrypt_token(access_token))
        r.close()

        # 2. Save token to .env file. This one stays plaintext on purpose:
        #    the env var is the manual operator fallback and is read without
        #    decryption. .env is gitignored — keep it that way.
        update_env_file("ZERODHA_ACCESS_TOKEN", access_token)

        print("\n" + "="*50)
        print("✅ SUCCESS — access token generated")
        print("="*50)
        # The token itself is deliberately not printed: it is live broker
        # credentials and would land in shell history/CI logs.
        print("👉 Stored encrypted in Redis, and written to your .env file.")
        print("="*50)
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m app.scripts.generate_zerodha_token <request_token>")
    else:
        generate_token(sys.argv[1])
