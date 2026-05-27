from fastapi import APIRouter, Request

router = APIRouter()

@router.get("/auth/zerodha/callback")
async def zerodha_callback(request: Request):

    request_token = request.query_params.get("request_token")

    return {
        "request_token": request_token
    }