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

# ==================== Base de Conocimiento PER CAPITAL ====================
QA_CATEGORIZED = {
    "per_capital": {
        "title": "🏦 PER CAPITAL",
        "questions": {
            "que_es_per_capital": {
                "title": "¿Qué es Per Capital?",
                "answer": "Es un grupo de empresas del Mercado de Valores Venezolano reguladas por la SUNAVAL."
            },
            "quien_regula": {
                "title": "¿Quién regula a PER CAPITAL?",
                "answer": "La SUNAVAL (Superintendencia Nacional de Valores)"
            },
            "que_es_sunaval": {
                "title": "¿Qué es la SUNAVAL?",
                "answer": "Es quien protege a inversionistas y regula a intermediarios y emisores del Mercado de Valores venezolano"
            },
            "bolsa_valores": {
                "title": "¿Qué es la Bolsa de Valores de Caracas?",
                "answer": "Es el lugar donde se compran y venden bonos, acciones y otros instrumentos de manera ordenada a través de las Casas de Bolsa y está regulada por la SUNAVAL"
            },
            "como_invertir_inicial": {
                "title": "¿Cómo invierto?",
                "answer": "Para invertir en el Fondo Mutual Abierto de PER CAPITAL debes descargar el app, registrarte, subir recaudos y colocar tus órdenes de compra."
            }
        }
    },
    "fondo_mutual": {
        "title": "💰 FONDO MUTUAL ABIERTO",
        "questions": {
            "que_es_fondo_mutual": {
                "title": "¿Qué es un Fondo Mutual?",
                "answer": "Es un instrumento de inversión en grupo donde varias personas ponen dinero en un fondo que es gestionado por expertos y está diseñado para ser diversificado, de bajo riesgo y dirigido a pequeños inversionistas con poca experiencia"
            },
            "unidad_inversion": {
                "title": "¿Qué es una Unidad de Inversión?",
                "answer": "Es una \"porción\" del fondo. Cuando inviertes adquieres unidades que representan tu parte del fondo."
            },
            "que_es_vui": {
                "title": "¿Qué es el VUI?",
                "answer": "El Valor de la Unidad de Inversión (VUI) es el precio de una Unidad de Inversión. Si el VUI sube tu inversión gana valor. Se calcula diariamente al cierre del día y depende del comportamiento de las inversiones del fondo."
            },
            "como_invertir_fondo": {
                "title": "¿Cómo invierto en el fondo?",
                "answer": "Descarga el app para Android y iOS, regístrate, sube recaudos, acepta los contratos, espera tu aprobación y suscribe Unidades de Inversión cuando quieras y cuantas veces desees"
            },
            "monto_minimo": {
                "title": "¿Cuál es el monto mínimo de inversión?",
                "answer": "1 Unidad de Inversión"
            },
            "como_gano": {
                "title": "¿Cómo gano dinero?",
                "answer": "Ganas por apreciación (subida del VUI) o por dividendo (en caso de que sea decretado)"
            },
            "tiempo_ganancia": {
                "title": "¿En cuánto tiempo gano?",
                "answer": "Ganas a largo plazo, se recomienda medir resultados trimestralmente"
            },
            "mas_informacion": {
                "title": "¿Dónde consigo más información?",
                "answer": "En los prospectos y hojas de términos en www.per-capital.com"
            }
        }
    },
    "app_uso": {
        "title": "📱 USO DE LA APP",
        "questions": {
            "comprar_acciones_bonos": {
                "title": "¿Puedo comprar acciones y bonos?",
                "answer": "No, nuestra app es únicamente para invertir en nuestro Fondo Mutual Abierto. Pronto saldrá la nueva versión de nuestra app para negociar"
            },
            "como_registro": {
                "title": "¿Cómo me registro?",
                "answer": "Descarga el app, completa 100% de los datos, acepta los contratos, sube tus recaudos como Cédula de Identidad y Selfie y espera tu aprobación."
            },
            "tiempo_aprobacion": {
                "title": "¿Cuánto tarda mi aprobación?",
                "answer": "De 2 a 5 días hábiles siempre que hayas completado 100% de registro y recaudos"
            },
            "no_aprobacion": {
                "title": "¿Qué hago si no me aprueban?",
                "answer": "Revisa que hayas completado 100% del registro y recaudos, sino contáctanos en SOPORTE"
            },
            "menor_edad": {
                "title": "¿Puedo invertir si soy menor de edad?",
                "answer": "Debes dirigirte a nuestras oficinas y registrarte con tu representante legal"
            },
            "modificar_datos": {
                "title": "¿Puedo modificar alguno de mis datos?",
                "answer": "Sí, pero por exigencia de la ley entras nuevamente en revisión"
            },
            "cuenta_caja_venezolana": {
                "title": "¿Debo tener cuenta en la Caja Venezolana?",
                "answer": "No, para invertir en nuestro Fondo Mutual Abierto no es necesaria la cuenta en la CVV"
            },
            "como_suscribir": {
                "title": "¿Cómo suscribo (compro)?",
                "answer": "Haz click en Negociación > Suscripción > Monto a invertir > Suscribir > Método de Pago. Recuerda pagar desde TU cuenta bancaria y subir comprobante de pago"
            },
            "como_pagar": {
                "title": "¿Cómo pago mi suscripción?",
                "answer": "Debes pagar desde TU cuenta bancaria vía Pago Móvil. Y recuerda subir comprobante. IMPORTANTE: no se aceptan pagos de terceros."
            },
            "pago_terceros": {
                "title": "¿Puede pagar alguien por mí?",
                "answer": "No, la ley prohíbe los pagos de terceros. Siempre debes pagar desde tu cuenta bancaria."
            },
            "ver_inversion": {
                "title": "¿Cómo veo mi inversión?",
                "answer": "En el Home en la sección Mi Cuenta"
            },
            "cuando_ver_inversion": {
                "title": "¿Cuándo veo mi inversión?",
                "answer": "Al cierre del sistema en días hábiles bancarios después del cierre de mercado y la publicación de tasas del Banco Central de Venezuela."
            },
            "comisiones": {
                "title": "¿Cuáles son las comisiones?",
                "answer": "3% flat Suscripción, 3% flat Rescate y 5% anual Administración"
            },
            "como_rescatar": {
                "title": "¿Cómo rescato (vendo)?",
                "answer": "Haz click en Negociación > Rescate > Unidades a Rescatar > Rescatar. Recuerda se enviarán fondos a TU cuenta bancaria"
            },
            "cuando_pagan_rescates": {
                "title": "¿Cuándo me pagan mis rescates?",
                "answer": "Al próximo día hábil bancario en horario de mercado"
            },
            "cuando_rescatar": {
                "title": "¿Cuándo puedo rescatar?",
                "answer": "Cuando tú quieras, y se liquida en días hábiles bancarios."
            },
            "actualizacion_posicion": {
                "title": "¿Cuándo se actualiza mi posición?",
                "answer": "Al cierre del sistema en días hábiles bancarios después del cierre de mercado y la publicación de tasas del Banco Central de Venezuela."
            },
            "por_que_varia_posicion": {
                "title": "¿Por qué varía mi posición?",
                "answer": "Tu saldo y rendimiento sube si suben los precios de las inversiones del fondo, se reciben dividendos o cupones y bajan si estos precios caen."
            },
            "ver_historico": {
                "title": "¿Dónde veo mi histórico?",
                "answer": "En la sección Historial"
            },
            "ver_reportes": {
                "title": "¿Dónde veo reportes?",
                "answer": "En la sección Documentos > Reportes > Año > Trimestre"
            }
        }
    },
    "riesgos_soporte": {
        "title": "⚠️ RIESGOS Y SOPORTE",
        "questions": {
            "riesgos_inversion": {
                "title": "¿Cuáles son los riesgos al invertir?",
                "answer": "Todas las inversiones están sujetas a riesgos y la pérdida de capital es posible. Algunos riesgos son: riesgo de mercado, riesgo país, riesgo cambiario, riesgo sector, entre otros."
            },
            "en_revision": {
                "title": "Estoy en revisión, ¿qué hago?",
                "answer": "Asegúrate de haber completado 100% datos y recaudos y espera tu aprobación. Si tarda más de lo habitual contáctanos en SOPORTE"
            },
            "no_llega_sms": {
                "title": "No me llega el SMS",
                "answer": "Asegúrate de tener buena señal y de que hayas colocado correctamente un número telefónico venezolano"
            },
            "no_llega_correo": {
                "title": "No me llega el correo",
                "answer": "Asegúrate de no dejar espacios al final cuando escribiste tu correo electrónico"
            },
            "no_descarga_app": {
                "title": "No logro descargar el App",
                "answer": "Asegúrate de que tu app store esté configurada en la región de Venezuela"
            },
            "no_abre_app": {
                "title": "No me abre el App",
                "answer": "Asegúrate de tener la versión actualizada y que tu tienda de apps esté configurada en la región de Venezuela"
            },
            "recuperar_clave": {
                "title": "¿Cómo recupero mi clave?",
                "answer": "Selecciona Recuperar, te llegará una clave temporal para ingresar y luego actualiza tu nueva clave"
            }
        }
    }
}

