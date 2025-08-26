import os
import hmac
import hashlib
import json
import re
from typing import Optional, Any, Dict, List
import logging
import asyncio

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
    "Inversiones": {
        "¿Como puedo invertir?": "Primero debe estar registrado y aprobado en la aplicación, luego Ingresa en la opción de negociación >Selecciona suscripción > ingresa el monto que desea invertir > hacer click en suscribir > ingresa método de pago. Una vez pagado se sube el comprobante y en el transcurso del día de hace efectivo al cierre del.dia o al día siguiente hábil.",
        "¿Que es el Fondo Mutual Abierto?": "El Fondo Mutual Abierto es una cesta llena de diferentes inversiones (acciones, bonos, etc.). Al suscribir estaría comprando acciones y renta fija indirectamente. Puedes ver en que esta diversificado el portafolio dentro de la aplicación.",
        "¿En que puedo invertir?": "Por ahora puede invertir en el fondo mutual abierto que posee un portafolio diversificado en bolívares en acciones que cotizan en la bolsa de valores y en dólares en papeles comerciales o renta fija.",
        "¿Que son las Unidades de Inversion (UI)?": "Las Unidades de Inversión (UI) de un fondo mutual abierto son instrumentos que representan una participación proporcional en el patrimonio de dicho fondo. Cada Ul representa una porción del total del fondo, y su valor fluctúa según el rendimiento de los activos que componen el fondo.",
        "¿Que es el valor de la unidad de inversión (VUI)?": "El Valor de la Unidad de Inversión (VUI) es el precio por unidad que se utiliza para calcular el valor de una inversión. Es el valor de mercado de cada una de las acciones o unidades de inversión que representan una participación en el patrimonio del fondo, y que cambian a diario.",
        "¿Por que baja el rendimiento?": "El valor de tu inversión está directamente ligado al valor total de los activos del fondo. Si el valor de las inversiones dentro del fondo disminuye, el valor de tu participación también disminuirá. Recuerda que el horizonte de inversión de los Fondos Mutuales es a largo plazo.",
        "¿QUE HAGO AHORA?": "Una vez suscrito no debe hacer más nada, solo monitorear su inversión, ya que nosotros gestionamos activamente las inversiones. Puede observar en que esta invertido su dinero dentro de la aplicación en la opción de portafolio.",
        "¿Comisiones?": "Las comisiones son de 3% por suscripción y 5% de administración anualizado.",
        "¿Desde cuanto puedo invertir?": "Desde un Bolivar.",
        "¿En cuanto tiempo veo ganancias?": "Si su horizonte de inversión es a corto plazo no le aconsejamos participar en el Fondo Mutual Abierto. Le sugerimos tenga paciencia ya que los rendimientos esperados en los Fondos Mutuales se esperan a largo plazo.",
        "¿Como compro acciones?": "Próximamente podrá comprar y vender acciones por la aplicación, mientras tanto puede invertir en unidades de inversión en el Fondo Mutual Abierto, cuyo portafolio está compuesto por algunas acciones que están en la bolsa de valores.",
    },
    "Retiros y Transacciones": {
        "¿Como hago un retiro?": "Selecciona rescate > ingresa las unidades de inversión a rescatar > luego calcula selección > selecciona rescatar > siga los pasos que indique la app.",
        "¿Nunca he rescatado?": "Si usted no ha realizado algún rescate, haga caso omiso al correo enviado. Le sugerimos que ingrese en la aplicación y valide sus fondos.",
        "¿Cuanto puedo retirar?": "Desde una Unidad de Inversion.",
        "¿Como rescato?": "Selecciona rescate > ingresa las unidades de inversión a rescatar > luego calcula selección > selecciona rescatar > siga los pasos.",
    },
    "Problemas con la Cuenta": {
        "¿Mi usuario esta en revision que debo hacer?": "Estimado inversionista por favor enviar numero de cedula para apoyarle. (Se verifica que tenga documentación e información completa y se activa).",
        "¿Como recupero la clave?": "Una vez seleccione la opción de 'Recuperar' y le llegara una clave temporal. Deberá ingresarla como nueva clave de su usuario y luego la aplicación le solicitará una nueva clave que deberá confirmar.",
        "¿Por que tardan tanto en responder o en aprobar?": "Debido al alto tráfico estamos presentando retrasos en la aprobación de registros, estamos trabajando arduamente para aprobarte y que empieces a invertir. Por favor envianos tu cedula escaneada a este correo.",
        "¿Aprobado?": "Su usuario ya se encuentra APROBADO. Recuerde que, si realiza alguna modificación de su información, entra en revisión, por ende, debe notificarnos para apoyarle. Si realiza una suscripción antes de las 12 del mediodía la vera reflejada al cierre del día aproximadamente 5-6 de la tarde.",
        "¿No me llega el mensaje de texto?": "Por favor intente en otra locación, si persiste el error intente en unas horas o el dia de mañana. En caso de no persistir el error, por favor, intente con otro numero de teléfono y luego lo actualizamos en sistema.",
    },
    "Otros Tipos de Inversión": {
        "¿Como invierto en dolares?": "Puede invertir en un Papel Comercial, que son instrumentos de deuda a corto plazo (menos de un año) emitidos por las empresas en el mercado de valores.",
        "¿Como invierto en un papel comercial?": "Debe estar registrado con Per Capital y en la Caja Venezolana con cedula, RIF y constancia de trabajo. Adjunto encontrara el link de la Caja Venezolana, una vez termine el registro nos avisa para apoyarle, el depositante deber ser Per Capital.",
        "¿Ya me registre en la Caja Venezolana?": "Por ahora no hace falta estar registrado en la caja venezolana para invertir en el fondo mutual abierto. Próximamente podrá comprar y vender acciones por la aplicación, mientras tanto puede invertir en unidades de inversión en el Fondo Mutual Abierto.",
        "¿Informacion del fondo mutual abierto y acciones?": "Por ahora puede invertir en el fondo mutual abierto, en el cual posee un portafolio diversificado en acciones que cotizan en la bolsa de valores de caracas y en papeles comerciales. El portafolio podrá verlo dentro de la aplicación en detalle.",
    }
}

