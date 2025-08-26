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

# ==================== Base de conocimiento ZENDESK Per Capital ====================
# Estructura actualizada con las preguntas y respuestas del documento ZENDESK
QA_ZENDESK = {
    "per_capital": {
        "title": "🏢 Per Capital",
        "questions": {
            "¿Qué es Per Capital?": "Es un grupo de empresas del Mercado de Valores Venezolano reguladas por la SUNAVAL.",
            "¿Quién regula a PER CAPITAL?": "La SUNAVAL (Superintendencia Nacional de Valores)",
            "¿Qué es la SUNAVAL?": "Es quien protege a inversionistas y regula a intermediarios y emisores del Mercado de Valores venezolano",
            "¿Qué es la Bolsa de Valores de Caracas?": "Es el lugar donde se compran y venden bonos, acciones y otros instrumentos de manera ordenada a través de las Casas de Bolsa y está regulada por la SUNAVAL",
            "¿Cómo invierto?": "Para invertir en el Fondo Mutual Abierto de PER CAPITAL debes descargar el app, registrarte, subir recaudos y colocar tus órdenes de compra."
        }
    },
    "fondo_mutual": {
        "title": "📊 Fondo Mutual Abierto",
        "questions": {
            "¿Qué es un Fondo Mutual?": "Es un instrumento de inversión en grupo donde varias personas ponen dinero en un fondo que es gestionado por expertos y está diseñado para ser diversificado, de bajo riesgo y dirigido a pequeños inversionistas con poca experiencia",
            "¿Qué es una Unidad de Inversión?": "Es una 'porción' del fondo. Cuando inviertes adquieres unidades que representan tu parte del fondo.",
            "¿Qué es el VUI?": "El Valor de la Unidad de Inversión (VUI) es el precio de una Unidad de Inversión. Si el VUI sube tu inversión gana valor. Se calcula diariamente al cierre del día y depende del comportamiento de las inversiones del fondo.",
            "¿Cómo invierto?": "Descarga el app para Android y iOS, regístrate, sube recaudos, acepta los contratos, espera tu aprobación y suscribe Unidades de Inversión cuando quieras y cuantas veces desees",
            "¿Cuál es el monto mínimo de inversión?": "1 Unidad de Inversión",
            "¿Cómo gano?": "Ganas por apreciación (subida del VUI) o por dividendo (en caso de que sea decretado)",
            "¿En cuánto tiempo gano?": "Ganas a largo plazo, se recomienda medir resultados trimestralmente",
            "¿Dónde consigo más información?": "En los prospectos y hojas de términos en www.per-capital.com"
        }
    },
    "app": {
        "title": "📱 Aplicación",
        "questions": {
            "¿Puedo comprar acciones y bonos?": "No, nuestra app es únicamente para invertir en nuestro Fondo Mutual Abierto. Pronto saldrá la nueva versión de nuestra app para negociar",
            "¿Cómo me registro?": "Descarga el app, completa 100% de los datos, acepta los contratos, sube recaudos como Cédula de Identidad y Selfie y espera tu aprobación.",
            "¿Cuánto tarda mi aprobación?": "De 2 a 5 días hábiles siempre que hayas completado 100% de registro y recaudos",
            "¿Qué hago si no me aprueban?": "Revisa que hayas completado 100% del registro y recaudos, sino contáctanos en SOPORTE",
            "¿Puedo invertir si soy menor de edad?": "Debes dirigirte a nuestras oficinas y registrarte con tu representante legal",
            "¿Puedo modificar alguno de mis datos?": "Sí, pero por exigencia de la ley entras nuevamente en revisión",
            "¿Debo tener cuenta en la Caja Venezolana?": "No, para invertir en nuestro Fondo Mutual Abierto no es necesaria la cuenta en la CVV"
        }
    },
    "transacciones": {
        "title": "💰 Suscripción y Rescate",
        "questions": {
            "¿Cómo suscribo (compro)?": "Haz click en Negociación > Suscripción > Monto a invertir > Suscribir > Método de Pago. Recuerda pagar desde TU cuenta bancaria y subir comprobante de pago",
            "¿Cómo pago mi suscripción?": "Debes pagar desde TU cuenta bancaria vía Pago Móvil. Y recuerda subir comprobante. IMPORTANTE: no se aceptan pagos de terceros.",
            "¿Puede pagar alguien por mí?": "No, la ley prohíbe los pagos de terceros. Siempre debes pagar desde tu cuenta bancaria.",
            "¿Cómo veo mi inversión?": "En el Home en la sección Mi Cuenta",
            "¿Cuándo veo mi inversión?": "Al cierre del sistema en días hábiles bancarios después del cierre de mercado y la publicación de tasas del Banco Central de Venezuela.",
            "¿Cuáles son las comisiones?": "3% flat Suscripción, 3% flat Rescate y 5% anual Administración",
            "¿Qué hago después de suscribir?": "Monitorea tu inversión desde el app",
            "¿Cómo rescato (vendo)?": "Haz click en Negociación > Rescate > Unidades a Rescatar > Rescatar. Recuerda se enviarán fondos a TU cuenta bancaria",
            "¿Cuándo me pagan mis rescates?": "Al próximo día hábil bancario en horario de mercado",
            "¿Cuándo puedo Rescatar?": "Cuando tú quieras, y se liquida en días hábiles bancarios."
        }
    },
    "soporte": {
        "title": "🛟 Soporte y Ayuda",
        "questions": {
            "¿Estoy en revisión, qué hago?": "Asegúrate de haber completado 100% datos y recaudos y espera tu aprobación. Si tarda más de lo habitual contáctanos en SOPORTE",
            "¿No me llega el SMS?": "Asegúrate de tener buena señal y de que hayas colocado correctamente un número telefónico venezolano",
            "¿No me llega el Correo?": "Asegúrate de no dejar espacios al final cuando escribiste tu correo electrónico",
            "¿No logro descargar el App?": "Asegúrate de que tu app store esté configurada en la región de Venezuela",
            "¿No me abre el App?": "Asegúrate de tener la versión actualizada y que tu tienda de apps esté configurada en la región de Venezuela",
            "¿Cómo recupero mi clave?": "Seleccione Recuperar, te llegará una clave temporal para ingresar y luego actualiza tu nueva clave",
            "¿Cuáles son los riesgos al invertir?": "Todas las inversiones están sujetas a riesgos y la pérdida de capital es posible. Algunos riesgos son: riesgo de mercado, riesgo país, riesgo cambiario, riesgo sector, entre otros.",
            "¿Por qué varía mi posición (saldo)?": "Tu saldo y rendimiento sube si suben los precios de las inversiones del fondo, se reciben dividendos o cupones y bajan si estos precios caen.",
            "¿Cuándo se actualiza mi posición?": "Al cierre del sistema en días hábiles bancarios después del cierre de mercado y la publicación de tasas del Banco Central de Venezuela."
        }
    }
}

