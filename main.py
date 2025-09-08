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

# Importa utilidades de BD desde tu propio cliente (REST/PostgREST)
from supabase_client import (
    ensure_user, get_user_name,
    set_session, get_session,
    insert_listing, get_listing,
    upsert_consent, set_consent_flag, get_consent,
    create_rental_request,
    BASE as SUPA_BASE,
    HEADERS as SUPA_HEADERS,
    HEADERS_RETURN as SUPA_HEADERS_RETURN,
)

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "").encode("utf-8")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

app = FastAPI(title="WhatsApp Cloud API Webhook (Render/FastAPI)")

# ==================== ENUM/STATE ====================
class Step(str, Enum):
    IDLE = "idle"
    PUBLISH_TITLE = "publish_title"
    PUBLISH_PRICE = "publish_price"
    PUBLISH_LOCATION = "publish_location"
    VERIFY_LOOKUP_WAIT_NUMBER = "verify_lookup_wait_number"

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
    # rows: [{"id":"publish_new","title":"Crear publicación", "description":"..."}, ...]
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

# ---------- MENÚ PRINCIPAL (LIST) ----------
async def send_main_menu(to_msisdn: str):
    rows = [
        {"id": "menu_publish",        "title": "Publicar artículo",            "description": "Crea una publicación"},
        {"id": "menu_rent",           "title": "Alquilar por ID",              "description": "Inicia una solicitud"},
        {"id": "menu_verify_me",      "title": "Verificar mi identidad",       "description": "Aumenta la confianza"},
        {"id": "menu_verify_lookup",  "title": "Buscar usuario verificado",     "description": "Consulta por teléfono"},
        {"id": "menu_help",           "title": "Ayuda",                         "description": "Cómo usar Renty"},
    ]
    return await send_list(
        to_msisdn,
        header_text="Renty",
        body_text="¿Qué te gustaría hacer?",
        button_text="Abrir menú",
        rows=rows,
        footer_text="Puedes escribir MENU en cualquier momento",
        section_title="Acciones",
    )

# ---------- verificación (simple usando reputation como flag) ----------
async def set_user_verified_flag(msisdn: str, value: bool) -> bool:
    """Marca verificado usando users.reputation (>=1 => verificado). Si tienes users.verified, cambia este PATCH."""
    payload = {"reputation": 1 if value else 0}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.patch(
            f"{SUPA_BASE}/users",
            headers=SUPA_HEADERS_RETURN,
            params={"wa_id": f"eq.{msisdn}", "select": "*"},
            json=payload,
        )
        r.raise_for_status()
        return True

async def is_user_verified(msisdn: str) -> Optional[bool]:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{SUPA_BASE}/users",
            headers=SUPA_HEADERS,
            params={"select": "reputation", "wa_id": f"eq.{msisdn}", "limit": 1},
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        return (rows[0].get("reputation") or 0) >= 1

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

async def introduce_parties(item_id: str, actor_msisdn: str | None = None):
    c = await get_consent(item_id)
    if not c:
        return
    buyer = c["buyer_wa"]
    seller = c["seller_wa"]
    buyer_name = await get_user_name(buyer)
    seller_name = await get_user_name(seller)

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

    # tras terminar el flujo, ofrece menú al actor (si viene de un botón)
    if actor_msisdn:
        await send_main_menu(actor_msisdn)

# ==================== helpers ====================
PHONE_RX = re.compile(r"\+?\d{7,15}")

