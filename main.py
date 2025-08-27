import os
import json
import re
import asyncio
import logging
import hmac
import hashlib
import unicodedata
from typing import Dict, List, Optional, Any
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import httpx

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment variables
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "your_whatsapp_token_here")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "your_phone_number_id_here")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "your_verify_token_here")
APP_SECRET = os.getenv("APP_SECRET", "your_app_secret_here")

# WhatsApp API configuration
GRAPH_API_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_TOKEN}",
    "Content-Type": "application/json"
}

# Initialize FastAPI app
app = FastAPI(title="Per Capital WhatsApp Chatbot")

# Global state management (in production, use Redis or database)
user_sessions: Dict[str, Dict] = {}
user_ratings: List[Dict] = []

# ==================== DATA STRUCTURE ====================
# KEEPING KNOWLEDGE_BASE EXACTLY AS YOU PROVIDED (DO NOT CHANGE)
KNOWLEDGE_BASE = {
    "PER CAPITAL": {
        "¿Qué es Per Capital?": "Es un grupo de empresas del Mercado de Valores Venezolano reguladas por la SUNAVAL, compuesta por Casa de Bolsa, Sociedad Administradora de EIC, Asesores de Inversión y Titularizadora.",
        "¿Qué es la SUNAVAL?": "Es el ente que regula el Mercado de Valores en Venezuela y protege a los inversionistas. www.sunaval.gob.ve",
        "¿Qué es la Bolsa de Valores de Caracas?": "Es el lugar donde se compran y venden bonos, acciones y otros instrumentos de manera ordenada a través de las Casas de Bolsa y está regulada por la SUNAVAL.",
        "¿Cómo invierto?": "Para invertir en el Fondo Mutual Abierto de PER CAPITAL debes descargar el app y registrarte. Para invertir directamente en acciones o bonos debes acudir a una Casa de Bolsa autorizada."
    },
    "FONDO MUTUAL ABIERTO": {
        "¿Qué es un Fondo Mutual?": "Es un instrumento de inversión en grupo donde varias personas ponen dinero en un fondo gestionado por expertos, diseñado para ser de bajo riesgo, dirigido a pequeños inversionistas con poca experiencia.",
        "¿Qué es una Unidad de Inversión?": "Es una porción del fondo. Cuando inviertes adquieres unidades que representan tu parte del fondo.",
        "¿Qué es el VUI?": "El Valor de la Unidad de Inversión (VUI) es el precio de una Unidad de Inversión. Se calcula diariamente y depende del comportamiento de las inversiones del fondo.",
        "¿Cómo invierto?": "Descarga el app para Android y iOS, regístrate al 100%, espera tu aprobación y suscribe Unidades de Inversión cuando quieras y cuantas veces desees.",
        "¿Cuál es el monto mínimo de inversión?": "1 Unidad de Inversión.",
        "¿Cómo gano?": "Por apreciación (subida del VUI) o por dividendo (si es decretado).",
        "¿En cuánto tiempo gano?": "Es recomendable medir resultados de forma trimestral.",
        "¿Dónde consigo más información?": "En los prospectos y hojas de términos en www.per-capital.com."
    },
    "REGISTRO": {
        "¿Cómo me registro?": "Descarga el app, completa 100% de los datos, acepta los contratos, sube tus recaudos y espera tu aprobación.",
        "¿Cuánto tarda mi aprobación?": "De 2 a 5 días hábiles siempre que hayas completado 100% de registro y recaudos.",
        "¿Qué hago si no me aprueban?": "Revisa que hayas completado 100% del registro o contáctanos.",
        "¿Puedo invertir si soy menor de edad?": "Debes dirigirte a nuestras oficinas y registrarte con tu representante legal.",
        "¿Puedo modificar alguno de mis datos?": "Sí, pero por exigencia de la ley entras nuevamente en revisión.",
        "¿Debo tener cuenta en la Caja Venezolana?": "No, no es necesaria para invertir en nuestro Fondo Mutual Abierto."
    },
    "SUSCRIPCIÓN": {
        "¿Cómo suscribo (compro)?": "Haz click en Negociación > Suscripción > Monto a invertir > Suscribir > Método de Pago. Paga desde TU cuenta bancaria y sube comprobante.",
        "¿Cómo pago mi suscripción?": "Debes pagar desde tu cuenta bancaria vía Pago Móvil. No se aceptan pagos de terceros.",
        "¿Puede pagar alguien por mí?": "No, la ley prohíbe los pagos de terceros.",
        "¿Cómo veo mi inversión?": "En el Home en la sección Mi Cuenta.",
        "¿Cuándo veo mi inversión?": "Al cierre del sistema entre 5 pm y 7 pm en días hábiles de mercado.",
        "¿Cuáles son las comisiones?": "3% flat Suscripción, 3% flat Rescate y 5% anual Administración.",
        "¿Qué hago después de suscribir?": "Monitorea tu inversión desde el app.",
        "¿Puedo invertir el monto que quiera?": "Sí, puedes invertir el monto que desees.",
        "¿Puedo invertir cuando quiera?": "Sí, puedes invertir cuando quieras, las veces que quieras."
    },
    "RESCATE": {
        "¿Cómo rescato (vendo)?": "Haz click en Negociación > Rescate > Unidades a Rescatar > Rescatar. Fondos se enviarán a TU cuenta bancaria.",
        "¿Cuándo me pagan mis rescates?": "Al próximo día hábil bancario en horario de mercado.",
        "¿Cómo veo el saldo de mi inversión?": "En el Home en la sección Mi Cuenta.",
        "¿Cuándo veo el saldo de mi inversión?": "Al cierre del sistema entre 5 pm y 7 pm en días hábiles de mercado.",
        "¿Cuándo puedo rescatar?": "Cuando quieras, puedes rescatar y retirarte del fondo.",
        "¿Cuáles son las comisiones?": "3% flat Suscripción, 3% flat Rescate y 5% anual Administración."
    },
    "POSICIÓN": {
        "¿Cuándo se actualiza mi posición (saldo)?": "Al cierre del sistema entre 5 pm y 7 pm en días hábiles de mercado.",
        "¿Por qué varía mi posición (saldo)?": "Sube si suben los precios de las inversiones o se reciben dividendos/cupones, baja si los precios caen.",
        "¿Dónde veo mi histórico?": "En la sección Historial.",
        "¿Dónde veo reportes?": "En la sección Documentos > Reportes > Año > Trimestre."
    },
    "RIESGOS": {
        "¿Cuáles son los riesgos al invertir?": "Todas las inversiones están sujetas a riesgos y la pérdida de capital es posible. Algunos riesgos son: mercado, país, cambiario, sector, entre otros."
    },
    "SOPORTE": {
        "Estoy en revisión, ¿qué hago?": "Asegúrate de haber completado 100% datos y recaudos y espera tu aprobación. Si tarda más, contáctanos.",
        "No me llega el SMS": "Verifica señal y que tu número telefónico venezolano esté correcto.",
        "No me llega el correo": "Asegúrate de no dejar espacios al final al escribir tu correo.",
        "No logro descargar el App": "Asegúrate de que tu App Store esté configurada en la región de Venezuela.",
        "No me abre el App": "Verifica tener la versión actualizada y que tu tienda de apps esté configurada en Venezuela.",
        "¿Cómo recupero mi clave?": "Selecciona Recuperar, recibirás una clave temporal y luego actualiza tu nueva clave."
    }
}