# Variable global para almacenar el estado de la conversación
conversation_state: Dict[str, str] = {}

# ==================== Funciones de la lógica del menú ====================

def get_category_by_id(category_id: str) -> Optional[Dict[str, Any]]:
    """
    Obtiene una categoría por su ID.
    """
    return QA_ZENDESK.get(category_id)


def get_answer_by_question_index(category_id: str, question_index: int) -> str:
    """
    Obtiene la respuesta usando el ID de categoría y el índice de la pregunta.
    """
    category = get_category_by_id(category_id)
    if not category:
        return "Lo siento, la categoría seleccionada no es válida."
   
    questions = list(category["questions"].keys())
    if 1 <= question_index <= len(questions):
        question = questions[question_index - 1]
        return category["questions"][question]
   
    return "Lo siento, el número de pregunta no es válido. Por favor, elige un número del submenú."


def is_back_command(text: str) -> bool:
    """
    Verifica si el mensaje es un comando para volver al menú principal.
    """
    back_keywords = ["volver", "menu", "menú", "principal", "inicio", "back", "0"]
    return text.strip().lower() in back_keywords


buttons(to_msisdn: str) -> Dict[str, Any]:
async def send_main_menu_buttons(to_msisdn: str) -> Dict[str, Any]:
    """
    Envía el menú principal con botones para las categorías principales.
    """
    # Limpiar el estado de la conversación
    if to_msisdn in conversation_state:
        del conversation_state[to_msisdn]
        logging.info(f"Estado de conversación limpiado para {to_msisdn}")
   
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
                "text": "Selecciona la categoría sobre la que necesitas información:\n\n🏢 **Per Capital** - Información general\n📊 **Fondo Mutual** - Inversiones y rendimientos\n📱 **Aplicación** - Registro y uso del app"
            },
            "footer": {
                "text": "Elige una categoría"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "per_capital",
                            "title": "🏢 Per Capital"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "fondo_mutual",
                            "title": "📊 Fondo Mutual"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "app",
                            "title": "📱 Aplicación"
                        }
                    }
                ]
            }
        }
    }
    return await _post_messages(payload)


