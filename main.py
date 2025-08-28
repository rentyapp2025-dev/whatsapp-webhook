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
from fastapi.responses import JSONResponse, PlainTextResponse
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
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# -------------------- App & State --------------------
app = FastAPI(title="Per Capital WhatsApp Chatbot - Rewritten")

# In-memory state (for production use Redis or DB)
user_sessions: Dict[str, Dict[str, Any]] = {}
user_ratings: List[Dict[str, Any]] = []
QUESTION_ID_MAP: Dict[str, Dict[str, Any]] = {}

# -------------------- Knowledge base (desde el PDF, APP sin Riesgos/Soporte) --------------------
KNOWLEDGE_BASE: Dict[str, Any] = {
    "PER CAPITAL": {
        "¿Qué es Per Capital?": "Es un grupo de empresas del Mercado de Valores Venezolano reguladas por la SUNAVAL.",
        "¿Quién regula a PER CAPITAL?": "La SUNAVAL (Superintendencia Nacional de Valores).",
        "¿Qué es la SUNAVAL?": "Es quien protege a los inversionistas y regula a intermediarios y emisores del Mercado de Valores venezolano.",
        "¿Qué es la Bolsa de Valores de Caracas?": "Es el lugar donde se compran y venden bonos, acciones y otros instrumentos de manera ordenada a través de las Casas de Bolsa y está regulada por la SUNAVAL.",
        "¿Cómo invierto?": "Para invertir en el Fondo Mutual Abierto de PER CAPITAL debes descargar la app, registrarte, subir recaudos y colocar tus órdenes de compra."
    },

    # APP con subcategorías (GENERAL, REGISTRO, SUSCRIPCIÓN, RESCATE, POSICIÓN)
    "APP": {
        "GENERAL": {
            "¿Puedo comprar acciones y bonos?": "No, la app actual es únicamente para invertir en el Fondo Mutual Abierto. Próximamente saldrá una nueva versión para negociar."
        },
        "REGISTRO": {
            "¿Cómo me registro?": "Descarga la app, completa 100% de los datos, acepta los contratos, sube tus recaudos (Cédula de Identidad y Selfie) y espera tu aprobación.",
            "¿Cuánto tarda mi aprobación?": "De 2 a 5 días hábiles siempre que hayas completado 100% de registro y recaudos.",
            "¿Qué hago si no me aprueban?": "Revisa que hayas completado 100% del registro y recaudos; si persiste, contáctanos en SOPORTE.",
            "¿Puedo invertir si soy menor de edad?": "Debes dirigirte a nuestras oficinas y registrarte con tu representante legal.",
            "¿Puedo modificar alguno de mis datos?": "Sí, pero por exigencia de la ley entras nuevamente en revisión.",
            "¿Debo tener cuenta en la Caja Venezolana?": "No, para invertir en nuestro Fondo Mutual Abierto no es necesaria la cuenta en la CVV."
        },
        "SUSCRIPCIÓN": {
            "¿Cómo suscribo (compro)?": "Haz click en Negociación > Suscripción > Monto a invertir > Suscribir > Método de Pago. Recuerda pagar desde TU cuenta bancaria y subir el comprobante.",
            "¿Cómo pago mi suscripción?": "Debes pagar desde TU cuenta bancaria vía Pago Móvil y subir el comprobante. IMPORTANTE: no se aceptan pagos de terceros.",
            "¿Puede pagar alguien por mí?": "No, la ley prohíbe los pagos de terceros. Siempre debes pagar desde tu cuenta bancaria.",
            "¿Cómo veo mi inversión?": "En el Home en la sección Mi Cuenta.",
            "¿Cuándo veo mi inversión?": "Al cierre del sistema en días hábiles bancarios después del cierre de mercado y la publicación de tasas del Banco Central de Venezuela.",
            "¿Cuáles son las comisiones?": "3% flat Suscripción, 3% flat Rescate y 5% anual Administración.",
            "¿Qué hago después de suscribir?": "Monitorea tu inversión desde la app.",
            "¿Debo invertir siempre el mismo monto?": "No, puedes invertir el monto que desees.",
            "¿Puedo invertir cuando quiera?": "Sí, puedes invertir cuando quieras, las veces que quieras."
        },
        "RESCATE": {
            "¿Cómo rescato (vendo)?": "Haz click en Negociación > Rescate > Unidades a Rescatar > Rescatar. Los fondos se enviarán a TU cuenta bancaria.",
            "¿Cuándo me pagan mis rescates (ventas)?": "Al próximo día hábil bancario en horario de mercado."
        },
        "POSICIÓN": {
            "¿Cómo veo el saldo de mi inversión?": "En el Home en la sección Mi Cuenta.",
            "¿Cuándo se actualiza/ve mi posición (saldo)?": "Al cierre del sistema en días hábiles bancarios después del cierre de mercado y la publicación de tasas del Banco Central de Venezuela.",
            "¿Dónde veo mi histórico?": "En la sección Historial.",
            "¿Dónde veo reportes?": "En la sección Documentos > Reportes > Año > Trimestre."
        }
    },

    "FONDO MUTUAL ABIERTO": {
        "¿Qué es un Fondo Mutual?": "Es un instrumento de inversión en grupo donde varias personas ponen dinero en un fondo gestionado por expertos; está diseñado para ser diversificado, de bajo riesgo y dirigido a pequeños inversionistas con poca experiencia.",
        "¿Qué es una Unidad de Inversión?": "Es una porción del fondo. Cuando inviertes adquieres unidades que representan tu parte del fondo.",
        "¿Qué es el VUI?": "El Valor de la Unidad de Inversión (VUI) es el precio de una Unidad de Inversión. Si el VUI sube, tu inversión gana valor. Se calcula diariamente al cierre del día y depende del comportamiento de las inversiones del fondo.",
        "¿Cómo invierto?": "Descarga la app para Android y iOS, regístrate, sube recaudos, acepta los contratos, espera tu aprobación y suscribe Unidades de Inversión cuando quieras y cuantas veces desees.",
        "¿Cuál es el monto mínimo de inversión?": "1 Unidad de Inversión.",
        "¿Cómo gano?": "Ganas por apreciación (subida del VUI) o por dividendo (si es decretado).",
        "¿En cuánto tiempo gano?": "Ganas a largo plazo; se recomienda medir resultados trimestralmente.",
        "¿Dónde consigo más información?": "En los prospectos y hojas de términos en www.per-capital.com."
    },

    # Top-level fuera de APP
    "RIESGOS": {
        "¿Cuáles son los riesgos al invertir?": "Todas las inversiones están sujetas a riesgos y la pérdida de capital es posible. Algunos riesgos son: riesgo de mercado, país, cambiario y sector."
    },
    "SOPORTE": {
        "Estoy en revisión, ¿qué hago?": "Asegúrate de haber completado 100% datos y recaudos y espera tu aprobación. Si tarda más de lo habitual, contáctanos en SOPORTE.",
        "No me llega el SMS": "Asegúrate de tener buena señal y de haber colocado correctamente un número telefónico venezolano.",
        "No me llega el correo": "Asegúrate de no dejar espacios al final cuando escribiste tu correo electrónico.",
        "No logro descargar el App": "Asegúrate de que tu App Store esté configurada en la región de Venezuela.",
        "No me abre el App": "Asegúrate de tener la versión actualizada y que tu tienda de apps esté configurada en la región de Venezuela.",
        "¿Cómo recupero mi clave?": "Selecciona Recuperar; te llegará una clave temporal para ingresar y luego actualiza tu nueva clave."
    },
}

