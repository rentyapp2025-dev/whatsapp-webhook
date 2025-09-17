from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse

from .config import VERIFY_TOKEN
from .wa_api import verify_signature
from .handlers import handle_message

app = FastAPI(title="WhatsApp Cloud API Webhook for Renty")

@app.get("/webhook")
async def verify_webhook(req: Request):
    if req.query_params.get("hub.mode") == "subscribe" and req.query_params.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(req.query_params.get("hub.challenge"))
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhook")
async def receive_webhook(request: Request):
    body_bytes = await request.body()
    if not verify_signature(request.headers.get("X-Hub-Signature-256"), body_bytes):
        raise HTTPException(status_code=403, detail="Invalid signature")

    data = await request.json()
    if data.get("object") != "whatsapp_business_account":
        return Response(status_code=200)

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages")
            if not messages:
                continue
            for msg in messages:
                await handle_message(value, msg)

    return Response(status_code=200)

@app.get("/")
async def health_check():
    return {"status": "ok"}
