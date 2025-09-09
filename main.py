import os
import json
import time
import asyncio
import logging
import hmac
import hashlib
import re
from typing import Dict, List, Optional, Any
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import httpx

# -------------------- Logging --------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("hielo-el-manantial-bot")

# -------------------- Env --------------------
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "your_whatsapp_token_here")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "your_phone_number_id_here")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "your_verify_token_here")
APP_SECRET = os.getenv("APP_SECRET", "your_app_secret_here")

# Ruta del JSON con la base de conocimiento
KNOWLEDGE_BASE_PATH = os.getenv("KNOWLEDGE_BASE_PATH", "hielo_kb.json")

GRAPH_API_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
MEDIA_UPLOAD_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/media"
HEADERS = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

# >>> EMAIL (Mailjet SMTP) <<<
import smtplib, ssl
from email.utils import formataddr, make_msgid
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SMTP_HOST = os.getenv("SMTP_HOST", "in-v3.mailjet.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "436f971e525f9e2ff955ad1dc84de9d3")  # Mailjet API Key
SMTP_PASS = os.getenv("SMTP_PASS", "345c10295f46f0c78d8279fea3533acc")  # Mailjet API Secret
OPS_EMAIL_TO = os.getenv("OPS_EMAIL_TO", "planasnico@gmail.com")       # uno o varios separados por coma
OPS_EMAIL_FROM = os.getenv("OPS_EMAIL_FROM", "rentyapp2025@gmail.com") # remitente verificado en Mailjet
REPLY_TO = os.getenv("REPLY_TO")                                       # opcional
ORDERS_BCC = os.getenv("ORDERS_BCC")                                   # opcional, coma-separado

def _split_csv(s: Optional[str]) -> List[str]:
    return [x.strip() for x in s.split(",")] if s else []

async def send_ops_email(subject: str, text: str) -> bool:
    """Envía correo operacional (pedido) usando SMTP de Mailjet. Texto plano."""
    try:
        if not all([SMTP_USER, SMTP_PASS, OPS_EMAIL_FROM, OPS_EMAIL_TO]):
            logging.error("SMTP/ENV incompletos para Mailjet (revisa SMTP_USER, SMTP_PASS, OPS_EMAIL_FROM, OPS_EMAIL_TO).")
            return False

        tos = _split_csv(OPS_EMAIL_TO)
        bccs = _split_csv(ORDERS_BCC)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = formataddr(("Hielo El Manantial Bot", OPS_EMAIL_FROM))
        msg["To"] = ", ".join(tos)
        if REPLY_TO:
            msg.add_header("Reply-To", REPLY_TO)
        if bccs:
            msg["Bcc"] = ", ".join(bccs)
        msg.add_header("Message-ID", make_msgid(domain=OPS_EMAIL_FROM.split("@")[-1]))
        msg.add_header("X-Mailer", "HieloManantialWA/1.0")
        msg.add_header("X-Mailjet-Campaign", "whatsapp-orders")

        msg.attach(MIMEText(text, "plain", "utf-8"))

        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(OPS_EMAIL_FROM, tos + bccs, msg.as_string())

        logging.info(f"Pedido reenviado por email (Mailjet) a: {tos} (bcc: {bccs})")
        return True
    except Exception as e:
        logging.error(f"Error enviando email (Mailjet): {e}")
        return False

# >>> SUPABASE (REST) — opcional / no requerido <<<
SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""

_SUPA_HEADERS = {
    "apikey": SUPABASE_SERVICE_ROLE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
} if SUPABASE_SERVICE_ROLE_KEY else {}

async def supabase_insert_rating(phone: str, name: Optional[str], rating: str) -> None:
    """Inserta una fila en public.ratings (silencioso si no está configurado)."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        logger.info("Supabase no configurado (ratings) — omitido.")
        return
    payload = {"phone": phone, "name": name or "", "rating": rating}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/ratings",
                headers=_SUPA_HEADERS,
                json=payload,
                timeout=15.0
            )
            if r.status_code >= 400:
                logger.error(f"Supabase ratings HTTP {r.status_code}: {r.text}")
                return
            logger.info(f"Rating enviado a Supabase: {r.json() if r.content else 'OK (sin cuerpo)'}")
    except Exception as e:
        logging.error(f"Supabase ratings error: {e}")

async def supabase_insert_order(order: Dict[str, Any]) -> None:
    """Inserta una fila en public.orders (silencioso si no está configurado)."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        logger.info("Supabase no configurado (orders) — omitido.")
        return
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/orders",
                headers=_SUPA_HEADERS,
                json=order,
                timeout=15.0
            )
            if r.status_code >= 400:
                logger.error(f"Supabase orders HTTP {r.status_code}: {r.text}")
                return
            logger.info(f"Pedido guardado en Supabase: {r.json() if r.content else 'OK (sin cuerpo)'}")
    except Exception as e:
        logger.error(f"Supabase orders error: {e}")

# -------------------- App & Estado --------------------
app = FastAPI(title="Distribuidor de Hielo — El Manantial (WhatsApp Bot)")

user_sessions: Dict[str, Dict] = {}
user_ratings: List[Dict] = []
KNOWLEDGE_BASE: Dict[str, Any] = {}  # se cargará desde JSON