# Variable global para almacenar el estado de la conversación
conversation_state: Dict[str, str] = {}

# ==================== Funciones para listas dinámicas ====================

def get_category_by_id(category_id: str) -> Optional[Dict[str, Any]]:
    """Obtiene una categoría por su ID."""
    return QA_CATEGORIZED.get(category_id)

def get_answer_by_ids(category_id: str, question_id: str) -> str:
    """Obtiene la respuesta usando IDs de categoría y pregunta."""
    category = get_category_by_id(category_id)
    if not category:
        return "Lo siento, la categoría seleccionada no es válida."
   
    question = category["questions"].get(question_id)
    if not question:
        return "Lo siento, la pregunta seleccionada no es válida."
   
    return question["answer"]

# ==================== Funciones para enviar mensajes ====================

async def send_initial_menu_with_buttons(to_msisdn: str) -> Dict[str, Any]:
    """Envía un menú interactivo con dos botones para la selección inicial."""
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

async def send_main_menu_list(to_msisdn: str) -> Dict[str, Any]:
    """Envía el menú principal usando lista dinámica."""
   
    # Preparar las secciones de la lista
    sections = []
    for category_id, category_data in QA_CATEGORIZED.items():
        sections.append({
            "title": category_data["title"],
            "rows": [
                {
                    "id": f"cat_{category_id}",
                    "title": "Ver preguntas",
                    "description": f"Explorar {category_data['title'].split(' ', 1)[1] if ' ' in category_data['title'] else category_data['title']}"
                }
            ]
        })
   
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {
                "type": "text",
                "text": "📋 Menú Principal"
            },
            "body": {
                "text": "Selecciona la categoría sobre la que necesitas información:\n\n💡 Después de elegir una categoría, podrás ver todas las preguntas disponibles en una lista."
            },
            "footer": {
                "text": "Per Capital - Tu inversión, nuestro compromiso"
            },
            "action": {
                "button": "Ver categorías",
                "sections": sections
            }
        }
    }
   
    # Limpiar el estado de la conversación
    if to_msisdn in conversation_state:
        del conversation_state[to_msisdn]
        logging.info(f"Estado de conversación limpiado para {to_msisdn}")
   
    return await _post_messages(payload)

