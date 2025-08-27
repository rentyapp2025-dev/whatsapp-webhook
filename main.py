import os
import hmac
import hashlib
import json
import asyncio
from typing import Optional, Any, Dict, List, Tuple
import logging

from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.responses import PlainTextResponse
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ========== Config / Env ==========
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "").encode("utf-8") if os.getenv("APP_SECRET") else b""
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

if not all([VERIFY_TOKEN, WHATSAPP_TOKEN, PHONE_NUMBER_ID]):
    logging.warning("Faltan variables: VERIFY_TOKEN, WHATSAPP_TOKEN o PHONE_NUMBER_ID")

app = FastAPI(title="WhatsApp Bot Per Capital - Interactive Lists")

# ========== Base de Conocimiento (exactamente como pediste) ==========
QA_CATEGORIZED: Dict[str, Dict[str, str]] = {
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

# Conversational state in-memory (per phone number). In prod use Redis.
conversation_state: Dict[str, Dict[str, Any]] = {}

# Helper: ordered categories list (to keep predictable indices)
def categories_list() -> List[Tuple[str, Dict[str, str]]]:
    return list(QA_CATEGORIZED.items())

# ========== Finders ==========
def find_question_by_uid(uid: str) -> Optional[Tuple[int, int, str, str]]:
    # uid format: qa:{cat_idx}:{q_idx}
    try:
        parts = uid.split(":")
        if parts[0] != "qa":
            return None
        cat_idx = int(parts[1])
        q_idx = int(parts[2])
        cats = categories_list()
        if 1 <= cat_idx <= len(cats):
            cat_title, qdict = cats[cat_idx - 1]
            questions = list(qdict.items())
            if 1 <= q_idx <= len(questions):
                q_text, a_text = questions[q_idx - 1]
                return cat_idx, q_idx, q_text, a_text
    except Exception:
        return None
    return None

# ========== Payload builders ==========
def build_text_payload(to_msisdn: str, text: str) -> Dict[str, Any]:
    return {"messaging_product": "whatsapp", "to": to_msisdn, "type": "text", "text": {"body": text}}

def build_reply_buttons_payload(to_msisdn: str, header_text: str, body_text: str, buttons: List[Dict[str, str]], footer_text: Optional[str] = None) -> Dict[str, Any]:
    action_buttons = [{"type": "reply", "reply": {"id": b["id"], "title": b["title"]}} for b in buttons]
    interactive = {"type": "button", "header": {"type": "text", "text": header_text}, "body": {"text": body_text}, "action": {"buttons": action_buttons}}
    if footer_text:
        interactive["footer"] = {"text": footer_text}
    return {"messaging_product": "whatsapp", "to": to_msisdn, "type": "interactive", "interactive": interactive}

def build_list_payload(to_msisdn: str, header_text: str, body_text: str, section_title: str, rows: List[Dict[str, str]], footer_text: Optional[str] = None) -> Dict[str, Any]:
    interactive = {
        "type": "list",
        "header": {"type": "text", "text": header_text},
        "body": {"text": body_text},
        "action": {"button": "Seleccionar", "sections": [{"title": section_title, "rows": [{"id": r["id"], "title": r["title"], "description": r.get("description", "")} for r in rows]}]}
    }
    if footer_text:
        interactive["footer"] = {"text": footer_text}
    return {"messaging_product": "whatsapp", "to": to_msisdn, "type": "interactive", "interactive": interactive}

# ========== HTTP send ==========
async def _post_messages(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            logging.info("Mensaje enviado: tipo=%s to=%s", payload.get("type"), payload.get("to"))
            return resp.json()
    except httpx.HTTPStatusError as e:
        logging.error("HTTP error sending message: %s - %s", e.response.status_code, e.response.text)
        raise HTTPException(status_code=500, detail="Error sending message")
    except Exception as e:
        logging.exception("Unexpected error sending message")
        raise HTTPException(status_code=500, detail="Unexpected error sending message")

async def send_text(to_msisdn: str, text: str) -> Dict[str, Any]:
    return await _post_messages(build_text_payload(to_msisdn, text))

async def send_reply_buttons(to_msisdn: str, header: str, body: str, buttons: List[Dict[str, str]], footer: Optional[str] = None) -> Dict[str, Any]:
    return await _post_messages(build_reply_buttons_payload(to_msisdn, header, body, buttons, footer))

async def send_list(to_msisdn: str, header: str, body: str, section_title: str, rows: List[Dict[str, str]], footer: Optional[str] = None) -> Dict[str, Any]:
    return await _post_messages(build_list_payload(to_msisdn, header, body, section_title, rows, footer))

# ========== Conversation flow helpers ==========
def is_back_command(text: str) -> bool:
    return text.strip().lower() in {"volver", "menu", "menú", "principal", "inicio", "back", "0"}

def is_greeting(text: str) -> bool:
    t = text.strip().lower()
    return any(t.startswith(g) for g in ("hola", "buenos", "buenas", "saludos", "hi", "hey"))

async def send_welcome_sequence(to_msisdn: str) -> None:
    await send_text(to_msisdn, "🏦 ¡Bienvenido a Per Capital!\n\nSoy tu asistente virtual. Puedo ayudarte con información sobre inversiones, la app y soporte.")
    await asyncio.sleep(1.5)
    await send_initial_menu_buttons(to_msisdn)

async def send_initial_menu_buttons(to_msisdn: str) -> None:
    await send_reply_buttons(
        to_msisdn,
        "Per Capital - ¿Cómo te ayudo?",
        "Elige una opción para comenzar:",
        [{"id": "bot_qa", "title": "🤖 Asistente Virtual"}, {"id": "human_support", "title": "👨‍💼 Soporte Humano"}],
        footer_text="Selecciona una opción"
    )

async def send_main_menu_text(to_msisdn: str) -> None:
    cats = categories_list()
    text = "📋 Menú Principal - Per Capital\n\n"
    for i, (title, _) in enumerate(cats, start=1):
        text += f"{i}. {title}\n"
    text += "\nEnvía solo el número de la categoría (ej. '1') o escribe 'volver'."
    conversation_state.pop(to_msisdn, None)
    await send_text(to_msisdn, text)

async def send_questions_menu(to_msisdn: str, cat_idx: int) -> None:
    cats = categories_list()
    if not (1 <= cat_idx <= len(cats)):
        await send_text(to_msisdn, "❌ Categoría no válida.")
        await send_main_menu_text(to_msisdn)
        return
    title, qdict = cats[cat_idx - 1]
    questions = list(qdict.items())  # list of (q_text, a_text)
    # save state
    conversation_state[to_msisdn] = {"state": "awaiting_question", "category_index": cat_idx}
    # Decide list vs buttons
    if len(questions) >= 4:
        rows = []
        for q_idx, (q_text, _) in enumerate(questions, start=1):
            rows.append({"id": f"qa:{cat_idx}:{q_idx}", "title": q_text, "description": ""})
        await send_list(to_msisdn, title, "Selecciona la pregunta que te interesa:", "Preguntas", rows, footer_text="Selecciona una opción")
    else:
        buttons = []
        for q_idx, (q_text, _) in enumerate(questions, start=1):
            buttons.append({"id": f"qa:{cat_idx}:{q_idx}", "title": q_text})
        await send_reply_buttons(to_msisdn, title, "Selecciona una pregunta:", buttons, footer_text="Selecciona una opción")

async def ask_follow_up_more_help(to_msisdn: str) -> None:
    await asyncio.sleep(0.8)
    await send_reply_buttons(to_msisdn, "¿Te fue útil la respuesta?", "¿Necesitas más ayuda?", [{"id": "more_yes", "title": "Sí, por favor"}, {"id": "more_no", "title": "No, gracias"}])

async def ask_for_rating(to_msisdn: str) -> None:
    await asyncio.sleep(0.6)
    await send_reply_buttons(to_msisdn, "Califica nuestro servicio", "¿Cómo calificarías la ayuda recibida?", [{"id": "rating_5", "title": "Excelente"}, {"id": "rating_3", "title": "Bien"}, {"id": "rating_1", "title": "Necesita mejorar"}])

# ========== Signature verification ==========
def verify_signature(signature: Optional[str], body: bytes) -> bool:
    if not APP_SECRET:
        logging.warning("APP_SECRET no configurado - saltando verificación")
        return True
    if not signature or not signature.startswith("sha256="):
        logging.error("Firma ausente o malformada")
        return False
    their = signature.split("sha256=")[-1].strip()
    mac = hmac.new(APP_SECRET, msg=body, digestmod=hashlib.sha256)
    ours = mac.hexdigest()
    valid = hmac.compare_digest(ours, their)
    if not valid:
        logging.error("Firma no coincide")
    return valid

# ========== Message processing ==========
async def process_text_message(from_msisdn: str, message_text: str) -> None:
    text = message_text.strip()
    logging.info("Texto recibido de %s: %s", from_msisdn, text)
    if is_back_command(text):
        await send_main_menu_text(from_msisdn)
        return
    if is_greeting(text):
        await send_welcome_sequence(from_msisdn)
        return
    # try interpret as number
    try:
        choice = int(text)
        state = conversation_state.get(from_msisdn)
        if not state:
            # select category
            cats = categories_list()
            if 1 <= choice <= len(cats):
                await send_questions_menu(from_msisdn, choice)
            else:
                await send_text(from_msisdn, f"❌ Opción no válida. Elige un número entre 1 y {len(cats)}.")
                await send_main_menu_text(from_msisdn)
        else:
            if state.get("state") == "awaiting_question":
                cat_idx = state.get("category_index")
                # find question by indices
                cats = categories_list()
                if not (1 <= cat_idx <= len(cats)):
                    await send_text(from_msisdn, "❌ Error. Categoría no encontrada.")
                    await send_main_menu_text(from_msisdn)
                    return
                _, qdict = cats[cat_idx - 1]
                questions = list(qdict.items())
                if 1 <= choice <= len(questions):
                    q_text, a_text = questions[choice - 1]
                    await send_text(from_msisdn, f"✅ Respuesta:\n\n{a_text}")
                    await ask_follow_up_more_help(from_msisdn)
                else:
                    await send_text(from_msisdn, "❌ Pregunta no válida. Envía el número de la pregunta.")
                    await send_questions_menu(from_msisdn, cat_idx)
            else:
                await send_main_menu_text(from_msisdn)
    except ValueError:
        # not a number -> show initial menu
        await send_initial_menu_buttons(from_msisdn)

async def process_interactive_message(from_msisdn: str, interactive_data: Dict[str, Any]) -> None:
    itype = interactive_data.get("type")
    logging.info("Interactive recibido de %s: %s", from_msisdn, itype)
    if itype == "button_reply":
        br = interactive_data.get("button_reply", {})
        btn_id = br.get("id")
        btn_title = br.get("title")
        logging.info("Button reply id=%s title=%s", btn_id, btn_title)
        # global buttons
        if btn_id == "bot_qa":
            await send_text(from_msisdn, "🤖 Has seleccionado Asistente Virtual. Aquí están nuestras categorías:")
            await send_main_menu_text(from_msisdn)
            return
        if btn_id == "human_support":
            await send_text(from_msisdn, "👨‍💼 Soporte humano activado. Un agente se pondrá en contacto contigo.")
            conversation_state.pop(from_msisdn, None)
            return
        if btn_id == "more_yes":
            await send_text(from_msisdn, "Perfecto, muéstrame otra consulta:")
            await send_main_menu_text(from_msisdn)
            return
        if btn_id == "more_no":
            await send_text(from_msisdn, "Gracias. Por favor, califica nuestro servicio:")
            await ask_for_rating(from_msisdn)
            return
        if btn_id and btn_id.startswith("rating_"):
            await send_text(from_msisdn, f"Gracias por tu calificación ({btn_title}). ¡Nos ayuda mucho!")
            conversation_state.pop(from_msisdn, None)
            return
        # QA button pressed (ids like qa:{cat}:{q})
        if btn_id and btn_id.startswith("qa:"):
            found = find_question_by_uid(btn_id)
            if found:
                _, _, q_text, a_text = found
                await send_text(from_msisdn, f"✅ Respuesta:\n\n{a_text}")
                await ask_follow_up_more_help(from_msisdn)
                return
        logging.warning("Botón desconocido: %s", btn_id)
        await send_initial_menu_buttons(from_msisdn)
        return

    if itype == "list_reply":
        lr = interactive_data.get("list_reply", {})
        lr_id = lr.get("id")
        lr_title = lr.get("title")
        logging.info("List reply id=%s title=%s", lr_id, lr_title)
        # expected qa:{cat}:{q}
        if lr_id and lr_id.startswith("qa:"):
            found = find_question_by_uid(lr_id)
            if found:
                _, _, q_text, a_text = found
                await send_text(from_msisdn, f"✅ Respuesta:\n\n{a_text}")
                await ask_follow_up_more_help(from_msisdn)
                return
        logging.warning("Payload de lista desconocido: %s", lr_id)
        await send_initial_menu_buttons(from_msisdn)
        return

    logging.info("Tipo interactivo no manejado")
    await send_initial_menu_buttons(from_msisdn)

# ========== FastAPI endpoints ==========
@app.get("/webhook")
async def verify_webhook(hub_mode: str | None = Query(None, alias="hub.mode"), hub_challenge: str | None = Query(None, alias="hub.challenge"), hub_verify_token: str | None = Query(None, alias="hub.verify_token")):
    logging.info("Verificando webhook")
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge or "", status_code=200)
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhook")
async def receive_webhook(request: Request):
    try:
        body_bytes = await request.body()
        signature = request.headers.get("X-Hub-Signature-256")
        if not verify_signature(signature, body_bytes):
            raise HTTPException(status_code=403, detail="Invalid signature")
        data = await request.json()
        logging.info("Webhook payload received")
        if data.get("object") != "whatsapp_business_account":
            return Response(status_code=200)
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages")
                if not messages:
                    continue
                for message in messages:
                    from_msisdn = message.get("from")
                    mtype = message.get("type")
                    logging.info("Mensaje de %s tipo=%s", from_msisdn, mtype)
                    if mtype == "text":
                        await process_text_message(from_msisdn, message.get("text", {}).get("body", ""))
                    elif mtype == "interactive":
                        await process_interactive_message(from_msisdn, message.get("interactive", {}))
                    else:
                        await send_initial_menu_buttons(from_msisdn)
        return Response(status_code=200)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except HTTPException:
        raise
    except Exception:
        logging.exception("Error procesando webhook")
        return Response(status_code=500, content="Internal Server Error")

@app.get("/")
async def health_check():
    return {"status": "ok", "service": "WhatsApp Bot Per Capital", "version": "4.0", "categories": len(QA_CATEGORIZED), "active_conversations": len(conversation_state)}

@app.get("/clear-conversations")
async def clear_conversations():
    c = len(conversation_state)
    conversation_state.clear()
    logging.info("Conversaciones limpiadas: %d", c)
    return {"status": "success", "cleared": c}

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logging.exception("Excepción global no manejada")
    return Response(status_code=500, content=json.dumps({"error": "Internal server error"}), media_type="application/json")

if __name__ == "__main__":
    print("🚀 Iniciando WhatsApp Bot Per Capital (Interactive Lists)...")
    print(f"📚 Categorías cargadas: {len(QA_CATEGORIZED)}")
    for idx, (title, qdict) in enumerate(categories_list(), start=1):
        print(f"  {idx}. {title}: {len(qdict)} preguntas")
    print("✅ Listo.")