# Variable global para almacenar el estado de la conversación
# En producción, considera usar Redis o una base de datos para persistencia
conversation_state: Dict[str, str] = {}

# ==================== Funciones de la lógica del menú ====================

def get_category_by_name(category_name: str) -> Optional[Dict[str, str]]:
    """
    Obtiene un submenú de preguntas por el nombre de la categoría.
    
    Args:
        category_name: Nombre de la categoría
    
    Returns:
        Dict con preguntas de la categoría, o None si no existe
    """
    return QA_CATEGORIZED.get(category_name)


def get_answer_by_category_and_question(category_name: str, question_text: str) -> Optional[str]:
    """
    Obtiene la respuesta de la base de conocimiento usando el nombre de categoría y pregunta.
    
    Args:
        category_name: Nombre de la categoría
        question_text: Texto de la pregunta
    
    Returns:
        Respuesta correspondiente o None si no existe
    """
    category_questions = get_category_by_name(category_name)
    if category_questions:
        return category_questions.get(question_text)
    return None


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


async def send_categories_buttons(to_msisdn: str) -> Dict[str, Any]:
    """
    Envía el menú principal de categorías con botones interactivos.
    """
    # Limpiar el estado de la conversación cuando se envía el menú principal
    if to_msisdn in conversation_state:
        del conversation_state[to_msisdn]
        logging.info(f"Estado de conversación limpiado para {to_msisdn}")
    
    # Crear botones para cada categoría (máximo 3 botones por mensaje en WhatsApp)
    categories = list(QA_CATEGORIZED.keys())
    
    # Primer grupo de categorías (3 botones)
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "text",
                "text": "📋 Menú Principal - Per Capital"
            },
            "body": {
                "text": "Por favor, elige una categoría para continuar con tu consulta:"
            },
            "footer": {
                "text": "Selecciona una opción"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": f"category_{categories[0]}",
                            "title": f"💰 {categories[0]}"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": f"category_{categories[1]}",
                            "title": f"💳 {categories[1]}"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "more_categories",
                            "title": "➡️ Más opciones"
                        }
                    }
                ]
            }
        }
    }
    return await _post_messages(payload)


async def send_more_categories_buttons(to_msisdn: str) -> Dict[str, Any]:
    """
    Envía las categorías restantes con botones interactivos.
    """
    categories = list(QA_CATEGORIZED.keys())
    
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "text",
                "text": "📋 Más Categorías"
            },
            "body": {
                "text": "Aquí tienes las categorías adicionales disponibles:"
            },
            "footer": {
                "text": "Selecciona una opción"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": f"category_{categories[2]}",
                            "title": f"🔐 {categories[2]}"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": f"category_{categories[3]}",
                            "title": f"📈 {categories[3]}"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "back_to_main",
                            "title": "⬅️ Volver"
                        }
                    }
                ]
            }
        }
    }
    return await _post_messages(payload)


