import os
import hmac
import hashlib
import json
import re
from typing import Optional, Any, Dict, List
import logging

from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.responses import PlainTextResponse
import httpx

# Configurar el logging para ver mensajes detallados
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Obtener variables de entorno. Es crucial que estas estén configuradas correctamente.
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "").encode("utf-8")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Verificar que las variables de entorno cruciales estén presentes
if not all([VERIFY_TOKEN, WHATSAPP_TOKEN, PHONE_NUMBER_ID]):
    logging.error("Faltan variables de entorno cruciales: VERIFY_TOKEN, WHATSAPP_TOKEN, o PHONE_NUMBER_ID no están configuradas.")
    logging.info("Asegúrate de configurar estas variables en tu entorno de despliegue (por ejemplo, en Render.com).")

app = FastAPI(title="WhatsApp Cloud API Webhook (Render/FastAPI)")

# ==================== Documento de Preguntas y Respuestas (base de conocimiento) ====================
# Estructura categorizada de preguntas y respuestas
QA_CATEGORIZED = {
    "1. Inversiones": {
        "1. ¿Como puedo invertir?": "Primero debe estar registrado y aprobado en la aplicación, luego Ingresa en la opción de negociación >Selecciona suscripción > ingresa el monto que desea invertir > hacer click en suscribir > ingresa método de pago. Una vez pagado se sube el comprobante y en el transcurso del día de hace efectivo al cierre del.dia o al día siguiente hábil.",
        "2. ¿Que es el Fondo Mutual Abierto?": "El Fondo Mutual Abierto es una cesta llena de diferentes inversiones (acciones, bonos, etc.). Al suscribir estaría comprando acciones y renta fija indirectamente. Puedes ver en que esta diversificado el portafolio dentro de la aplicación.",
        "3. ¿En que puedo invertir?": "Por ahora puede invertir en el fondo mutual abierto que posee un portafolio diversificado en bolívares en acciones que cotizan en la bolsa de valores y en dólares en papeles comerciales o renta fija.",
        "4. ¿Que son las Unidades de Inversion (UI)?": "Las Unidades de Inversión (UI) de un fondo mutual abierto son instrumentos que representan una participación proporcional en el patrimonio de dicho fondo. Cada Ul representa una porción del total del fondo, y su valor fluctúa según el rendimiento de los activos que componen el fondo.",
        "5. ¿Que es el valor de la unidad de inversión (VUI)?": "El Valor de la Unidad de Inversión (VUI) es el precio por unidad que se utiliza para calcular el valor de una inversión. Es el valor de mercado de cada una de las acciones o unidades de inversión que representan una participación en el patrimonio del fondo, y que cambian a diario.",
        "6. ¿Por que baja el rendimiento?": "El valor de tu inversión está directamente ligado al valor total de los activos del fondo. Si el valor de las inversiones dentro del fondo disminuye, el valor de tu participación también disminuirá. Recuerda que el horizonte de inversión de los Fondos Mutuales es a largo plazo.",
        "7. ¿QUE HAGO AHORA?": "Una vez suscrito no debe hacer más nada, solo monitorear su inversión, ya que nosotros gestionamos activamente las inversiones. Puede observar en que esta invertido su dinero dentro de la aplicación en la opción de portafolio.",
        "8. ¿Comisiones?": "Las comisiones son de 3% por suscripción y 5% de administración anualizado.",
        "9. ¿Desde cuanto puedo invertir?": "Desde un Bolivar.",
        "10. ¿En cuanto tiempo veo ganancias?": "Si su horizonte de inversión es a corto plazo no le aconsejamos participar en el Fondo Mutual Abierto. Le sugerimos tenga paciencia ya que los rendimientos esperados en los Fondos Mutuales se esperan a largo plazo.",
        "11. ¿Como compro acciones?": "Próximamente podrá comprar y vender acciones por la aplicación, mientras tanto puede invertir en unidades de inversión en el Fondo Mutual Abierto, cuyo portafolio está compuesto por algunas acciones que están en la bolsa de valores.",
    },
    "2. Retiros y Transacciones": {
        "1. ¿Como hago un retiro?": "Selecciona rescate > ingresa las unidades de inversión a rescatar > luego calcula selección > selecciona rescatar > siga los pasos que indique la app.",
        "2. ¿Nunca he rescatado?": "Si usted no ha realizado algún rescate, haga caso omiso al correo enviado. Le sugerimos que ingrese en la aplicación y valide sus fondos.",
        "3. ¿Cuanto puedo retirar?": "Desde una Unidad de Inversion.",
        "4. ¿Como rescato?": "Selecciona rescate > ingresa las unidades de inversión a rescatar > luego calcula selección > selecciona rescatar > siga los pasos.",
    },
    "3. Problemas con la Cuenta": {
        "1. ¿Mi usuario esta en revision que debo hacer?": "Estimado inversionista por favor enviar numero de cedula para apoyarle. (Se verifica que tenga documentación e información completa y se activa).",
        "2. ¿Como recupero la clave?": "Una vez seleccione la opción de 'Recuperar' y le llegara una clave temporal. Deberá ingresarla como nueva clave de su usuario y luego la aplicación le solicitará una nueva clave que deberá confirmar.",
        "3. ¿Por que tardan tanto en responder o en aprobar?": "Debido al alto tráfico estamos presentando retrasos en la aprobación de registros, estamos trabajando arduamente para aprobarte y que empieces a invertir. Por favor envianos tu cedula escaneada a este correo.",
        "4. ¿Aprobado?": "Su usuario ya se encuentra APROBADO. Recuerde que, si realiza alguna modificación de su información, entra en revisión, por ende, debe notificarnos para apoyarle. Si realiza una suscripción antes de las 12 del mediodía la vera reflejada al cierre del día aproximadamente 5-6 de la tarde.",
        "5. ¿No me llega el mensaje de texto?": "Por favor intente en otra locación, si persiste el error intente en unas horas o el dia de mañana. En caso de no persistir el error, por favor, intente con otro numero de teléfono y luego lo actualizamos en sistema.",
    },
    "4. Otros Tipos de Inversión": {
        "1. ¿Como invierto en dolares?": "Puede invertir en un Papel Comercial, que son instrumentos de deuda a corto plazo (menos de un año) emitidos por las empresas en el mercado de valores.",
        "2. ¿Como invierto en un papel comercial?": "Debe estar registrado con Per Capital y en la Caja Venezolana con cedula, RIF y constancia de trabajo. Adjunto encontrara el link de la Caja Venezolana, una vez termine el registro nos avisa para apoyarle, el depositante deber ser Per Capital.",
        "3. ¿Ya me registre en la Caja Venezolana?": "Por ahora no hace falta estar registrado en la caja venezolana para invertir en el fondo mutual abierto. Próximamente podrá comprar y vender acciones por la aplicación, mientras tanto puede invertir en unidades de inversión en el Fondo Mutual Abierto.",
        "4. ¿Informacion del fondo mutual abierto y acciones?": "Por ahora puede invertir en el fondo mutual abierto, en el cual posee un portafolio diversificado en acciones que cotizan en la bolsa de valores de caracas y en papeles comerciales. El portafolio podrá verlo dentro de la aplicación en detalle.",
    }
}

