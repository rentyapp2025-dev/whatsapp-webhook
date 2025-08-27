import os
import hmac
import hashlib
import json
import re
import asyncio
from typing import Optional, Any, Dict, List
import logging

from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.responses import PlainTextResponse
import httpx

# Configurar el logging para ver mensajes detallados
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ==================== Configuración / Entorno ====================
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "").encode("utf-8") if os.getenv("APP_SECRET") else b""
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

if not all([VERIFY_TOKEN, WHATSAPP_TOKEN, PHONE_NUMBER_ID]):
    logging.warning("Faltan variables de entorno. Asegúrate de configurar VERIFY_TOKEN, WHATSAPP_TOKEN y PHONE_NUMBER_ID.")

app = FastAPI(title="WhatsApp Cloud API Webhook (Per Capital)")

# ==================== Base de Conocimiento (estructura con IDs) ====================
# Cada categoría tiene una lista de preguntas; cada pregunta es dict con id,q,a
QA_CATEGORIZED = [
    {
        "id": "cat_1",
        "title": "Per Capital",
        "questions": [
            {"id": "q_pc_1", "q": "¿Qué es Per Capital?", "a": "Es un grupo de empresas del Mercado de Valores Venezolano reguladas por la SUNAVAL."},
            {"id": "q_pc_2", "q": "¿Quién regula a Per Capital?", "a": "La SUNAVAL (Superintendencia Nacional de Valores)."},
            {"id": "q_pc_3", "q": "¿Qué es la SUNAVAL?", "a": "Es quien protege a inversionistas y regula a intermediarios y emisores del Mercado de Valores venezolano."},
            {"id": "q_pc_4", "q": "¿Qué es la Bolsa de Valores de Caracas?", "a": "Es el lugar donde se compran y venden bonos, acciones y otros instrumentos de manera ordenada a través de las Casas de Bolsa y está regulada por la SUNAVAL."},
            {"id": "q_pc_5", "q": "¿Cómo invierto?", "a": "Para invertir en el Fondo Mutual Abierto de Per Capital debes descargar la app, registrarte, subir recaudos y colocar tus órdenes de compra."},
        ],
    },
    {
        "id": "cat_2",
        "title": "Fondo Mutual Abierto",
        "questions": [
            {"id": "q_fm_1", "q": "¿Qué es un Fondo Mutual?", "a": "Es un instrumento de inversión en grupo gestionado por expertos, diseñado para diversificar y dirigido a pequeños inversionistas."},
            {"id": "q_fm_2", "q": "¿Qué es una Unidad de Inversión?", "a": "Es una porción del fondo. Cuando inviertes adquieres unidades que representan tu parte del fondo."},
            {"id": "q_fm_3", "q": "¿Qué es el VUI?", "a": "El Valor de la Unidad de Inversión (VUI) es el precio de una Unidad de Inversión. Se calcula diariamente al cierre del día."},
            {"id": "q_fm_4", "q": "¿Cuál es el monto mínimo de inversión?", "a": "1 Unidad de Inversión."},
            {"id": "q_fm_5", "q": "¿Cómo gano?", "a": "Ganas por apreciación (subida del VUI) o por dividendos en caso de ser decretados."},
        ],
    },
    {
        "id": "cat_3",
        "title": "App - Registro",
        "questions": [
            {"id": "q_app_reg_1", "q": "¿Cómo me registro?", "a": "Descarga la app, completa el 100% de los datos, acepta los contratos, sube recaudos (cédula y selfie) y espera aprobación."},
            {"id": "q_app_reg_2", "q": "¿Cuánto tarda mi aprobación?", "a": "De 2 a 5 días hábiles, siempre que hayas completado el 100% del registro y los recaudos."},
            {"id": "q_app_reg_3", "q": "¿Qué hago si no me aprueban?", "a": "Revisa que hayas completado el 100% del registro y los recaudos. Si persiste, contacta al soporte."},
        ],
    },
    {
        "id": "cat_4",
        "title": "App - Suscripción y Rescate",
        "questions": [
            {"id": "q_app_sub_1", "q": "¿Cómo suscribo (compro)?", "a": "Negociación > Suscripción > Monto > Suscribir > Método de Pago. Sube comprobante si aplica."},
            {"id": "q_app_sub_2", "q": "¿Cómo pago mi suscripción?", "a": "Paga desde tu cuenta bancaria vía Pago Móvil y sube el comprobante. No se aceptan pagos de terceros."},
            {"id": "q_app_res_1", "q": "¿Cómo rescato (vendo)?", "a": "Negociación > Rescate > Unidades a rescatar > Rescatar. Los fondos se envían a tu cuenta bancaria."},
            {"id": "q_app_res_2", "q": "¿Cuándo me pagan mis rescates?", "a": "Al próximo día hábil bancario en horario de mercado."},
            {"id": "q_app_pos_1", "q": "¿Cómo veo el saldo de mi inversión?", "a": "En el Home, sección 'Mi Cuenta' y en 'Historial' para histórico."},
        ],
    },
    {
        "id": "cat_5",
        "title": "Riesgos",
        "questions": [
            {"id": "q_risk_1", "q": "¿Cuáles son los riesgos al invertir?", "a": "Todas las inversiones están sujetas a riesgos; la pérdida de capital es posible. Riesgos comunes: de mercado, país, cambiario, sector."},
        ],
    },
    {
        "id": "cat_6",
        "title": "Soporte",
        "questions": [
            {"id": "q_sup_1", "q": "Estoy en revisión, ¿qué hago?", "a": "Asegúrate de haber completado el 100% de los datos y recaudos; contacta soporte si tarda más de lo habitual."},
            {"id": "q_sup_2", "q": "No me llega el SMS", "a": "Revisa señal y que el número sea venezolano. Si persiste, intenta con otro número."},
            {"id": "q_sup_3", "q": "No me llega el correo", "a": "Revisa que no haya espacios al final en tu correo al registrarlo."},
            {"id": "q_sup_4", "q": "¿Cómo recupero mi clave?", "a": "Selecciona 'Recuperar', te llegará una clave temporal; ingrésala y luego configura una nueva clave."},
        ],
    },
]

