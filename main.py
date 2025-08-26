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
APP_SECRET = os.getenv("APP_SECRET", "").encode("utf-8") if os.getenv("APP_SECRET") else b""
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
# En producción, considera usar Redis o una base de datos para persistencia
conversation_state: Dict[str, str] = {}

# ==================== Funciones de la lógica del menú ====================

def get_menu_by_category_index(index: int) -> Optional[Dict[str, str]]:
    """
    Obtiene un submenú de preguntas por el índice de la categoría.
    
    Args:
        index: Número de categoría (1-4)
    
    Returns:
        Dict con título y preguntas de la categoría, o None si el índice es inválido
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
    
    Args:
        category_index: Número de categoría (1-4)
        question_index: Número de pregunta dentro de la categoría
    
    Returns:
        Respuesta correspondiente o mensaje de error
    """
    category_menu = get_menu_by_category_index(category_index)
    if not category_menu:
        return "Lo siento, la categoría seleccionada no es válida. Por favor, elige una categoría del menú principal."
    
    questions = list(category_menu["questions"].keys())
    if 1 <= question_index <= len(questions):
        question = questions[question_index - 1]
        return category_menu["questions"][question]
    
    return "Lo siento, el número de pregunta no es válido. Por favor, elige un número del submenú."


def is_back_command(text: str) -> bool:
    """
    Verifica si el mensaje es un comando para volver al menú principal.
    
    Args:
        text: Texto del mensaje del usuario
    
    Returns:
        True si es un comando de retorno, False de lo contrario
    """
    back_keywords = ["volver", "menu", "menú", "principal", "inicio", "back", "0"]
    return text.strip().lower() in back_keywords


# ==================== Funciones para enviar mensajes ====================

async def send_initial_menu_with_buttons(to_msisdn: str) -> Dict[str, Any]:
    """
    Envía un menú interactivo con dos botones para la selección inicial.
    Esta función se llama siempre que el usuario envía un mensaje que no es una respuesta a botón.
    
    Args:
        to_msisdn: Número de teléfono del destinatario
    
    Returns:
        Respuesta de la API de WhatsApp
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "text",
                "text": "🏦 Bienvenido a Per Capital"
            },
            "body": {
                "text": "¡Hola! Gracias por contactarnos. ¿Cómo te gustaría continuar?\n\n• Puedes hablar con nuestro asistente virtual para respuestas inmediatas\n• O contactar directamente con un agente de soporte humano"
            },
            "footer": {
                "text": "Selecciona una opción"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "bot_qa",
                            "title": "🤖 Asistente Virtual"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "human_support",
                            "title": "👨‍💼 Soporte Humano"
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
    
    Args:
        to_msisdn: Número de teléfono del destinatario
    
    Returns:
        Respuesta de la API de WhatsApp
    """
    menu_text = "📋 *Menú Principal - Per Capital*\n\n"
    menu_text += "Por favor, elige una categoría enviando el número correspondiente:\n\n"
    
    for i, category in enumerate(QA_CATEGORIZED.keys(), 1):
        menu_text += f"*{i}.* {category.split('. ', 1)[1]}\n"
    
    menu_text += "\n💡 *Instrucciones:*\n"
    menu_text += "• Envía solo el número de la categoría (ej. '1')\n"
    menu_text += "• Escribe 'volver' en cualquier momento para regresar aquí"
    
    # Limpiar el estado de la conversación cuando se envía el menú principal
    if to_msisdn in conversation_state:
        del conversation_state[to_msisdn]
        logging.info(f"Estado de conversación limpiado para {to_msisdn}")
    
    return await send_text(to_msisdn, menu_text)



async def send_subcategory_menu(to_msisdn: str, category_index: int) -> Dict[str, Any]:
    category_menu = get_menu_by_category_index(category_index)
    if not category_menu:
        await send_text(to_msisdn, "❌ Categoría no válida. Por favor, envía un número de categoría válido.")
        await send_main_menu(to_msisdn)
        return {}

    sections = [
        {
            "title": category_menu["title"],
            "rows": [
                {
                    "id": f"q_{category_index}_{i+1}",
                    "title": re.sub(r'^\d+\.\s*', '', question),
                    "description": "Haz clic para ver la respuesta"
                }
                for i, question in enumerate(category_menu["questions"].keys())
            ]
        }
    ]

    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {
                "type": "text",
                "text": f"📚 {category_menu['title']}"
            },
            "body": {
                "text": "Selecciona una pregunta para ver la respuesta."
            },
            "footer": {
                "text": "Escribe 'volver' para regresar al menú principal."
            },
            "action": {
                "button": "Ver preguntas",
                "sections": sections
            }
        }
    }

    conversation_state[to_msisdn] = str(category_index)
    return await _post_messages(payload)
