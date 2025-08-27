# main.py
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

# -------------------- Logging --------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("per-capital-whatsapp-bot")

# -------------------- Environment --------------------
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "your_whatsapp_token_here")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "your_phone_number_id_here")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "your_verify_token_here")
APP_SECRET = os.getenv("APP_SECRET", "")  # optional, but recommended

GRAPH_API_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_TOKEN}",
    "Content-Type": "application/json"
}

# -------------------- App & State --------------------
app = FastAPI(title="Per Capital WhatsApp Chatbot - Rewritten")

# In-memory state (for production use Redis or DB)
user_sessions: Dict[str, Dict[str, Any]] = {}
user_ratings: List[Dict[str, Any]] = []
QUESTION_ID_MAP: Dict[str, Dict[str, Any]] = {}

# -------------------- Knowledge base (kept as original content) --------------------
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
        "¿Cuánto tarda mi aprobación?": "De 2 a 5 días hábiles siempre que hayas completado 100% del registro y recaudos.",
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

# -------------------- Helpers: normalization & ids --------------------
def _normalize_key(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    s_norm = unicodedata.normalize("NFKD", s)
    s_no_accents = "".join(ch for ch in s_norm if not unicodedata.combining(ch))
    filtered = "".join(ch for ch in s_no_accents if ch.isalnum() or ch.isspace())
    return " ".join(filtered.split()).upper()

def _category_to_id(k: str) -> str:
    return _normalize_key(k).replace(" ", "_")

def _make_question_id(category_key: str, idx: int) -> str:
    cat_safe = _category_to_id(category_key)
    return f"{cat_safe}::Q{idx+1}"

def find_category_key(selection_id: str, allow_fuzzy: bool = False) -> Optional[str]:
    if not selection_id:
        return None
    # if it's a generated question id, not a category
    if "::Q" in selection_id:
        return None
    sel_candidate = selection_id.replace("_", " ").strip()
    norm_candidate = _normalize_key(sel_candidate)
    # try exact by key or normalized equality
    for k in KNOWLEDGE_BASE.keys():
        if selection_id == k or _normalize_key(k) == norm_candidate or _normalize_key(k) == _normalize_key(selection_id):
            return k
    if allow_fuzzy:
        for k in KNOWLEDGE_BASE.keys():
            nk = _normalize_key(k)
            if norm_candidate in nk or nk in norm_candidate:
                return k
    return None

# -------------------- Builders --------------------
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
            "footer": {"text": "Per Capital - Tu asistente virtual"},
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
            "footer": {"text": "Per Capital - Tu asistente virtual"},
            "action": {"buttons": buttons}
        }
    }

# -------------------- Sending / HTTP --------------------
async def send_message(payload: Dict) -> bool:
    """Send message to WhatsApp via Graph API."""
    # sanity: ensure token and phone id present
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID or "your_" in WHATSAPP_TOKEN.lower():
        logger.error("Missing or placeholder credentials - message not sent.")
        return False
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(GRAPH_API_URL, headers=HEADERS, json=payload)
            r.raise_for_status()
            logger.info("Sent message to %s, type=%s", payload.get("to"), payload.get("type"))
            return True
    except httpx.HTTPStatusError as e:
        logger.error("WhatsApp API returned %s: %s", e.response.status_code, e.response.text)
        return False
    except Exception as e:
        logger.error("Error sending message: %s", e)
        return False

async def send_typing_and_wait(to: str, seconds: float = 1.5):
    # Simulate typing by waiting; optionally could send read receipts if API used
    await asyncio.sleep(0.3)
    await asyncio.sleep(seconds)

# -------------------- Conversation flows --------------------
async def send_welcome_sequence(to: str):
    text = (
        "¡Hola! 👋 Bienvenido a Per Capital\n\n"
        "Soy tu asistente virtual y estoy aquí para ayudarte con tus consultas.\n\n"
        "¿Cómo puedo ayudarte hoy?"
    )
    await send_typing_and_wait(to, 1.0)
    await send_message(build_text_message(to, text))
    await asyncio.sleep(0.5)
    await send_main_menu(to)

