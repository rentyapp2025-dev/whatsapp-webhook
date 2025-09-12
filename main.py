import os
import json
import time
import asyncio
import logging
import hmac
import hashlib
import re
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

# NEW: load .env in local/dev
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from rapidfuzz import process, fuzz

# --- Router de lenguaje natural ---
from llm_client import chat_completion

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import httpx

# -------------------- Logging --------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("nurse-life-bot")

# -------------------- Env --------------------
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "your_whatsapp_token_here")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "your_phone_number_id_here")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "your_verify_token_here")
APP_SECRET = os.getenv("APP_SECRET", "your_app_secret_here")

# Ruta del JSON con la base de conocimiento
KNOWLEDGE_BASE_PATH = os.getenv("KNOWLEDGE_BASE_PATH", "nurse.json")

GRAPH_API_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
MEDIA_UPLOAD_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/media"
HEADERS = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

# Modo exclusivamente lenguaje natural
NL_ONLY = (os.getenv("NL_ONLY", "true").lower() == "true")

# >>> EMAIL (Mailjet SMTP) <<<
import smtplib, ssl
from email.utils import formataddr, make_msgid
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SMTP_HOST = os.getenv("SMTP_HOST", "in-v3.mailjet.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")  # Mailjet API Key
SMTP_PASS = os.getenv("SMTP_PASS", "")  # Mailjet API Secret
OPS_EMAIL_TO = os.getenv("OPS_EMAIL_TO", "ops@nurselifeshop.local")   # uno o varios separados por coma
OPS_EMAIL_FROM = os.getenv("OPS_EMAIL_FROM", "bot@nurselifeshop.local")
REPLY_TO = os.getenv("REPLY_TO")                                       # opcional
ORDERS_BCC = os.getenv("ORDERS_BCC")                                   # opcional, coma-separado

# --- Perfil de negocio para el LLM ---
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Nurse Life Shop")
BUSINESS_TONE = os.getenv("BUSINESS_TONE", "amable, cercano y claro")

ROUTER_INSTRUCTION = (
    "Clasifica la intención del usuario en una sola palabra: "
    "FAQ | ORDER | CHITCHAT | OTHER.\n"
    "Responde SIEMPRE con este JSON plano (sin explicación):\n"
    "{\"intent\":\"...\",\"answer\":\"...\"}"
)

def nlu_answer(user_text: str) -> Tuple[str, str]:
    """
    Devuelve (intent, answer)
    """
    ctx_snippets = retrieve_context(user_text, k=5)
    context_block = "\n\n".join(ctx_snippets) if ctx_snippets else "SIN_CONTEXTO"

    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": (
            f"{ROUTER_INSTRUCTION}\n\n"
            f"[CONTEXTO DEL NEGOCIO]\n{context_block}\n\n"
            f"[PREGUNTA]\n{user_text}\n"
            f"[NOTAS]\n- Si 'ORDER', pregunta en lenguaje natural por datos de compra (producto, talla/color, cantidad, dirección si aplica y método de pago).\n"
            f"- Si 'FAQ', usa SOLO el contexto si aplica; si no hay datos, dilo breve y pide más detalle.\n"
            f"- Si 'CHITCHAT', responde amable y breve.\n"
        )}
    ]
    raw = chat_completion(messages, temperature=0.3, max_tokens=400)

    # Parseo robusto del JSON devuelto
    try:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        obj = json.loads(m.group(0)) if m else {"intent":"OTHER","answer":raw}
        intent = (obj.get("intent") or "OTHER").upper().strip()
        answer = (obj.get("answer") or "").strip()
        return intent, answer
    except Exception:
        return "OTHER", raw.strip()

def build_system_prompt() -> str:
    return (
        f"Eres un asistente de WhatsApp para {BUSINESS_NAME}. "
        f"Respondes SIEMPRE en español con un tono {BUSINESS_TONE}. "
        "Usa el contexto del negocio si existe para responder preguntas sobre productos, precios, delivery, pagos y catálogo. "
        "Si no hay datos en el contexto, dilo breve y pide más detalle. "
        "Si detectas intención de ORDEN/COMPRA, guía por TEXTO LIBRE (sin botones) para pedir los datos necesarios y confirmar el pedido. "
        "No uses botones ni menús; todo debe fluir por texto libre."
    )