# >>> Almacenamiento en memoria de pedidos
ORDERS: List[Dict[str, Any]] = []

# >>> Negocio (constantes)
BUSINESS_NAME = "Distribuidor de Hielo — El Manantial"
BUSINESS_SHORT = "Hielo El Manantial"
BUSINESS_LOCATION = "Av. Los Pinos 123, Col. Centro, Ciudad Azul"
ICE_BAG_KG = 5
ICE_BAG_PRICE = 1.00      # USD
DELIVERY_FEE = 3.00       # USD

# ==================== Helpers de NOMBRE ====================
def extract_first_name(full_name: str) -> str:
    if not full_name:
        return ""
    name = re.sub(r'[^\wÁÉÍÓÚáéíóúÑñ\s\'.-]', '', full_name, flags=re.UNICODE).strip()
    parts = [p for p in name.split() if p and p.lower() not in {"de", "del", "la", "el"}]
    if not parts:
        return name
    first = parts[0]
    return first[:1].upper() + first[1:]

def set_user_name(phone: str, full_name: str):
    if not phone or not full_name:
        return
    session = user_sessions.setdefault(phone, {})
    session["full_name"] = full_name.strip()
    session["first_name"] = extract_first_name(full_name)
    session["last_interaction"] = datetime.now().isoformat()
    logger.info(f"Saved name for {phone}: {session['first_name']} ({session['full_name']})")

def parse_and_set_name_from_text(phone: str, text: str) -> Optional[str]:
    if not text:
        return None
    patterns = [
        r"(?:^|\b)(?:me llamo|mi nombre es)\s+([A-Za-zÁÉÍÓÚáéíóúÑñ\'.\- ]{2,})",
        r"(?:^|\b)soy\s+([A-Za-zÁÉÍÓÚáéíóúÑñ\'.\- ]{2,})"
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE | re.UNICODE)
        if m:
            candidate = m.group(1).strip().rstrip(".!,;:)")
            if len(candidate.split()) > 5:
                continue
            set_user_name(phone, candidate)
            return user_sessions.get(phone, {}).get("first_name")
    return None

def get_first_name(phone: str) -> str:
    return user_sessions.get(phone, {}).get("first_name", "")

# >>> Normalización de teléfonos (quita '+')
def normalize_phone(p: Optional[str]) -> str:
    return re.sub(r'^\+', '', (p or '').strip())

# ==================== Utilidades ====================
def truncate_text(text: str, max_length: int, add_ellipsis: bool = True) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length-3] + "..." if add_ellipsis and max_length > 3 else text[:max_length]

def format_question_for_list(question: Dict, index: int) -> Dict:
    title = f"{index}. {question.get('short_title', truncate_text(question['text'], 20))}"
    if len(title) > 24:
        title = title[:24]
    description = truncate_text(question["text"], 72)
    return {"title": title, "description": description}

def format_question_for_button(question: Dict, index: int) -> str:
    short_title = question.get('short_title', truncate_text(question['text'], 15))
    title = f"{index}. {short_title}"
    return title[:20] if len(title) > 20 else title

# -------------------- Knowledge Base --------------------
def _validate_kb(kb: Dict[str, Any]):
    if not isinstance(kb, dict):
        raise ValueError("El JSON debe ser un objeto (dict) de categorías.")
    for cid, cat in kb.items():
        if not isinstance(cat, dict):
            raise ValueError(f"La categoría {cid} no es un objeto.")
        for key in ("id", "title", "questions"):
            if key not in cat:
                raise ValueError(f"La categoría {cid} no tiene la clave requerida '{key}'.")
        if not isinstance(cat["questions"], list):
            raise ValueError(f"La categoría {cid} tiene 'questions' que no es lista.")
        for q in cat["questions"]:
            for qk in ("id", "text", "answer"):
                if qk not in q:
                    raise ValueError(f"La categoría {cid} posee una pregunta sin '{qk}'.")

def load_knowledge_base(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        kb = json.load(f)
    _validate_kb(kb)
    logger.info(f"Knowledge base cargada: {len(kb)} categorías")
    return kb

def get_total_questions(kb: Dict[str, Any]) -> int:
    return sum(len(cat.get("questions", [])) for cat in kb.values())

# Orden sugerido para Hielo El Manantial
MANANTIAL_PREFERRED_ORDER = [
    "INFO_NEGOCIO", "PRODUCTOS", "PRECIOS", "DELIVERY",
    "PEDIDOS", "RETIRO_LOCAL", "PAGO", "HORARIOS", "SOPORTE", "COMO_PEDIR"
]

# -------------------- Builders de WhatsApp --------------------
def build_text_message(to: str, text: str) -> Dict:
    return {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}

def build_interactive_list_message(to: str, header: str, body: str, sections: List[Dict]) -> Dict:
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": header},
            "body": {"text": body},
            "footer": {"text": f"{BUSINESS_SHORT} — Tu asistente virtual"},
            "action": {"button": "Ver opciones", "sections": sections}
        }
    }

def build_reply_button_message(to: str, body: str, buttons: List[Dict]) -> Dict:
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "footer": {"text": f"{BUSINESS_SHORT} — Tu asistente virtual"},
            "action": {"buttons": buttons}
        }
    }

def build_read_receipt(message_id: str) -> Dict:
    return {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}

