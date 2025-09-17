import os
import hmac
import hashlib
import re
from enum import Enum
from typing import Optional, Any, Dict, List
from datetime import datetime, date, timedelta
from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.responses import PlainTextResponse
import httpx

# --- Importaciones del cliente de base de datos ---
# Asegúrate de que tu archivo supabase_client.py contenga todas estas funciones.
from supabase_client import (
    ensure_user, get_user_name,
    set_session, get_session,
    insert_listing, get_listing,
    upsert_consent, set_consent_flag, get_consent,
    create_rental_request,
    mark_introduced_once,
    # --- NUEVAS FUNCIONES REQUERIDAS ---
    get_active_rentals_for_item,
    update_listing_status,
    add_review,
    get_reviews_for_user,
    request_rental_cancellation,
)

# --- Constantes y Configuración ---
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "").encode("utf-8")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# === Credenciales para PostgREST (no es necesario si usas supabase_client)
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPA_BASE = f"{SUPABASE_URL}/rest/v1"
SUPA_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
}

app = FastAPI(title="WhatsApp Cloud API Webhook (Render/FastAPI)")

# ==================== MÁQUINA DE ESTADOS (STATE MACHINE) ====================
class Step(str, Enum):
    IDLE = "idle"
    PUBLISH_TITLE = "publish_title"
    PUBLISH_PRICE = "publish_price"
    PUBLISH_ZONE = "publish_zone"
    PUBLISH_PAYMENTS = "publish_payments"
    VERIFY_LOOKUP_WAIT_NUMBER = "verify_lookup_wait_number"
    RENTAL_WAIT_DATES = "rental_wait_dates"
    RENTAL_WAIT_PAYMENT = "rental_wait_payment"  # <-- NUEVO STEP

def step_val(st: Dict[str, Any] | None) -> str:
    v = (st or {}).get("step")
    return v.value if isinstance(v, Step) else v or Step.IDLE.value

# ==================== UTILIDADES DE WHATSAPP API ====================
def verify_signature(signature: Optional[str], body: bytes) -> bool:
    if not APP_SECRET: return True
    if not signature or not signature.startswith("sha256="): return False
    their_sig = signature.split("sha256=")[-1].strip()
    mac = hmac.new(APP_SECRET, msg=body, digestmod=hashlib.sha256)
    my_sig = mac.hexdigest()
    return hmac.compare_digest(my_sig, their_sig)