# -------------------- Utils --------------------
def _split_csv(s: Optional[str]) -> List[str]:
    return [x.strip() for x in s.split(",")] if s else []

async def send_ops_email(subject: str, text: str) -> bool:
    """Envía correo operacional usando SMTP de Mailjet. Texto plano."""
    try:
        if not all([SMTP_USER, SMTP_PASS, OPS_EMAIL_FROM, OPS_EMAIL_TO]):
            logging.error("SMTP/ENV incompletos para Mailjet (revisa SMTP_USER, SMTP_PASS, OPS_EMAIL_FROM, OPS_EMAIL_TO).")
            return False

        tos = _split_csv(OPS_EMAIL_TO)
        bccs = _split_csv(ORDERS_BCC)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = formataddr(("Nurse Life Bot", OPS_EMAIL_FROM))
        msg["To"] = ", ".join(tos)
        if REPLY_TO:
            msg.add_header("Reply-To", REPLY_TO)
        if bccs:
            msg["Bcc"] = ", ".join(bccs)
        msg.add_header("Message-ID", make_msgid(domain=OPS_EMAIL_FROM.split("@")[-1]))
        msg.add_header("X-Mailer", "NurseLifeWA/1.0")
        msg.add_header("X-Mailjet-Campaign", "whatsapp-ops")

        msg.attach(MIMEText(text, "plain", "utf-8"))

        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(OPS_EMAIL_FROM, tos + bccs, msg.as_string())

        logging.info(f"Correo operacional enviado a: {tos} (bcc: {bccs})")
        return True
    except Exception as e:
        logging.error(f"Error enviando email (Mailjet): {e}")
        return False

# -------------------- App & Estado --------------------
app = FastAPI(title="Nurse Life Shop WhatsApp Chatbot")

user_sessions: Dict[str, Dict] = {}
user_ratings: List[Dict] = []
KNOWLEDGE_BASE: Dict[str, Any] = {}  # se cargará desde JSON

# >>> NUEVO: almacenamiento en memoria de pedidos
ORDERS: List[Dict[str, Any]] = []

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

# ==================== KB & Mini-RAG ====================

def _validate_kb(kb: Dict[str, Any]):
    """Admite categorías con 'questions' o con 'products' (catálogo)."""
    if not isinstance(kb, dict):
        raise ValueError("El JSON debe ser un objeto (dict) de categorías.")
    for cid, cat in kb.items():
        if not isinstance(cat, dict):
            raise ValueError(f"La categoría {cid} no es un objeto.")
        if "id" not in cat or "title" not in cat:
            raise ValueError(f"La categoría {cid} debe incluir 'id' y 'title'.")

        has_questions = isinstance(cat.get("questions"), list)
        has_products  = isinstance(cat.get("products"), list)

        if not has_questions and not has_products:
            raise ValueError(f"La categoría {cid} debe tener 'questions' o 'products'.")

        if has_questions:
            for q in cat["questions"]:
                for qk in ("id", "text", "answer"):
                    if qk not in q:
                        raise ValueError(f"La categoría {cid} posee una pregunta sin '{qk}'.")
        if has_products:
            for p in cat["products"]:
                for pk in ("id", "nombre", "categoria", "precio"):
                    if pk not in p:
                        raise ValueError(f"La categoría {cid} posee un producto sin '{pk}'.")


def kb_iter_texts() -> List[Dict]:
    out = []
    for cid, cat in KNOWLEDGE_BASE.items():
        for q in cat.get("questions", []) or []:
            full = f"PREGUNTA: {q['text']}\nRESPUESTA: {q['answer']}"
            out.append({"text": full, "source": f"faq:{cid}:{q['id']}"})
        for p in cat.get("products", []) or []:
            full = f"PRODUCTO: {p['nombre']} | CATEGORÍA: {p['categoria']} | PRECIO: {p['precio']}"
            out.append({"text": full, "source": f"prod:{cid}:{p['id']}"})
    return out

_KB_CACHE = None

def get_kb_corpus():
    global _KB_CACHE
    if _KB_CACHE is None:
        _KB_CACHE = kb_iter_texts()
    return _KB_CACHE

def retrieve_context(user_text: str, k: int = 5) -> List[str]:
    corpus = get_kb_corpus()
    choices = [c["text"] for c in corpus]
    results = process.extract(user_text, choices, scorer=fuzz.token_set_ratio, limit=k)
    top_texts = []
    for match_text, score, idx in results:
        if score >= 55:
            top_texts.append(match_text)
    return top_texts