async def send_main_menu(to: str):
    rows = []
    # add app virtual item first
    rows.append({"id": "APP_MAIN", "title": "App Per Capital", "description": "Registro, suscripción, rescate y más"})
    for k in KNOWLEDGE_BASE.keys():
        rows.append({"id": _category_to_id(k), "title": k, "description": f"Información sobre {k}"})
    sections = [{"title": "Categorías disponibles", "rows": rows}]
    payload = build_interactive_list_message(
        to=to,
        header="Menú Principal",
        body="Selecciona la categoría sobre la que necesitas información:",
        sections=sections
    )
    await send_message(payload)
    user_sessions[to] = {"state": "main_menu", "last_interaction": datetime.now().isoformat()}

async def send_app_submenu(to: str):
    # collect app-related categories
    app_keys = []
    for k in KNOWLEDGE_BASE.keys():
        kn = _normalize_key(k)
        if any(tok in kn for tok in ("REGISTRO", "SUSCRIP", "RESCAT", "POSICION")):
            app_keys.append(k)
    if not app_keys:
        app_keys = ["REGISTRO", "SUSCRIPCIÓN", "RESCATE", "POSICIÓN"]
    rows = [{"id": _category_to_id(k), "title": k, "description": f"Consultas sobre {k}"} for k in app_keys if k in KNOWLEDGE_BASE]
    sections = [{"title": "Opciones de la App", "rows": rows}]
    payload = build_interactive_list_message(
        to=to,
        header="App Per Capital",
        body="¿Sobre qué aspecto de la app necesitas información?",
        sections=sections
    )
    await send_message(payload)
    user_sessions[to] = {"state": "app_submenu", "last_interaction": datetime.now().isoformat()}

async def send_category_questions(to: str, category_id: str):
    # support an APP_GENERAL virtual id
    if category_id == "APP_MAIN" or category_id == "APP_GENERAL":
        # combine relevant sections
        combined = {}
        for k in KNOWLEDGE_BASE.keys():
            kn = _normalize_key(k)
            if any(tok in kn for tok in ("REGISTRO", "SUSCRIP", "RESCAT", "POSICION")):
                combined.update(KNOWLEDGE_BASE[k])
        category_map = combined
        category_title = "App Per Capital (Resumen)"
        session_key = "APP_GENERAL"
    else:
        mapped = find_category_key(category_id, allow_fuzzy=True)
        if not mapped:
            # try reversing underscores
            mapped = find_category_key(category_id.replace("_", " "), allow_fuzzy=True)
        if not mapped:
            await send_message(build_text_message(to, "Lo siento, no pude encontrar esa categoría."))
            await send_main_menu(to)
            return
        category_map = KNOWLEDGE_BASE.get(mapped, {})
        category_title = mapped
        session_key = mapped

    # build question list and populate QUESTION_ID_MAP
    questions_local = []
    for i, (q_text, q_answer) in enumerate(category_map.items()):
        qid = _make_question_id(category_title, i)
        unique_qid = qid
        suffix = 1
        while unique_qid in QUESTION_ID_MAP:
            unique_qid = f"{qid}_{suffix}"
            suffix += 1
        QUESTION_ID_MAP[unique_qid] = {"category": category_title, "text": q_text, "answer": q_answer}
        questions_local.append({"id": unique_qid, "text": q_text, "answer": q_answer})

    if not questions_local:
        await send_message(build_text_message(to, "No hay preguntas disponibles en esta categoría."))
        await send_main_menu(to)
        return

    if len(questions_local) <= 3:
        buttons = []
        for i, q in enumerate(questions_local[:3]):
            title = q["text"]
            if len(title) > 40:
                title = title[:37] + "..."
            buttons.append({"type": "reply", "reply": {"id": q["id"], "title": f"{i+1}. {title}"}})
        payload = build_reply_button_message(to=to, body=f"*{category_title}*\n\nSelecciona tu pregunta:", buttons=buttons)
    else:
        rows = []
        for i, q in enumerate(questions_local):
            title_short = q["text"] if len(q["text"]) <= 24 else q["text"][:21] + "..."
            desc = q["text"] if len(q["text"]) <= 72 else q["text"][:69] + "..."
            rows.append({"id": q["id"], "title": f"{i+1}. {title_short}", "description": desc})
        sections = [{"title": category_title, "rows": rows}]
        payload = build_interactive_list_message(to=to, header=category_title, body="Selecciona tu pregunta:", sections=sections)

    await send_message(payload)
    user_sessions[to] = {"state": "questions_menu", "category": session_key, "last_interaction": datetime.now().isoformat()}