# In-memory question id map: generated_id -> {category, text, answer}
QUESTION_ID_MAP: Dict[str, Dict[str, Any]] = {}

# Helper: normalize strings (remove accents, punctuation, multiple spaces, uppercase)
def _normalize_key(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    s_norm = unicodedata.normalize("NFKD", s)
    s_no_accents = "".join(ch for ch in s_norm if not unicodedata.combining(ch))
    # keep alphanumerics and spaces only
    filtered = "".join(ch for ch in s_no_accents if ch.isalnum() or ch.isspace())
    # collapse spaces and uppercase
    return " ".join(filtered.split()).upper()

def _category_to_id(k: str) -> str:
    """Generate the menu id from a category key consistently."""
    return _normalize_key(k).replace(" ", "_")

def find_category_key(selection_id: str, allow_fuzzy: bool = False) -> Optional[str]:
    if not selection_id:
        return None

    # Si es un ID de pregunta, nunca es una categoría
    if "::Q" in selection_id:
        return None

    # Try direct match
    for k in KNOWLEDGE_BASE.keys():
        if selection_id == k:
            return k

    candidate = selection_id.replace("_", " ").strip()
    norm_candidate = _normalize_key(candidate)

    for k in KNOWLEDGE_BASE.keys():
        if _normalize_key(k) == norm_candidate:
            return k

    norm_sel = _normalize_key(selection_id)
    for k in KNOWLEDGE_BASE.keys():
        if _normalize_key(k) == norm_sel:
            return k

    if allow_fuzzy:
        for k in KNOWLEDGE_BASE.keys():
            nk = _normalize_key(k)
            if norm_candidate in nk or nk in norm_candidate:
                return k

    return None

def _make_question_id(category_key: str, idx: int) -> str:
    """Create a stable generated ID for a question"""
    cat_safe = _category_to_id(category_key)
    return f"{cat_safe}::Q{idx+1}"

# ==================== MESSAGE BUILDERS ====================

def build_text_message(to: str, text: str) -> Dict:
    """Build a text message payload"""
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }

def build_interactive_list_message(to: str, header: str, body: str, sections: List[Dict]) -> Dict:
    """Build an interactive list message payload"""
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": header},
            "body": {"text": body},
            "footer": {"text": "Per Capital - Tu asistente virtual"},
            "action": {
                "button": "Ver opciones",
                "sections": sections
            }
        }
    }

def build_reply_button_message(to: str, body: str, buttons: List[Dict]) -> Dict:
    """Build a reply button message payload"""
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "footer": {"text": "Per Capital - Tu asistente virtual"},
            "action": {"buttons": buttons}
        }
    }

def build_read_receipt(message_id: str) -> Dict:
    """Build a read receipt payload"""
    return {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id
    }

def build_typing_indicator(to: str) -> Dict:
    """Build typing indicator payload"""
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": "typing..."}
    }

# ==================== WHATSAPP API FUNCTIONS ====================

async def send_message(payload: Dict) -> bool:
    """Send message to WhatsApp API"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(GRAPH_API_URL, headers=HEADERS, json=payload, timeout=30.0)
            response.raise_for_status()
            logger.info(f"Message sent successfully to {payload.get('to')}")
            return True
    except httpx.RequestError as e:
        logger.error(f"Request error sending message: {e}")
        return False
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error sending message: {e.response.status_code} - {e.response.text}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending message: {e}")
        return False

async def send_typing_indicator_and_wait(to: str, seconds: float = 2.0):
    """Send typing indicator and wait"""
    try:
        # Mark as read first (simulate natural behavior)
        await asyncio.sleep(0.5)
        # Wait for the specified time (simulating typing)
        await asyncio.sleep(seconds)
    except Exception as e:
        logger.error(f"Error in typing indicator: {e}")

async def send_welcome_sequence(to: str):
    """Send welcome message sequence with typing indicators"""
    welcome_text = (
        "¡Hola! 👋 Bienvenido a Per Capital\n\n"
        "Soy tu asistente virtual y estoy aquí para ayudarte con todas tus consultas "
        "sobre inversiones, nuestra app y servicios financieros.\n\n"
        "¿Cómo puedo ayudarte hoy?"
    )
    await send_typing_indicator_and_wait(to, 1.5)
    await send_message(build_text_message(to, welcome_text))
    await asyncio.sleep(1.0)
    await send_main_menu(to)

async def send_main_menu(to: str):
    """Send main interactive menu — build entries dynamically from KNOWLEDGE_BASE"""
    rows = []
    # Add categories dynamically so ids match the true keys
    for k in KNOWLEDGE_BASE.keys():
        rows.append({
            "id": _category_to_id(k),
            "title": k,
            "description": "Información sobre " + k
        })
    # Also add the App virtual category at the top
    sections = [{
        "title": "Categorías disponibles",
        "rows": [
            {"id": "APP_MAIN", "title": "App Per Capital", "description": "Registro, suscripción, rescate y más"},
        ] + rows
    }]

    payload = build_interactive_list_message(
        to=to,
        header="Menú Principal",
        body="Selecciona la categoría sobre la que necesitas información:",
        sections=sections
    )
    await send_message(payload)
    user_sessions[to] = {
        "state": "main_menu",
        "last_interaction": datetime.now().isoformat()
    }

async def send_app_submenu(to: str):
    """Send App submenu. Build submenu from relevant categories automatically."""
    app_keys = []
    for k in KNOWLEDGE_BASE.keys():
        kn = _normalize_key(k)
        if any(token in kn for token in ("REGISTRO", "SUSCRIP", "RESCAT", "POSICION")):
            app_keys.append(k)

    rows = []
    if not app_keys:
        app_keys = ["REGISTRO", "SUSCRIPCIÓN", "RESCATE", "POSICIÓN"]

    for k in app_keys:
        if k in KNOWLEDGE_BASE:
            rows.append({
                "id": _category_to_id(k),
                "title": k,
                "description": "Consultas sobre " + k
            })

    sections = [{"title": "Opciones de la App", "rows": rows}]
    payload = build_interactive_list_message(
        to=to,
        header="App Per Capital",
        body="¿Sobre qué aspecto de la app necesitas información?",
        sections=sections
    )
    await send_message(payload)
    user_sessions[to] = {
        "state": "app_submenu",
        "last_interaction": datetime.now().isoformat()
    }

async def send_category_questions(to: str, category_id: str):
    """
    Send questions for a specific category.
    Works with the original KNOWLEDGE_BASE format (category -> { question: answer }).
    Generates temporary question IDs and stores them in QUESTION_ID_MAP.
    """
    # Special virtual category APP_GENERAL: combine app-related categories
    if category_id == "APP_GENERAL":
        combined = {}
        for k in KNOWLEDGE_BASE.keys():
            kn = _normalize_key(k)
            if any(token in kn for token in ("REGISTRO", "SUSCRIP", "RESCAT", "POSICION")):
                combined.update(KNOWLEDGE_BASE[k])
        category = combined
        category_title = "App Per Capital (Resumen)"
        mapped_key_for_session = "APP_GENERAL"
    else:
        mapped = find_category_key(category_id)
        if not mapped:
            # if category_id looks like a generated id (_category_to_id) try reversing
            # try replacing underscores with spaces and searching
            alt = category_id.replace("_", " ")
            mapped = find_category_key(alt)
        if not mapped:
            await send_message(build_text_message(to, "Lo siento, no pude encontrar esa categoría."))
            await send_main_menu(to)
            return
        category = KNOWLEDGE_BASE.get(mapped, {})
        category_title = mapped
        mapped_key_for_session = mapped

    # Build question list and populate QUESTION_ID_MAP
    questions_local: List[Dict[str, str]] = []
    for i, (q_text, q_answer) in enumerate(category.items()):
        qid = _make_question_id(category_title, i)
        unique_qid = qid
        suffix = 1
        while unique_qid in QUESTION_ID_MAP:
            unique_qid = f"{qid}_{suffix}"
            suffix += 1
        QUESTION_ID_MAP[unique_qid] = {
            "category": category_title,
            "text": q_text,
            "answer": q_answer
        }
        questions_local.append({"id": unique_qid, "text": q_text, "answer": q_answer})

    if len(questions_local) == 0:
        await send_message(build_text_message(to, "No hay preguntas disponibles en esta categoría."))
        await send_main_menu(to)
        return

    if len(questions_local) <= 3:
        buttons = []
        for i, q in enumerate(questions_local[:3]):
            title_short = q["text"]
            if len(title_short) > 40:
                title_short = title_short[:37] + "..."
            buttons.append({
                "type": "reply",
                "reply": {
                    "id": q["id"],
                    "title": f"{i+1}. {title_short}"
                }
            })
        payload = build_reply_button_message(
            to=to,
            body=f"*{category_title}*\n\nSelecciona tu pregunta:",
            buttons=buttons
        )
    else:
        rows = []
        for i, q in enumerate(questions_local):
            title_short = q["text"] if len(q["text"]) <= 24 else q["text"][:21] + "..."
            desc = q["text"] if len(q["text"]) <= 72 else q["text"][:69] + "..."
            rows.append({
                "id": q["id"],
                "title": f"{i+1}. {title_short}",
                "description": desc
            })
        sections = [{"title": category_title, "rows": rows}]
        payload = build_interactive_list_message(
            to=to,
            header=category_title,
            body="Selecciona tu pregunta:",
            sections=sections
        )

    await send_message(payload)
    user_sessions[to] = {
        "state": "questions_menu",
        "category": mapped_key_for_session,
        "last_interaction": datetime.now().isoformat()
    }

async def send_answer(to: str, question_id: str):
    """Send answer for a specific question using QUESTION_ID_MAP or fallbacks"""
    qdata = QUESTION_ID_MAP.get(question_id)
    if not qdata:
        # try to match by normalized question text (user might have sent text)
        norm_in = _normalize_key(question_id or "")
        for qid, data in QUESTION_ID_MAP.items():
            if _normalize_key(data.get("text", "")) == norm_in:
                qdata = data
                break

    if not qdata:
        # search in KNOWLEDGE_BASE by normalized question text
        for cat_key, qa_map in KNOWLEDGE_BASE.items():
            for q_text, q_answer in qa_map.items():
                if _normalize_key(q_text) == _normalize_key(question_id):
                    qdata = {"category": cat_key, "text": q_text, "answer": q_answer}
                    break
            if qdata:
                break

    if not qdata and "::Q" in (question_id or ""):
        # try to rebuild from pattern Category_ID::Qn
        try:
            cat_part = question_id.split("::Q")[0]
            cat_name_candidate = cat_part.replace("_", " ").strip()
            mapped = find_category_key(cat_name_candidate)
            if mapped and mapped in KNOWLEDGE_BASE:
                qlist = list(KNOWLEDGE_BASE[mapped].items())
                try:
                    qindex = int(question_id.split("::Q")[1].split("_")[0]) - 1
                    if 0 <= qindex < len(qlist):
                        q_text, q_answer = qlist[qindex]
                        qdata = {"category": mapped, "text": q_text, "answer": q_answer}
                        QUESTION_ID_MAP[question_id] = qdata
                except Exception:
                    qdata = None
        except Exception:
            qdata = None

    if not qdata:
        await send_message(build_text_message(to, "Lo siento, no pude encontrar la respuesta a esa pregunta."))
        await send_main_menu(to)
        return

    answer = qdata["answer"]
    await send_typing_indicator_and_wait(to, 1.0)
    answer_text = f"📝 *Respuesta:*\n\n{answer}"
    await send_message(build_text_message(to, answer_text))
    await asyncio.sleep(1.5)
    await send_more_help_options(to)

async def send_more_help_options(to: str):
    """Send options to continue or finish conversation"""
    buttons = [
        {
            "type": "reply",
            "reply": {"id": "YES", "title": "Sí, por favor"}
        },
        {
            "type": "reply",
            "reply": {"id": "NO", "title": "No, gracias"}
        }
    ]
    payload = build_reply_button_message(
        to=to,
        body="¿Necesitas ayuda con alguna otra cosa?",
        buttons=buttons
    )
    await send_message(payload)
    user_sessions[to] = {
        "state": "more_help",
        "last_interaction": datetime.now().isoformat()
    }

async def send_rating_request(to: str):
    """Send rating options"""
    buttons = [
        {
            "type": "reply",
            "reply": {"id": "RATE_EXCELLENT", "title": "Excelente"}
        },
        {
            "type": "reply",
            "reply": {"id": "RATE_GOOD", "title": "Bien"}
        },
        {
            "type": "reply",
            "reply": {"id": "RATE_NEEDS_IMPROVEMENT", "title": "Necesita mejorar"}
        }
    ]
    payload = build_reply_button_message(
        to=to,
        body="¡Gracias por usar nuestro asistente virtual! 😊\n\n¿Cómo calificarías la ayuda recibida?",
        buttons=buttons
    )
    await send_message(payload)
    user_sessions[to] = {
        "state": "rating",
        "last_interaction": datetime.now().isoformat()
    }

async def handle_rating(to: str, rating_id: str):
    """Handle user rating"""
    rating_map = {
        "RATE_EXCELLENT": "Excelente",
        "RATE_GOOD": "Bien",
        "RATE_NEEDS_IMPROVEMENT": "Necesita mejorar"
    }
    rating = rating_map.get(rating_id, "Desconocida")
    user_ratings.append({
        "user": to,
        "rating": rating,
        "timestamp": datetime.now().isoformat()
    })
    thank_you_text = (
        f"¡Gracias por tu calificación: *{rating}*! 🙏\n\n"
        "Tu opinión es muy importante para nosotros y nos ayuda a mejorar nuestro servicio.\n\n"
        "Si necesitas más ayuda en el futuro, no dudes en escribirnos. "
        "¡Que tengas un excelente día! 😊"
    )
    await send_message(build_text_message(to, thank_you_text))
    if to in user_sessions:
        del user_sessions[to]
    logger.info(f"User {to} rated the service as: {rating}")

# ==================== MESSAGE PROCESSING ====================

def is_greeting(text: str) -> bool:
    """Check if message is a greeting"""
    if not text:
        return False
    greetings = [
        "hola", "hello", "hi", "buenas", "buenos dias", "buenas tardes",
        "buenas noches", "saludos", "que tal", "hey", "inicio"
    ]
    return text.lower().strip() in greetings

async def process_text_message(from_number: str, text: str, message_id: str):
    """Process incoming text message"""
    logger.info(f"Processing text message from {from_number}: {text}")
    if is_greeting(text):
        await send_welcome_sequence(from_number)
        return
    user_state = user_sessions.get(from_number, {}).get("state", "new")
    if user_state == "new":
        await send_welcome_sequence(from_number)
    else:
        redirect_text = (
            "Para brindarte la mejor ayuda, por favor utiliza los botones y opciones del menú. "
            "Te muestro nuevamente las opciones disponibles:"
        )
        await send_message(build_text_message(from_number, redirect_text))
        await asyncio.sleep(1.0)
        await send_main_menu(from_number)

async def process_interactive_message(from_number: str, interactive_data: Dict):
    """Robust handler for interactive messages (list_reply and button_reply)."""
    # Defensive: interactive_data sometimes lacks "type", so derive it.
    logger.info(f"[interactive] Raw interactive_data from {from_number}: {interactive_data}")

    # Rozar keys to infer type:
    msg_type = interactive_data.get("type")
    if not msg_type:
        if "list_reply" in interactive_data:
            msg_type = "list_reply"
        elif "button_reply" in interactive_data:
            msg_type = "button_reply"
        else:
            # Sometimes the structure is nested: interactive: {"type":"...","list_reply":{...}}
            # fall back to keys present:
            keys = set(interactive_data.keys())
            if "list_reply" in keys or "sections" in keys:
                msg_type = "list_reply"
            elif "button_reply" in keys or "buttons" in keys:
                msg_type = "button_reply"

    # Helper extractor: try id, title, payload, name. Clean known suffixes and JSON-strings.
    def _extract_candidate(obj: Dict) -> Optional[str]:
        raw = (
            (obj.get("id") if isinstance(obj.get("id"), str) else None)
            or (obj.get("title") if isinstance(obj.get("title"), str) else None)
            or (obj.get("payload") if isinstance(obj.get("payload"), str) else None)
            or (obj.get("name") if isinstance(obj.get("name"), str) else None)
            or ""
        )
        raw = raw.strip()

        # If looks like JSON string, try parse and extract common fields
        if raw.startswith("{") and raw.endswith("}"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    for k in ("id", "title", "payload", "name", "value"):
                        if parsed.get(k):
                            raw = str(parsed.get(k)).strip()
                            break
            except Exception:
                # ignore parse errors, keep raw
                pass

        # Remove common UI suffixes that WhatsApp client shows in previews
        for sep in ["Información sobre", "Information about", "\n", " - ", " | "]:
            if sep in raw:
                raw = raw.split(sep)[0].strip()

        # If there is a colon-encoded separator or URL-encoded parts, decode naive
        raw = raw.replace("%3A%3A", "::")

        # Truncate and return
        return raw or None

    # small helper: remove "1. " prefix
    def _strip_index_prefix(s: str) -> str:
        return re.sub(r'^\s*\d+\s*[\.\-\)\:]?\s*', '', s or '')

    # show useful debug state
    logger.debug(f"user_sessions[{from_number}] = {user_sessions.get(from_number)}")
    logger.debug(f"QUESTION_ID_MAP keys (sample) = {list(QUESTION_ID_MAP.keys())[:20]}")

    # Process list_reply
    if msg_type == "list_reply":
        list_reply = interactive_data.get("list_reply", {}) or {}
        selection_raw = _extract_candidate(list_reply)
        logger.info(f"[list_reply] extracted selection_raw='{selection_raw}'")

        if not selection_raw:
            await send_message(build_text_message(from_number, "No pude leer tu selección. Intentemos de nuevo."))
            await send_main_menu(from_number)
            return

        # 1) If it's explicitly a generated question id or contains ::Q -> answer directly
        sel = selection_raw
        if sel in QUESTION_ID_MAP or "::Q" in sel:
            await send_answer(from_number, sel)
            return

        # 2) Special app main
        if sel == "APP_MAIN":
            await send_app_submenu(from_number)
            return

        # 3) Try strict category mapping
        mapped = find_category_key(sel, allow_fuzzy=False)
        if mapped:
            await send_category_questions(from_number, mapped)
            return

        # 4) If the selection looks like "1. ..." attempt index resolution from session category
        stripped = _strip_index_prefix(selection_raw)
        session_cat = user_sessions.get(from_number, {}).get("category")
        if session_cat and session_cat in KNOWLEDGE_BASE:
            m = re.match(r"^\s*(\d+)", selection_raw or "")
            if m:
                idx = int(m.group(1)) - 1
                qlist = list(KNOWLEDGE_BASE[session_cat].items())
                if 0 <= idx < len(qlist):
                    q_text, q_answer = qlist[idx]
                    gen_id = _make_question_id(session_cat, idx)
                    QUESTION_ID_MAP.setdefault(gen_id, {"category": session_cat, "text": q_text, "answer": q_answer})
                    await send_answer(from_number, gen_id)
                    return

            # fallback: try to match by (normalized) prefix inside that category
            norm_sel = _normalize_key(stripped)
            for q_text, q_answer in KNOWLEDGE_BASE[session_cat].items():
                if _normalize_key(q_text).startswith(norm_sel) or norm_sel.startswith(_normalize_key(q_text)[:max(5, len(_normalize_key(q_text))//2)]):
                    # found candidate
                    gen_id = _make_question_id(session_cat, list(KNOWLEDGE_BASE[session_cat].keys()).index(q_text))
                    QUESTION_ID_MAP.setdefault(gen_id, {"category": session_cat, "text": q_text, "answer": q_answer})
                    await send_answer(from_number, gen_id)
                    return

        # 5) Last-resort: try to find exact question by normalized text across all KB
        await send_answer(from_number, sel)
        return

    # Process button_reply
    elif msg_type == "button_reply":
        button_reply = interactive_data.get("button_reply", {}) or {}
        button_raw = _extract_candidate(button_reply)
        logger.info(f"[button_reply] extracted button_raw='{button_raw}'")

        if not button_raw:
            await send_message(build_text_message(from_number, "No pude leer tu selección. Intentemos de nuevo."))
            await send_main_menu(from_number)
            return

        bid = button_raw

        # quick button flows
        if bid == "YES":
            await send_main_menu(from_number)
            return
        if bid == "NO":
            await send_rating_request(from_number)
            return
        if bid and bid.startswith("RATE_"):
            await handle_rating(from_number, bid)
            return

        # If looks like question id
        if bid in QUESTION_ID_MAP or "::Q" in bid:
            await send_answer(from_number, bid)
            return

        # Try index resolution if it's "1. Title"
        stripped = _strip_index_prefix(bid)
        session_cat = user_sessions.get(from_number, {}).get("category")
        if session_cat and session_cat in KNOWLEDGE_BASE:
            m = re.match(r"^\s*(\d+)", bid or "")
            if m:
                idx = int(m.group(1)) - 1
                qlist = list(KNOWLEDGE_BASE[session_cat].items())
                if 0 <= idx < len(qlist):
                    q_text, q_answer = qlist[idx]
                    gen_id = _make_question_id(session_cat, idx)
                    QUESTION_ID_MAP.setdefault(gen_id, {"category": session_cat, "text": q_text, "answer": q_answer})
                    await send_answer(from_number, gen_id)
                    return

            # fallback: prefix match in session category
            norm_bid = _normalize_key(stripped)
            for q_text, q_answer in KNOWLEDGE_BASE[session_cat].items():
                if _normalize_key(q_text).startswith(norm_bid) or norm_bid.startswith(_normalize_key(q_text)[:max(5, len(_normalize_key(q_text))//2)]):
                    gen_id = _make_question_id(session_cat, list(KNOWLEDGE_BASE[session_cat].keys()).index(q_text))
                    QUESTION_ID_MAP.setdefault(gen_id, {"category": session_cat, "text": q_text, "answer": q_answer})
                    await send_answer(from_number, gen_id)
                    return

        # Try strict category mapping (in case buttons were categories)
        mapped = find_category_key(bid, allow_fuzzy=False)
        if mapped:
            await send_category_questions(from_number, mapped)
            return

        # fallback
        await send_answer(from_number, bid)
        return

    # Unknown interactive shape
    else:
        logger.warning(f"[interactive] Unknown interactive shape for {from_number}. interactive_data keys: {list(interactive_data.keys())}")
        # As recovery, resend main menu
        await send_main_menu(from_number)
        return

# ==================== WEBHOOK VERIFICATION ====================

def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Verify webhook signature"""
    if not APP_SECRET:
        logger.warning("APP_SECRET not set, skipping signature verification")
        return True
    expected_signature = hmac.new(
        APP_SECRET.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected_signature}", signature)

