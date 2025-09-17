import hmac
import hashlib
from typing import Optional, Dict, Any, List

import httpx

from .config import GRAPH_BASE, WHATSAPP_TOKEN, PHONE_NUMBER_ID, APP_SECRET_RAW

APP_SECRET = APP_SECRET_RAW.encode("utf-8") if APP_SECRET_RAW else b""

def verify_signature(signature: Optional[str], body: bytes) -> bool:
    if not APP_SECRET:
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    their_sig = signature.split("sha256=")[-1].strip()
    mac = hmac.new(APP_SECRET, msg=body, digestmod=hashlib.sha256)
    my_sig = mac.hexdigest()
    return hmac.compare_digest(my_sig, their_sig)

async def _post_messages(payload: Dict[str, Any]):
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers=headers, json=payload)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Log mínimo
            print(f"[GraphAPI] {e.response.status_code} - {e.response.text}")
            raise

async def send_text(to_msisdn: str, text: str):
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "text",
        "text": {"body": text}
    }
    await _post_messages(payload)

async def send_reply_buttons(
    to_msisdn: str, header: str, body: str, buttons: List[Dict], footer: str = ""
):
    action = {"buttons": [{"type": "reply", "reply": b} for b in buttons][:3]}
    interactive = {
        "type": "button",
        "header": {"type": "text", "text": header},
        "body": {"text": body},
        "action": action
    }
    if footer:
        interactive["footer"] = {"text": footer}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": interactive
    }
    await _post_messages(payload)

async def send_list(
    to_msisdn: str, header: str, body: str, button_text: str, rows: List[Dict],
    footer: str = "", section_title: str = "Opciones"
):
    action = {"button": button_text, "sections": [{"title": section_title, "rows": rows[:10]}]}
    interactive = {
        "type": "list",
        "header": {"type": "text", "text": header},
        "body": {"text": body},
        "action": action
    }
    if footer:
        interactive["footer"] = {"text": footer}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": interactive
    }
    await _post_messages(payload)

async def send_main_menu(to_msisdn: str):
    rows = [
        {"id": "menu_publish", "title": "Publicar un artículo", "description": "Ofrece algo en alquiler."},
        {"id": "menu_rent", "title": "Alquilar por ID", "description": "Inicia una solicitud de alquiler."},
        {"id": "menu_my_reviews", "title": "Ver mis reseñas", "description": "Lo que otros opinan de ti."},
        {"id": "menu_help", "title": "Ayuda y Comandos", "description": "Descubre más funciones."},
    ]
    await send_list(
        to_msisdn,
        "Menú de Renty",
        "¿Qué te gustaría hacer?",
        "Ver Opciones",
        rows,
        footer="Escribe MENU para volver aquí",
    )
