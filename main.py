import os
import hmac
import hashlib
from typing import Optional, Any, Dict

from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.responses import PlainTextResponse
import httpx

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "").encode("utf-8")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

app = FastAPI(title="WhatsApp Cloud API Webhook (Render/FastAPI)")

def verify_signature(signature: Optional[str], body: bytes) -> bool:
    if not signature or not signature.startswith("sha256=") or not APP_SECRET:
        return False
    their = signature.split("sha256=")[-1].strip()
    mac = hmac.new(APP_SECRET, msg=body, digestmod=hashlib.sha256)
    mine = mac.hexdigest()
    return hmac.compare_digest(mine, their)

async def send_text(to_msisdn: str, text: str) -> Dict[str, Any]:
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "text",
        "text": {"body": text}
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge or "", status_code=200)
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhook")
async def receive_webhook(request: Request):
    body_bytes = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")

    # valida la firma (recomendado en prod)
    if not verify_signature(signature, body_bytes):
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
                from_msisdn = msg.get("from")
                msg_type = msg.get("type")
                if msg_type == "text":
                    text = (msg.get("text") or {}).get("body", "").strip()
                else:
                    text = ""

                reply = "Recibido ✅. Escribe PUBLICAR o ALQUILAR #ID."
                if text.upper().startswith("PUBLICAR"):
                    reply = "¡Perfecto! Empecemos tu publicación. ¿Título del artículo?"
                elif text.upper().startswith("ALQUILAR"):
                    reply = "¿Autorizas compartir tu contacto con el vendedor? Responde SI/NO."

                try:
                    await send_text(from_msisdn, reply)
                except httpx.HTTPStatusError as e:
                    print("Error al responder:", e.response.text)

    return Response(status_code=200)

@app.get("/")
async def health():
    return {"status": "ok"}