# -------------------- Builders de WhatsApp --------------------

def build_text_message(to: str, text: str) -> Dict:
    return {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}

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

async def send_typing_indicator_and_wait(to: str, seconds: float = 1.0):
    try:
        await asyncio.sleep(0.3)
        await asyncio.sleep(seconds)
    except Exception as e:
        logger.error(f"Typing indicator error: {e}")

# -------------------- NL Welcome --------------------
async def send_welcome_sequence(to: str):
    name = get_first_name(to)
    saludo = f"¡Hola, {name}! 👋" if name else "¡Hola! 👋"
    text = (
        f"{saludo} Soy el asistente de *{BUSINESS_NAME}*. \n"
        "Cuéntame en tus palabras qué necesitas: precios, disponibilidad, envíos, pagos o hacer un pedido."
    )
    await send_typing_indicator_and_wait(to, 0.8)
    await send_message(build_text_message(to, text))

# -------------------- Pedido por TEXTO (sin botones) --------------------
ORDER_CANCEL_WORDS = {"cancel", "cancelar", "salir", "stop"}


def _ensure_order_session(phone: str) -> Dict[str, Any]:
    session = user_sessions.setdefault(phone, {})
    order = session.get("order")
    if not order:
        order = {
            "status": "in_progress",
            "step": "mode",           # mode -> address? -> items -> payment -> confirm
            "mode": None,              # DELIVERY | PICKUP
            "address": None,
            "items": None,
            "payment": None,
            "created_at": datetime.now().isoformat()
        }
        session["order"] = order
    return order

async def start_order_wizard(to: str):
    _ensure_order_session(to)
    msg = (
        "Vamos a armar tu pedido.\n"
        "¿Prefieres *envío a domicilio* o *retiro en tienda*? (escribe: envio / retiro)"
    )
    await send_message(build_text_message(to, msg))

async def _ask_for_address(to: str):
    await send_message(build_text_message(
        to,
        "Perfecto. Por favor, indícame la *dirección completa* para el envío (calle/av, referencia, ciudad)."
    ))

async def _ask_for_items(to: str):
    example = "Ejemplo: 1 par Difarfala 503 (talla 38), 1 Uniforme Meropenem (M), 1 Gorro Tropical"
    await send_message(build_text_message(
        to,
        f"¡Genial! Cuéntame *qué productos* deseas (modelo, talla/color si aplica). {example}"
    ))

async def _ask_for_payment(to: str):
    await send_message(build_text_message(
        to,
        "¿Cómo deseas pagar? Puedes escribir: efectivo / tarjeta / transferencia / pago móvil."
    ))


def _order_summary_text(phone: str) -> str:
    order = user_sessions.get(phone, {}).get("order", {})
    name = get_first_name(phone)
    parts = []
    parts.append(f"👤 Cliente: {name or phone}")
    parts.append(f"🧾 Modo: {order.get('mode') or '-'}")
    if order.get("mode") == "DELIVERY":
        parts.append(f"📍 Dirección: {order.get('address') or '-'}")
    parts.append(f"🛒 Pedido: {order.get('items') or '-'}")
    parts.append(f"💳 Pago: {order.get('payment') or '-'}")
    if order.get("note"):
        parts.append(f"📝 Nota: {order.get('note')}")
    return "\n".join(parts)

async def _ask_for_confirmation(to: str):
    summary = _order_summary_text(to)
    body = (
        f"Por favor, confirma tu pedido (escribe *confirmo* para aceptar o *cancelar* para anular):\n\n{summary}"
    )
    await send_message(build_text_message(to, body))

