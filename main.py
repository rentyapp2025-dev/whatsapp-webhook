import os
import hmac
import hashlib
import json
import re
from enum import Enum
from typing import Optional, Any, Dict, List

from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.responses import PlainTextResponse
import httpx
from fuzzywuzzy import fuzz

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "").encode("utf-8")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

app = FastAPI(title="WhatsApp Cloud API Webhook (Render/FastAPI)")

# ==================== Documento de Preguntas y Respuestas (base de conocimiento) ====================
# Extraído de "Preguntas y respuesta Per Capital.docx.pdf"
QA_DATA = {
    "¿Mi usuario esta en revision que debo hacer?": "Estimado inversionista por favor enviar numero de cedula para apoyarle. (Se verifica que tenga documentación e información completa y se activa).",
    "¿Como puedo invertir?": "Primero debe estar registrado y aprobado en la aplicación, luego Ingresa en la opción de negociación >Selecciona suscripción > ingresa el monto que desea invertir > hacer click en suscribir > ingresa método de pago. Una vez pagado se sube el comprobante y en el transcurso del día de hace efectivo al cierre del.dia o al día siguiente hábil.",
    "¿Que es el Fondo Mutual Abierto?": "El Fondo Mutual Abierto es una cesta llena de diferentes inversiones (acciones, bonos, etc.). Al suscribir estaría comprando acciones y renta fija indirectamente. Puedes ver en que esta diversificado el portafolio dentro de la aplicación.",
    "¿En que puedo invertir?": "Por ahora puede invertir en el fondo mutual abierto que posee un portafolio diversificado en bolívares en acciones que cotizan en la bolsa de valores y en dólares en papeles comerciales o renta fija.",
    "¿Que son las Unidades de Inversion (UI)?": "Las Unidades de Inversión (UI) de un fondo mutual abierto son instrumentos que representan una participación proporcional en el patrimonio de dicho fondo. Cada Ul representa una porción del total del fondo, y su valor fluctúa según el rendimiento de los activos que componen el fondo.",
    "¿Que es el valor de la unidad de inversión (VUI)?": "El Valor de la Unidad de Inversión (VUI) es el precio por unidad que se utiliza para calcular el valor de una inversión. Es el valor de mercado de cada una de las acciones o unidades de inversión que representan una participación en el patrimonio del fondo, y que cambian a diario.",
    "¿Por que baja el rendimiento?": "El valor de tu inversión está directamente ligado al valor total de los activos del fondo. Si el valor de las inversiones dentro del fondo disminuye, el valor de tu participación también disminuirá. Recuerda que el horizonte de inversión de los Fondos Mutuales es a largo plazo.",
    "¿QUE HAGO AHORA?": "Una vez suscrito no debe hacer más nada, solo monitorear su inversión, ya que nosotros gestionamos activamente las inversiones. Puede observar en que esta invertido su dinero dentro de la aplicación en la opción de portafolio.",
    "¿Como recupero la clave?": "Una vez seleccione la opción de 'Recuperar' y le llegara una clave temporal. Deberá ingresarla como nueva clave de su usuario y luego la aplicación le solicitará una nueva clave que deberá confirmar.",
    "¿Por que tardan tanto en responder o en aprobar?": "Debido al alto tráfico estamos presentando retrasos en la aprobación de registros, estamos trabajando arduamente para aprobarte y que empieces a invertir. Por favor envianos tu cedula escaneada a este correo.",
    "¿Como compro acciones?": "Próximamente podrá comprar y vender acciones por la aplicación, mientras tanto puede invertir en unidades de inversión en el Fondo Mutual Abierto, cuyo portafolio está compuesto por algunas acciones que están en la bolsa de valores.",
    "¿En cuanto tiempo veo ganancias?": "Si su horizonte de inversión es a corto plazo no le aconsejamos participar en el Fondo Mutual Abierto. Le sugerimos tenga paciencia ya que los rendimientos esperados en los Fondos Mutuales se esperan a largo plazo.",
    "¿Comisiones?": "Las comisiones son de 3% por suscripción y 5% de administración anualizado.",
    "¿Desde cuanto puedo invertir?": "Desde un Bolivar.",
    "¿Cuanto puedo retirar?": "Desde una Unidad de Inversion.",
    "¿Como rescato?": "Selecciona rescate > ingresa las unidades de inversión a rescatar > luego calcula selección > selecciona rescatar > siga los pasos.",
    "¿Como invierto en dolares?": "Puede invertir en un Papel Comercial, que son instrumentos de deuda a corto plazo (menos de un año) emitidos por las empresas en el mercado de valores.",
    "¿Como invierto en un papel comercial?": "Debe estar registrado con Per Capital y en la Caja Venezolana con cedula, RIF y constancia de trabajo. Adjunto encontrara el link de la Caja Venezolana, una vez termine el registro nos avisa para apoyarle, el depositante deber ser Per Capital.",
    "¿No me llega el mensaje de texto?": "Por favor intente en otra locación, si persiste el error intente en unas horas o el dia de mañana. En caso de no persistir el error, por favor, intente con otro numero de teléfono y luego lo actualizamos en sistema.",
    "¿Ya me registre en la Caja Venezolana?": "Por ahora no hace falta estar registrado en la caja venezolana para invertir en el fondo mutual abierto. Próximamente podrá comprar y vender acciones por la aplicación, mientras tanto puede invertir en unidades de inversión en el Fondo Mutual Abierto.",
    "¿Informacion del fondo mutual abierto y acciones?": "Por ahora puede invertir en el fondo mutual abierto, en el cual posee un portafolio diversificado en acciones que cotizan en la bolsa de valores de caracas y en papeles comerciales. El portafolio podrá verlo dentro de la aplicación en detalle.",
    "¿Aprobado?": "Su usuario ya se encuentra APROBADO. Recuerde que, si realiza alguna modificación de su información, entra en revisión, por ende, debe notificarnos para apoyarle. Si realiza una suscripción antes de las 12 del mediodía la vera reflejada al cierre del día aproximadamente 5-6 de la tarde.",
    "¿Como hago un retiro?": "Selecciona rescate > ingresa las unidades de inversión a rescatar > luego calcula selección > selecciona rescatar > siga los pasos que indique la app.",
    "¿Nunca he rescatado?": "Si usted no ha realizado algún rescate, haga caso omiso al correo enviado. Le sugerimos que ingrese en la aplicación y valide sus fondos."
}