# ==================== FASTAPI ENDPOINTS ====================

@app.get("/webhook")
async def verify_webhook(request: Request):
    """Verify webhook for WhatsApp"""
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return JSONResponse(content=int(hub_challenge))
    logger.error("Webhook verification failed")
    raise HTTPException(status_code=403, detail="Forbidden")

@app.post("/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle incoming WhatsApp messages"""
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
                    if "messages" in value:
                        for message in value["messages"]:
                            background_tasks.add_task(process_message, message)
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
    """Process individual message"""
    try:
        from_number = message.get("from")
        message_id = message.get("id")
        message_type = message.get("type")
        logger.info(f"Processing message {message_id} from {from_number}, type: {message_type}")
        if message_type == "text":
            text_data = message.get("text", {})
            text_body = text_data.get("body", "")
            await process_text_message(from_number, text_body, message_id)
        elif message_type == "interactive":
            interactive_data = message.get("interactive", {})
            await process_interactive_message(from_number, interactive_data)
        elif message_type in ["image", "document", "audio", "video", "sticker"]:
            media_response = (
                "He recibido tu archivo multimedia. "
                "Para brindarte la mejor ayuda, por favor utiliza el menú de opciones:"
            )
            await send_message(build_text_message(from_number, media_response))
            await asyncio.sleep(1.0)
            await send_main_menu(from_number)
        else:
            logger.info(f"Unsupported message type: {message_type}")
            await send_main_menu(from_number)
    except Exception as e:
        logger.error(f"Error processing message: {e}")

@app.get("/")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "Per Capital WhatsApp Chatbot",
        "version": "1.0.0",
        "active_sessions": len(user_sessions),
        "total_ratings": len(user_ratings)
    }