# Variable global para almacenar el estado de la conversación (categoría actual)
conversation_state: Dict[str, str] = {}


def get_menu_by_category_index(index: int) -> Optional[Dict[str, str]]:
    """
    Obtiene un submenú de preguntas por el índice de la categoría.
    """
    categories = list(QA_CATEGORIZED.keys())
    if 1 <= index <= len(categories):
        category_name = categories[index - 1]
        return {
            "title": category_name,
            "questions": QA_CATEGORIZED[category_name]
        }
    return None

def get_answer_by_full_index(category_index: int, question_index: int) -> str:
    """
    Obtiene la respuesta de la base de conocimiento usando el índice de la categoría y la pregunta.
    """
    category_menu = get_menu_by_category_index(category_index)
    if not category_menu:
        return "Lo siento, la categoría seleccionada no es válida. Por favor, elige una categoría del menú principal."
    
    questions = list(category_menu["questions"].keys())
    if 1 <= question_index <= len(questions):
        question = questions[question_index - 1]
        return category_menu["questions"][question]
    
    return "Lo siento, el número de pregunta no es válido. Por favor, elige un número del submenú."


# ==================== Funciones para enviar mensajes ====================

async def send_initial_menu_with_buttons(to_msisdn: str) -> Dict[str, Any]:
    """
    Envía un menú interactivo con dos botones para la selección inicial.
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "text",
                "text": "Bienvenido a Per Capital"
            },
            "body": {
                "text": "¿Cómo te gustaría continuar? Puedes hablar con nuestro asistente virtual o contactar a un agente."
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "bot_qa",
                            "title": "Hablar con el bot"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "human_support",
                            "title": "Contactar a soporte humano"
                        }
                    }
                ]
            }
        }
    }
    return await _post_messages(payload)

async def send_main_menu(to_msisdn: str) -> Dict[str, Any]:
    """
    Envía el menú principal de categorías.
    """
    menu_text = "¡Hola! Soy tu asistente de Per Capital. Por favor, elige una categoría de la siguiente lista:\n\n"
    for category in QA_CATEGORIZED.keys():
        menu_text += f"{category}\n"
    
    menu_text += "\nEnvía solo el número de la categoría (ej. '1')."
    await send_text(to_msisdn, menu_text)
    # Limpiar el estado de la conversación cuando se envía el menú principal
    if to_msisdn in conversation_state:
        del conversation_state[to_msisdn]
    return {}

async def send_subcategory_menu(to_msisdn: str, category_index: int) -> Dict[str, Any]:
    """
    Envía el submenú de preguntas para una categoría.
    """
    category_menu = get_menu_by_category_index(category_index)
    if not category_menu:
        await send_text(to_msisdn, "Categoría no válida. Por favor, envía un número de categoría válido para ver las preguntas.")
        return {}

    menu_text = f"Has seleccionado **{category_menu['title']}**\n\nPor favor, elige una pregunta de la siguiente lista:\n\n"
    for question in category_menu["questions"].keys():
        menu_text += f"{question}\n"
    
    menu_text += "\nEnvía solo el número de la pregunta (ej. '1') o envía 'volver' para regresar al menú principal."
    
    # Guardar el estado de la categoría actual para el usuario
    conversation_state[to_msisdn] = str(category_index)
    return await send_text(to_msisdn, menu_text)


# ==================== utilidades WhatsApp ====================
def verify_signature(signature: Optional[str], body: bytes) -> bool:
    if not APP_SECRET:
        logging.warning("APP_SECRET no está configurada. La verificación de firma está deshabilitada.")
        return True
    if not signature or not signature.startswith("sha256="):
        logging.error("Firma de la solicitud no válida o ausente.")
        return False
    their = signature.split("sha256=")[-1].strip()
    mac = hmac.new(APP_SECRET, msg=body, digestmod=hashlib.sha256)
    mine = mac.hexdigest()
    is_valid = hmac.compare_digest(mine, their)
    if not is_valid:
        logging.error("La firma de la solicitud no coincide con la firma generada. Verifica tu APP_SECRET.")
    return is_valid

async def _post_messages(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            logging.info(f"Mensaje enviado con éxito a {payload.get('to')}")
            return r.json()
    except httpx.HTTPStatusError as e:
        logging.error(f"Error al enviar el mensaje. Código de estado: {e.response.status_code}")
        logging.error(f"Cuerpo del error: {e.response.text}")
        raise
    except Exception as e:
        logging.error(f"Ocurrió un error inesperado al enviar el mensaje: {e}")
        raise

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
    logging.info("Verificando el webhook...")
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logging.info("Verificación exitosa.")
        return PlainTextResponse(content=hub_challenge or "", status_code=200)
    logging.error("Fallo en la verificación. Token o modo incorrectos.")
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhook")
async def receive_webhook(request: Request):
    try:
        body_bytes = await request.body()
        signature = request.headers.get("X-Hub-Signature-256")

        if not verify_signature(signature, body_bytes):
            raise HTTPException(status_code=403, detail="Invalid signature")

        data = await request.json()
        logging.info(f"Datos recibidos del webhook: {json.dumps(data, indent=2)}")

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
                    msg_type = msg.get("type")
                    
                    if msg_type == "interactive":
                        interactive_data = msg.get("interactive", {})
                        if interactive_data.get("type") == "button_reply":
                            button_id = interactive_data.get("button_reply", {}).get("id")
                            if button_id == "bot_qa":
                                await send_main_menu(from_msisdn)
                            elif button_id == "human_support":
                                await send_text(from_msisdn, "Gracias por contactarnos. Un miembro de nuestro equipo se pondrá en contacto contigo pronto. Esta conversación ha finalizado.")
                        continue

                    # Si el mensaje es de texto, lo procesamos para el flujo de preguntas y respuestas
                    if msg_type == "text":
                        text = (msg.get("text") or {}).get("body", "") or ""
                        text_lower = text.strip().lower()
                        
                        # Si el usuario quiere volver al menú principal
                        if text_lower == "volver" or text_lower == "menu" or text_lower == "menú":
                            await send_main_menu(from_msisdn)
                            continue

                        # Manejar la lógica de la conversación basada en el estado
                        try:
                            choice = int(text_lower)
                            current_category = conversation_state.get(from_msisdn)
                            
                            if current_category is None:
                                # El usuario está en el menú principal
                                await send_subcategory_menu(from_msisdn, choice)
                            else:
                                # El usuario está en un submenú, busca la respuesta
                                category_index = int(current_category)
                                response_text = get_answer_by_full_index(category_index, choice)
                                await send_text(from_msisdn, response_text)
                                # Volver al menú principal después de dar la respuesta
                                await send_main_menu(from_msisdn)

                        except (ValueError, IndexError):
                            # Si el input no es un número o es inválido, muestra el menú apropiado
                            if from_msisdn in conversation_state:
                                await send_subcategory_menu(from_msisdn, int(conversation_state[from_msisdn]))
                            else:
                                # Si el usuario envía texto no numérico, le volvemos a enviar el menú inicial de botones.
                                await send_initial_menu_with_buttons(from_msisdn)
                    
                    # Para cualquier otro tipo de mensaje (audio, imagen, etc.), se vuelve a mostrar el menú de selección inicial.
                    else:
                        logging.info(f"Mensaje recibido de tipo '{msg_type}'. Enviando menú inicial.")
                        await send_initial_menu_with_buttons(from_msisdn)

        return Response(status_code=200)

    except json.JSONDecodeError:
        logging.error("Error al decodificar JSON en la solicitud.")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logging.error(f"Ocurrió un error inesperado al procesar el webhook: {e}", exc_info=True)
        return Response(status_code=500, content="Internal Server Error")

@app.get("/")
async def health():
    return {"status": "ok"}