# -------------------- Helpers: normalization & ids --------------------
def _normalize_key(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    s = s.replace("_", " ")
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
    if "::Q" in selection_id:
        return None
    sel_candidate = selection_id.strip()
    norm_candidate = _normalize_key(sel_candidate)
    for k in KNOWLEDGE_BASE.keys():
        if selection_id == k or _normalize_key(k) == norm_candidate or _normalize_key(k) == _normalize_key(selection_id):
            return k
    if allow_fuzzy:
        for k in KNOWLEDGE_BASE.keys():
            nk = _normalize_key(k)
            if norm_candidate in nk or nk in norm_candidate:
                return k
    return None

# --- helpers de routing ---
def _is_category_id(candidate: str) -> Optional[str]:
    if not candidate:
        return None
    cid = candidate.strip()
    if cid in ("APP_MAIN", "APP_GENERAL"):
        return cid
    mapped = find_category_key(cid, allow_fuzzy=True)
    return mapped

def _is_question_id(candidate: str) -> bool:
    if not candidate:
        return False
    cid = candidate.strip()
    if cid in QUESTION_ID_MAP:
        return True
    if "::Q" in cid:
        return True
    norm_in = _normalize_key(cid)
    for data in QUESTION_ID_MAP.values():
        if _normalize_key(data.get("text", "")) == norm_in:
            return True
    # Buscar en top-level y APP anidado
    for cat_key, qa_map in KNOWLEDGE_BASE.items():
        if isinstance(qa_map, dict):
            if cat_key == "APP":
                for sub, sub_map in qa_map.items():
                    if isinstance(sub_map, dict):
                        for q_text in sub_map.keys():
                            if _normalize_key(q_text) == norm_in:
                                return True
            else:
                for q_text in qa_map.keys():
                    if _normalize_key(q_text) == norm_in:
                        return True
    return False

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
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID or "your_" in WHATSAPP_TOKEN.lower():
        logger.error("Missing or placeholder credentials - message not sent.")
        return False
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(GRAPH_API_URL, headers=HEADERS, json=payload)
            r.raise_for_status()
            logger.info("Sent message to %s, type=%s, payload_keys=%s", payload.get("to"), payload.get("type"), list(payload.keys()))
            return True
    except httpx.HTTPStatusError as e:
        logger.error("WhatsApp API returned %s: %s", e.response.status_code, e.response.text)
        return False
    except Exception as e:
        logger.error("Error sending message: %s", e)
        return False

async def send_typing_and_wait(to: str, seconds: float = 1.5):
    await asyncio.sleep(0.3)
    await asyncio.sleep(seconds)

# -------------------- Conversation flows --------------------
async def send_welcome_sequence(to: str):
    text = (
        """👋 ¡Bienvenido, inversionista!
Soy Benjamín, tu asistente virtual en Per Capital, y estoy aquí para ayudarte con cualquier consulta que tengas.
¿En qué puedo ayudarte hoy?"""
    )
    await send_typing_and_wait(to, 1.0)
    await send_message(build_text_message(to, text))
    await asyncio.sleep(0.5)
    await send_main_menu(to)

async def send_main_menu(to: str):
    rows = []
    # Ítem APP (abre submenú)
    rows.append({"id": "APP", "title": "App Per Capital", "description": "Registro, suscripción, rescate y posición"})
    # Demás categorías (top-level)
    for k in KNOWLEDGE_BASE.keys():
        if k == "APP":
            continue
        title_short = k if len(k) <= 24 else k[:21] + "..."
        rows.append({"id": _category_to_id(k), "title": title_short, "description": f"Información sobre {k}"})
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
    """Submenú de APP basado en KNOWLEDGE_BASE['APP'] (sin Riesgos/Soporte)"""
    app_node = KNOWLEDGE_BASE.get("APP", {})
    subcats = [k for k, v in app_node.items() if isinstance(v, dict)]
    # Orden deseado
    order = ["GENERAL", "REGISTRO", "SUSCRIPCIÓN", "RESCATE", "POSICIÓN"]
    subcats = [c for c in order if c in app_node] + [c for c in subcats if c not in order]

    rows = []
    for sub in subcats:
        title = sub if len(sub) <= 24 else sub[:21] + "..."
        rows.append({
            "id": f"APP::{_category_to_id(sub)}",
            "title": title,
            "description": f"Preguntas sobre {sub}"
        })
    sections = [{"title": "App Per Capital", "rows": rows}]
    payload = build_interactive_list_message(
        to=to,
        header="App Per Capital",
        body="Elige una sección de la App:",
        sections=sections
    )
    await send_message(payload)
    user_sessions[to] = {"state": "app_submenu", "last_interaction": datetime.now().isoformat()}

def _resolve_app_subcategory(app_sub_id: str) -> Optional[str]:
    """Convierte 'APP::REGISTRO' (normalizado) en el nombre real de la subcategoría."""
    if not app_sub_id or not app_sub_id.startswith("APP::"):
        return None
    subnorm = _normalize_key(app_sub_id.split("APP::", 1)[1])
    app_node = KNOWLEDGE_BASE.get("APP", {})
    for sub in app_node.keys():
        if _normalize_key(sub) == subnorm:
            return sub
    return None

async def send_category_questions(to: str, category_id: str):
    # 1) APP::<SUBCAT>
    if category_id and category_id.startswith("APP::"):
        sub = _resolve_app_subcategory(category_id)
        if not sub:
            await send_message(build_text_message(to, "No encontré esa sección de la App."))
            await send_app_submenu(to)
            return
        category_map = KNOWLEDGE_BASE.get("APP", {}).get(sub, {})
        category_title = f"App • {sub}"
        session_key = f"APP::{sub}"

    # 2) APP_MAIN / APP_GENERAL / APP → abrir submenú
    elif category_id in ("APP_MAIN", "APP_GENERAL", "APP"):
        await send_app_submenu(to)
        return

    else:
        # Categoría normal top-level
        mapped = find_category_key(category_id, allow_fuzzy=True)
        if not mapped:
            mapped = find_category_key(category_id.replace("_", " "), allow_fuzzy=True)
        if not mapped:
            await send_message(build_text_message(to, "Lo siento, no pude encontrar esa categoría."))
            await send_main_menu(to)
            return
        category_map = KNOWLEDGE_BASE.get(mapped, {})
        category_title = mapped
        session_key = mapped

    # Construir preguntas
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
        if category_title.startswith("App •"):
            await send_app_submenu(to)
        else:
            await send_main_menu(to)
        return

    if len(questions_local) <= 3:
        buttons = []
        for q in questions_local[:3]:
            title = q["text"]
            if len(title) > 20:
                title = title[:17] + "..."
            buttons.append({"type": "reply", "reply": {"id": q["id"], "title": title}})
        payload = build_reply_button_message(to=to, body=f"*{category_title}*\n\nSelecciona tu pregunta:", buttons=buttons)
    else:
        rows = []
        for q in questions_local:
            title_short = q["text"] if len(q["text"]) <= 24 else q["text"][:21] + "..."
            # Importante: NO enviar description para evitar duplicado visual
            rows.append({"id": q["id"], "title": title_short})
        sections = [{"title": category_title, "rows": rows}]
        payload = build_interactive_list_message(to=to, header=category_title, body="Selecciona tu pregunta:", sections=sections)

    await send_message(payload)
    user_sessions[to] = {"state": "questions_menu", "category": session_key, "last_interaction": datetime.now().isoformat()}

async def send_answer(to: str, question_id: str):
    qdata = QUESTION_ID_MAP.get(question_id)
    if not qdata:
        norm_in = _normalize_key(question_id or "")
        for _, data in QUESTION_ID_MAP.items():
            if _normalize_key(data.get("text", "")) == norm_in:
                qdata = data
                break

    # Buscar también dentro de APP anidado
    if not qdata:
        for cat_key, qa_map in KNOWLEDGE_BASE.items():
            if isinstance(qa_map, dict):
                if cat_key == "APP":
                    for sub, sub_map in qa_map.items():
                        if isinstance(sub_map, dict):
                            for q_text, q_answer in sub_map.items():
                                if _normalize_key(q_text) == _normalize_key(question_id or ""):
                                    qdata = {"category": f"App • {sub}", "text": q_text, "answer": q_answer}
                                    break
                        if qdata:
                            break
                else:
                    for q_text, q_answer in qa_map.items():
                        if _normalize_key(q_text) == _normalize_key(question_id or ""):
                            qdata = {"category": cat_key, "text": q_text, "answer": q_answer}
                            break
            if qdata:
                break

    # fallback CAT::Qn
    if not qdata and "::Q" in (question_id or ""):
        try:
            cat_part = question_id.split("::Q")[0]
            cat_guess = cat_part.replace("_", " ").strip()
            # intentar resolver App • SUB
            if cat_guess.upper().startswith("APP •"):
                sub = cat_guess.split("•", 1)[1].strip()
                app_map = KNOWLEDGE_BASE.get("APP", {}).get(sub, {})
                qlist = list(app_map.items())
            else:
                mapped = find_category_key(cat_guess, allow_fuzzy=True)
                qlist = list(KNOWLEDGE_BASE.get(mapped, {}).items()) if mapped else []
            idx = int(question_id.split("::Q")[1].split("_")[0]) - 1
            if 0 <= idx < len(qlist):
                q_text, q_answer = qlist[idx]
                qdata = {"category": cat_guess, "text": q_text, "answer": q_answer}
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
        {"type": "reply", "reply": {"id": "RATE_NEEDS_IMPROVEMENT", "title": "Mejorar"}}
    ]
    payload = build_reply_button_message(to=to, body="¡Gracias por usar nuestro asistente virtual! 😊\n\n¿Cómo calificarías la ayuda recibida?", buttons=buttons)
    await send_message(payload)
    user_sessions[to] = {"state": "rating", "last_interaction": datetime.now().isoformat()}

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