async def send_answer(to: str, question_id: str):
    # Resolve qdata from QUESTION_ID_MAP or from KB by normalized matching
    qdata = QUESTION_ID_MAP.get(question_id)
    if not qdata:
        # try normalized exact match across question texts
        norm_in = _normalize_key(question_id or "")
        for qid, data in QUESTION_ID_MAP.items():
            if _normalize_key(data.get("text", "")) == norm_in:
                qdata = data
                break
        if not qdata:
            for cat_key, qa_map in KNOWLEDGE_BASE.items():
                for q_text, q_answer in qa_map.items():
                    if _normalize_key(q_text) == norm_in:
                        qdata = {"category": cat_key, "text": q_text, "answer": q_answer}
                        break
                if qdata:
                    break

    # fallback: if looks like generated "CAT::Qn"
    if not qdata and "::Q" in (question_id or ""):
        try:
            cat_part = question_id.split("::Q")[0]
            cat_guess = cat_part.replace("_", " ").strip()
            mapped = find_category_key(cat_guess, allow_fuzzy=True)
            if mapped and mapped in KNOWLEDGE_BASE:
                qlist = list(KNOWLEDGE_BASE[mapped].items())
                idx = int(question_id.split("::Q")[1].split("_")[0]) - 1
                if 0 <= idx < len(qlist):
                    q_text, q_answer = qlist[idx]
                    qdata = {"category": mapped, "text": q_text, "answer": q_answer}
                    QUESTION_ID_MAP[question_id] = qdata
        except Exception:
            qdata = None

    if not qdata:
        await send_message(build_text_message(to, "Lo siento, no pude encontrar la respuesta a esa pregunta."))
        await send_main_menu(to)
        return

    answer = qdata["answer"]
    await send_typing_and_wait(to, 1.0)
    await send_message(build_text_message(to, answer))
    await asyncio.sleep(0.8)
    await send_more_help_options(to)

async def send_more_help_options(to: str):
    buttons = [
        {"type": "reply", "reply": {"id": "YES", "title": "Sí, por favor"}},
        {"type": "reply", "reply": {"id": "NO", "title": "No, gracias"}}
    ]
    payload = build_reply_button_message(to=to, body="¿Necesitas ayuda con alguna otra cosa?", buttons=buttons)
    await send_message(payload)
    user_sessions[to] = {"state": "more_help", "last_interaction": datetime.now().isoformat()}

async def send_rating_request(to: str):
    buttons = [
        {"type": "reply", "reply": {"id": "RATE_EXCELLENT", "title": "Excelente"}},
        {"type": "reply", "reply": {"id": "RATE_GOOD", "title": "Bien"}},
        {"type": "reply", "reply": {"id": "RATE_NEEDS_IMPROVEMENT", "title": "Necesita mejorar"}}
    ]
    payload = build_reply_button_message(to=to, body="¡Gracias por usar nuestro asistente virtual! 😊\n\n¿Cómo calificarías la ayuda recibida?", buttons=buttons)
    await send_message(payload)
    user_sessions[to] = {"state": "rating", "last_interaction": datetime.now().isoformat()}