async def send_questions_list(to_msisdn: str, category_id: str) -> Dict[str, Any]:
    """Envía la lista de preguntas para una categoría específica."""
    category = get_category_by_id(category_id)
    if not category:
        await send_text(to_msisdn, "❌ Categoría no válida.")
        await send_main_menu_list(to_msisdn)
        return {}

    # Preparar las filas de preguntas
    rows = []
    for question_id, question_data in category["questions"].items():
        # Usar solo el título completo, sin descripción adicional
        full_title = question_data["title"]
       
        # Crear título para mostrar (máximo 24 caracteres para el título)
        if len(full_title) > 24:
            display_title = full_title[:21] + "..."
        else:
            display_title = full_title
           
        rows.append({
            "id": f"q_{category_id}_{question_id}",
            "title": display_title
            # NO incluir description para evitar duplicación
        })

    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {
                "type": "text",
                "text": category["title"]
            },
            "body": {
                "text": f"Selecciona la pregunta que te interesa:\n\n💡 Después de leer la respuesta, podrás volver al menú principal."
            },
            "footer": {
                "text": "Escribe 'volver' para regresar al menú"
            },
            "action": {
                "button": "Ver preguntas",
                "sections": [
                    {
                        "title": "Preguntas frecuentes",
                        "rows": rows
                    }
                ]
            }
        }
    }
   
    # Guardar el estado de la categoría actual
    conversation_state[to_msisdn] = category_id
    logging.info(f"Estado guardado para {to_msisdn}: categoría {category_id}")
   
    return await _post_messages(payload)

# ==================== Utilidades WhatsApp ====================