@app.get("/stats")
async def get_stats():
    """Get chatbot statistics"""
    rating_counts = {}
    for rating_data in user_ratings:
        rating = rating_data["rating"]
        rating_counts[rating] = rating_counts.get(rating, 0) + 1
    total_questions = sum(len(cat) for cat in KNOWLEDGE_BASE.values())
    return {
        "active_sessions": len(user_sessions),
        "total_ratings": len(user_ratings),
        "rating_breakdown": rating_counts,
        "knowledge_base_categories": len(KNOWLEDGE_BASE),
        "total_questions": total_questions
    }

@app.post("/send-message")
async def send_manual_message(request: Request):
    """Manual message sending endpoint for testing"""
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
        success = await send_message(payload)
        if success:
            return {"status": "success", "message": "Message sent"}
        else:
            raise HTTPException(status_code=500, detail="Failed to send message")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

@app.delete("/sessions/{phone_number}")
async def clear_user_session(phone_number: str):
    """Clear a specific user's session"""
    if phone_number in user_sessions:
        del user_sessions[phone_number]
        return {"status": "success", "message": f"Session cleared for {phone_number}"}
    else:
        raise HTTPException(status_code=404, detail="Session not found")

@app.delete("/sessions")
async def clear_all_sessions():
    """Clear all user sessions"""
    count = len(user_sessions)
    user_sessions.clear()
    return {"status": "success", "message": f"Cleared {count} sessions"}