async def send_questions_list(to_msisdn: str, category_name: str) -> Dict[str, Any]:
    """
    Envía las preguntas de una categoría como List Message interactivo.
    """
    category_questions = get_category_by_name(category_name)
    if not category_questions:
        await send_text(to_msisdn, "❌ Categoría no encontrada.")
        await send_categories_buttons(to_msisdn)
        return {}

    # Guardar el estado de la categoría actual para el usuario
    conversation_state[to_msisdn] = category_name
    logging.info(f"Estado guardado para {to_msisdn}: categoría {category_name}")
    
    # Crear las filas de la lista (máximo 10 elementos)
    rows = []
    for i, question in enumerate(list(category_questions.keys())[:10], 1):
        rows.append({
            "id": f"question_{category_name}_{question}",
            "title": question[:24],  # WhatsApp limita el título a 24 caracteres
            "description": question if len(question) <= 72 else question[:69] + "..."  # Descripción hasta 72 caracteres
        })

    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {
                "type": "text",
                "text": f"📂 {category_name}"
            },
            "body": {
                "text": "Selecciona la pregunta que mejor describe tu consulta:"
            },
            "footer": {
                "text": "Toca para ver todas las opciones"
            },
            "action": {
                "button": "Ver preguntas",
                "sections": [
                    {
                        "title": "Preguntas disponibles",
                        "rows": rows
                    }
                ]
            }
        }
    }
    
    return await _post_messages(payload)


async def send_search_notification(to_msisdn: str) -> Dict[str, Any]:
    """
    Envía una notificación de búsqueda antes de la respuesta.
    """
    return await send_text(to_msisdn, "Revisando en nuestra base de datos... ⏳")