def verify_signature(signature: Optional[str], body: bytes) -> bool:
    """Verifica la firma HMAC-SHA256 de la solicitud de WhatsApp."""
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
    """Función auxiliar para enviar mensajes a través de la API de WhatsApp."""
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
    """Envía un mensaje de texto simple."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "text",
        "text": {"body": text}
    }
    return await _post_messages(payload)

# ==================== Procesamiento de mensajes ====================

def is_back_command(text: str) -> bool:
    """Verifica si el mensaje es un comando para volver al menú principal."""
    back_keywords = ["volver", "menu", "menú", "principal", "inicio", "back", "0"]
    return text.strip().lower() in back_keywords

async def process_text_message(from_msisdn: str, message_text: str) -> None:
    """Procesa los mensajes de texto del usuario."""
    text_clean = message_text.strip()
   
    logging.info(f"📝 Procesando mensaje de texto de {from_msisdn}: '{text_clean}'")
   
    # Verificar si es un comando para volver al menú principal
    if is_back_command(text_clean):
        logging.info(f"🔄 Usuario {from_msisdn} solicitó volver al menú principal")
        await send_main_menu_list(from_msisdn)
        return
   
    # Para cualquier otro mensaje de texto, mostrar el menú inicial con botones
    logging.info(f"🔄 Enviando menú inicial con botones a {from_msisdn}")
    await send_initial_menu_with_buttons(from_msisdn)

async def process_interactive_message(from_msisdn: str, interactive_data: Dict[str, Any]) -> None:
    """Procesa los mensajes interactivos (respuestas de botones y listas)."""
    interactive_type = interactive_data.get("type")
   
    if interactive_type == "button_reply":
        button_reply = interactive_data.get("button_reply", {})
        button_id = button_reply.get("id")
        button_title = button_reply.get("title")
       
        logging.info(f"🔘 Usuario {from_msisdn} presionó botón: {button_id} ({button_title})")
       
        if button_id == "bot_qa":
            await send_text(from_msisdn, "🤖 *Perfecto!* Has seleccionado el asistente virtual.\n\nAhora te mostraré las categorías disponibles:")
            await send_main_menu_list(from_msisdn)
        elif button_id == "human_support":
            await send_text(from_msisdn,
                "👨‍💼 *Soporte Humano Activado*\n\n"
                "Gracias por contactarnos. Un miembro especializado de nuestro equipo de Per Capital "
                "se pondrá en contacto contigo a la brevedad posible.\n\n"
                "📞 También puedes llamarnos directamente si tu consulta es urgente.\n\n"
                "Esta conversación automática ha finalizado. ¡Que tengas un excelente día! 🙋‍♀️")
            if from_msisdn in conversation_state:
                del conversation_state[from_msisdn]
        else:
            logging.warning(f"⚠️ ID de botón desconocido: {button_id}")
            await send_initial_menu_with_buttons(from_msisdn)
   
    elif interactive_type == "list_reply":
        list_reply = interactive_data.get("list_reply", {})
        list_id = list_reply.get("id")
        list_title = list_reply.get("title")
       
        logging.info(f"📋 Usuario {from_msisdn} seleccionó de lista: {list_id} ({list_title})")
       
        if list_id.startswith("cat_"):
            # Selección de categoría
            category_id = list_id.replace("cat_", "")
            await send_questions_list(from_msisdn, category_id)
           
        elif list_id.startswith("q_"):
            # Selección de pregunta
            parts = list_id.replace("q_", "").split("_", 1)
            if len(parts) == 2:
                category_id, question_id = parts
                
                # Obtener la pregunta y respuesta de la base de datos
                category = get_category_by_id(category_id)
                if category and question_id in category["questions"]:
                    question_title = category["questions"][question_id]["title"]
                    answer = category["questions"][question_id]["answer"]
                    
                    # Enviar la pregunta y respuesta sin duplicación
                    await send_text(from_msisdn, f"❓ *{question_title}*\n\n✅ *Respuesta:*\n{answer}")
                else:
                    await send_text(from_msisdn, "❌ Error: Pregunta no encontrada.")
                    await send_main_menu_list(from_msisdn)
                    return
               
                # Pequeña pausa antes de enviar opciones
                import asyncio
                await asyncio.sleep(2)
               
                # Ofrecer opciones para continuar
                await send_text(from_msisdn, "📋 ¿Necesitas información sobre otro tema?")
                await send_main_menu_list(from_msisdn)
            else:
                logging.error(f"❌ Formato de ID de pregunta inválido: {list_id}")
                await send_text(from_msisdn, "❌ Error al procesar la pregunta seleccionada.")
                await send_main_menu_list(from_msisdn)
        else:
            logging.warning(f"⚠️ ID de lista desconocido: {list_id}")
            await send_main_menu_list(from_msisdn)
    else:
        logging.warning(f"⚠️ Tipo de mensaje interactivo desconocido: {interactive_type}")
        await send_initial_menu_with_buttons(from_msisdn)

# ==================== Endpoints de FastAPI ====================

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
):
    """Endpoint para la verificación del webhook de WhatsApp."""
    logging.info(f"🔍 Verificando webhook - Mode: {hub_mode}, Token: {hub_verify_token}")
   
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logging.info("✅ Verificación de webhook exitosa")
        return PlainTextResponse(content=hub_challenge or "", status_code=200)
   
    logging.error("❌ Fallo en la verificación del webhook - Token o modo incorrectos")
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhook")
async def receive_webhook(request: Request):
    """Endpoint principal para recibir mensajes de WhatsApp."""
    try:
        body_bytes = await request.body()
        signature = request.headers.get("X-Hub-Signature-256")
       
        if not verify_signature(signature, body_bytes):
            logging.error("❌ Firma de solicitud inválida")
            raise HTTPException(status_code=403, detail="Invalid signature")
       
        data = await request.json()
        logging.info(f"📨 Webhook recibido: {json.dumps(data, indent=2)}")
       
        if data.get("object") != "whatsapp_business_account":
            logging.info("ℹ️ Notificación ignorada - No es de WhatsApp Business")
            return Response(status_code=200)
       
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
               
                messages = value.get("messages")
                if not messages:
                    logging.info("ℹ️ No hay mensajes en esta notificación")
                    continue
               
                for message in messages:
                    from_msisdn = message.get("from")
                    message_type = message.get("type")
                    message_id = message.get("id")
                   
                    logging.info(f"📬 Procesando mensaje {message_id} de {from_msisdn} (tipo: {message_type})")
                   
                    if message_type == "interactive":
                        interactive_data = message.get("interactive", {})
                        await process_interactive_message(from_msisdn, interactive_data)
                       
                    elif message_type == "text":
                        text_data = message.get("text", {})
                        message_text = text_data.get("body", "")
                        await process_text_message(from_msisdn, message_text)
                       
                    else:
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
    """Endpoint de salud para verificar que el servicio está funcionando."""
    return {
        "status": "ok",
        "service": "WhatsApp Bot Per Capital - Lista Dinámica",
        "version": "3.0",
        "categories": len(QA_CATEGORIZED),
        "active_conversations": len(conversation_state)
    }

@app.get("/status")
async def status_endpoint():
    """Endpoint de estado detallado para monitoreo."""
    return {
        "service_status": "running",
        "environment_variables": {
            "VERIFY_TOKEN": "✅" if VERIFY_TOKEN else "❌",
            "WHATSAPP_TOKEN": "✅" if WHATSAPP_TOKEN else "❌",
            "PHONE_NUMBER_ID": "✅" if PHONE_NUMBER_ID else "❌",
            "APP_SECRET": "✅" if APP_SECRET else "❌"
        },
        "qa_categories": list(QA_CATEGORIZED.keys()),
        "total_questions": sum(len(cat["questions"]) for cat in QA_CATEGORIZED.values()),
        "active_conversations": len(conversation_state),
        "graph_api_version": GRAPH_API_VERSION
    }

@app.get("/clear-conversations")
async def clear_conversations():
    """Endpoint para limpiar todas las conversaciones activas."""
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
    """Maneja todas las excepciones no capturadas."""
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
    print("🚀 Iniciando WhatsApp Bot Per Capital con Listas Dinámicas...")
    print(f"📊 Categorías cargadas: {len(QA_CATEGORIZED)}")
    total_questions = 0
    for category_id, category_data in QA_CATEGORIZED.items():
        questions_count = len(category_data["questions"])
        total_questions += questions_count
        print(f"   • {category_data['title']}: {questions_count} preguntas")
    print(f"📝 Total de preguntas: {total_questions}")
    print("✅ Bot con listas dinámicas listo para recibir mensajes!")