# ==================== STARTUP VALIDATION ====================

@app.on_event("startup")
async def startup_event():
    """Validate environment variables on startup"""
    required_vars = {
        "WHATSAPP_TOKEN": WHATSAPP_TOKEN,
        "PHONE_NUMBER_ID": PHONE_NUMBER_ID,
        "VERIFY_TOKEN": VERIFY_TOKEN
    }
    missing_vars = []
    placeholder_vars = []
    for var_name, var_value in required_vars.items():
        if not var_value:
            missing_vars.append(var_name)
        elif "your_" in var_value.lower() and "_here" in var_value.lower():
            placeholder_vars.append(var_name)
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
    if placeholder_vars:
        logger.warning(f"Please update placeholder values for: {', '.join(placeholder_vars)}")
    total_questions = sum(len(cat) for cat in KNOWLEDGE_BASE.values())
    logger.info("Per Capital WhatsApp Chatbot started successfully!")
    logger.info(f"Knowledge base loaded with {len(KNOWLEDGE_BASE)} categories")
    logger.info(f"Total questions available: {total_questions}")

# ==================== ERROR HANDLERS ====================

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=404,
        content={"error": "Endpoint not found", "detail": "The requested endpoint does not exist"}
    )

@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception):
    logger.error(f"Internal server error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": "An unexpected error occurred"}
    )