async def send_subcategory_menu(to_msisdn: str, category_index: int) -> Dict[str, Any]:
    """
    Envía el submenú de preguntas para una categoría específica.
    
    Args:
        to_msisdn: Número de teléfono del destinatario
        category_index: Índice de la categoría (1-4)
    
    Returns:
        Respuesta de la API de WhatsApp
    """
    category_menu = get_menu_by_category_index(category_index)
    if not category_menu:
        await send_text(to_msisdn, "❌ Categoría no válida. Por favor, envía un número de categoría válido (1-4).")
        await send_main_menu(to_msisdn)
        return {}

    menu_text = f"📂 *{category_menu['title']}*\n\n"
    menu_text += "Selecciona una pregunta enviando el número correspondiente:\n\n"
    
    for i, question in enumerate(category_menu["questions"].keys(), 1):
        # Limpiar el número del principio de la pregunta si existe
        clean_question = re.sub(r'^\d+\.\s*', '', question)
        menu_text += f"*{i}.* {clean_question}\n"
    
    menu_text += f"\n💡 *Opciones:*\n"
    menu_text += "• Envía el número de la pregunta (ej. '1')\n"
    menu_text += "• Escribe 'volver' para regresar al menú principal"
    
    # Guardar el estado de la categoría actual para el usuario
    conversation_state[to_msisdn] = str(category_index)
    logging.info(f"Estado guardado para {to_msisdn}: categoría {category_index}")
    
    return await send_text(to_msisdn, menu_text)


# ==================== Utilidades WhatsApp ====================

def verify_signature(signature: Optional[str], body: bytes) -> bool:
    """
    Verifica la firma HMAC-SHA256 de la solicitud de WhatsApp.
    
    Args:
        signature: Firma en el header X-Hub-Signature-256
        body: Cuerpo de la solicitud en bytes
    
    Returns:
        True si la firma es válida, False de lo contrario
    """
    if not APP_SECRET:
        logging.warning("APP_SECRET no está configurada. La verificación de firma está deshabilitada.")
        return True
    
    if not signature or not signature.startswith("sha256="):
        logging.error("Firma de la solicitud no válida o ausente.")
        return False
    
    their_signature = signature.split("sha256=")[-1].strip()
    mac = hmac.new(APP_SECRET, msg=body, digestmod=hashlib.sha256)
    our_signature = mac.hexdigest()
    
    is_valid = hmac.compare_digest(our_signature, their_signature)
    if not is_valid:
        logging.error("La firma de la solicitud no coincide. Verifica tu APP_SECRET.")
    return is_valid


async def _post_messages(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Función auxiliar para enviar mensajes a través de la API de WhatsApp.
    
    Args:
        payload: Datos del mensaje a enviar
    
    Returns:
        Respuesta de la API de WhatsApp
    
    Raises:
        HTTPException: Si hay un error en la solicitud HTTP
    """
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            logging.info(f"✅ Mensaje enviado con éxito a {payload.get('to')}")
            return response.json()
    except httpx.HTTPStatusError as e:
        logging.error(f"❌ Error HTTP al enviar mensaje. Status: {e.response.status_code}")
        logging.error(f"Respuesta: {e.response.text}")
        raise HTTPException(status_code=500, detail=f"Error sending message: {e.response.status_code}")
    except Exception as e:
        logging.error(f"❌ Error inesperado al enviar mensaje: {e}")
        raise HTTPException(status_code=500, detail="Unexpected error sending message")


async def send_text(to_msisdn: str, text: str) -> Dict[str, Any]:
    """
    Envía un mensaje de texto simple.
    
    Args:
        to_msisdn: Número de teléfono del destinatario
        text: Contenido del mensaje
    
    Returns:
        Respuesta de la API de WhatsApp
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "text",
        "text": {"body": text}
    }
    return await _post_messages(payload)


# ==================== Procesamiento de mensajes ====================