# Estado de conversaciones (en memoria). En producción usar Redis u otra persistencia.
conversation_state: Dict[str, Dict[str, Any]] = {}

# ==================== Utilidades: Búsqueda en la base de conocimiento ====================
def find_category_by_index(index: int) -> Optional[Dict[str, Any]]:
    if 1 <= index <= len(QA_CATEGORIZED):
        return QA_CATEGORIZED[index - 1]
    return None

def find_question_by_ids(cat_idx: int, q_idx: int) -> Optional[Dict[str, Any]]:
    cat = find_category_by_index(cat_idx)
    if not cat:
        return None
    if 1 <= q_idx <= len(cat["questions"]):
        return cat["questions"][q_idx - 1]
    return None

def find_question_by_uid(uid: str) -> Optional[Dict[str, Any]]:
    for cat_index, cat in enumerate(QA_CATEGORIZED, start=1):
        for q_index, q in enumerate(cat["questions"], start=1):
            if q["id"] == uid:
                return {"category_index": cat_index, "question_index": q_index, "question": q}
    return None

# ==================== Helpers para construir payloads (modularidad) ====================
def build_text_payload(to_msisdn: str, text: str) -> Dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "text",
        "text": {"body": text}
    }

def build_reply_buttons_payload(to_msisdn: str, header_text: str, body_text: str, buttons: List[Dict[str, str]], footer_text: Optional[str] = None) -> Dict[str, Any]:
    # buttons: list of {"id": "...", "title": "..."}
    action_buttons = [{"type": "reply", "reply": {"id": b["id"], "title": b["title"]}} for b in buttons]
    interactive = {
        "type": "button",
        "header": {"type": "text", "text": header_text},
        "body": {"text": body_text},
        "action": {"buttons": action_buttons}
    }
    if footer_text:
        interactive["footer"] = {"text": footer_text}
    return {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": interactive
    }