async def handle_rating(to: str, rating_id: str):
    rating_map = {
        "RATE_EXCELLENT": "Excelente",
        "RATE_GOOD": "Bien",
        "RATE_NEEDS_IMPROVEMENT": "Necesita mejorar"
    }
    rating = rating_map.get(rating_id, "Desconocida")
    user_ratings.append({"user": to, "rating": rating, "timestamp": datetime.now().isoformat()})
    thank_you = (
        f"¡Gracias por tu calificación: *{rating}*! 🙏\n\n"
        "Tu opinión es muy importante para nosotros.\n\n"
        "Si necesitas más ayuda en el futuro, escríbenos. ¡Que tengas un excelente día! 😊"
    )
    await send_message(build_text_message(to, thank_you))
    if to in user_sessions:
        del user_sessions[to]
    logger.info("Saved rating %s for user %s", rating, to)

# -------------------- Message processing (text & interactive) --------------------
def is_greeting(text: str) -> bool:
    if not text:
        return False
    greetings = ["hola", "hello", "hi", "buenas", "buenos dias", "buenas tardes", "buenas noches", "saludos", "que tal", "hey", "inicio"]
    return text.lower().strip() in greetings

async def process_text_message(from_number: str, text: str, message_id: Optional[str] = None):
    logger.info("process_text_message from=%s text=%s", from_number, text)
    if is_greeting(text):
        await send_welcome_sequence(from_number)
        return
    session_state = user_sessions.get(from_number, {}).get("state", "new")
    if session_state == "new":
        await send_welcome_sequence(from_number)
    else:
        await send_message(build_text_message(from_number, "Para brindarte la mejor ayuda, por favor utiliza los botones y opciones del menú. Te muestro el menú:"))
        await asyncio.sleep(0.6)
        await send_main_menu(from_number)

