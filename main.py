import os
import hmac
import hashlib
import json
import re
from enum import Enum
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

# ==================== almacenamiento efímero (demo) ====================
USERS: Dict[str, Dict[str, str]] = {}        # { msisdn: {"name": "..."} }
LISTINGS: Dict[str, Dict[str, str]] = {}     # { "123": {"owner": "+58...", "title":"...", "price":"...", "location":"...", "status":"publicado"} }
CONSENTS: Dict[str, Dict[str, Any]] = {}     # { "123": {"buyer":"+58...", "seller":"+58...", "buyer_ok":False, "seller_ok":False} }
STATE: Dict[str, Dict[str, Any]] = {}        # { msisdn: {"step": "...", "draft": {...}} }

class Step(str, Enum):
    IDLE = "idle"
    PUBLISH_TITLE = "publish_title"
    PUBLISH_PRICE = "publish_price"
    PUBLISH_LOCATION = "publish_location"

def get_user(msisdn: str) -> dict:
    return USERS.setdefault(msisdn, {"name": msisdn})

def set_state(msisdn: str, step: Step, draft: dict | None = None):
    STATE[msisdn] = {"step": step, "draft": draft or {}}

def get_state(msisdn: str) -> dict:
    return STATE.get(msisdn, {"step": Step.IDLE, "draft": {}})

def save_listing(owner: str, title: str, price: str, location: str) -> str:
    new_id = str(100 + len(LISTINGS))
    LISTINGS[new_id] = {
        "owner": owner,
        "title": title,
        "price": price,
        "location": location,
        "status": "publicado"
    }
    return new_id

# ==================== utilidades WhatsApp ====================
def verify_signature(signature: Optional[str], body: bytes) -> bool:
    # En prod: exige firma; para pruebas permite si no hay APP_SECRET
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
            print("Graph error:", r.status_code, r.text)
            raise
        return r.json()

# ---------- helpers de envío ----------
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
    # buttons: [{"id":"rent_yes", "title":"Alquilar"}, ...]  (máx 3)
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
    # rows: [{"id":"publish_new","title":"Crear publicación"}, ...]  (máx 10 por sección)
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

# ---------- consentimiento + contactos ----------
async def send_consent_buttons(to_msisdn: str, role: str, item_id: str):
    body = (
        f"¿Autorizas que compartamos tu contacto con la otra parte para el artículo #{item_id}?"
        f"\nRol: {role.capitalize()}"
    )
    return await send_reply_buttons(
        to_msisdn,
        header_text="Consentimiento",
        body_text=body,
        footer_text="Renty • Privacidad",
        buttons=[
            {"id": f"consent_yes_{item_id}", "title": "Sí, autorizo"},
            {"id": f"consent_no_{item_id}",  "title": "No"}
        ]
    )

def build_vcard(display_name: str, phone_e164: str) -> dict:
    vcard_text = (
        "BEGIN:VCARD\n"
        "VERSION:3.0\n"
        f"N:{display_name};;;;\n"
        f"FN:{display_name}\n"
        f"TEL;type=CELL;type=VOICE;waid={phone_e164}:{phone_e164}\n"
        "END:VCARD"
    )
    return {
        "contacts": [
            {
                "name": {
                    "formatted_name": display_name,
                    "first_name": display_name
                },
                "phones": [
                    {"phone": phone_e164, "type": "CELL", "wa_id": phone_e164}
                ],
                "vcard": vcard_text
            }
        ]
    }

async def send_contact(to_msisdn: str, display_name: str, phone_e164: str):
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "contacts",
        **build_vcard(display_name, phone_e164)
    }
    return await _post_messages(payload)

async def introduce_parties(item_id: str):
    c = CONSENTS.get(item_id)
    if not c:
        return
    buyer, seller = c["buyer"], c["seller"]
    buyer_name = get_user(buyer)["name"]
    seller_name = get_user(seller)["name"]

    # envía contactos cruzados
    await send_contact(buyer, seller_name, seller)
    await send_contact(seller, buyer_name, buyer)

    # mensaje de presentación
    await send_text(
        buyer,
        f"Les presento a {seller_name} (vendedor) para coordinar el alquiler del artículo #{item_id}. ¡Éxitos! ✨"
    )
    await send_text(
        seller,
        f"{buyer_name} está interesado en el artículo #{item_id}. Ya tienen sus contactos para coordinar."
    )