def build_list_payload(to_msisdn: str, header_text: str, body_text: str, section_title: str, rows: List[Dict[str, str]], footer_text: Optional[str] = None) -> Dict[str, Any]:
    # rows: list of {"id": "...", "title": "...", "description": "..."}
    interactive = {
        "type": "list",
        "header": {"type": "text", "text": header_text},
        "body": {"text": body_text},
        "action": {
            "button": "Seleccionar",
            "sections": [
                {
                    "title": section_title,
                    "rows": [{"id": r["id"], "title": r["title"], "description": r.get("description", "")} for r in rows]
                }
            ]
        }
    }
    if footer_text:
        interactive["footer"] = {"text": footer_text}
    return {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": interactive
    }

# ==================== Envío de mensajes (HTTP) ====================
async def _post_messages(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            logging.info(f"✅ Mensaje enviado a {payload.get('to')}, tipo={payload.get('type')}")
            return response.json()
    except httpx.HTTPStatusError as e:
        logging.error(f"❌ Error HTTP al enviar mensaje. Status: {e.response.status_code} - {e.response.text}")
        raise HTTPException(status_code=500, detail=f"Error sending message: {e.response.status_code}")
    except Exception as e:
        logging.error(f"❌ Error inesperado al enviar mensaje: {e}")
        raise HTTPException(status_code=500, detail="Unexpected error sending message")

async def send_text(to_msisdn: str, text: str) -> Dict[str, Any]:
    payload = build_text_payload(to_msisdn, text)
    return await _post_messages(payload)

async def send_reply_buttons(to_msisdn: str, header_text: str, body_text: str, buttons: List[Dict[str, str]], footer_text: Optional[str] = None) -> Dict[str, Any]:
    payload = build_reply_buttons_payload(to_msisdn, header_text, body_text, buttons, footer_text)
    return await _post_messages(payload)

async def send_list(to_msisdn: str, header_text: str, body_text: str, section_title: str, rows: List[Dict[str, str]], footer_text: Optional[str] = None) -> Dict[str, Any]:
    payload = build_list_payload(to_msisdn, header_text, body_text, section_title, rows, footer_text)
    return await _post_messages(payload)

# ==================== Lógica del flujo conversacional ====================
def is_back_command(text: str) -> bool:
    back_keywords = ["volver", "menu", "menú", "principal", "inicio", "back", "0"]
    return text.strip().lower() in back_keywords

def is_greeting(text: str) -> bool:
    greetings = ["hola", "buenos días", "buenos dias", "buenas", "saludos", "buenas tardes", "buenas noches", "hi", "hey"]
    return any(text.strip().lower().startswith(g) for g in greetings)

async def send_welcome_sequence(to_msisdn: str) -> None:
    # Primer mensaje - bienvenida
    welcome = "🏦 ¡Bienvenido a Per Capital!\n\nSoy tu asistente virtual. Puedo ayudarte con información sobre inversiones, la app y soporte."
    await send_text(to_msisdn, welcome)
    # Simular "escribiendo" con pausa asincrónica entre 1 y 2 segundos
    await asyncio.sleep(1.5)
    # Segundo mensaje - menú inicial con botones
    await send_initial_menu_with_buttons(to_msisdn)

async def send_initial_menu_with_buttons(to_msisdn: str) -> Dict[str, Any]:
    header = "Per Capital - ¿Cómo te ayudo?"
    body = "Elige una opción para comenzar:"
    buttons = [
        {"id": "bot_qa", "title": "🤖 Asistente Virtual"},
        {"id": "human_support", "title": "👨‍💼 Soporte Humano"}
    ]
    return await send_reply_buttons(to_msisdn, header, body, buttons, footer_text="Selecciona una opción")

async def send_main_menu(to_msisdn: str) -> None:
    # Construye un mensaje con índices para que el usuario envíe solo el número
    menu_text = "📋 Menú Principal - Per Capital\n\n"
    for i, cat in enumerate(QA_CATEGORIZED, start=1):
        menu_text += f"{i}. {cat['title']}\n"
    menu_text += "\nEnvía solo el número de la categoría (ej. '1'). Escribe 'volver' para regresar."
    # Limpiar estado de conversación (dejamos solo lo necesario)
    if to_msisdn in conversation_state:
        conversation_state.pop(to_msisdn, None)
        logging.info(f"Estado limpiado para {to_msisdn}")
    await send_text(to_msisdn, menu_text)

async def send_subcategory_menu(to_msisdn: str, category_index: int) -> None:
    cat = find_category_by_index(category_index)
    if not cat:
        await send_text(to_msisdn, "❌ Categoría no válida. Por favor, envía un número de categoría válido.")
        await send_main_menu(to_msisdn)
        return

    questions = cat["questions"]
    # Guardar estado: categoría actual
    conversation_state[to_msisdn] = {"category_index": category_index}
    logging.info(f"Estado guardado para {to_msisdn}: categoría {category_index}")

    # Dependiendo del número de preguntas, enviar lista o botones
    if len(questions) >= 4:
        # Interactive List Message
        rows = []
        for q in questions:
            rows.append({"id": f"qa:{cat['id']}:{q['id']}", "title": q["q"], "description": ""})
        header = f"{cat['title']}"
        body = "Selecciona la pregunta que te interesa:"
        section_title = "Preguntas"
        await send_list(to_msisdn, header, body, section_title, rows, footer_text="Selecciona una opción")
    else:
        # Reply Buttons (max 3)
        buttons = []
        for q in questions[:3]:
            buttons.append({"id": f"qa:{cat['id']}:{q['id']}", "title": re.sub(r'^\d+\.\s*', '', q["q"])})
        header = f"{cat['title']}"
        body = "Selecciona una pregunta:"
        await send_reply_buttons(to_msisdn, header, body, buttons, footer_text="Selecciona una opción")

async def ask_follow_up_more_help(to_msisdn: str) -> None:
    header = "¿Te fue útil la respuesta?"
    body = "¿Necesitas más ayuda?"
    buttons = [
        {"id": "more_yes", "title": "Sí, por favor"},
        {"id": "more_no", "title": "No, gracias"}
    ]
    await asyncio.sleep(0.8)
    await send_reply_buttons(to_msisdn, header, body, buttons)

async def ask_for_rating(to_msisdn: str) -> None:
    header = "Califica nuestro servicio"
    body = "¿Cómo calificarías la ayuda recibida?"
    buttons = [
        {"id": "rating_5", "title": "Excelente"},
        {"id": "rating_3", "title": "Bien"},
        {"id": "rating_1", "title": "Necesita mejorar"},
    ]
    await asyncio.sleep(0.6)
    await send_reply_buttons(to_msisdn, header, body, buttons)

# ==================== Verificación de firma ====================
def verify_signature(signature: Optional[str], body: bytes) -> bool:
    if not APP_SECRET:
        logging.warning("APP_SECRET no configurado. Saltando verificación de firma.")
        return True
    if not signature or not signature.startswith("sha256="):
        logging.error("Firma ausente o malformada.")
        return False
    their_sig = signature.split("sha256=")[-1].strip()
    mac = hmac.new(APP_SECRET, msg=body, digestmod=hashlib.sha256)
    our_sig = mac.hexdigest()
    is_valid = hmac.compare_digest(our_sig, their_sig)
    if not is_valid:
        logging.error("La firma no coincide.")
    return is_valid

# ==================== Procesamiento de mensajes ====================
async def process_text_message(from_msisdn: str, message_text: str) -> None:
    text_clean = message_text.strip()
    logging.info(f"Procesando texto de {from_msisdn}: '{text_clean}'")

    if is_back_command(text_clean):
        logging.info(f"{from_msisdn} solicitó volver al menú principal")
        await send_main_menu(from_msisdn)
        return

    if is_greeting(text_clean):
        logging.info(f"{from_msisdn} saludo detectado")
        await send_welcome_sequence(from_msisdn)
        return

    # Intentar interpretar como número de menú
    try:
        choice = int(text_clean)
        current_state = conversation_state.get(from_msisdn)
        if not current_state:
            # Selección de categoría
            logging.info(f"{from_msisdn} seleccionó categoría {choice}")
            if 1 <= choice <= len(QA_CATEGORIZED):
                await send_subcategory_menu(from_msisdn, choice)
            else:
                await send_text(from_msisdn, f"❌ Opción no válida. Elige un número entre 1 y {len(QA_CATEGORIZED)}.")
                await send_main_menu(from_msisdn)
        else:
            # Selección de pregunta por número dentro de la categoría
            cat_idx = current_state.get("category_index")
            logging.info(f"{from_msisdn} está en categoría {cat_idx} y seleccionó pregunta {choice}")
            q = find_question_by_ids(cat_idx, choice)
            if q:
                await send_text(from_msisdn, f"✅ *Respuesta:*\n\n{q['a']}")
                # Después de responder preguntar si necesita más ayuda
                await ask_follow_up_more_help(from_msisdn)
            else:
                await send_text(from_msisdn, "❌ Pregunta no válida. Por favor, envía el número de la pregunta.")
                await send_subcategory_menu(from_msisdn, cat_idx)
    except ValueError:
        # No es número. En menú principal enviaremos el menú inicial con botones
        logging.info(f"Entrada no numérica de {from_msisdn}")
        await send_initial_menu_with_buttons(from_msisdn)

async def process_interactive_message(from_msisdn: str, interactive_data: Dict[str, Any]) -> None:
    itype = interactive_data.get("type")
    logging.info(f"Procesando interactivo de {from_msisdn}: type={itype}")

    if itype == "button_reply":
        btn = interactive_data.get("button_reply", {})
        btn_id = btn.get("id")
        btn_title = btn.get("title")
        logging.info(f"Button reply id={btn_id} title={btn_title}")

        # Manejo de botones globales
        if btn_id == "bot_qa":
            await send_text(from_msisdn, "🤖 Has seleccionado Asistente Virtual. Te muestro las categorías:")
            await send_main_menu(from_msisdn)
            return
        if btn_id == "human_support":
            await send_text(from_msisdn,
                "👨‍💼 Soporte Humano activado.\n\nUn agente se pondrá en contacto contigo. Si es urgente, llama a nuestros números de soporte.")
            # limpiar estado y terminar
            conversation_state.pop(from_msisdn, None)
            return
        if btn_id == "more_yes":
            await send_text(from_msisdn, "Perfecto, muéstrame otra consulta:")
            await send_main_menu(from_msisdn)
            return
        if btn_id == "more_no":
            await send_text(from_msisdn, "Gracias por confirmar. Te pedimos por favor calificar nuestro servicio:")
            await ask_for_rating(from_msisdn)
            return
        if btn_id and btn_id.startswith("rating_"):
            rating = btn_id.split("_", 1)[1]
            await send_text(from_msisdn, f"Gracias por tu calificación ({btn_title}). ¡Tu opinión nos ayuda a mejorar!")
            # limpiar estado y terminar
            conversation_state.pop(from_msisdn, None)
            return

        # Si el botón contiene un QA payload (id esquema qa:catid:qid) tratamos de localizar la pregunta
        if btn_id and btn_id.startswith("qa:"):
            # formato esperado: qa:{cat_id}:{q_id}
            parts = btn_id.split(":")
            if len(parts) >= 3:
                q_uid = parts[2]
                found = find_question_by_uid(q_uid)
                if found:
                    q_obj = found["question"]
                    await send_text(from_msisdn, f"✅ *Respuesta:*\n\n{q_obj['a']}")
                    await ask_follow_up_more_help(from_msisdn)
                    return

        logging.warning(f"ID de botón desconocido: {btn_id}")
        await send_initial_menu_with_buttons(from_msisdn)
        return

    if itype == "list_reply":
        lr = interactive_data.get("list_reply", {})
        lr_id = lr.get("id")
        lr_title = lr.get("title")
        logging.info(f"List reply id={lr_id} title={lr_title}")
        # Esperamos id con formato qa:{cat_id}:{q_id}
        if lr_id and lr_id.startswith("qa:"):
            parts = lr_id.split(":")
            if len(parts) >= 3:
                q_uid = parts[2]
                found = find_question_by_uid(q_uid)
                if found:
                    q_obj = found["question"]
                    await send_text(from_msisdn, f"✅ *Respuesta:*\n\n{q_obj['a']}")
                    await ask_follow_up_more_help(from_msisdn)
                    return
        logging.warning(f"Payload de lista desconocido o mal formado: {lr_id}")
        await send_initial_menu_with_buttons(from_msisdn)
        return

    logging.info("Tipo de interactivo no manejado, enviando menú inicial.")
    await send_initial_menu_with_buttons(from_msisdn)

# ==================== Endpoints FastAPI ====================
@app.get("/webhook")
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
):
    logging.info(f"Verificando webhook - mode={hub_mode}")
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logging.info("Verificación exitosa")
        return PlainTextResponse(content=hub_challenge or "", status_code=200)
    logging.error("Verificación fallida")
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhook")
async def receive_webhook(request: Request):
    try:
        body_bytes = await request.body()
        signature = request.headers.get("X-Hub-Signature-256")
        if not verify_signature(signature, body_bytes):
            logging.error("Firma inválida")
            raise HTTPException(status_code=403, detail="Invalid signature")
        data = await request.json()
        logging.info(f"Webhook recibido: {json.dumps(data)}")

        if data.get("object") != "whatsapp_business_account":
            logging.info("Notificación ignorada (no es whatsapp_business_account)")
            return Response(status_code=200)

        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages")
                if not messages:
                    continue
                for message in messages:
                    from_msisdn = message.get("from")
                    message_type = message.get("type")
                    message_id = message.get("id")
                    logging.info(f"Procesando mensaje {message_id} de {from_msisdn} tipo={message_type}")

                    if message_type == "interactive":
                        await process_interactive_message(from_msisdn, message.get("interactive", {}))
                    elif message_type == "text":
                        await process_text_message(from_msisdn, message.get("text", {}).get("body", ""))
                    else:
                        # Otros tipos: enviar menú inicial
                        await send_initial_menu_with_buttons(from_msisdn)

        return Response(status_code=200)
    except json.JSONDecodeError:
        logging.error("JSON inválido en webhook")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("Error procesando webhook")
        return Response(status_code=500, content="Internal Server Error")

