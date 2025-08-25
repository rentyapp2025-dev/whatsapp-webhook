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
    "1. PER CAPITAL": {
        "1. ¿Qué es Per Capital?": "Es un grupo de empresas del Mercado de Valores Venezolano reguladas por la SUNAVAL, compuesta por Casa de Bolsa, Sociedad Administradora de EIC, Asesores de Inversion y Titularizadora.",
        "2. ¿Qué es la SUNAVAL?": "Es el ente que regula el Mercado de Valores en Venezuela y protege a los inversionistas www.sunaval.gob.ve.",
        "3. ¿Qué es la Bolsa de Valores de Caracas?": "Es el lugar donde se compran y venden bonos, acciones y otros instrumentos de manera ordenada a traves de las Casas de Bolsa y esta regulada por la SUNAVAL.",
        "4. ¿Cómo invierto?": "Para invertir en el Fondo Mutual Abierto de PER CAPITAL debes descargar el app y registrate. Para invertir directamente en acciones o bonos debes acudir a una Casa de Bolsa autorizada."
    },
    "2. FONDO MUTUAL ABIERTO": {
        "1. ¿Qué es un Fondo Mutual?": "Es un instrumento de inversion en grupo donde varias personas ponen dinero en un fondo que es gestionado por expertos y esta disenado para ser de bajo riesgo, dirigido a pequenos inversionistas con poca experiencia.",
        "2. ¿Qué es una Unidad de Inversion?": "Es una “porcion” del fondo. Cuando inviertes adquieres unidades que representan tu parte del fondo.",
        "3. ¿Qué es el VUI?": "El Valor de la Unidad de Inversion (VUI) es el precio de una Unidad de Inversion. Si el VUI sube tu inversion gana valor. Se calcula diariamente al cierre del dia y depende del comportamiento de las inversiones del fondo.",
        "4. ¿Cómo invierto?": "Descarga el app para Android y IOS, registrate al 100%, espera tu aprobacion y suscribe Unidades de Inversion cuando quieras y cuantas veces desees.",
        "5. ¿Cuál es el monto mínimo de inversion?": "1 Unidad de Inversion.",
        "6. ¿Cómo gano?": "Ganas por apreciacion (subida del VUI) o por dividendo (en caso de que sea decretado).",
        "7. ¿En cuánto tiempo gano?": "Por ser un instrumento de renta variable a largo plazo, se recomienda medir resultados trimestrales.",
        "8. ¿Dónde consigo más información?": "En los prospectos y hojas de terminos en www.per-capital.com."
    },
    "3. REGISTRO": {
        "1. ¿Cómo me registro?": "Descarga el app, completa 100% de los datos, acepta los contratos, sube tus recaudos como Cedula de Identidad y Selfie y espera tu aprobacion.",
        "2. ¿Cuánto tarda mi aprobación?": "De 2 a 5 dias habiles siempre que hayas completado 100% de registro y recaudos.",
        "3. ¿Qué hago si no me aprueban?": "Revisa que hayas completado 100% del registro o contactanos.",
        "4. ¿Puedo invertir si soy menor de edad?": "Debes dirigirte a nuestras oficinas y registrarte con tu representante legal.",
        "5. ¿Puedo modificar alguno de mis datos?": "Si, pero por exigencia de ley entras nuevamente en revision.",
        "6. ¿Debo tener cuenta en la Caja Venezolana?": "No, para invertir en nuestro Fondo Mutual Abierto no es necesaria la cuenta en la CVV."
    },
    "4. SUSCRIPCIÓN": {
        "1. ¿Cómo suscribo (compro)?": "Haz click en Negociacion > Suscripcion > Monto a invertir > Suscribir > Metodo de Pago. Recuerda pagar de TU cuenta bancaria y subir comprobante.",
        "2. ¿Cómo pago mi suscripción?": "Debes pagar desde TU cuenta bancaria via Pago Movil. Y recuerda subir comprobante y que no se aceptan pagos de terceros.",
        "3. ¿Puede pagar alguien por mí?": "No, la ley prohibe los pagos de terceros. Siempre debes pagar desde tu cuenta bancaria.",
        "4. ¿Cómo veo mi inversión?": "En el Home en la seccion Mi Cuenta.",
        "5. ¿Cuándo veo mi inversión?": "Al cierre del sistema entre las 5 pm y 7 pm despues del cierre de la publicacion de tasas del Banco Central de Venezuela.En dias habiles de Mercado.",
        "6. ¿Cuáles son las comisiones?": "3% flat Suscripcion, 3% flat Rescate y 5% anual Administracion.",
        "7. ¿Qué hago después de suscribir?": "Monitorea tu inversion desde el app.",
        "8. ¿Puedo invertir el monto que quiera?": "Si, puedes invertir el monto que desees.",
        "9. ¿Puedo invertir cuando quiera?": "Si, puedes invertir cuando quieras, las veces que quieras."
    },
    "5. RESCATE": {
        "1. ¿Cómo rescato (vendo)?": "Haz click en Negociacion > Rescate > Unidades a Rescatar > Rescatar. Recuerda se enviaran fondos a TU cuenta bancaria.",
        "2. ¿Cuándo me pagan mis rescates (ventas)?": "Al proximo dia habil bancario en horario de mercado.",
        "3. ¿Cómo veo el saldo de mi inversión?": "En el Home en la seccion Mi Cuenta.",
        "4. ¿Cuándo veo el saldo de mi inversión?": "Al cierre del sistema entre las 5 pm y 7 pm despues del cierre de la publicacion de tasas del Banco Central de Venezuela.En dias habiles de mercado.",
        "5. ¿Cuándo puedo Rescatar?": "Cuando tu quieras, puedes rescatar y retirarte del fondo.",
        "6. ¿Cuáles son las comisiones?": "3% flat Suscripcion, 3% flat Rescate y 5% anual Administracion."
    },
    "6. POSICIÓN (SALDO)": {
        "1. ¿Cuándo se actualiza mi posición (saldo)?": "Al cierre del sistema entre las 5 pm y 7 pm despues del cierre de la publicacion de tasas del Banco Central de Venezuela en dias habiles de mercado.",
        "2. ¿Por qué varía mi posición (saldo)?": "Tu saldo y rendimiento sube si suben los precios de las inversiones del fondo, se reciben dividendos o cupones y bajan si estos precios caen.",
        "3. ¿Dónde veo mi histórico?": "En la seccion Historial.",
        "4. ¿Dónde veo reportes?": "En la seccion Documentos > Reportes > Año > Trimestre."
    },
    "7. RIESGOS": {
        "1. ¿Cuáles son los riesgos al invertir?": "Todas las inversionbes estan sujetas a riesgos y la perdida de capital es posible. Agunos riesgos son: riesgo de mercado, riesgo pais, riesgo cambiario, riesgo sector, entre otros."
    },
    "8. SOPORTE": {
        "1. Estoy en revisión, ¿qué hago?": "Asegurate de haber completado 100% datos y recaudos y espera tu aprobacion. Si tarda mas de lo habitual contactanos.",
        "2. No me llega el SMS": "Asegurate de tener buena senal y de que hayas colocado correctamente un numero telefonico venezolano.",
        "3. No me llega el Correo": "Asegurate de no dejar espacios al final cuando escribiste tu correo electronico.",
        "4. No logro descargar el App": "Asegurate de que tu app store este configurada en la region de Venezuela.",
        "5. No me abre el App": "Asegurate de tener la version actualizada y que tu tienda de apps este configurada en la region de Venezuela.",
        "6. ¿Cómo recupero mi clave?": "Seleccione Recuperar, te legara una clave temporal para ingresar y luego actualiza tu nueva clave."
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