def build_image_message(to: str, link: str, caption: Optional[str] = None) -> Dict:
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"link": link}
    }
    if caption:
        payload["image"]["caption"] = caption
    return payload

def build_image_id_message(to: str, media_id: str, caption: Optional[str] = None) -> Dict:
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"id": media_id}
    }
    if caption:
        payload["image"]["caption"] = caption
    return payload

# -------------------- WhatsApp HTTP --------------------
async def send_message(payload: Dict) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(GRAPH_API_URL, headers=HEADERS, json=payload, timeout=30.0)
            r.raise_for_status()
            logger.info(f"Message sent to {payload.get('to')} ({payload.get('type')})")
            return True
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP {e.response.status_code} sending message: {e.response.text}")
        return False
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return False

async def upload_media_from_url(url: str) -> Optional[str]:
    """Descarga una imagen desde URL y la sube al endpoint /media de WhatsApp. Devuelve media_id o None."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=30.0)
            resp.raise_for_status()
            mime = resp.headers.get("Content-Type", "").split(";")[0].strip() or "image/jpeg"
            filename = os.path.basename(url.split("?")[0]) or "image.jpg"

            files = {"file": (filename, resp.content, mime)}
            data = {"messaging_product": "whatsapp", "type": mime}
            headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
            up = await client.post(MEDIA_UPLOAD_URL, headers=headers, data=data, files=files, timeout=30.0)
            up.raise_for_status()
            media_id = up.json().get("id")
            logger.info(f"Media uploaded. id={media_id}")
            return media_id
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP {e.response.status_code} uploading media: {e.response.text}")
        return None
    except Exception as e:
        logger.error(f"Error uploading media: {e}")
        return None

async def send_image_with_fallback(to: str, url: str, caption: Optional[str] = None):
    """Intenta enviar por link; si falla, sube la imagen y reintenta por media_id."""
    sent = await send_message(build_image_message(to, url, caption))
    if sent:
        return
    media_id = await upload_media_from_url(url)
    if media_id:
        await send_message(build_image_id_message(to, media_id, caption))

async def send_typing_indicator_and_wait(to: str, seconds: float = 1.2):
    try:
        await asyncio.sleep(0.4)
        await asyncio.sleep(seconds)
    except Exception as e:
        logger.error(f"Typing indicator error: {e}")

# -------------------- Flujos conversacionales --------------------
async def send_welcome_sequence(to: str):
    name = get_first_name(to)
    saludo = f"¡Hola, {name}! ❄️" if name else "¡Hola! ❄️"
    caption = (
        f"{saludo} Bienvenido a *{BUSINESS_NAME}* 🧊\n\n"
        f"Vendemos *bolsas de {ICE_BAG_KG} kg a ${ICE_BAG_PRICE:.2f}*.\n"
        f"Delivery a domicilio por *${DELIVERY_FEE:.2f}*.\n"
        f"Ubicación: *{BUSINESS_LOCATION}*.\n\n"
        "Puedo ayudarte con productos, precios, delivery, pagos, horarios y *cómo pedir*.\n\n"
        "¿Qué te gustaría saber?"
    )
    await send_typing_indicator_and_wait(to, 1.0)
    # Imagen de hielo (placeholder libre)
    await send_image_with_fallback(
        to,
        "https://scontent-mia3-2.xx.fbcdn.net/v/t1.15752-9/542420442_1423330479507859_180935009960783780_n.jpg?_nc_cat=103&ccb=1-7&_nc_sid=0024fc&_nc_ohc=j95AxKjQrxUQ7kNvwHaocEX&_nc_oc=Adlfyk4fp1YSi5lrn5OyA0s7g3jEdkooYQxpilTzacQxn3jpuRYl5CwmtKV8ZnKb-0s&_nc_ad=z-m&_nc_cid=0&_nc_zt=23&_nc_ht=scontent-mia3-2.xx&oh=03_Q7cD3QE2ZNy1DvyfHNcuHd0kQ3UDrsU5NDHpGYfFj9BRC1jofA&oe=68E70ABE",
        caption=caption
    )
    await asyncio.sleep(0.5)
    await send_main_menu(to)

def _ordered_categories() -> List[str]:
    ordered = [cid for cid in MANANTIAL_PREFERRED_ORDER if cid in KNOWLEDGE_BASE]
    remaining = [cid for cid in KNOWLEDGE_BASE.keys() if cid not in ordered]
    return ordered + remaining

async def send_main_menu(to: str):
    rows = []
    for cid in _ordered_categories():
        cat = KNOWLEDGE_BASE[cid]
        title = cat["title"]
        desc = f"Información sobre {title}"
        rows.append({"id": cid, "title": title if len(title) <= 24 else title[:24], "description": desc})
    if not rows:
        await send_message(build_text_message(to, "Aún no hay información disponible. Inténtalo más tarde."))
        return

    sections = [{"title": "Menú Principal", "rows": rows}]
    payload = build_interactive_list_message(
        to=to, header=BUSINESS_SHORT, body="Elige una categoría para ver preguntas frecuentes:", sections=sections
    )
    await send_message(payload)
    user_sessions.setdefault(to, {})
    user_sessions[to].update({"state": "main_menu", "last_interaction": datetime.now().isoformat()})

async def send_category_questions(to: str, category_id: str):
    category = KNOWLEDGE_BASE.get(category_id)
    if not category:
        await send_message(build_text_message(to, "Lo siento, no encontré esa categoría."))
        await send_main_menu(to)
        return

    questions = category.get("questions", [])
    if not questions:
        await send_message(build_text_message(to, "No hay preguntas disponibles en esta categoría."))
        await send_main_menu(to)
        return

    if len(questions) <= 3:
        buttons = []
        for i, q in enumerate(questions[:3]):
            buttons.append({"type": "reply", "reply": {"id": q["id"], "title": format_question_for_button(q, i+1)}})
        payload = build_reply_button_message(to=to, body=f"*{category['title']}*\n\nSelecciona tu pregunta:", buttons=buttons)
    else:
        rows = []
        for i, q in enumerate(questions[:10]):
            fq = format_question_for_list(q, i+1)
            rows.append({"id": q["id"], "title": fq["title"], "description": fq["description"]})
        sections = [{"title": category["title"], "rows": rows}]
        payload = build_interactive_list_message(to=to, header=category["title"], body="Selecciona tu pregunta:", sections=sections)

    await send_message(payload)
    user_sessions.setdefault(to, {})
    user_sessions[to].update({"state": "questions_menu", "category": category_id, "last_interaction": datetime.now().isoformat()})

async def send_answer(to: str, question_id: str):
    answer = None
    question_text = None
    for category in KNOWLEDGE_BASE.values():
        for q in category.get("questions", []):
            if q["id"] == question_id:
                answer = q["answer"]
                question_text = q["text"]
                break
        if answer:
            break

    if not answer:
        await send_message(build_text_message(to, "Lo siento, no pude encontrar la respuesta a esa pregunta."))
        await send_main_menu(to)
    else:
        await send_typing_indicator_and_wait(to, 1.0)
        name = get_first_name(to)
        header = f"📋 *Pregunta*{f' ({name})' if name else ''}:\n"
        txt = f"{header}{question_text}\n\n💡 *Respuesta:*\n{answer}"
        await send_message(build_text_message(to, txt))
        await asyncio.sleep(0.9)
        await send_more_help_options(to)

async def send_more_help_options(to: str):
    name = get_first_name(to)
    body = f"¿Algo más{', ' + name if name else ''}? 👋"
    buttons = [
        {"type": "reply", "reply": {"id": "HELP_YES", "title": "Sí, por favor"}},
        {"type": "reply", "reply": {"id": "HELP_NO", "title": "No, gracias"}}
    ]
    await send_message(build_reply_button_message(to=to, body=body, buttons=buttons))
    user_sessions.setdefault(to, {})
    user_sessions[to].update({"state": "more_help", "last_interaction": datetime.now().isoformat()})

async def send_rating_request(to: str):
    name = get_first_name(to)
    pref = f"¡Gracias{', ' + name if name else ''} por usar nuestro asistente! 😊"
    body = f"{pref}\n\nPor favor, califica la atención recibida:"
    buttons = [
        {"type": "reply", "reply": {"id": "RATE_EXCELLENT", "title": "⭐⭐⭐ Excelente"}},
        {"type": "reply", "reply": {"id": "RATE_GOOD", "title": "⭐⭐ Bueno"}},
        {"type": "reply", "reply": {"id": "RATE_POOR", "title": "⭐ Necesita mejorar"}}
    ]
    await send_message(build_reply_button_message(to=to, body=body, buttons=buttons))
    user_sessions.setdefault(to, {})
    user_sessions[to].update({"state": "rating", "last_interaction": datetime.now().isoformat()})

async def handle_rating(to: str, rating_id: str):
    rating_map = {"RATE_EXCELLENT": "Excelente ⭐⭐⭐", "RATE_GOOD": "Bueno ⭐⭐", "RATE_POOR": "Necesita mejorar ⭐"}
    rating = rating_map.get(rating_id, "Desconocida")
    user_ratings.append({"user": to, "rating": rating, "timestamp": datetime.now().isoformat()})
    # >>> SUPABASE opcional
    try:
        norm = "Excelente" if rating.startswith("Excelente") else "Bueno" if rating.startswith("Bueno") else "Necesita mejorar" if rating.startswith("Necesita") else rating
        phone_norm = normalize_phone(to)
        await supabase_insert_rating(phone_norm, get_first_name(to), norm)
    except Exception as e:
        logger.error(f"No se pudo guardar rating en Supabase: {e}")
    name = get_first_name(to)
    txt = (
        f"¡Muchas gracias{', ' + name if name else ''} por tu calificación: *{rating}*! 🙏\n\n"
        "Tu opinión nos ayuda a mejorar cada día."
    )
    await send_message(build_text_message(to, txt))
    await asyncio.sleep(1.2)
    await send_conversation_end(to)

async def send_conversation_end(to: str):
    name = get_first_name(to)
    end = (
        f"🔚 *Esta conversación ha terminado*{f', {name}' if name else ''}\n\n"
        "Si necesitas algo más, escríbenos cuando quieras. ¡Gracias por elegir Hielo El Manantial! 🧊\n\n"
        f"_{BUSINESS_SHORT} — frescura garantizada_"
    )
    await send_message(build_text_message(to, end))
    user_sessions.setdefault(to, {})
    user_sessions[to].update({"state": "finished", "last_interaction": datetime.now().isoformat()})
    logger.info(f"Conversation ended for user {to}")

# -------------------- WIZARD DE PEDIDOS (HIELO) --------------------
ORDER_CANCEL_WORDS = {"cancel", "cancelar", "salir", "stop"}

def _ensure_order_session(phone: str) -> Dict[str, Any]:
    session = user_sessions.setdefault(phone, {})
    order = session.get("order")
    if not order:
        order = {
            "status": "in_progress",
            "step": "mode",           # mode -> address? -> qty -> payment -> confirm
            "mode": None,             # DELIVERY | PICKUP
            "address": None,
            "qty": None,              # número de bolsas
            "items": None,            # texto resumen
            "payment": None,
            "costs": {},              # subtotales
            "created_at": datetime.now().isoformat()
        }
        session["order"] = order
    return order

async def start_order_wizard(to: str):
    _ensure_order_session(to)
    body = "¿Cómo prefieres tu orden?"
    buttons = [
        {"type": "reply", "reply": {"id": "ORDER_DELIVERY", "title": "🚚 Delivery ($3)"}},
        {"type": "reply", "reply": {"id": "ORDER_PICKUP", "title": "🏬 Retiro en local"}}
    ]
    await send_message(build_reply_button_message(to=to, body=body, buttons=buttons))

async def _ask_for_address(to: str):
    await send_message(build_text_message(
        to,
        "Perfecto 📝\nPor favor, indícame la *dirección completa* para el delivery (calle/av, referencia, ciudad)."
    ))

async def _ask_for_qty(to: str):
    await send_message(build_text_message(
        to,
        f"¡Genial! 🧊\n¿Cuántas *bolsas de {ICE_BAG_KG} kg* deseas? (Ej: 10)"
    ))

async def _ask_for_payment(to: str):
    body = "¿Cómo deseas pagar?"
    buttons = [
        {"type": "reply", "reply": {"id": "ORDER_PAY_EFECTIVO", "title": "💵 Efectivo"}},
        {"type": "reply", "reply": {"id": "ORDER_PAY_TARJETA", "title": "💳 Tarjeta"}},
        {"type": "reply", "reply": {"id": "ORDER_PAY_PM", "title": "📲 Pago móvil"}}
    ]
    await send_message(build_reply_button_message(to=to, body=body, buttons=buttons))

def _calc_costs(mode: str, qty: int) -> Dict[str, float]:
    subtotal = qty * ICE_BAG_PRICE
    delivery = DELIVERY_FEE if mode == "DELIVERY" else 0.0
    total = subtotal + delivery
    return {"subtotal": round(subtotal, 2), "delivery": round(delivery, 2), "total": round(total, 2)}

def _order_summary_text(phone: str) -> str:
    order = user_sessions.get(phone, {}).get("order", {})
    name = get_first_name(phone)
    parts = []
    parts.append(f"👤 Cliente: {name or phone}")
    parts.append(f"🧾 Modo: {order.get('mode') or '-'}")
    if order.get("mode") == "DELIVERY":
        parts.append(f"📍 Dirección: {order.get('address') or '-'}")
    if order.get("qty") is not None:
        parts.append(f"🧊 Cantidad: {order.get('qty')} bolsa(s) de {ICE_BAG_KG} kg c/u")
    if order.get("items"):
        parts.append(f"📝 Detalle: {order.get('items')}")
    parts.append(f"💳 Pago: {order.get('payment') or '-'}")
    c = order.get("costs") or {}
    if c:
        parts.append(f"💵 Subtotal: ${c.get('subtotal', 0):.2f}")
        parts.append(f"🚚 Delivery: ${c.get('delivery', 0):.2f}")
        parts.append(f"✅ Total: ${c.get('total', 0):.2f}")
    if order.get("note"):
        parts.append(f"🗒️ Nota: {order.get('note')}")
    return "\n".join(parts)

async def _ask_for_confirmation(to: str):
    summary = _order_summary_text(to)
    body = f"Por favor, confirma tu pedido:\n\n{summary}"
    buttons = [
        {"type": "reply", "reply": {"id": "ORDER_CONFIRM", "title": "✅ Confirmar"}},
        {"type": "reply", "reply": {"id": "ORDER_CANCEL", "title": "❌ Cancelar"}}
    ]
    await send_message(build_reply_button_message(to=to, body=body, buttons=buttons))

async def save_and_finish_order(to: str):
    order = user_sessions.get(to, {}).get("order", {})
    order_no = f"HM-{int(time.time())}"
    order["order_no"] = order_no
    order["user"] = to
    order["status"] = "confirmed"
    ORDERS.append(order.copy())

    await send_message(build_text_message(
        to,
        f"🎉 ¡Listo! Tu pedido *{order_no}* fue recibido.\n{_order_summary_text(to)}\n\n"
        f"Para retiro: *{BUSINESS_LOCATION}*.\n"
        "Te avisaremos cuando esté en camino o listo para retirar. ¡Gracias por elegir Hielo El Manantial! 🧊"
    ))

    # >>> EMAIL
    email_subject = f"Nuevo pedido #{order_no} - {BUSINESS_SHORT}"
    email_text = (
        f"📦 Nuevo pedido confirmado #{order_no}\n"
        f"{_order_summary_text(to)}\n"
        f"📱 Cliente WA: {to}\n"
        f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    await send_ops_email(email_subject, email_text)

    # >>> SUPABASE opcional
    try:
        await supabase_insert_order({
            "order_no": order_no,
            "phone": normalize_phone(to),
            "name": get_first_name(to),
            "mode": order.get("mode"),
            "address": order.get("address"),
            "qty": order.get("qty"),
            "items": order.get("items"),
            "payment": order.get("payment"),
            "note": order.get("note"),
            "subtotal": order.get("costs", {}).get("subtotal"),
            "delivery": order.get("costs", {}).get("delivery"),
            "total": order.get("costs", {}).get("total"),
        })
    except Exception as e:
        logger.error(f"No se pudo guardar pedido en Supabase: {e}")

    user_sessions.get(to, {}).pop("order", None)
    await asyncio.sleep(0.6)
    await send_more_help_options(to)

async def cancel_order(to: str):
    user_sessions.get(to, {}).pop("order", None)
    await send_message(build_text_message(to, "Tu pedido fue cancelado. Si deseas, podemos empezar uno nuevo en cualquier momento."))
    await asyncio.sleep(0.4)
    await send_more_help_options(to)

async def handle_order_button_reply(phone: str, bid: str):
    order = _ensure_order_session(phone)
    bid = (bid or "").upper().strip()

    if bid == "ORDER_DELIVERY":
        order["mode"] = "DELIVERY"
        order["step"] = "address"
        await _ask_for_address(phone)
        return

    if bid == "ORDER_PICKUP":
        order["mode"] = "PICKUP"
        order["step"] = "qty"
        await _ask_for_qty(phone)
        return

    if bid in {"ORDER_PAY_EFECTIVO", "ORDER_PAY_TARJETA", "ORDER_PAY_PM"}:
        payment = {"ORDER_PAY_EFECTIVO": "Efectivo", "ORDER_PAY_TARJETA": "Tarjeta", "ORDER_PAY_PM": "Pago móvil"}[bid]
        order["payment"] = payment
        order["step"] = "confirm"
        await _ask_for_confirmation(phone)
        return

    if bid == "ORDER_CONFIRM":
        await save_and_finish_order(phone)
        return

    if bid == "ORDER_CANCEL":
        await cancel_order(phone)
        return

async def handle_order_text_input(phone: str, text: str):
    if not text:
        return
    if text.lower().strip() in ORDER_CANCEL_WORDS:
        await cancel_order(phone)
        return

    order = _ensure_order_session(phone)
    step = order.get("step")

    if step == "address":
        order["address"] = text.strip()
        order["step"] = "qty"
        await _ask_for_qty(phone)
        return

    if step == "qty":
        # Intentar extraer un entero de la entrada
        m = re.search(r'\d+', text)
        if not m:
            await send_message(build_text_message(phone, "Por favor envía un número válido. Ej: 10"))
            return
        qty = max(1, int(m.group(0)))
        order["qty"] = qty
        order["items"] = f"{qty} bolsa(s) de {ICE_BAG_KG} kg (${'{:.2f}'.format(ICE_BAG_PRICE)} c/u)"
        order["costs"] = _calc_costs(order.get("mode") or "PICKUP", qty)
        order["step"] = "payment"
        await _ask_for_payment(phone)
        return

    if step == "confirm":
        note = text.strip()
        if note:
            order["note"] = note
        await _ask_for_confirmation(phone)
        return

# ==================== Procesamiento de mensajes =====================
def is_greeting(text: str) -> bool:
    greetings = ["hola", "hello", "hi", "buenas", "buenos dias", "buenas tardes",
                 "buenas noches", "saludos", "que tal", "hey", "inicio", "empezar",
                 "comenzar", "start"]
    return text.lower().strip() in greetings

def is_negative_response(text: str) -> bool:
    negative_responses = ["no", "no gracias", "no, gracias", "nada más", "nada mas",
                          "ya no", "suficiente", "está bien", "esta bien", "listo",
                          "perfecto", "ok", "vale"]
    return text.lower().strip() in negative_responses

def is_order_intent(text: str) -> bool:
    if not text:
        return False
    t = text.lower().strip()
    # palabras claves para pedir
    return any(k in t for k in ["pedido", "orden", "ordenar", "comprar", "pedir", "delivery", "envío", "envio"])

async def process_text_message(from_number: str, text: str, message_id: str):
    logger.info(f"Processing text message from {from_number}: {text}")
    parsed_first = parse_and_set_name_from_text(from_number, text)

    # si el usuario ya está en el wizard de pedidos, dirigir aquí
    if user_sessions.get(from_number, {}).get("order", {}).get("status") == "in_progress":
        await handle_order_text_input(from_number, text)
        return

    # intención de pedido por texto libre
    if is_order_intent(text):
        await start_order_wizard(from_number)
        return

    if is_greeting(text):
        await send_welcome_sequence(from_number)
        return

    user_state = user_sessions.get(from_number, {}).get("state", "new")
    if user_state in ["new", "finished"] or from_number not in user_sessions:
        await send_welcome_sequence(from_number)
        return

    if user_state == "more_help" and is_negative_response(text):
        await send_rating_request(from_number)
        return

    if parsed_first and user_state not in ["main_menu", "questions_menu"]:
        await send_message(build_text_message(from_number, f"¡Encantado, {parsed_first}! He guardado tu nombre. Te muestro el menú principal:"))
        await asyncio.sleep(0.5)
        await send_main_menu(from_number)
        return

    redirect = ("Para ayudarte mejor, utiliza los botones del menú. "
                "Te muestro nuevamente las opciones disponibles:")
    await send_message(build_text_message(from_number, redirect))
    await asyncio.sleep(0.7)
    await send_main_menu(from_number)

async def process_interactive_message(from_number: str, interactive_data: Dict):
    mtype = interactive_data.get("type")

    if mtype == "list_reply":
        sel = (interactive_data.get("list_reply") or {}).get("id")
        logger.info(f"List reply from {from_number}: {sel}")

        # si selecciona la categoría PEDIDOS, iniciar wizard (conserva "cómo pedir")
        if sel == "PEDIDOS":
            await start_order_wizard(from_number)
            return

        if sel in KNOWLEDGE_BASE:
            await send_category_questions(from_number, sel)
        else:
            await send_answer(from_number, sel)

    elif mtype == "button_reply":
        bid = (interactive_data.get("button_reply") or {}).get("id")
        logger.info(f"Button reply from {from_number}: {bid}")

        # botones del wizard de pedidos
        if isinstance(bid, str) and (bid.startswith("ORDER_")):
            await handle_order_button_reply(from_number, bid)
            return

        if bid == "HELP_YES":
            await send_main_menu(from_number)
        elif bid == "HELP_NO":
            await send_rating_request(from_number)
        elif isinstance(bid, str) and bid.startswith("RATE_"):
            await handle_rating(from_number, bid)
        else:
            await send_answer(from_number, bid)

# -------------------- Webhook / Firma --------------------
def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    # Permite pruebas si APP_SECRET es placeholder o si el header no viene
    if (not APP_SECRET) or APP_SECRET.startswith("your_") or (not signature):
        logger.warning("Skipping webhook signature verification (APP_SECRET vacío/placeholder o header ausente).")
        return True
    try:
        if not signature.strip().startswith("sha256="):
            logger.error("Firma de webhook con formato inválido (sin prefijo sha256=).")
            return False
        expected_hex = hmac.new(APP_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        expected_header = f"sha256={expected_hex}"
        ok = hmac.compare_digest(expected_header, signature.strip())
        if not ok:
            logger.error("Webhook signature mismatch (no coincide con APP_SECRET).")
        return ok
    except Exception as e:
        logger.error(f"Error verificando firma de webhook: {e}")
        return False

# -------------------- Endpoints --------------------
@app.get("/webhook")
async def verify_webhook(request: Request):
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")

    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        try:
            return JSONResponse(content=int(hub_challenge))
        except Exception:
            return JSONResponse(content=hub_challenge)

    logger.error("Webhook verification failed")
    raise HTTPException(status_code=403, detail="Forbidden")

@app.post("/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")

        if not verify_webhook_signature(body, signature):
            logger.error("Invalid webhook signature")
            raise HTTPException(status_code=403, detail="Invalid signature")

        data = json.loads(body.decode())

        if data.get("object") == "whatsapp_business_account":
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})

                    # Capturar nombres desde contacts
                    for contact in value.get("contacts", []) or []:
                        wa_id = contact.get("wa_id")
                        profile_name = ((contact.get("profile") or {}).get("name") or "").strip()
                        if wa_id and profile_name:
                            set_user_name(wa_id, profile_name)

                    if "messages" in value:
                        for message in value["messages"]:
                            await process_message(message)

                    if "statuses" in value:
                        for status in value["statuses"]:
                            logger.info(f"Message status update: {status}")

        return JSONResponse(content={"status": "success"})

    except json.JSONDecodeError:
        logger.error("Invalid JSON in webhook")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

async def process_message(message: Dict):
    try:
        from_number = message.get("from")
        message_id = message.get("id")
        mtype = message.get("type")

        # Si este mensaje trae nombre (poco común), guárdalo
        maybe_name = ((message.get("profile") or {}).get("name") or "").strip()
        if from_number and maybe_name:
            set_user_name(from_number, maybe_name)

        logger.info(f"Processing message {message_id} from {from_number}, type: {mtype}")

        if mtype == "text":
            text_body = (message.get("text") or {}).get("body", "")
            await process_text_message(from_number, text_body, message_id)

        elif mtype == "interactive":
            interactive_data = (message.get("interactive") or {})
            await process_interactive_message(from_number, interactive_data)

        elif mtype in ["image", "document", "audio", "video", "sticker"]:
            media_response = "He recibido tu archivo. Para ayudarte mejor, usa el menú de opciones:"
            await send_message(build_text_message(from_number, media_response))
            await asyncio.sleep(0.5)
            await send_main_menu(from_number)

        else:
            logger.info(f"Unsupported message type: {mtype}")
            await send_main_menu(from_number)

    except Exception as e:
        logger.error(f"Error processing message: {e}")

# -------------------- Admin: recargar KB --------------------
@app.post("/admin/reload-kb")
async def reload_kb():
    global KNOWLEDGE_BASE
    KNOWLEDGE_BASE = load_knowledge_base(KNOWLEDGE_BASE_PATH)
    return {"status": "ok", "categories": len(KNOWLEDGE_BASE), "total_questions": get_total_questions(KNOWLEDGE_BASE)}

# -------------------- Health & Stats --------------------
@app.get("/")
async def health_check():
    return {
        "status": "healthy",
        "service": f"{BUSINESS_NAME} WhatsApp Chatbot",
        "version": "1.1.0",
        "active_sessions": len(user_sessions),
        "total_ratings": len(user_ratings),
        "categories": len(KNOWLEDGE_BASE),
        "total_questions": get_total_questions(KNOWLEDGE_BASE),
        "pricing": {
            "bag_kg": ICE_BAG_KG,
            "bag_price": ICE_BAG_PRICE,
            "delivery_fee": DELIVERY_FEE
        },
        "location": BUSINESS_LOCATION
    }

@app.get("/stats")
async def get_stats():
    rating_counts = {}
    for rating_data in user_ratings:
        rating = rating_data["rating"]
        rating_counts[rating] = rating_counts[rating] + 1 if rating in rating_counts else 1
    return {
        "active_sessions": len(user_sessions),
        "total_ratings": len(user_ratings),
        "rating_breakdown": rating_counts,
        "knowledge_base_categories": len(KNOWLEDGE_BASE),
        "total_questions": get_total_questions(KNOWLEDGE_BASE)
    }

@app.post("/send-message")
async def send_manual_message(request: Request):
    try:
        data = await request.json()
        to = data.get("to")
        message = data.get("message")
        message_type = data.get("type", "text")

        if not to or not message:
            raise HTTPException(status_code=400, detail="Missing 'to' or 'message' fields")

        if message_type == "text":
            payload = build_text_message(to, message)
        else:
            raise HTTPException(status_code=400, detail="Only text messages supported in manual send")

        ok = await send_message(payload)
        if ok:
            return {"status": "success", "message": "Message sent"}
        else:
            raise HTTPException(status_code=500, detail="Failed to send message")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

@app.delete("/sessions/{phone_number}")
async def clear_user_session(phone_number: str):
    if phone_number in user_sessions:
        del user_sessions[phone_number]
        return {"status": "success", "message": f"Session cleared for {phone_number}"}
    else:
        raise HTTPException(status_code=404, detail="Session not found")

@app.delete("/sessions")
async def clear_all_sessions():
    count = len(user_sessions)
    user_sessions.clear()
    return {"status": "success", "message": f"Cleared {count} sessions"}

# -------------------- Startup --------------------
@app.on_event("startup")
async def startup_event():
    global KNOWLEDGE_BASE
    try:
        KNOWLEDGE_BASE = load_knowledge_base(KNOWLEDGE_BASE_PATH)
    except Exception as e:
        logger.error(f"No se pudo cargar el knowledge_base.json: {e}")
        KNOWLEDGE_BASE = {}
    required_vars = {
        "WHATSAPP_TOKEN": WHATSAPP_TOKEN,
        "PHONE_NUMBER_ID": PHONE_NUMBER_ID,
        "VERIFY_TOKEN": VERIFY_TOKEN
    }
    missing = [k for k, v in required_vars.items() if not v]
    if missing:
        logger.error(f"Missing required env vars: {', '.join(missing)}")
    logger.info(f"Bot iniciado. KB categorías={len(KNOWLEDGE_BASE)} preguntas={get_total_questions(KNOWLEDGE_BASE)}")

    # Supabase env checks (opcional)
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        logger.info("Supabase no configurado (opcional).")
    else:
        logger.info(f"Supabase listo. URL={SUPABASE_URL} KEY_LEN={len(SUPABASE_SERVICE_ROLE_KEY)}")

# -------------------- Main --------------------
if __name__ == "__main__":
    import uvicorn
    print(f"Starting {BUSINESS_NAME} WhatsApp Chatbot...")
    print("Env check:")
    print(f"  WHATSAPP_TOKEN: {'✓' if WHATSAPP_TOKEN and 'your_' not in WHATSAPP_TOKEN.lower() else '✗'}")
    print(f"  PHONE_NUMBER_ID: {'✓' if PHONE_NUMBER_ID and 'your_' not in PHONE_NUMBER_ID.lower() else '✗'}")
    print(f"  VERIFY_TOKEN: {'✓' if VERIFY_TOKEN and 'your_' not in VERIFY_TOKEN.lower() else '✗'}")
    print(f"  APP_SECRET: {'✓' if APP_SECRET and 'your_' not in APP_SECRET.lower() else '✗ (optional)'}")
    print(f"  KNOWLEDGE_BASE_PATH: {KNOWLEDGE_BASE_PATH}")
    print(f"  SMTP_USER set: {'✓' if SMTP_USER else '✗'}")
    print(f"  SMTP_PASS set: {'✓' if SMTP_PASS else '✗'}")
    print(f"  OPS_EMAIL_FROM: {OPS_EMAIL_FROM}")
    print(f"  OPS_EMAIL_TO: {OPS_EMAIL_TO}")
    print(f"  Pricing: bag={ICE_BAG_KG}kg @ ${ICE_BAG_PRICE:.2f}, delivery=${DELIVERY_FEE:.2f}")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