@app.get("/")
async def health_check():
    return {
        "status": "ok",
        "service": "WhatsApp Bot Per Capital",
        "version": "3.0",
        "categories": len(QA_CATEGORIZED),
        "active_conversations": len(conversation_state)
    }

@app.get("/status")
async def status_endpoint():
    return {
        "service_status": "running",
        "environment_variables": {
            "VERIFY_TOKEN": "✅" if VERIFY_TOKEN else "❌",
            "WHATSAPP_TOKEN": "✅" if WHATSAPP_TOKEN else "❌",
            "PHONE_NUMBER_ID": "✅" if PHONE_NUMBER_ID else "❌",
            "APP_SECRET": "✅" if APP_SECRET else "❌"
        },
        "qa_categories": [c["title"] for c in QA_CATEGORIZED],
        "active_conversations": len(conversation_state),
        "graph_api_version": GRAPH_API_VERSION
    }

@app.get("/clear-conversations")
async def clear_conversations():
    count = len(conversation_state)
    conversation_state.clear()
    logging.info(f"Conversaciones limpiadas: {count}")
    return {"status": "success", "cleared": count}

# Manejo global de excepciones
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logging.exception("Excepción global no manejada")
    return Response(status_code=500, content=json.dumps({"error": "Internal server error"}), media_type="application/json")

if __name__ == "__main__":
    print("🚀 Iniciando WhatsApp Bot Per Capital (FastAPI)...")
    print(f"📚 Categorías cargadas: {len(QA_CATEGORIZED)}")
    for c in QA_CATEGORIZED:
        print(f"  • {c['title']}: {len(c['questions'])} preguntas")
    print("✅ Listo.")