# ==================== endpoints ====================
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

    if not verify_signature(signature, body_bytes):
        raise HTTPException(status_code=403, detail="Invalid signature")

    data = await request.json()
    # debug opcional:
    #print(json.dumps(data, indent=2, ensure_ascii=False))

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
                get_user(from_msisdn)  # asegura registro mínimo
                msg_type = msg.get("type")

                # ========== respuestas interactivas ==========
                if msg_type == "interactive":
                    interactive = msg.get("interactive", {})
                    itype = interactive.get("type")

                    # ----- botones -----
                    if itype == "button_reply":
                        btn = interactive.get("button_reply", {}) or {}
                        btn_id = btn.get("id")
                        btn_title = btn.get("title", "")

                        # consentimiento: consent_yes_<ID> / consent_no_<ID>
                        if btn_id and btn_id.startswith("consent_"):
                            parts = btn_id.split("_")
                            if len(parts) == 3:
                                answer, item_id = parts[1], parts[2]
                                c = CONSENTS.get(item_id)
                                if not c:
                                    await send_text(from_msisdn, "No encontré la solicitud. Usa: ALQUILAR #ID.")
                                    continue

                                role = "buyer" if from_msisdn == c["buyer"] else "seller"
                                key = f"{role}_ok"
                                c[key] = (answer == "yes")

                                if c["buyer_ok"] and c["seller_ok"]:
                                    await send_text(from_msisdn, "¡Perfecto! Conectando a ambas partes…")
                                    await introduce_parties(item_id)
                                else:
                                    other = c["seller"] if role == "buyer" else c["buyer"]
                                    if answer == "yes":
                                        await send_text(from_msisdn, "Gracias. Esperamos la autorización de la otra parte.")
                                        await send_text(other, f"La otra parte ya autorizó. Falta tu confirmación para #{item_id}.")
                                    else:
                                        await send_text(from_msisdn, "Entendido. No compartiremos tus datos.")
                                        await send_text(other, "La otra parte no autorizó compartir contacto. Conversación cerrada.")
                            continue

                        # otros botones de ejemplo
                        if btn_id == "rent_yes":
                            await send_text(from_msisdn, "¡Genial! ¿Qué fechas te sirven para el alquiler?")
                        elif btn_id == "see_details":
                            await send_text(from_msisdn, "Detalles del artículo #123:\n• Estado: excelente\n• Precio: $10/día\n• Depósito: $30")
                        elif btn_id == "cancel":
                            await send_text(from_msisdn, "Cancelado ✅. Si necesitas otra cosa, dime.")
                        else:
                            await send_text(from_msisdn, f"Seleccionaste: {btn_title}")
                        continue  # siguiente mensaje

                    # ----- lista -----
                    if itype == "list_reply":
                        row = interactive.get("list_reply", {}) or {}
                        row_id = row.get("id")
                        row_title = row.get("title", "")
                        if row_id == "publish_new":
                            set_state(from_msisdn, Step.PUBLISH_TITLE, {"title": "", "price": "", "location": ""})
                            await send_text(from_msisdn, "Perfecto. Dime el *título* del artículo.")
                        elif row_id == "publish_from_template":
                            await send_text(from_msisdn, "Te envío una plantilla para completar la publicación.")
                        else:
                            await send_text(from_msisdn, f"Opción elegida: {row_title}")
                        continue

                # ========== mensajes de texto ==========
                # Extrae texto robustamente: usa body si es text, o caption si vino con imagen/documento
                text = ""
                if msg_type == "text":
                    text = (msg.get("text") or {}).get("body", "") or ""
                else:
                    text = (msg.get("caption") or "")  # algunos tipos traen caption
                text = text.strip()
                upper = text.upper()

                if text:
                    # ---- flujo ALQUILAR #ID (acepta ALQUILAR en cualquier parte) ----
                    if "ALQUILAR" in upper:
                        m = re.search(r"ALQUILAR\s*#?(\d+)", upper)
                        item_id = (m.group(1) if m else "").strip()
                        if not item_id or item_id not in LISTINGS:
                            await send_text(from_msisdn, "No encuentro ese artículo. Asegúrate de usar: ALQUILAR #ID")
                            continue

                        listing = LISTINGS[item_id]
                        seller = listing["owner"]
                        buyer = from_msisdn
                        CONSENTS[item_id] = {"buyer": buyer, "seller": seller, "buyer_ok": False, "seller_ok": False}

                        await send_consent_buttons(buyer, "comprador", item_id)
                        await send_consent_buttons(seller, "vendedor", item_id)
                        await send_text(buyer, "Te pedimos autorización para compartir tu contacto con el vendedor.")
                        await send_text(seller, f"Tienes una solicitud de alquiler para #{item_id}. ¿Autorizas compartir tu contacto?")
                        continue

                    # ---- flujo PUBLICAR (acepta PUBLICAR en cualquier parte) ----
                    if "PUBLICAR" in upper:
                        set_state(from_msisdn, Step.PUBLISH_TITLE, {"title": "", "price": "", "location": ""})
                        await send_text(from_msisdn, "¡Genial! Dime el *título* del artículo.")
                        continue

                    # ---- pasos de publicación ----
                    st = get_state(from_msisdn)
                    if st["step"] == Step.PUBLISH_TITLE:
                        st["draft"]["title"] = text
                        set_state(from_msisdn, Step.PUBLISH_PRICE, st["draft"])
                        await send_text(from_msisdn, "Anota el *precio por día* (ej: 10 USD).")
                        continue

                    if st["step"] == Step.PUBLISH_PRICE:
                        st["draft"]["price"] = text
                        set_state(from_msisdn, Step.PUBLISH_LOCATION, st["draft"])
                        await send_text(from_msisdn, "¿En qué *ciudad* está el artículo?")
                        continue

                    if st["step"] == Step.PUBLISH_LOCATION:
                        st["draft"]["location"] = text
                        d = st["draft"]
                        item_id = save_listing(from_msisdn, d["title"], d["price"], d["location"])
                        set_state(from_msisdn, Step.IDLE, {})
                        await send_text(
                            from_msisdn,
                            f"¡Listo! Publicación creada con ID #{item_id}:\n"
                            f"• {d['title']}\n• Precio/día: {d['price']}\n• Ciudad: {d['location']}\n"
                            f"Estado: pendiente de moderación ✅"
                        )
                        continue

                    # ---- respuesta por defecto ----
                    await send_text(
                        from_msisdn,
                        "Recibido ✅. Escribe PUBLICAR para crear un artículo o ALQUILAR #ID para iniciar un alquiler."
                    )
                    continue

    return Response(status_code=200)

@app.get("/")
async def health():
    return {"status": "ok"}