async def process_text_message(from_msisdn: str, message_text: str) -> None:
    """
    Procesa los mensajes de texto del usuario según el flujo de conversación.
    
    Args:
        from_msisdn: Número de teléfono del remitente
        message_text: Contenido del mensaje de texto
    """
    text_clean = message_text.strip()
    text_lower = text_clean.lower()
    
    logging.info(f"📝 Procesando mensaje de texto de {from_msisdn}: '{text_clean}'")
    
    # Verificar si es un comando para volver al menú principal
    if is_back_command(text_clean):
        logging.info(f"🔄 Usuario {from_msisdn} solicitó volver al menú principal")
        await send_main_menu(from_msisdn)
        return
    
    # Intentar interpretar el mensaje como un número
    try:
        choice = int(text_clean)
        current_category = conversation_state.get(from_msisdn)
        
        if current_category is None:
            # El usuario está en el menú principal - seleccionando categoría
            logging.info(f"🗂️ Usuario {from_msisdn} seleccionó categoría {choice}")
            if 1 <= choice <= len(QA_CATEGORIZED):
                await send_subcategory_menu(from_msisdn, choice)
            else:
                await send_text(from_msisdn, f"❌ Opción no válida. Por favor, elige un número entre 1 y {len(QA_CATEGORIZED)}.")
                await send_main_menu(from_msisdn)
        else:
            # El usuario está en un submenú - seleccionando pregunta
            category_index = int(current_category)
            logging.info(f"❓ Usuario {from_msisdn} seleccionó pregunta {choice} de categoría {category_index}")
            
            response_text = get_answer_by_full_index(category_index, choice)
            
            # Enviar la respuesta
            await send_text(from_msisdn, f"✅ *Respuesta:*\n\n{response_text}")
            
            # Pequeña pausa antes de enviar el menú principal
            import asyncio
            await asyncio.sleep(1)
            
            # Volver al menú principal después de dar la respuesta
            await send_text(from_msisdn, "📋 ¿Tienes alguna otra consulta?")
            await send_main_menu(from_msisdn)
            
    except (ValueError, IndexError):
        # El input no es un número válido
        logging.info(f"⚠️ Entrada no numérica de {from_msisdn}: '{text_clean}'")
        current_category = conversation_state.get(from_msisdn)
        
        if current_category is not None:
            # Si está en un submenú, reenviar el submenú con instrucciones
            await send_text(from_msisdn, "⚠️ Por favor, envía solo el número de la pregunta que te interesa.")
            await send_subcategory_menu(from_msisdn, int(current_category))
        else:
            # Si está en el menú principal o no hay estado, enviar menú inicial con botones
            logging.info(f"🔄 Enviando menú inicial con botones a {from_msisdn}")
            await send_initial_menu_with_buttons(from_msisdn)


async def process_interactive_message(from_msisdn: str, interactive_data: Dict[str, Any]) -> None:
    """
    Procesa los mensajes interactivos (respuestas de botones).
    
    Args:
        from_msisdn: Número de teléfono del remitente
        interactive_data: Datos del mensaje interactivo
    """
    if interactive_data.get("type") == "button_reply":
        button_reply = interactive_data.get("button_reply", {})
        button_id = button_reply.get("id")
        button_title = button_reply.get("title")
        
        logging.info(f"🔘 Usuario {from_msisdn} presionó botón: {button_id} ({button_title})")
        
        if button_id == "bot_qa":
            # Iniciar flujo de Q&A
            await send_text(from_msisdn, "🤖 *Perfecto!* Has seleccionado el asistente virtual.\n\nAhora te mostraré las categorías disponibles:")
            await send_main_menu(from_msisdn)
        elif button_id == "human_support":
            # Contactar soporte humano
            await send_text(from_msisdn, 
                "👨‍💼 *Soporte Humano Activado*\n\n"
                "Gracias por contactarnos. Un miembro especializado de nuestro equipo de Per Capital "
                "se pondrá en contacto contigo a la brevedad posible.\n\n"
                "📞 También puedes llamarnos directamente si tu consulta es urgente.\n\n"
                "Esta conversación automática ha finalizado. ¡Que tengas un excelente día! 🙋‍♀️")
            # Limpiar estado de conversación
            if from_msisdn in conversation_state:
                del conversation_state[from_msisdn]

    elif interactive_data.get("type") == "list_reply":
        list_reply = interactive_data.get("list_reply", {})
        row_id = list_reply.get("id", "")
        match = re.match(r"q_(\d+)_(\d+)", row_id)
        if match:
            category_index = int(match.group(1))
            question_index = int(match.group(2))
            response_text = get_answer_by_full_index(category_index, question_index)
            await send_text(from_msisdn, f"✅ *Respuesta:*{response_text}")
            import asyncio
            await asyncio.sleep(1)
            await send_text(from_msisdn, "📋 ¿Tienes alguna otra consulta?")
            await send_main_menu(from_msisdn)
        else:
            await send_text(from_msisdn, "❌ No se pudo interpretar tu selección. Intenta nuevamente.")
            await send_main_menu(from_msisdn)
    else:
        logging.warning(f"⚠️ ID de botón desconocido: {button_id}")
        await send_initial_menu_with_buttons(from_msisdn)


# ==================== Endpoints de FastAPI ====================

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
):
    """
    Endpoint para la verificación del webhook de WhatsApp.
    Facebook/Meta llama a este endpoint para verificar la autenticidad del webhook.
    """
    logging.info(f"🔍 Verificando webhook - Mode: {hub_mode}, Token: {hub_verify_token}")
    
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logging.info("✅ Verificación de webhook exitosa")
        return PlainTextResponse(content=hub_challenge or "", status_code=200)
    
    logging.error("❌ Fallo en la verificación del webhook - Token o modo incorrectos")
    raise HTTPException(status_code=403, detail="Verification token mismatch")