def find_best_match(user_question: str) -> str:
    """
    Encuentra la pregunta más similar en la base de conocimiento usando fuzzy string matching.
    """
    best_match = None
    best_score = 0
    
    # Limpiar y estandarizar la pregunta del usuario
    cleaned_user_q = re.sub(r'[¿?]', '', user_question).strip().lower()

    for q in QA_DATA.keys():
        cleaned_qa_q = re.sub(r'[¿?]', '', q).strip().lower()
        score = fuzz.ratio(cleaned_user_q, cleaned_qa_q)
        if score > best_score:
            best_score = score
            best_match = q
            
    # Devuelve la respuesta si la similitud es alta (por ejemplo, > 70)
    if best_score > 70:
        return QA_DATA.get(best_match, "No estoy seguro de cómo responder a esa pregunta. Por favor, intente reformularla.")
    else:
        # Verifica si hay palabras clave para respuestas específicas, si no se encuentra un buen match
        if re.search(r"revision|cedula|documentacion|aprobad", cleaned_user_q):
            return QA_DATA.get("¿Mi usuario esta en revision que debo hacer?", "No estoy seguro de cómo responder a esa pregunta. Por favor, intente reformularla.")
        if re.search(r"invertir|inversion", cleaned_user_q):
            return QA_DATA.get("¿Como puedo invertir?", "No estoy seguro de cómo responder a esa pregunta. Por favor, intente reformularla.")
        if re.search(r"retiro|retirar|rescate|rescatar", cleaned_user_q):
            return QA_DATA.get("¿Como hago un retiro?", "No estoy seguro de cómo responder a esa pregunta. Por favor, intente reformularla.")
        
    return "No estoy seguro de cómo responder a esa pregunta. Por favor, intente reformularla."


# ==================== Funciones para enviar mensajes interactivos ====================
async def send_interactive_menu(to_msisdn: str) -> Dict[str, Any]:
    """
    Envía un menú interactivo con botones de las preguntas más comunes.
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": "¡Hola! Soy tu asistente de Per Capital. ¿En qué puedo ayudarte hoy? Selecciona una opción del menú:"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "faq_invertir",
                            "title": "Cómo invertir"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "faq_retirar",
                            "title": "Cómo retirar"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "faq_fondo_mutual",
                            "title": "Qué es el Fondo Mutual"
                        }
                    }
                ]
            }
        }
    }
    return await _post_messages(payload)

def get_response_by_id(button_id: str) -> str:
    """
    Obtiene la respuesta de la base de conocimiento según el ID del botón.
    """
    if button_id == "faq_invertir":
        return QA_DATA.get("¿Como puedo invertir?", "Información no encontrada.")
    elif button_id == "faq_retirar":
        return QA_DATA.get("¿Como hago un retiro?", "Información no encontrada.")
    elif button_id == "faq_fondo_mutual":
        return QA_DATA.get("¿Que es el Fondo Mutual Abierto?", "Información no encontrada.")
    else:
        return "Lo siento, no pude encontrar la respuesta a esa pregunta. Por favor, intenta de nuevo o reformula tu pregunta."

# ==================== Almacenamiento efímero (demo) ====================
USERS: Dict[str, Dict[str, str]] = {}
LISTINGS: Dict[str, Dict[str, str]] = {}
CONSENTS: Dict[str, Dict[str, Any]] = {}
STATE: Dict[str, Dict[str, Any]] = {}

class Step(str, Enum):
    IDLE = "idle"

def get_user(msisdn: str) -> dict:
    return USERS.setdefault(msisdn, {"name": msisdn})

def set_state(msisdn: str, step: Step, draft: dict | None = None):
    STATE[msisdn] = {"step": step, "draft": draft or {}}

def get_state(msisdn: str) -> dict:
    return STATE.get(msisdn, {"step": Step.IDLE, "draft": {}})

# ==================== utilidades WhatsApp ====================
def verify_signature(signature: Optional[str], body: bytes) -> bool:
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

async def send_text(to_msisdn: str, text: str) -> Dict[str, Any]:
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "text",
        "text": {"body": text}
    }
    return await _post_messages(payload)

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
                get_user(from_msisdn)
                msg_type = msg.get("type")
                
                if msg_type == "text":
                    text = (msg.get("text") or {}).get("body", "") or ""
                    text = text.strip().lower()

                    if re.match(r"^(hola|buenas|menu|start|ayuda|help)$", text):
                        await send_interactive_menu(from_msisdn)
                    else:
                        response_text = find_best_match(text)
                        await send_text(from_msisdn, response_text)

                elif msg_type == "interactive":
                    interactive_data = msg.get("interactive", {})
                    if interactive_data.get("type") == "button_reply":
                        button_id = interactive_data.get("button_reply", {}).get("id")
                        response_text = get_response_by_id(button_id)
                        await send_text(from_msisdn, response_text)

    return Response(status_code=200)

@app.get("/")
async def health():
    return {"status": "ok"}