async def send_secondary_menu_buttons(to_msisdn: str) -> Dict[str, Any]:
    """
    Envía el segundo menú con botones para categorías adicionales.
    """
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
                "text": "Aquí tienes más categorías disponibles:\n\n💰 **Transacciones** - Suscripción y rescate\n🛟 **Soporte** - Ayuda y resolución de problemas\n\n¿Sobre qué tema necesitas ayuda?"
            },
            "footer": {
                "text": "Elige una categoría"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "transacciones",
                            "title": "💰 Transacciones"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "soporte",
                            "title": "🛟 Soporte"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "back_to_main",
                            "title": "🔙 Menú Principal"
                        }
                    }
                ]
            }
        }
    }
    return await _post_messages(payload)


async def send_list_questions(to_msisdn: str, category_id: str) -> Dict[str, Any]:
    """
    Envía una lista de preguntas para una categoría específica usando list messages.
    """
    category = get_category_by_id(category_id)
    if not category:
        await send_text(to_msisdn, "❌ Categoría no válida.")
        await send_main_menu_buttons(to_msisdn)
        return {}

    questions = list(category["questions"].keys())
   
    # WhatsApp permite máximo 10 opciones en una lista
    if len(questions) > 10:
        questions = questions[:10]
        logging.warning(f"Limitando preguntas a 10 para la categoría {category_id}")

    # Crear las filas de la lista
    rows = []
    for i, question in enumerate(questions, 1):
        rows.append({
            "id": f"{category_id}_{i}",
            "title": f"{i}. " + (question[:20] + "..." if len(question) > 20 else question),
            "description": question[:60] + "..." if len(question) > 60 else question
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
                "text": f"Selecciona una pregunta de la categoría *{category['title']}*:\n\nEscribe 'volver' para regresar al menú principal."
            },
            "footer": {
                "text": "Per Capital - FAQ"
            },
            "action": {
                "button": "Ver Preguntas",
                "sections": [
                    {
                        "title": "Preguntas Disponibles",
):
.strip()
signature, their_signature)
ID}/messages"
            # Devuelve el JSON de la respuesta si lo hay
            try:
                return response.json()
            except Exception:
                return {}
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
    Procesa los mensajes de texto del usuario según el flujo de conversación.
    """
    text_clean = message_text.strip()
    logging.info(f"📝 Procesando mensaje de texto de {from_msisdn}: '{text_clean}'")
   
    # Verificar si es un comando para volver al menú principal
    if is_back_command(text_clean):
        logging.info(f"🔄 Usuario {from_msisdn} solicitó volver al menú principal")
        await send_main_menu_buttons(from_msisdn)
        return
   
    # Para cualquier mensaje de texto, enviar el menú inicial con botones
    logging.info(f"🔄 Enviando menú inicial con botones a {from_msisdn}")
    await send_initial_menu_with_buttons(from_msisdn)


async def process_interactive_message(from_msisdn: str, interactive_data: Dict[str, Any]) -> None:
    """
    Procesa los mensajes interactivos (respuestas de botones y listas).
    """
    interactive_type = interactive_data.get("type")
   
    if interactive_type == "button_reply":
        button_reply = interactive_data.get("button_reply", {})
        button_id = button_reply.get("id")
        button_title = button_reply.get("title")
       
        logging.info(f"🔘 Usuario {from_msisdn} presionó botón: {button_id} ({button_title})")
       
        if button_id == "bot_qa":
            # Iniciar flujo de Q&A con menú principal de botones
            await send_text(from_msisdn, "🤖 *¡Perfecto!* Has seleccionado el asistente virtual.\n\nAhora te mostraré las categorías disponibles:")
            await send_main_menu_buttons(from_msisdn)
           
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
               
        elif button_id == "back_to_main":
            # Volver al menú principal
            await send_main_menu_buttons(from_msisdn)
           
        elif button_id == "more_categories":
            # Mostrar más categorías
            await send_secondary_menu_buttons(from_msisdn)
           
        elif button_id in ["per_capital", "fondo_mutual", "app"]:
            # Mostrar lista de preguntas para estas categorías
            await send_list_questions(from_msisdn, button_id)
           
        elif button_id in ["transacciones", "soporte"]:
            # Mostrar lista de preguntas para estas categorías
            await send_list_questions(from_msisdn, button_id)
           
        else:
            logging.warning(f"⚠️ ID de botón desconocido: {button_id}")
            await send_initial_menu_with_buttons(from_msisdn)
   
    elif interactive_type == "list_reply":
        list_reply = interactive_data.get("list_reply", {})
        list_id = list_reply.get("id")
        list_title = list_reply.get("title")
       
        logging.info(f"📋 Usuario {from_msisdn} seleccionó de lista: {list_id} ({list_title})")
       
        # Parsear el ID para obtener categoría y número de pregunta
        if "_" in list_id:
            category_id, question_index_str = list_id.rsplit("_", 1)
            try:
                question_index = int(question_index_str)
               
                # Obtener la respuesta
                response_text = get_answer_by_question_index(category_id, question_index)
               
                # Enviar la respuesta
                await send_text(from_msisdn, f"✅ *Respuesta:*\n\n{response_text}")
               
                # Pequeña pausa antes de mostrar opciones
                import asyncio
                await asyncio.sleep(1)
               
                # Preguntar si necesita algo más y ofrecer opciones
                await send_text(from_msisdn, "📋 ¿Necesitas ayuda con algo más?")
               
                # Ofrecer el menú secundario si es una de las primeras 3 categorías
                if category_id in ["per_capital", "fondo_mutual", "app"]:
                    await send_secondary_menu_buttons(from_msisdn)
                else:
                    await send_main_menu_buttons(from_msisdn)
                   
            except (ValueError, IndexError):
                logging.error(f"❌ Error parseando list_id: {list_id}")
                await send_text(from_msisdn, "❌ Error procesando tu selección.")
                await send_main_menu_buttons(from_msisdn)
        else:
            logging.error(f"❌ Formato de list_id inválido: {list_id}")
            await send_main_menu_buttons(from_msisdn)
   
    else:
        logging.warning(f"⚠️ Tipo de interacción desconocido: {interactive_type}")
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
   
    logging.warning("❌ Intento de verificación fallido del webhook")
    return PlainTextResponse(content="Verification token mismatch", status_code=403)


# ==================== Endpoint para recibir mensajes (POST) ====================

@app.post("/webhook")
async def handle_webhook(request: Request):
    """
    Endpoint que recibe mensajes entrantes de WhatsApp.
    WhatsApp enviará aquí cada evento (mensajes, estados, etc.)
    """
    try:
        # Obtener headers y body en bytes (necesario para verificar firma)
        signature = request.headers.get("X-Hub-Signature-256")
        body_bytes = await request.body()

        # Verificar firma HMAC
        if not verify_signature(signature, body_bytes):
            logging.error("❌ Firma no válida en la solicitud entrante")
            raise HTTPException(status_code=403, detail="Firma inválida")

        # Parsear JSON desde body_bytes (evita leer el body dos veces)
        try:
            data = json.loads(body_bytes.decode("utf-8"))
        except Exception as e:
            logging.error(f"❌ No se pudo parsear JSON del body: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON")

        logging.info(f"📩 Webhook recibido: {json.dumps(data, indent=2, ensure_ascii=False)}")

        # Navegar la estructura típica del webhook de WhatsApp
        entries = data.get("entry", [])
        if not entries:
            logging.info("ℹ️ Webhook sin 'entry' - nothing to process")
            return Response(status_code=200)

        # Iterar entradas y cambios por seguridad (puede venir más de una)
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                # Mensajes entrantes
                messages = value.get("messages", []) or []
                if not messages:
                    logging.info("ℹ️ Cambio sin mensajes (posible status update) - skipping")
                    continue

                for message in messages:
                    from_msisdn = message.get("from")
                    if not from_msisdn:
                        logging.warning("⚠️ Mensaje sin campo 'from' - skipping")
                        continue

                    message_type = message.get("type")
                    logging.info(f"📥 Mensaje de {from_msisdn} tipo {message_type}")

                    if message_type == "text":
                        text_body = message.get("text", {}).get("body", "")
                        await process_text_message(from_msisdn, text_body)

                    elif message_type == "interactive":
                        interactive_data = message.get("interactive", {})
                        await process_interactive_message(from_msisdn, interactive_data)

                    else:
                        logging.warning(f"⚠️ Tipo de mensaje no manejado: {message_type}")
                        await send_text(from_msisdn, "Lo siento, aún no puedo procesar este tipo de mensaje 🙏")

        return Response(status_code=200)

    except HTTPException:
        raise
    except Exception as e:
        logging.exception(f"❌ Error en handle_webhook: {e}")
        raise HTTPException(status_code=500, detail="Error interno en webhook")


# ==================== Run local (opcional) ====================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)