@app.post("/webhook")
async def receive_webhook(request: Request):
    """
    Endpoint principal para recibir mensajes de WhatsApp.
    Procesa todos los mensajes entrantes y maneja la lógica del chatbot.
    """
    try:
        # Leer el cuerpo de la solicitud
        body_bytes = await request.body()
        signature = request.headers.get("X-Hub-Signature-256")
        
        # Verificar la firma de seguridad
        if not verify_signature(signature, body_bytes):
            logging.error("❌ Firma de solicitud inválida")
            raise HTTPException(status_code=403, detail="Invalid signature")
        
        # Parsear los datos JSON
        data = await request.json()
        logging.info(f"📨 Webhook recibido: {json.dumps(data, indent=2)}")
        
        # Verificar que sea una notificación de WhatsApp Business
        if data.get("object") != "whatsapp_business_account":
            logging.info("ℹ️ Notificación ignorada - No es de WhatsApp Business")
            return Response(status_code=200)
        
        # Procesar cada entrada en la notificación
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                
                # Verificar si hay mensajes
                messages = value.get("messages")
                if not messages:
                    logging.info("ℹ️ No hay mensajes en esta notificación")
                    continue
                
                # Procesar cada mensaje
                for message in messages:
                    from_msisdn = message.get("from")
                    message_type = message.get("type")
                    message_id = message.get("id")
                    
                    logging.info(f"📬 Procesando mensaje {message_id} de {from_msisdn} (tipo: {message_type})")
                    
                    if message_type == "interactive":
                        # Procesar mensajes interactivos (botones)
                        interactive_data = message.get("interactive", {})
                        await process_interactive_message(from_msisdn, interactive_data)
                        
                    elif message_type == "text":
                        # Procesar mensajes de texto
                        text_data = message.get("text", {})
                        message_text = text_data.get("body", "")
                        await process_text_message(from_msisdn, message_text)
                        
                    else:
                        # Para cualquier otro tipo de mensaje (audio, imagen, documento, etc.)
                        logging.info(f"📎 Mensaje de tipo '{message_type}' recibido - Enviando menú inicial")
                        await send_initial_menu_with_buttons(from_msisdn)
        
        return Response(status_code=200)
        
    except json.JSONDecodeError:
        logging.error("❌ Error al decodificar JSON en la solicitud")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logging.error(f"❌ Error inesperado procesando webhook: {e}", exc_info=True)
        return Response(status_code=500, content="Internal Server Error")


@app.get("/")
async def health_check():
    """
    Endpoint de salud para verificar que el servicio está funcionando.
    """
    return {
        "status": "ok",
        "service": "WhatsApp Bot Per Capital",
        "version": "2.0",
        "categories": len(QA_CATEGORIZED),
        "active_conversations": len(conversation_state)
    }


@app.get("/status")
async def status_endpoint():
    """
    Endpoint de estado detallado para monitoreo.
    """
    return {
        "service_status": "running",
        "environment_variables": {
            "VERIFY_TOKEN": "✅" if VERIFY_TOKEN else "❌",
            "WHATSAPP_TOKEN": "✅" if WHATSAPP_TOKEN else "❌",
            "PHONE_NUMBER_ID": "✅" if PHONE_NUMBER_ID else "❌",
            "APP_SECRET": "✅" if APP_SECRET else "❌"
        },
        "qa_categories": list(QA_CATEGORIZED.keys()),
        "active_conversations": len(conversation_state),
        "graph_api_version": GRAPH_API_VERSION
    }


@app.get("/clear-conversations")
async def clear_conversations():
    """
    Endpoint para limpiar todas las conversaciones activas (útil para testing).
    """
    global conversation_state
    count = len(conversation_state)
    conversation_state.clear()
    logging.info(f"🧹 Conversaciones limpiadas: {count}")
    return {
        "status": "success",
        "cleared_conversations": count,
        "message": f"Se limpiaron {count} conversaciones activas"
    }


# ==================== Manejo de errores globales ====================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Maneja todas las excepciones no capturadas para evitar que el servicio se caiga.
    """
    logging.error(f"❌ Excepción global no manejada: {exc}", exc_info=True)
    return Response(
        status_code=500,
        content=json.dumps({
            "error": "Internal server error",
            "message": "Se produjo un error inesperado en el servidor"
        }),
        media_type="application/json"
    )


# ==================== Mensaje de inicio del servidor ====================

if __name__ == "__main__":
    print("🚀 Iniciando WhatsApp Bot Per Capital...")
    print(f"📊 Categorías de Q&A cargadas: {len(QA_CATEGORIZED)}")
    for category in QA_CATEGORIZED.keys():
        questions_count = len(QA_CATEGORIZED[category])
        print(f"   • {category}: {questions_count} preguntas")
    print("✅ Bot listo para recibir mensajes!")