async def save_and_finish_order(to: str):
    order = user_sessions.get(to, {}).get("order", {})
    order_no = f"NLS-{int(time.time())}"
    order["order_no"] = order_no
    order["user"] = to
    order["status"] = "confirmed"
    ORDERS.append(order.copy())

    await send_message(build_text_message(
        to,
        f"🎉 ¡Listo! Tu pedido *{order_no}* fue recibido.\n{_order_summary_text(to)}\n\n"
        "Te contactaremos para coordinar envío o retiro. ¡Gracias por elegir Nurse Life Shop! 🩺"
    ))

    # >>> EMAIL a operaciones
    email_subject = f"Nuevo pedido #{order_no} - Nurse Life Shop"
    email_text = (
        f"📦 Nuevo pedido confirmado #{order_no}\n"
        f"{_order_summary_text(to)}\n"
        f"📱 Cliente WA: {to}\n"
        f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    await send_ops_email(email_subject, email_text)

    # Limpia sesión de pedido
    user_sessions.get(to, {}).pop("order", None)

async def cancel_order(to: str):
    user_sessions.get(to, {}).pop("order", None)
    await send_message(build_text_message(to, "Tu pedido fue cancelado. Si deseas, podemos empezar uno nuevo en cualquier momento."))

# --- Parsers de entrada libre ---
def _parse_mode(text: str) -> Optional[str]:
    t = (text or "").lower()
    if any(w in t for w in ["envio", "envío", "delivery", "enviar", "domicilio", "reparto"]):
        return "DELIVERY"
    if any(w in t for w in ["retiro", "retirar", "pickup", "tienda", "local"]):
        return "PICKUP"
    return None

_def_pay_map = {
    "efectivo": "Efectivo",
    "tarjeta": "Tarjeta/Transferencia",
    "transferencia": "Tarjeta/Transferencia",
    "pago movil": "Pago móvil",
    "pagomovil": "Pago móvil",
    "pago móvil": "Pago móvil",
}

def _parse_payment(text: str) -> Optional[str]:
    t = (text or "").lower()
    for k, v in _def_pay_map.items():
        if k in t:
            return v
    return None

async def handle_order_text_input(phone: str, text: str):
    if not text:
        return
    if text.lower().strip() in ORDER_CANCEL_WORDS:
        await cancel_order(phone)
        return

    order = _ensure_order_session(phone)
    step = order.get("step")

    if step == "mode":
        chosen = _parse_mode(text)
        if not chosen:
            await send_message(build_text_message(phone, "No te entendí. ¿Envio o retiro?"))
            return
        order["mode"] = chosen
        order["step"] = "address" if chosen == "DELIVERY" else "items"
        if order["step"] == "address":
            await _ask_for_address(phone)
        else:
            await _ask_for_items(phone)
        return

    if step == "address":
        order["address"] = text.strip()
        order["step"] = "items"
        await _ask_for_items(phone)
        return

    if step == "items":
        order["items"] = text.strip()
        order["step"] = "payment"
        await _ask_for_payment(phone)
        return

    if step == "payment":
        pay = _parse_payment(text)
        if not pay:
            await send_message(build_text_message(phone, "No reconocí el método. Escribe: efectivo / tarjeta / transferencia / pago móvil."))
            return
        order["payment"] = pay
        order["step"] = "confirm"
        await _ask_for_confirmation(phone)
        return

    if step == "confirm":
        t = (text or "").lower().strip()
        if t.startswith("confirm"):
            await save_and_finish_order(phone)
            return
        if t in ORDER_CANCEL_WORDS:
            await cancel_order(phone)
            return
        # si escribe otra cosa, lo tomamos como nota
        order["note"] = text.strip()
        await _ask_for_confirmation(phone)
        return

# ==================== Procesamiento de mensajes =====================

def is_greeting(text: str) -> bool:
    greetings = ["hola", "hello", "hi", "buenas", "buenos dias", "buenas tardes",
                 "buenas noches", "saludos", "que tal", "hey", "inicio", "empezar",
                 "comenzar", "start"]
    return text.lower().strip() in greetings


def is_order_intent(text: str) -> bool:
    if not text:
        return False
    t = text.lower().strip()
    # palabras sueltas para activar flujo de pedido
    keys = ["pedido", "orden", "ordenar", "ordenarme", "comprar", "pedir"]
    return any(k in t for k in keys)

async def process_text_message(from_number: str, text: str, message_id: str):
    logger.info(f"Processing text message from {from_number}: {text}")
    parse_and_set_name_from_text(from_number, text)

    # 1) Wizard de pedidos activo tiene prioridad
    if user_sessions.get(from_number, {}).get("order", {}).get("status") == "in_progress":
        await handle_order_text_input(from_number, text)
        return

    # 2) Intención de pedido por heurística rápida
    if is_order_intent(text):
        await start_order_wizard(from_number)
        return

    # 3) Saludos => NL welcome
    if is_greeting(text):
        await send_welcome_sequence(from_number)
        return

    # 4) LLM NLU cuando no hay flujo claro
    try:
        intent, answer = nlu_answer(text)
        logger.info(f"NLU intent={intent}")

        if intent == "ORDER":
            if answer:
                await send_message(build_text_message(from_number, answer))
            await start_order_wizard(from_number)
            return

        elif intent == "FAQ":
            if answer:
                await send_message(build_text_message(from_number, answer))
                return

        elif intent == "CHITCHAT":
            await send_message(build_text_message(from_number, answer or "¡Hola! ¿En qué más te ayudo?"))
            return

        # OTHER o sin respuesta clara
        if answer:
            await send_message(build_text_message(from_number, answer))
            return

    except Exception as e:
        logger.error(f"NLU error: {e}")

    # 5) Fallback NL-only
    fallback = (
        "No estoy seguro de haber entendido. ¿Podrías darme más detalle? "
        "Ej: ‘precio del uniforme Meropenem talla M’ o ‘quiero comprar 1 par Difarfala 503 talla 38’."
    )
    await send_message(build_text_message(from_number, fallback))

# (NL_ONLY ignora mensajes interactivos)
async def process_interactive_message(from_number: str, interactive_data: Dict):
    await send_message(build_text_message(from_number, "Puedes escribir tu consulta en texto. 😊"))

# -------------------- WhatsApp Webhook / Firma --------------------

def verify_webhook_signature(payload: bytes, signature: str) -> bool:
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

        maybe_name = ((message.get("profile") or {}).get("name") or "").strip()
        if from_number and maybe_name:
            set_user_name(from_number, maybe_name)

        logger.info(f"Processing message {message_id} from {from_number}, type: {mtype}")

        if mtype == "text":
            text_body = (message.get("text") or {}).get("body", "")
            await process_text_message(from_number, text_body, message_id)

        elif mtype == "interactive":
            # En NL_ONLY simplemente respondemos que escriba en texto
            await process_interactive_message(from_number, (message.get("interactive") or {}))

        elif mtype in ["image", "document", "audio", "video", "sticker"]:
            media_response = (
                "He recibido tu archivo. Para ayudarte mejor, cuéntame en texto qué necesitas (precio, disponibilidad, envíos, pagos o hacer un pedido)."
            )
            await send_message(build_text_message(from_number, media_response))

        else:
            logger.info(f"Unsupported message type: {mtype}")
            await send_message(build_text_message(from_number, "Puedes escribir tu consulta en texto. 😊"))

    except Exception as e:
        logger.error(f"Error processing message: {e}")

# -------------------- Admin: recargar KB --------------------
@app.post("/admin/reload-kb")
async def reload_kb():
    global KNOWLEDGE_BASE, _KB_CACHE
    KNOWLEDGE_BASE = load_knowledge_base(KNOWLEDGE_BASE_PATH)
    _KB_CACHE = None  # reset cache
    return {"status": "ok", "categories": len(KNOWLEDGE_BASE), "total_questions": get_total_questions(KNOWLEDGE_BASE)}

# -------------------- Health & Stats --------------------
@app.get("/")
async def health_check():
    return {
        "status": "healthy",
        "service": "Nurse Life Shop WhatsApp Chatbot",
        "version": "1.1.0-nurse-nl-only",
        "active_sessions": len(user_sessions),
        "total_ratings": len(user_ratings),
        "categories": len(KNOWLEDGE_BASE),
        "total_questions": get_total_questions(KNOWLEDGE_BASE)
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

# -------------------- KB Loaders --------------------
def load_knowledge_base(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        kb = json.load(f)
    _validate_kb(kb)
    logger.info(f"Knowledge base cargada: {len(kb)} categorías")
    return kb

def get_total_questions(kb: Dict[str, Any]) -> int:
    total = 0
    for cat in kb.values():
        total += len(cat.get("questions", []))
    return total

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
    logger.info(
        f"Bot iniciado (NL_ONLY={NL_ONLY}). KB categorías={len(KNOWLEDGE_BASE)} "
        f"preguntas={get_total_questions(KNOWLEDGE_BASE)}"
    )

# -------------------- Main --------------------
if __name__ == "__main__":
    import uvicorn
    print("Starting Nurse Life Shop WhatsApp Chatbot (NL-only)...")
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
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