def _extract_interactive_candidate(obj: Dict) -> Optional[str]:
    if not obj:
        return None
    for key in ("id", "title", "payload", "name", "value"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            raw = v.strip()
            if raw.startswith("{") and raw.endswith("}"):
                try:
                    parsed = json.loads(raw)
                    for k2 in ("id", "title", "payload", "name", "value"):
                        if parsed.get(k2):
                            return str(parsed.get(k2)).strip()
                except Exception:
                    pass
            raw = raw.replace("%3A%3A", "::")
            for sep in [" - ", " | ", "\n", "Información sobre", "Information about"]:
                if sep in raw:
                    raw = raw.split(sep)[0].strip()
            return raw
    return None

async def handle_feedback(from_number: str, reply_id: str):
    rid = (reply_id or "").strip().lower()
    if rid in ("yes", "sí", "si"):
        # Quiere más ayuda → vuelve al menú
        response = "¡Perfecto! Te llevo al menú principal para seguir ayudándote. 👇"
        await send_message(build_text_message(from_number, response))
        await asyncio.sleep(0.8)
        await send_main_menu(from_number)

    elif rid == "no":
        # No necesita más ayuda → pedir calificación
        response = "¡Entendido! Antes de cerrar, ¿podrías calificar la atención? 😊"
        await send_message(build_text_message(from_number, response))
        await asyncio.sleep(0.3)
        await send_rating_request(from_number)  # ← muestra los botones de calificación

    else:
        response = "No entendí tu respuesta."
        await send_message(build_text_message(from_number, response))
        await asyncio.sleep(0.8)
        await send_main_menu(from_number)

async def handle_rating_buttons(from_number: str, reply_id: str):
    rid = (reply_id or "").strip()
    rid_upper = rid.upper()
    rate_map = {
        "RATE_EXCELLENT": "Excelente",
        "RATE_GOOD": "Bien",
        "RATE_NEEDS_IMPROVEMENT": "Necesita mejorar",
    }
    if rid_upper in rate_map:
        rating = rate_map[rid_upper]
        user_ratings.append({"user": from_number, "rating": rating, "timestamp": datetime.now().isoformat()})
        msg = f"¡Gracias por tu calificación: *{rating}*! 🙏"
    elif rid.lower().startswith("rating_"):
        value = rid.lower().replace("rating_", "")
        msg = f"¡Gracias por calificarnos con {value} ⭐!"
        user_ratings.append({"user": from_number, "rating": f"{value} estrellas", "timestamp": datetime.now().isoformat()})
    else:
        msg = "Gracias por tu valoración."
    await send_message(build_text_message(from_number, msg))
    await asyncio.sleep(1.0)
    await send_main_menu(from_number)

async def process_interactive_message(from_number: str, interactive_data: Dict):
    try:
        interactive_type = interactive_data.get("type")
        logger.info(f"Interactive type: {interactive_type} from {from_number} | payload={interactive_data}")

        reply_id, reply_title = None, None
        if interactive_type == "list_reply":
            obj = interactive_data.get("list_reply", {}) or {}
            reply_id = obj.get("id") or _extract_interactive_candidate(obj)
            reply_title = obj.get("title")
        elif interactive_type == "button_reply":
            obj = interactive_data.get("button_reply", {}) or {}
            reply_id = obj.get("id") or _extract_interactive_candidate(obj)
            reply_title = obj.get("title")
        else:
            logger.warning(f"Unknown interactive type: {interactive_type}")
            await send_main_menu(from_number)
            return

        logger.info(f"Interactive reply from {from_number}: id={reply_id} title={reply_title}")
        if not reply_id:
            logger.warning("Interactive reply without id")
            await send_main_menu(from_number)
            return

        rid = reply_id.strip()
        rid_upper = rid.upper()

        # 0) Feedback / ratings
        if rid_upper in ("YES", "NO", "SÍ", "SI"):
            await handle_feedback(from_number, rid)
            return
        if rid_upper.startswith("RATE_") or rid.lower().startswith("rating_"):
            await handle_rating_buttons(from_number, rid)
            return

        # 1) PRIORIDAD: subcategorías de APP (evita que el fuzzy las convierta en 'APP')
        if rid_upper.startswith("APP::"):
            await send_category_questions(from_number, rid_upper)
            return

        # 2) Categorías normales (incluye 'APP' que abre el submenú)
        mapped_cat = _is_category_id(rid)
        if mapped_cat:
            if mapped_cat in ("APP_MAIN", "APP_GENERAL", "APP"):
                await send_app_submenu(from_number)
            else:
                await send_category_questions(from_number, mapped_cat)
            return

        # 3) Si el cliente mandó el 'title' en vez del 'id'
        if reply_title:
            title_up = reply_title.upper().strip()
            if title_up.startswith("APP::"):
                await send_category_questions(from_number, title_up)
                return
            mapped_cat = _is_category_id(reply_title)
            if mapped_cat:
                if mapped_cat in ("APP_MAIN", "APP_GENERAL", "APP"):
                    await send_app_submenu(from_number)
                else:
                    await send_category_questions(from_number, mapped_cat)
                return

        # 4) ¿Seleccionó una pregunta directamente?
        if _is_question_id(rid):
            await send_answer(from_number, rid)
            return

        logger.info(f"Unknown interactive reply id: {rid}")
        await send_main_menu(from_number)

    except Exception as e:
        logger.error(f"Error in process_interactive_message: {e}")
        await send_main_menu(from_number)

# -------------------- Webhook signature --------------------
def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    if not APP_SECRET:
        logger.warning("APP_SECRET not set; skipping signature verification")
        return True
    if not signature:
        logger.error("No signature header provided")
        return False
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
        return PlainTextResponse(content=hub_challenge or "")
    logger.error("Webhook verification failed")
    raise HTTPException(status_code=403, detail="Forbidden")

@app.post("/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not verify_webhook_signature(body, signature):
            logger.error("Invalid signature")
            raise HTTPException(status_code=403, detail="Invalid signature")
        data = json.loads(body.decode())
        logger.debug("Webhook payload: %s", data)
        if data.get("object") == "whatsapp_business_account":
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    if "messages" in value:
                        for message in value["messages"]:
                            try:
                                background_tasks.add_task(process_message, message)
                            except Exception:
                                asyncio.create_task(process_message(message))
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
    def count_questions(node: Any) -> int:
        if isinstance(node, dict):
            total = 0
            for v in node.values():
                if isinstance(v, dict):
                    total += count_questions(v)
                else:
                    total += 1
            return total
        return 0
    total_questions = count_questions(KNOWLEDGE_BASE)
    return {
        "status": "healthy",
        "service": "Per Capital WhatsApp Chatbot",
        "version": "1.1.1",
        "active_sessions": len(user_sessions),
        "total_ratings": len(user_ratings),
        "total_questions": total_questions
    }

@app.get("/stats")
async def get_stats():
    rating_counts = {}
    for r in user_ratings:
        rating_counts[r["rating"]] = rating_counts.get(r["rating"], 0) + 1

    def count_questions(node: Any) -> int:
        if isinstance(node, dict):
            total = 0
            for v in node.values():
                if isinstance(v, dict):
                    total += count_questions(v)
                else:
                    total += 1
            return total
        return 0

    return {
        "active_sessions": len(user_sessions),
        "total_ratings": len(user_ratings),
        "rating_breakdown": rating_counts,
        "knowledge_base_categories": len(KNOWLEDGE_BASE),
        "total_questions": count_questions(KNOWLEDGE_BASE)
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
    logger.info("Bot startup complete. KB categories=%d", len(KNOWLEDGE_BASE))

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
