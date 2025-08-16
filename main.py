import os
import hmac
import hashlib
from typing import Optional, Any, Dict

from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.responses import PlainTextResponse
import httpx
import re

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "").encode("utf-8")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

app = FastAPI(title="WhatsApp Cloud API Webhook (Render/FastAPI)")

# -------------------- utils --------------------
def verify_signature(signature: Optional[str], body: bytes) -> bool:
    # En prod: exige firma; en dev puedes relajar si no hay APP_SECRET
    if not APP_SECRET:
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    their = signature.split("sha256=")[-1].strip()
    mac = hmac.new(APP_SECRET, msg=body, digestmod=hashlib.sha256)
    mine = mac.hexdigest()
    return hmac.compare_digest(mine, their)

async def _post_messages(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers=headers, json=payload)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError:
            # imprime el error de Graph para depurar
            print("Graph error:", r.status_code, r.text)
            raise
        return r.json()

# -------------------- send helpers --------------------
async def send_text(to_msisdn: str, text: str) -> Dict[str, Any]:
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "text",
        "text": {"body": text}
    }
    return await _post_messages(payload)

async def send_reply_buttons(
    to_msisdn: str,
    header_text: str,
    body_text: str,
    footer_text: str = "",
    buttons: Optional[list] = None
) -> Dict[str, Any]:
    # buttons: lista de dicts {"id":"rent_yes", "title":"Alquilar"}
    if not buttons:
        buttons = [
            {"id": "rent_yes", "title": "Alquilar"},
            {"id": "see_details", "title": "Ver detalles"},
            {"id": "cancel", "title": "Cancelar"}
        ]
    btns = [{"type": "reply", "reply": b} for b in buttons][:3]

    interactive = {
        "type": "button",
        "header": {"type": "text", "text": header_text},
        "body": {"text": body_text},
        "action": {"buttons": btns}
    }
    if footer_text:
        interactive["footer"] = {"text": footer_text}

    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": interactive
    }
    return await _post_messages(payload)

async def send_list(
    to_msisdn: str,
    header_text: str,
    body_text: str,
    button_text: str,
    rows: list,
    footer_text: str = "",
    section_title: str = "Opciones"
) -> Dict[str, Any]:
    # rows: [{"id":"publish_new","title":"Crear publicación"},
    #        {"id":"publish_from_template","title":"Usar plantilla"}]
    interactive = {
        "type": "list",
        "header": {"type": "text", "text": header_text},
        "body": {"text": body_text},
        "action": {
            "button": button_text,
            "sections": [
                {"title": section_title, "rows": rows[:10]}
            ]
        }
    }
    if footer_text:
        interactive["footer"] = {"text": footer_text}

    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": interactive
    }
    return await _post_messages(payload)

# -------------------- webhook verify --------------------
@app.get("/webhook")
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge or "", status_code=200)
    raise HTTPException(status_code=403, detail="Verification token mismatch")

# -------------------- webhook receive --------------------
@app.post("/webhook")
async def receive_webhook(request: Request):
    body_bytes = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")

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

                # ----- respuestas interactivas -----
                if msg_type == "interactive":
                    interactive = msg.get("interactive", {})
                    itype = interactive.get("type")

                    if itype == "button_reply":
                        btn = interactive.get("button_reply", {}) or {}
                        btn_id = btn.get("id")
                        btn_title = btn.get("title", "")

                        if btn_id == "rent_yes":
                            await send_text(from_msisdn, "¡Genial! ¿Qué fechas te sirven para el alquiler?")
                        elif btn_id == "see_details":
                            await send_text(from_msisdn, "Detalles del artículo #123:\n• Estado: excelente\n• Precio: $10/día\n• Depósito: $30")
                        elif btn_id == "cancel":
                            await send_text(from_msisdn, "Cancelado ✅. Si necesitas otra cosa, dime.")
                        else:
                            await send_text(from_msisdn, f"Seleccionaste: {btn_title}")
                        continue  # siguiente mensaje

                    if itype == "list_reply":
                        row = interactive.get("list_reply", {}) or {}
                        row_id = row.get("id")
                        row_title = row.get("title", "")
                        if row_id == "publish_new":
                            await send_text(from_msisdn, "Perfecto. Envíame el título del artículo para publicar.")
                        elif row_id == "publish_from_template":
                            await send_text(from_msisdn, "Te envío una plantilla para completar la publicación.")
                        else:
                            await send_text(from_msisdn, f"Opción elegida: {row_title}")
                        continue

                # ----- mensajes de texto -----
                text = ""
                if msg_type == "text":
                    text = (msg.get("text") or {}).get("body", "").strip()

                if text:
                    upper = text.upper()

                    # ALQUILAR #123 → extrae ID si viene
                    if upper.startswith("ALQUILAR"):
                        # intenta extraer un ID como número tras ALQUILAR
                        m = re.search(r"ALQUILAR\s*#?(\d+)", upper)
                        item_id = m.group(1) if m else "123"

                        try:
                            await send_reply_buttons(
                                to_msisdn=from_msisdn,
                                header_text=f"Artículo #{item_id}",
                                body_text="¿Qué te gustaría hacer?",
                                footer_text="Renty • Comunidad",
                                buttons=[
                                    {"id": "rent_yes", "title": "Alquilar"},
                                    {"id": "see_details", "title": "Ver detalles"},
                                    {"id": "cancel", "title": "Cancelar"},
                                ]
                            )
                        except httpx.HTTPStatusError as e:
                            print("Error al enviar botones:", e.response.text)

                        continue

                    if upper.startswith("PUBLICAR"):
                        try:
                            await send_list(
                                to_msisdn=from_msisdn,
                                header_text="Publicar artículo",
                                body_text="Elige cómo quieres publicar:",
                                button_text="Ver opciones",
                                footer_text="Renty • Comunidad",
                                rows=[
                                    {"id": "publish_new", "title": "Crear publicación"},
                                    {"id": "publish_from_template", "title": "Usar plantilla guiada"}
                                ],
                                section_title="Acciones"
                            )
                        except httpx.HTTPStatusError as e:
                            print("Error al enviar lista:", e.response.text)
                        continue

                    # respuesta por defecto
                    try:
                        await send_text(
                            from_msisdn,
                            "Recibido ✅. Escribe PUBLICAR para publicar un artículo o ALQUILAR #ID para iniciar un alquiler."
                        )
                    except httpx.HTTPStatusError as e:
                        print("Error al responder:", e.response.text)

    return Response(status_code=200)

@app.get("/")
async def health():
    return {"status": "ok"}