def normalize_msisdn(s: str) -> str:
    # Convierte a solo dígitos (como viene en from: de WhatsApp)
    return re.sub(r"\D", "", s or "")

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
                # asegura registro mínimo de usuario
                await ensure_user(from_msisdn)
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
                                cons = await set_consent_flag(item_id, from_msisdn, ok=(answer == "yes"))
                                if not cons:
                                    await send_text(from_msisdn, "No encontré la solicitud. Usa: ALQUILAR #ID.")
                                    continue

                                # si ambos autorizaron, presentar contactos y luego menú
                                if cons.get("buyer_ok") and cons.get("seller_ok"):
                                    await send_text(from_msisdn, "¡Perfecto! Conectando a ambas partes…")
                                    await introduce_parties(item_id, actor_msisdn=from_msisdn)
                                else:
                                    # avisa a la otra parte
                                    other = cons["seller_wa"] if from_msisdn == cons["buyer_wa"] else cons["buyer_wa"]
                                    if answer == "yes":
                                        await send_text(from_msisdn, "Gracias. Esperamos la autorización de la otra parte.")
                                        await send_text(other, f"La otra parte ya autorizó. Falta tu confirmación para #{item_id}.")
                                    else:
                                        await send_text(from_msisdn, "Entendido. No compartiremos tus datos.")
                                        await send_text(other, "La otra parte no autorizó compartir contacto. Conversación cerrada.")
                                        await send_main_menu(from_msisdn)
                            continue

                        # otros botones de ejemplo
                        if btn_id == "rent_yes":
                            await send_text(from_msisdn, "¡Genial! ¿Qué fechas te sirven para el alquiler? (formato: YYYY-MM-DD a YYYY-MM-DD)")
                            continue
                        if btn_id == "see_details":
                            await send_text(from_msisdn, "Detalles del artículo:\n• Estado: excelente\n• Precio: consultar publicación\n• Depósito: según acuerdo")
                            return Response(status_code=200)
                        if btn_id == "cancel":
                            await send_text(from_msisdn, "Cancelado ✅.")
                            await send_main_menu(from_msisdn)
                            continue

                        # Fallback para botones desconocidos
                        await send_text(from_msisdn, f"Seleccionaste: {btn_title}")
                        continue  # siguiente mensaje

                    # ----- lista -----
                    if itype == "list_reply":
                        row = interactive.get("list_reply", {}) or {}
                        row_id = row.get("id")
                        row_title = row.get("title", "")

                        if row_id == "menu_publish":
                            await set_session(from_msisdn, Step.PUBLISH_TITLE, {"title": "", "price": "", "location": ""})
                            await send_text(from_msisdn, "Perfecto. Dime el *título* del artículo.")
                            continue

                        if row_id == "menu_rent":
                            await send_text(from_msisdn, "Para alquilar, envía: ALQUILAR #ID (ej: ALQUILAR #123)")
                            continue

                        if row_id == "menu_verify_me":
                            await set_user_verified_flag(from_msisdn, True)
                            await send_text(from_msisdn, "Tu cuenta quedó *verificada* ✅. ¡Gracias!")
                            await send_main_menu(from_msisdn)
                            continue

                        if row_id == "menu_verify_lookup":
                            await set_session(from_msisdn, Step.VERIFY_LOOKUP_WAIT_NUMBER, {})
                            await send_text(from_msisdn, "Envíame el *número de WhatsApp* del usuario (E.164, ej: +584123456789) para consultar si está verificado.")
                            continue

                        if row_id == "menu_help":
                            await send_text(from_msisdn, "Ayuda rápida:\n• PUBLICAR: crea un artículo\n• ALQUILAR #ID: inicia solicitud\n• Verificación: mejora la confianza entre usuarios\n• Escribe MENU para ver las opciones")
                            continue

                        # Fallback
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

                # Comando explícito para mostrar menú
                if upper in {"MENU", "MENÚ"}:
                    await send_main_menu(from_msisdn)
                    continue

                if text:
                    # ---- flujo de consulta de verificación ----
                    st = await get_session(from_msisdn)
                    if st.get("step") == Step.VERIFY_LOOKUP_WAIT_NUMBER:
                        if not PHONE_RX.fullmatch(text):
                            await send_text(from_msisdn, "Por favor envía un número válido (7-15 dígitos, puede empezar con +).")
                            continue
                        lookup = normalize_msisdn(text)
                        status = await is_user_verified(lookup)
                        if status is None:
                            await send_text(from_msisdn, "Ese número aún no está registrado en Renty.")
                        elif status:
                            await send_text(from_msisdn, "✅ Usuario *verificado*.")
                        else:
                            await send_text(from_msisdn, "❌ Usuario *no verificado*.")
                        await set_session(from_msisdn, Step.IDLE, {})
                        await send_main_menu(from_msisdn)
                        continue

                    # ---- flujo ALQUILAR #ID (acepta ALQUILAR en cualquier parte) ----
                    if "ALQUILAR" in upper:
                        m = re.search(r"ALQUILAR\s*#?(\d+)", upper)
                        item_id = (m.group(1) if m else "").strip()
                        listing = await get_listing(item_id) if item_id else None
                        if not listing:
                            await send_text(from_msisdn, "No encuentro ese artículo. Asegúrate de usar: ALQUILAR #ID")
                            continue

                        seller = listing["owner_wa"]
                        buyer = from_msisdn
                        await upsert_consent(item_id, buyer, seller)

                        await send_consent_buttons(buyer, "comprador", item_id)
                        await send_consent_buttons(seller, "vendedor", item_id)
                        await send_text(buyer, "Te pedimos autorización para compartir tu contacto con el vendedor.")
                        await send_text(seller, f"Tienes una solicitud de alquiler para #{item_id}. ¿Autorizas compartir tu contacto?")
                        continue

                    # ---- captar fechas para crear solicitud de rental (opcional MVP) ----
                    # Formato esperado: "DEL 2025-09-10 AL 2025-09-12" o "2025-09-10 a 2025-09-12"
                    if re.search(r"\d{4}-\d{2}-\d{2}.*\d{4}-\d{2}-\d{2}", text):
                        m_id = re.search(r"#(\d+)", text)
                        if not m_id:
                            await send_text(from_msisdn, "Para crear la solicitud necesito el ID del artículo. Ej: ALQUILAR #123 del 2025-09-10 al 2025-09-12")
                            continue
                        item_id = m_id.group(1)
                        listing = await get_listing(item_id)
                        if not listing:
                            await send_text(from_msisdn, "No encuentro ese artículo. Revisa el ID.")
                            continue
                        m_dates = re.findall(r"(\d{4}-\d{2}-\d{2})", text)
                        if len(m_dates) >= 2:
                            start_iso, end_iso = m_dates[0], m_dates[1]
                            result = await create_rental_request(int(item_id), from_msisdn, start_iso, end_iso)
                            if result.get("ok"):
                                await send_text(from_msisdn, f"Solicitud registrada para #{item_id} del {start_iso} al {end_iso}. Estado: requested ✅")
                            else:
                                if result.get("error") == "FECHAS_NO_DISPONIBLES":
                                    await send_text(from_msisdn, "Lo siento, esas fechas no están disponibles para ese artículo.")
                                else:
                                    await send_text(from_msisdn, "Hubo un problema al registrar tu solicitud. Intenta de nuevo más tarde.")
                            continue

                    # ---- flujo PUBLICAR (acepta PUBLICAR en cualquier parte) ----
                    if "PUBLICAR" in upper:
                        await set_session(from_msisdn, Step.PUBLISH_TITLE, {"title": "", "price": "", "location": ""})
                        await send_text(from_msisdn, "¡Genial! Dime el *título* del artículo.")
                        continue

                    # ---- pasos de publicación ----
                    st = await get_session(from_msisdn)
                    if st.get("step") == Step.PUBLISH_TITLE:
                        st["draft"]["title"] = text
                        await set_session(from_msisdn, Step.PUBLISH_PRICE, st["draft"])
                        await send_text(from_msisdn, "Anota el *precio por día* (ej: 10 USD).")
                        continue

                    if st.get("step") == Step.PUBLISH_PRICE:
                        st["draft"]["price"] = text
                        await set_session(from_msisdn, Step.PUBLISH_LOCATION, st["draft"])
                        await send_text(from_msisdn, "¿En qué *ciudad* está el artículo?")
                        continue

                    if st.get("step") == Step.PUBLISH_LOCATION:
                        st["draft"]["location"] = text
                        d = st["draft"]
                        item_id = await insert_listing(from_msisdn, d["title"], d["price"], d["location"])
                        await set_session(from_msisdn, Step.IDLE, {})
                        await send_text(
                            from_msisdn,
                            f"¡Listo! Publicación creada con ID #{item_id}:\n"
                            f"• {d['title']}\n• Precio/día: {d['price']}\n• Ciudad: {d['location']}\n"
                            f"Estado: activa ✅"
                        )
                        # Al terminar un flujo, mostramos menú
                        await send_main_menu(from_msisdn)
                        continue

                    # ---- respuesta por defecto ----
                    # Si el usuario está idle y no reconocimos el comando, mostramos el menú (sin spam durante flujos)
                    if (st.get("step") == Step.IDLE):
                        await send_main_menu(from_msisdn)
                    else:
                        await send_text(from_msisdn, "Entendido. Continúa con el flujo actual o escribe MENU para ver opciones.")
                    continue

    return Response(status_code=200)

@app.get("/")
async def health():
    return {"status": "ok"}