async def _post_messages(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            print(f"Error en Graph API: {e.response.status_code} - {e.response.text}")
            raise

async def send_text(to_msisdn: str, text: str):
    payload = {"messaging_product": "whatsapp", "to": to_msisdn, "type": "text", "text": {"body": text}}
    await _post_messages(payload)

async def send_reply_buttons(to_msisdn: str, header: str, body: str, buttons: List[Dict], footer: str = ""):
    action = {"buttons": [{"type": "reply", "reply": b} for b in buttons][:3]}
    interactive = {"type": "button", "header": {"type": "text", "text": header}, "body": {"text": body}, "action": action}
    if footer: interactive["footer"] = {"text": footer}
    payload = {"messaging_product": "whatsapp", "to": to_msisdn, "type": "interactive", "interactive": interactive}
    await _post_messages(payload)

async def send_list(to_msisdn: str, header: str, body: str, button_text: str, rows: List[Dict], footer: str = "", section_title: str = "Opciones"):
    action = {"button": button_text, "sections": [{"title": section_title, "rows": rows[:10]}]}
    interactive = {"type": "list", "header": {"type": "text", "text": header}, "body": {"text": body}, "action": action}
    if footer: interactive["footer"] = {"text": footer}
    payload = {"messaging_product": "whatsapp", "to": to_msisdn, "type": "interactive", "interactive": interactive}
    await _post_messages(payload)

async def send_main_menu(to_msisdn: str):
    rows = [
        {"id": "menu_publish", "title": "Publicar un artículo", "description": "Ofrece algo en alquiler."},
        {"id": "menu_rent", "title": "Alquilar por ID", "description": "Inicia una solicitud de alquiler."},
        {"id": "menu_my_reviews", "title": "Ver mis reseñas", "description": "Lo que otros opinan de ti."},
        {"id": "menu_help", "title": "Ayuda y Comandos", "description": "Descubre más funciones."},
    ]
    await send_list(to_msisdn, "Menú de Renty", "¿Qué te gustaría hacer?", "Ver Opciones", rows, footer="Escribe MENU para volver aquí")

# ==================== HELPERS Y UTILIDADES DEL BOT ====================
def _parse_date_any(s: str) -> Optional[date]:
    if not s: return None
    s = s.strip()
    try:
        if m := re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", s):
            d, mth, y = map(int, m.groups()); return date(y, mth, d)
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception: return None

def _to_ve(d: date | str) -> str:
    d_obj = _parse_date_any(d) if isinstance(d, str) else d
    return d_obj.strftime("%d/%m/%Y") if d_obj else str(d)

def _extract_dates(text: str) -> Optional[tuple[str, str]]:
    dates = re.findall(r"\b(\d{2}/\d{2}/\d{4})\b", text)
    if len(dates) >= 2:
        d1, d2 = _parse_date_any(dates[0]), _parse_date_any(dates[1])
        if d1 and d2 and d1 <= d2: return d1.isoformat(), d2.isoformat()
    return None

async def finalize_and_introduce(item_id: str, actor_msisdn: str):
    """Lógica centralizada para finalizar una solicitud y presentar a las partes."""
    cons = await get_consent(item_id)
    if not cons: return

    # 1. Crear la solicitud de alquiler formalmente en la BD
    buyer_wa = cons["buyer_wa"]
    st = await get_session(buyer_wa)
    draft = st.get("draft", {})
    if 'start_iso' in draft and 'end_iso' in draft and 'selected_payment_method' in draft:
        await create_rental_request(
            int(item_id), buyer_wa, draft['start_iso'], draft['end_iso'], draft['selected_payment_method']
        )

    # 2. Presentar a las partes
    if await mark_introduced_once(item_id):
        # (Aquí iría tu lógica de `introduce_parties` si la necesitas)
        buyer_name = await get_user_name(cons["buyer_wa"])
        seller_name = await get_user_name(cons["seller_wa"])
        await send_text(cons["buyer_wa"], f"¡Acuerdo logrado! Ya puedes coordinar con {seller_name} (vendedor) el alquiler del artículo #{item_id}.")
        await send_text(cons["seller_wa"], f"¡Acuerdo logrado! {buyer_name} (comprador) te contactará para coordinar el alquiler del artículo #{item_id}.")

    # 3. Limpiar sesión y enviar menú al usuario que actuó
    await set_session(actor_msisdn, Step.IDLE, {})
    await send_main_menu(actor_msisdn)


# ==================== WEBHOOK ENDPOINTS ====================
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
    if data.get("object") != "whatsapp_business_account": return Response(status_code=200)

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages")
            if not messages: continue

            for msg in messages:
                from_msisdn = msg["from"]
                # --- CAMBIO: Capturar y guardar el nombre de perfil de WhatsApp ---
                profile_name = (value.get("contacts", [{}])[0].get("profile", {}) or {}).get("name")
                await ensure_user(from_msisdn, profile_name)

                st = await get_session(from_msisdn)
                s = step_val(st)

                # --- MANEJO DE RESPUESTAS INTERACTIVAS ---
                if msg["type"] == "interactive":
                    interactive, itype = msg["interactive"], msg["interactive"]["type"]

                    if itype == "button_reply":
                        btn_id = interactive["button_reply"]["id"]
                        if btn_id.startswith("consent_"):
                            answer, item_id = btn_id.split("_")[1], btn_id.split("_")[2]
                            cons = await set_consent_flag(item_id, from_msisdn, ok=(answer == "yes"))
                            if not cons:
                                await send_text(from_msisdn, "No se encontró la solicitud de alquiler."); continue
                            
                            if cons.get("buyer_ok") and cons.get("seller_ok"):
                                await finalize_and_introduce(item_id, from_msisdn)
                            elif answer == "no":
                                other = cons["seller_wa"] if from_msisdn == cons["buyer_wa"] else cons["buyer_wa"]
                                await send_text(from_msisdn, "Entendido. Tu decisión fue registrada.")
                                await send_text(other, "La otra parte ha rechazado la solicitud. La operación se canceló.")
                            else:
                                await send_text(from_msisdn, "Gracias. Esperamos la respuesta de la otra parte.")
                        continue

                    if itype == "list_reply":
                        row_id, row_title = interactive["list_reply"]["id"], interactive["list_reply"]["title"]

                        # Flujo de alquiler: Selección de método de pago
                        if s == Step.RENTAL_WAIT_PAYMENT:
                            draft = st["draft"]
                            draft["selected_payment_method"] = row_title
                            await set_session(from_msisdn, Step.IDLE, draft) # Guardamos el borrador completo
                            
                            item_id, start_iso, end_iso = str(draft['item_id']), draft['start_iso'], draft['end_iso']
                            listing = await get_listing(item_id)
                            seller, buyer = listing["owner_wa"], from_msisdn
                            
                            await upsert_consent(item_id, buyer, seller)
                            
                            # --- CAMBIO: Mensaje de autorización al vendedor con toda la información ---
                            msg_to_seller = (f"¡Nueva solicitud para tu artículo #{item_id}!\n\n"
                                             f"Fechas: del *{_to_ve(start_iso)}* al *{_to_ve(end_iso)}*\n"
                                             f"Método de pago: *{row_title}*\n\n"
                                             "¿Aceptas compartir tu contacto para coordinar?")
                            buttons = [{"id": f"consent_yes_{item_id}", "title": "Sí, acepto"}, {"id": f"consent_no_{item_id}", "title": "No, gracias"}]
                            await send_reply_buttons(seller, "Confirmación de Alquiler", msg_to_seller, buttons)

                            await send_text(buyer, "¡Excelente! Hemos enviado tu solicitud al dueño. Te notificaremos su respuesta.")
                            await set_session(from_msisdn, Step.IDLE, {})
                            continue

                        # Manejo del Menú Principal
                        if row_id == "menu_publish": await set_session(from_msisdn, Step.PUBLISH_TITLE, {}); await send_text(from_msisdn, "¡Vamos a publicar! Primero, dime el *título* de tu artículo.")
                        elif row_id == "menu_rent": await send_text(from_msisdn, "Para alquilar, escribe: ALQUILAR #ID (ej: ALQUILAR #123)")
                        elif row_id == "menu_my_reviews": await send_text(from_msisdn, "Para ver las reseñas que te han dejado, escribe: MIS RESEÑAS")
                        elif row_id == "menu_help": await send_text(from_msisdn, "Comandos útiles:\n- `ELIMINAR #ID`: Quita una publicación.\n- `CANCELAR RENTA #ID`: Cancela una renta.\n- `RESEÑA #ID_RENTA 1-5`: Deja una opinión.")
                        continue

                # --- MANEJO DE MENSAJES DE TEXTO ---
                text = (msg.get("text", {}).get("body", "")).strip()
                if not text: continue
                upper = text.upper()

                if upper in {"MENU", "MENÚ", "INICIO"}:
                    await set_session(from_msisdn, Step.IDLE, {}); await send_main_menu(from_msisdn); continue

                # --- NUEVOS COMANDOS DE TEXTO ---
                if upper.startswith("RESEÑA #"):
                    match = re.search(r"RESEÑA\s*#(\d+)\s*([1-5])\s*(.*)", text, re.IGNORECASE)
                    if not match: await send_text(from_msisdn, "Formato: RESEÑA #ID_RENTA CALIFICACIÓN COMENTARIO"); continue
                    rental_id, rating, comment = match.groups()
                    result = await add_review(int(rental_id), from_msisdn, int(rating), comment.strip())
                    if result.get("ok"): await send_text(from_msisdn, "¡Gracias por tu reseña!")
                    else: await send_text(from_msisdn, f"Error: {result.get('error', 'No se pudo guardar la reseña.')}")
                    continue

                if upper == "MIS RESEÑAS":
                    reviews = await get_reviews_for_user(from_msisdn)
                    if not reviews: await send_text(from_msisdn, "Aún no has recibido ninguna reseña."); continue
                    response = "Reseñas que te han dejado:\n\n" + "\n".join([f"⭐️ {r['rating']}/5: \"{r['comment'] or 'Sin comentario.'}\"" for r in reviews])
                    await send_text(from_msisdn, response); continue

                if upper.startswith("ELIMINAR #"):
                    item_id = text.split("#")[-1].strip()
                    if item_id.isdigit():
                        if await get_active_rentals_for_item(item_id):
                            await send_text(from_msisdn, f"No puedes eliminar la publicación #{item_id} porque tiene rentas activas.")
                        elif await update_listing_status(item_id, from_msisdn, "inactive"):
                            await send_text(from_msisdn, f"Publicación #{item_id} eliminada.")
                        else: await send_text(from_msisdn, f"No se pudo eliminar. Asegúrate de que el ID sea correcto y seas el dueño.")
                    else: await send_text(from_msisdn, "Proporciona un ID de artículo válido."); continue

                if upper.startswith("CANCELAR RENTA #"):
                    rental_id = text.split("#")[-1].strip()
                    if rental_id.isdigit():
                        result = await request_rental_cancellation(int(rental_id), from_msisdn)
                        status = result.get("status")
                        if status == "CANCELLED":
                            for party in result.get("parties", []): await send_text(party, f"La renta #{rental_id} fue cancelada por mutuo acuerdo.")
                        elif status == "WAITING_OTHER":
                            await send_text(from_msisdn, f"Solicitud de cancelación enviada. La otra parte debe confirmarla.")
                            await send_text(result.get("other_party"), f"El otro usuario quiere cancelar la renta #{rental_id}. Para aceptar, escribe: CANCELAR RENTA #{rental_id}")
                        else: await send_text(from_msisdn, "No se encontró la renta o no se puede cancelar.")
                    else: await send_text(from_msisdn, "Proporciona un ID de renta válido."); continue
                
                # --- LÓGICA DE LA MÁQUINA DE ESTADOS ---
                if s == Step.PUBLISH_TITLE:
                    await set_session(from_msisdn, Step.PUBLISH_PRICE, {"title": text})
                    await send_text(from_msisdn, "¡Bien! Ahora, indica el *precio por día* (ej: 10 USD).")
                elif s == Step.PUBLISH_PRICE:
                    st["draft"]["price"] = text; await set_session(from_msisdn, Step.PUBLISH_ZONE, st["draft"])
                    await send_text(from_msisdn, "Ok. ¿En qué *zona de Caracas* se encuentra? (ej: Chacao, El Paraíso)")
                elif s == Step.PUBLISH_ZONE:
                    st["draft"]["zone"] = text; await set_session(from_msisdn, Step.PUBLISH_PAYMENTS, st["draft"])
                    await send_text(from_msisdn, "Casi listo. ¿Qué *métodos de pago* aceptas? (separados por coma)")
                elif s == Step.PUBLISH_PAYMENTS:
                    pmts = [p.strip() for p in re.split(r"[,;]+", text) if p.strip()]
                    d = st["draft"]
                    item_id = await insert_listing(from_msisdn, d["title"], d["price"], d["zone"], pmts)
                    await set_session(from_msisdn, Step.IDLE, {})
                    pagos = ", ".join(pmts) if pmts else "A convenir"
                    await send_text(from_msisdn, f"¡Publicación creada! ID: *#{item_id}*\n- {d['title']}\n- Precio: {d['price']}\n- Zona: {d['zone']}\n- Pagos: {pagos}")
                    await send_main_menu(from_msisdn)
                
                elif upper.startswith("ALQUILAR"):
                    m = re.search(r"#(\d+)", text);
                    if not m: await send_text(from_msisdn, "Formato incorrecto. Usa: ALQUILAR #ID"); continue
                    item_id, listing = m.group(1), await get_listing(m.group(1))
                    if not listing: await send_text(from_msisdn, "No encontré un artículo con ese ID."); continue
                    if listing['owner_wa'] == from_msisdn: await send_text(from_msisdn, "No puedes alquilar tu propio artículo."); continue
                    
                    dates = _extract_dates(text)
                    if dates:
                        start_iso, end_iso = dates
                        await set_session(from_msisdn, Step.RENTAL_WAIT_PAYMENT, {"item_id": int(item_id), "start_iso": start_iso, "end_iso": end_iso})
                        # --- CAMBIO: Mostrar métodos de pago para seleccionar ---
                        payment_options = listing.get("payment_methods") or ["A convenir"]
                        rows = [{"id": p.replace(" ", "_"), "title": p} for p in payment_options]
                        await send_list(from_msisdn, f"Alquiler de #{item_id}", "Selecciona tu método de pago:", "Ver Pagos", rows)
                    else:
                        await set_session(from_msisdn, Step.RENTAL_WAIT_DATES, {"item_id": int(item_id)})
                        await send_text(from_msisdn, f"Perfecto. Ahora, indica las *fechas* que necesitas para el artículo #{item_id} (formato: DD/MM/AAAA a DD/MM/AAAA).")
                
                elif s == Step.RENTAL_WAIT_DATES:
                    dates = _extract_dates(text)
                    if not dates: await send_text(from_msisdn, "Formato de fechas no válido. Ejemplo: 15/10/2025 a 20/10/2025"); continue
                    start_iso, end_iso = dates; item_id = st["draft"]["item_id"]
                    await set_session(from_msisdn, Step.RENTAL_WAIT_PAYMENT, {"item_id": item_id, "start_iso": start_iso, "end_iso": end_iso})
                    listing = await get_listing(str(item_id))
                    payment_options = listing.get("payment_methods") or ["A convenir"]
                    rows = [{"id": p.replace(" ", "_"), "title": p} for p in payment_options]
                    await send_list(from_msisdn, f"Alquiler de #{item_id}", "¡Fechas guardadas! Ahora, selecciona tu método de pago.", "Ver Pagos", rows)
                
                elif s == Step.IDLE:
                    await send_main_menu(from_msisdn)

    return Response(status_code=200)

@app.get("/")
async def health_check():
    return {"status": "ok"}