# ==================== UTILITY FUNCTIONS ====================

def get_question_by_id(question_id: str) -> Optional[Dict]:
    """Get question data by ID from QUESTION_ID_MAP"""
    return QUESTION_ID_MAP.get(question_id)

def get_user_session_info(phone_number: str) -> Dict:
    """Get user session information"""
    session = user_sessions.get(phone_number, {})
    return {
        "exists": phone_number in user_sessions,
        "state": session.get("state", "new"),
        "last_interaction": session.get("last_interaction", "never"),
        "category": session.get("category", None)
    }

# ==================== MAIN EXECUTION ====================

if __name__ == "__main__":
    import uvicorn
    print("Starting Per Capital WhatsApp Chatbot...")
    print(f"Environment check:")
    print(f"  WHATSAPP_TOKEN: {'✓' if WHATSAPP_TOKEN and 'your_' not in WHATSAPP_TOKEN.lower() else '✗'}")
    print(f"  PHONE_NUMBER_ID: {'✓' if PHONE_NUMBER_ID and 'your_' not in PHONE_NUMBER_ID.lower() else '✗'}")
    print(f"  VERIFY_TOKEN: {'✓' if VERIFY_TOKEN and 'your_' not in VERIFY_TOKEN.lower() else '✗'}")
    print(f"  APP_SECRET: {'✓' if APP_SECRET and 'your_' not in APP_SECRET.lower() else '✗ (optional)'}")
    uvicorn.run(
        "main:app",  # Assuming this file is named main.py
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