# robust interactive parsing
def _extract_interactive_candidate(obj: Dict) -> Optional[str]:
    # Accept dicts from list_reply or button_reply
    if not obj:
        return None
    # common keys
    for key in ("id", "title", "payload", "name", "value"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            raw = v.strip()
            # if it's JSON-encoded, try parse
            if raw.startswith("{") and raw.endswith("}"):
                try:
                    parsed = json.loads(raw)
                    for k2 in ("id", "title", "payload", "name", "value"):
                        if parsed.get(k2):
                            return str(parsed.get(k2)).strip()
                except Exception:
                    pass
            # clean up common separators that clients sometimes append
            raw = raw.replace("%3A%3A", "::")
            # remove trailing descriptions separated by " - " or " | "
            for sep in [" - ", " | ", "\n", "Información sobre", "Information about"]:
                if sep in raw:
                    raw = raw.split(sep)[0].strip()
            return raw
    return None

def _strip_index_prefix(s: str) -> str:
    return re.sub(r'^\s*\d+\s*[\.\-\)\:]?\s*', '', s or '')

async def process_interactive_message(from_number: str, interactive_data: Dict):
    """
    Handle interactive messages robustly. interactive_data may contain:
    - { "type": "list_reply", "list_reply": { "id": "...", "title": "..."} }
    - { "type": "button_reply", "button_reply": { "id": "...", "title": "..."} }
    - or nested/variant shapes received from webhook
    """
    logger.info("[interactive] from=%s data_keys=%s", from_number, list(interactive_data.keys()))
    # determine type
    msg_type = interactive_data.get("type")
    if not msg_type:
        if "list_reply" in interactive_data:
            msg_type = "list_reply"
        elif "button_reply" in interactive_data:
            msg_type = "button_reply"
        else:
            # sometimes whatsapp nests interactive: { "interactive": { "list_reply": { ... } } }
            if any(k in interactive_data for k in ("list_reply", "button_reply")):
                msg_type = "list_reply" if "list_reply" in interactive_data else "button_reply"

    # extract candidate entity
    candidate = None
    if msg_type == "list_reply":
        candidate = _extract_interactive_candidate(interactive_data.get("list_reply", {}) or interactive_data)
        logger.info("[interactive][list_reply] candidate=%s", candidate)
        if not candidate:
            await send_message(build_text_message(from_number, "No pude leer tu selección. Intentemos de nuevo."))
            await send_main_menu(from_number)
            return

        sel = candidate
        # direct question id (we used ::Q) or previously generated
        if sel in QUESTION_ID_MAP or "::Q" in sel:
            await send_answer(from_number, sel)
            return

        # virtual app
        if sel.upper() in ("APP_MAIN", "APP_GENERAL"):
            await send_app_submenu(from_number)
            return

        # try as category
        mapped = find_category_key(sel, allow_fuzzy=True)
        if mapped:
            await send_category_questions(from_number, mapped)
            return

        # try index resolution using session category
        session_cat = user_sessions.get(from_number, {}).get("category")
        if session_cat and session_cat in KNOWLEDGE_BASE:
            # if candidate starts with a number like "1." resolve index
            m = re.match(r'^\s*(\d+)', candidate or "")
            if m:
                idx = int(m.group(1)) - 1
                qlist = list(KNOWLEDGE_BASE[session_cat].items())
                if 0 <= idx < len(qlist):
                    q_text, q_answer = qlist[idx]
                    gen_id = _make_question_id(session_cat, idx)
                    QUESTION_ID_MAP.setdefault(gen_id, {"category": session_cat, "text": q_text, "answer": q_answer})
                    await send_answer(from_number, gen_id)
                    return
            stripped = _strip_index_prefix(candidate)
            norm_stripped = _normalize_key(stripped)
            for q_text, q_answer in KNOWLEDGE_BASE[session_cat].items():
                if _normalize_key(q_text).startswith(norm_stripped) or norm_stripped.startswith(_normalize_key(q_text)[:max(5, len(_normalize_key(q_text))//2)]):
                    idx = list(KNOWLEDGE_BASE[session_cat].keys()).index(q_text)
                    gen_id = _make_question_id(session_cat, idx)
                    QUESTION_ID_MAP.setdefault(gen_id, {"category": session_cat, "text": q_text, "answer": q_answer})
                    await send_answer(from_number, gen_id)
                    return

        # last resort: try matching question text across KB
        await send_answer(from_number, sel)
        return

    elif msg_type == "button_reply":
        candidate = _extract_interactive_candidate(interactive_data.get("button_reply", {}) or interactive_data)
        logger.info("[interactive][button_reply] candidate=%s", candidate)
        if not candidate:
            await send_message(build_text_message(from_number, "No pude leer tu selección. Intentemos de nuevo."))
            await send_main_menu(from_number)
            return
        bid = candidate

        # predefined flows
        if bid.upper() == "YES" or bid.upper() == "SI" or bid.upper() == "SÍ":
            await send_main_menu(from_number)
            return
        if bid.upper() == "NO":
            await send_rating_request(from_number)
            return
        if bid.upper().startswith("RATE_"):
            await handle_rating(from_number, bid.upper())
            return

        # if looks like question id
        if bid in QUESTION_ID_MAP or "::Q" in bid:
            await send_answer(from_number, bid)
            return

        # try index resolution as with list_reply
        session_cat = user_sessions.get(from_number, {}).get("category")
        m = re.match(r'^\s*(\d+)', bid or "")
        if session_cat and session_cat in KNOWLEDGE_BASE and m:
            idx = int(m.group(1)) - 1
            qlist = list(KNOWLEDGE_BASE[session_cat].items())
            if 0 <= idx < len(qlist):
                q_text, q_answer = qlist[idx]
                gen_id = _make_question_id(session_cat, idx)
                QUESTION_ID_MAP.setdefault(gen_id, {"category": session_cat, "text": q_text, "answer": q_answer})
                await send_answer(from_number, gen_id)
                return

        # try category mapping
        mapped = find_category_key(bid, allow_fuzzy=True)
        if mapped:
            await send_category_questions(from_number, mapped)
            return

        # fallback try answer by text
        await send_answer(from_number, bid)
        return

    else:
        logger.warning("Unknown interactive shape for %s. data: %s", from_number, interactive_data)
        await send_main_menu(from_number)
        return

# -------------------- Webhook signature --------------------
def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    if not APP_SECRET:
        logger.warning("APP_SECRET not set; skipping signature verification")
        return True
    if not signature:
        logger.error("No signature header provided")
        return False
    # signature header usually like: "sha256=..."
    try:
        sig_value = signature
        if sig_value.startswith("sha256="):
            sig_value = sig_value.split("sha256=")[1]
        expected = hmac.new(APP_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig_value)
    except Exception as e:
        logger.error("Signature verification error: %s", e)
        return False

# -------------------- Web endpoints --------------------
@app.get("/webhook")
async def verify_webhook(request: Request):
    hub_mode = request.query_params.get("hub.mode")
    hub_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")
    if hub_mode == "subscribe" and hub_token == VERIFY_TOKEN:
        logger.info("Webhook verified")
        # return as plain text integer or string challenge
        return JSONResponse(content=int(hub_challenge) if hub_challenge and hub_challenge.isdigit() else hub_challenge)
    logger.error("Webhook verification failed")
    raise HTTPException(status_code=403, detail="Forbidden")

@app.post("/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")
        # verify signature
        if not verify_webhook_signature(body, signature):
            logger.error("Invalid signature")
            raise HTTPException(status_code=403, detail="Invalid signature")
        data = json.loads(body.decode())
        logger.debug("Webhook payload: %s", data)
        if data.get("object") == "whatsapp_business_account":
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    # messages
                    if "messages" in value:
                        for message in value["messages"]:
                            # schedule processing in background
                            try:
                                background_tasks.add_task(process_message, message)
                            except Exception:
                                # fallback to asyncio.create_task
                                asyncio.create_task(process_message(message))
                    # statuses (delivery/read) - just log for now
                    if "statuses" in value:
                        for status in value["statuses"]:
                            logger.info("Status update: %s", status)
        return JSONResponse(content={"status": "success"})
    except json.JSONDecodeError:
        logger.error("Invalid JSON in webhook")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logger.exception("Error processing webhook: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")

# central message processor (invoked from background task)
async def process_message(message: Dict):
    """Process a single incoming message (text, interactive, media...)"""
    try:
        from_number = message.get("from")
        message_id = message.get("id")
        message_type = message.get("type")
        logger.info("Processing message id=%s from=%s type=%s", message_id, from_number, message_type)

        if message_type == "text":
            text_body = (message.get("text") or {}).get("body", "")
            await process_text_message(from_number, text_body, message_id)
            return

        if message_type == "interactive":
            interactive_data = message.get("interactive", {}) or {}
            await process_interactive_message(from_number, interactive_data)
            return

        if message_type in ["image", "document", "audio", "video", "sticker"]:
            media_response = "He recibido tu archivo multimedia. Para brindarte la mejor ayuda, por favor utiliza el menú de opciones:"
            await send_message(build_text_message(from_number, media_response))
            await asyncio.sleep(0.6)
            await send_main_menu(from_number)
            return

        # fallback: unknown types
        logger.info("Unsupported message type: %s", message_type)
        await send_main_menu(from_number)
    except Exception as e:
        logger.exception("Error in process_message: %s", e)

# -------------------- Admin endpoints --------------------
@app.post("/send-message")
async def send_manual_message(request: Request):
    try:
        data = await request.json()
        to = data.get("to")
        message = data.get("message")
        mtype = data.get("type", "text")
        if not to or not message:
            raise HTTPException(status_code=400, detail="Missing 'to' or 'message'")
        if mtype == "text":
            payload = build_text_message(to, message)
        else:
            raise HTTPException(status_code=400, detail="Only 'text' messages supported via this endpoint")
        ok = await send_message(payload)
        if ok:
            return {"status": "success", "message": "Message sent"}
        else:
            raise HTTPException(status_code=500, detail="Failed to send message")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

@app.get("/")
async def health_check():
    total_questions = sum(len(cat) for cat in KNOWLEDGE_BASE.values())
    return {
        "status": "healthy",
        "service": "Per Capital WhatsApp Chatbot",
        "version": "1.0.0",
        "active_sessions": len(user_sessions),
        "total_ratings": len(user_ratings),
        "total_questions": total_questions
    }

@app.get("/stats")
async def get_stats():
    rating_counts = {}
    for r in user_ratings:
        rating_counts[r["rating"]] = rating_counts.get(r["rating"], 0) + 1
    return {
        "active_sessions": len(user_sessions),
        "total_ratings": len(user_ratings),
        "rating_breakdown": rating_counts,
        "knowledge_base_categories": len(KNOWLEDGE_BASE),
        "total_questions": sum(len(c) for c in KNOWLEDGE_BASE.values())
    }

@app.delete("/sessions/{phone_number}")
async def clear_user_session(phone_number: str):
    if phone_number in user_sessions:
        del user_sessions[phone_number]
        return {"status": "success", "message": f"Session cleared for {phone_number}"}
    raise HTTPException(status_code=404, detail="Session not found")

@app.delete("/sessions")
async def clear_all_sessions():
    count = len(user_sessions)
    user_sessions.clear()
    return {"status": "success", "message": f"Cleared {count} sessions"}

# -------------------- Utilities --------------------
def get_question_by_id(question_id: str) -> Optional[Dict]:
    return QUESTION_ID_MAP.get(question_id)

def get_user_session_info(phone_number: str) -> Dict:
    s = user_sessions.get(phone_number, {})
    return {"exists": phone_number in user_sessions, "state": s.get("state", "new"), "last_interaction": s.get("last_interaction", "never"), "category": s.get("category")}

# -------------------- Startup validations --------------------
@app.on_event("startup")
async def startup_event():
    required = {"WHATSAPP_TOKEN": WHATSAPP_TOKEN, "PHONE_NUMBER_ID": PHONE_NUMBER_ID, "VERIFY_TOKEN": VERIFY_TOKEN}
    missing = [k for k, v in required.items() if not v]
    placeholders = [k for k, v in required.items() if v and "your_" in v.lower()]
    if missing:
        logger.error("Missing required env vars: %s", ", ".join(missing))
    if placeholders:
        logger.warning("Placeholder env vars detected: %s", ", ".join(placeholders))
    logger.info("Bot startup complete. KB categories=%d total_questions=%d", len(KNOWLEDGE_BASE), sum(len(c) for c in KNOWLEDGE_BASE.values()))

# -------------------- Run --------------------
if __name__ == "__main__":
    import uvicorn
    print("Starting Per Capital WhatsApp Chatbot...")
    print("Env check:")
    print(f" WHATSAPP_TOKEN: {'✓' if WHATSAPP_TOKEN and 'your_' not in WHATSAPP_TOKEN.lower() else '✗'}")
    print(f" PHONE_NUMBER_ID: {'✓' if PHONE_NUMBER_ID and 'your_' not in PHONE_NUMBER_ID.lower() else '✗'}")
    print(f" VERIFY_TOKEN: {'✓' if VERIFY_TOKEN and 'your_' not in VERIFY_TOKEN.lower() else '✗'}")
    print(f" APP_SECRET: {'✓' if APP_SECRET and 'your_' not in APP_SECRET.lower() else '✗ (optional)'}")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