async def send_back_to_menu_buttons(to_msisdn: str) -> Dict[str, Any]:
    """
    Envía botones para volver al menú o hacer otra consulta.
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "text",
                "text": "¿Qué deseas hacer ahora?"
            },
            "body": {
                "text": "¿Tienes alguna otra consulta o prefieres contactar con soporte?"
            },
            "footer": {
                "text": "Selecciona una opción"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "new_question",
                            "title": "❓ Nueva consulta"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "human_support",
                            "title": "👨‍💼 Soporte humano"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "end_chat",
                            "title": "✅ Finalizar"
                        }
                    }
                ]
            }
        }
    }
    return await _post_messages(payload)


# ==================== Utilidades WhatsApp ====================

def verify_signature(signature: Optional[str], body: bytes) -> bool:
    """
    Verifica la firma HMAC-SHA256 de la solicitud de WhatsApp.
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
    Procesa los mensajes de texto del usuario.
    Para mensajes de texto normales, enviará el menú inicial con botones.
    """
    text_clean = message_text.strip()
    
    logging.info(f"📝 Procesando mensaje de texto de {from_msisdn}: '{text_clean}'")
    
    # Verificar si es un comando para volver al menú principal
    if is_back_command(text_clean):
        logging.info(f"🔄 Usuario {from_msisdn} solicitó volver al menú principal")
        await send_categories_buttons(from_msisdn)
        return
    
    # Para cualquier otro mensaje de texto, enviar menú inicial con botones
    logging.info(f"🔄 Enviando menú inicial con botones a {from_msisdn}")
    await send_initial_menu_with_buttons(from_msisdn)


async def process_interactive_message(from_msisdn: str, interactive_data: Dict[str, Any]) -> None:
    """
    Procesa los mensajes interactivos (respuestas de botones y listas).
    """
    interaction_type = interactive_data.get("type")
    
    if interaction_type == "button_reply":
        await process_button_reply(from_msisdn, interactive_data.get("button_reply", {}))
    elif interaction_type == "list_reply":
        await process_list_reply(from_msisdn, interactive_data.get("list_reply", {}))
    else:
        logging.warning(f"⚠️ Tipo de interacción desconocido: {interaction_type}")
        await send_initial_menu_with_buttons(from_msisdn)


async def process_button_reply(from_msisdn: str, button_reply: Dict[str, Any]) -> None:
    """
    Procesa las respuestas de botones interactivos.
    """
    button_id = button_reply.get("id", "")
    button_title = button_reply.get("title", "")
    
    logging.info(f"🔘 Usuario {from_msisdn} presionó botón: {button_id} ({button_title})")
    
    if button_id == "bot_qa":
        # Iniciar flujo de Q&A
        await send_text(from_msisdn, "🤖 *Perfecto!* Has seleccionado el asistente virtual.\n\nAhora te mostraré las categorías disponibles:")
        await send_categories_buttons(from_msisdn)
        
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
            
    elif button_id.startswith("category_"):
        # Selección de categoría
        category_name = button_id.replace("category_", "")
        logging.info(f"📂 Usuario {from_msisdn} seleccionó categoría: {category_name}")
        await send_questions_list(from_msisdn, category_name)
        
    elif button_id == "more_categories":
        # Mostrar más categorías
        await send_more_categories_buttons(from_msisdn)
        
    elif button_id == "back_to_main":
        # Volver al menú principal de categorías
        await send_categories_buttons(from_msisdn)
        
    elif button_id == "new_question":
        # Nueva consulta
        await send_categories_buttons(from_msisdn)
        
    elif button_id == "end_chat":
        # Finalizar conversación
        await send_text(from_msisdn, "✅ ¡Gracias por usar nuestro servicio!\n\nEn Per Capital siempre estamos aquí para ayudarte. ¡Que tengas un excelente día! 🏦✨")
        if from_msisdn in conversation_state:
            del conversation_state[from_msisdn]
    else:
        logging.warning(f"⚠️ ID de botón desconocido: {button_id}")
        await send_initial_menu_with_buttons(from_msisdn)


async def process_list_reply(from_msisdn: str, list_reply: Dict[str, Any]) -> None:
    """
    Procesa las respuestas de List Messages.
    """
    list_id = list_reply.get("id", "")
    list_title = list_reply.get("title", "")
    
    logging.info(f"📝 Usuario {from_msisdn} seleccionó de lista: {list_id} ({list_title})")
    
    if list_id.startswith("question_"):
        # Formato: "question_{category_name}_{question_text}"
        parts = list_id.split("_", 2)
        if len(parts) >= 3:
            category_name = parts[1]
            question_text = parts[2]
            
            # Enviar notificación de búsqueda
            await send_search_notification(from_msisdn)
            
            # Pequeña pausa para simular búsqueda
            await asyncio.sleep(2)
            
            # Obtener respuesta
            answer = get_answer_by_category_and_question(category_name, question_text)
            
            if answer:
                response_text = f"✅ *Respuesta:*\n\n{answer}"
                await send_text(from_msisdn, response_text)
                
                # Pequeña pausa antes de enviar opciones
                await asyncio.sleep(1)
                
                # Enviar opciones para continuar
                await send_back_to_menu_buttons(from_msisdn)
            else:
                await send_text(from_msisdn, "❌ Lo siento, no pude encontrar la respuesta a esa pregunta.")
                await send_categories_buttons(from_msisdn)
        else:
            logging.error(f"Formato de ID de lista inválido: {list_id}")
            await send_categories_buttons(from_msisdn)
    else:
        logging.warning(f"⚠️ ID de lista desconocido: {list_id}")
        await send_categories_buttons(from_msisdn)


# ==================== Endpoints de FastAPI ====================

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
):
    """
    Endpoint para la verificación del webhook de WhatsApp.
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
                        # Procesar mensajes interactivos (botones y listas)
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
        "version": "3.0 - Interactive Components",
        "categories": len(QA_CATEGORIZED),
        "active_conversations": len(conversation_state),
        "features": [
            "Interactive Buttons",
            "List Messages",
            "Search Notifications",
            "Dynamic Flow Control"
        ]
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
        "graph_api_version": GRAPH_API_VERSION,
        "interactive_features": {
            "button_messages": "✅ Enabled",
            "list_messages": "✅ Enabled", 
            "search_notifications": "✅ Enabled",
            "dynamic_flow": "✅ Enabled"
        }
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


@app.get("/test-message/{phone_number}")
async def test_message(phone_number: str):
    """
    Endpoint para probar el envío de mensajes (útil para desarrollo).
    """
    try:
        await send_initial_menu_with_buttons(phone_number)
        return {
            "status": "success",
            "message": f"Mensaje de prueba enviado a {phone_number}",
            "phone_number": phone_number
        }
    except Exception as e:
        logging.error(f"Error enviando mensaje de prueba: {e}")
        raise HTTPException(status_code=500, detail=f"Error sending test message: {str(e)}")


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
    print("🚀 Iniciando WhatsApp Bot Per Capital v3.0 - Interactive Components...")
    print(f"📊 Categorías de Q&A cargadas: {len(QA_CATEGORIZED)}")
    for category in QA_CATEGORIZED.keys():
        questions_count = len(QA_CATEGORIZED[category])
        print(f"   • {category}: {questions_count} preguntas")
    print("\n🎛️ Características interactivas habilitadas:")
    print("   ✅ Menú de bienvenida con botones")
    print("   ✅ Categorías con botones interactivos")
    print("   ✅ Preguntas con List Messages")
    print("   ✅ Notificaciones de búsqueda")
    print("   ✅ Flujo de conversación dinámico")
    print("\n✅ Bot listo para recibir mensajes interactivos!")