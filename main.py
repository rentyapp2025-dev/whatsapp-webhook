import os
import hmac
import hashlib
import json
import re
import asyncio
from typing import Optional, Any, Dict, List, Tuple
import logging
from datetime import datetime

from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.responses import PlainTextResponse
import httpx

# Configurar el logging para ver mensajes detallados
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Obtener variables de entorno
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "").encode("utf-8") if os.getenv("APP_SECRET") else b""
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Verificar que las variables de entorno cruciales estén presentes
if not all([VERIFY_TOKEN, WHATSAPP_TOKEN, PHONE_NUMBER_ID]):
    logging.error("Faltan variables de entorno cruciales: VERIFY_TOKEN, WHATSAPP_TOKEN, o PHONE_NUMBER_ID")

app = FastAPI(title="WhatsApp Business Bot - Per Capital")

# ==================== BASE DE CONOCIMIENTO ESTRUCTURADA ====================
# Organizada exactamente como en el documento Zendesk
QA_DATABASE = {
    "PER_CAPITAL": {
        "title": "📊 Per Capital",
        "questions": {
            "QUE_ES_PER_CAPITAL": {
                "question": "¿Qué es Per Capital?",
                "answer": "Es un grupo de empresas del Mercado de Valores Venezolano reguladas por la SUNAVAL."
            },
            "QUIEN_REGULA": {
                "question": "¿Quién regula a Per Capital?",
                "answer": "La SUNAVAL (Superintendencia Nacional de Valores)"
            },
            "QUE_ES_SUNAVAL": {
                "question": "¿Qué es la SUNAVAL?",
                "answer": "Es quien protege a inversionistas y regula a intermediarios y emisores del Mercado de Valores venezolano"
            },
            "QUE_ES_BVC": {
                "question": "¿Qué es la Bolsa de Valores de Caracas?",
                "answer": "Es el lugar donde se compran y venden bonos, acciones y otros instrumentos de manera ordenada a través de las Casas de Bolsa y está regulada por la SUNAVAL"
            },
            "COMO_INVIERTO_GENERAL": {
                "question": "¿Cómo invierto?",
                "answer": "Para invertir en el Fondo Mutual Abierto de PER CAPITAL debes descargar el app, registrarte, subir recaudos y colocar tus órdenes de compra."
            }
        }
    },
    "FONDO_MUTUAL": {
        "title": "💰 Fondo Mutual Abierto",
        "questions": {
            "QUE_ES_FONDO": {
                "question": "¿Qué es un Fondo Mutual?",
                "answer": "Es un instrumento de inversión en grupo donde varias personas ponen dinero en un fondo que es gestionado por expertos y está diseñado para ser diversificado, de bajo riesgo y dirigido a pequeños inversionistas con poca experiencia"
            },
            "QUE_ES_UI": {
                "question": "¿Qué es una Unidad de Inversión?",
                "answer": "Es una \"porción\" del fondo. Cuando inviertes adquieres unidades que representan tu parte del fondo."
            },
            "QUE_ES_VUI": {
                "question": "¿Qué es el VUI?",
                "answer": "El Valor de la Unidad de Inversión (VUI) es el precio de una Unidad de Inversión. Si el VUI sube tu inversión gana valor. Se calcula diariamente al cierre del día y depende del comportamiento de las inversiones del fondo."
            },
            "COMO_INVIERTO_FONDO": {
                "question": "¿Cómo invierto en el fondo?",
                "answer": "Descarga el app para Android y iOS, regístrate, sube recaudos, acepta los contratos, espera tu aprobación y suscribe Unidades de Inversión cuando quieras y cuantas veces desees"
            },
            "MONTO_MINIMO": {
                "question": "¿Cuál es el monto mínimo de inversión?",
                "answer": "1 Unidad de Inversión"
            },
            "COMO_GANO": {
                "question": "¿Cómo gano dinero?",
                "answer": "Ganas por apreciación (subida del VUI) o por dividendo (en caso de que sea decretado)"
            },
            "TIEMPO_GANANCIA": {
                "question": "¿En cuánto tiempo gano?",
                "answer": "Ganas a largo plazo, se recomienda medir resultados trimestralmente"
            },
            "MAS_INFORMACION": {
                "question": "¿Dónde consigo más información?",
                "answer": "En los prospectos y hojas de términos en www.per-capital.com"
            }
        }
    },
    "APP": {
        "title": "📱 Aplicación",
        "questions": {
            "COMPRAR_ACCIONES": {
                "question": "¿Puedo comprar acciones y bonos?",
                "answer": "No, nuestra app es únicamente para invertir en nuestro Fondo Mutual Abierto. Pronto saldrá la nueva versión de nuestra app para negociar"
            },
            "COMO_REGISTRO": {
                "question": "¿Cómo me registro?",
                "answer": "Descarga el app, completa 100% de los datos, acepta los contratos, sube tus recaudos como Cédula de Identidad y Selfie y espera tu aprobación."
            },
            "TIEMPO_APROBACION": {
                "question": "¿Cuánto tarda mi aprobación?",
                "answer": "De 2 a 5 días hábiles siempre que hayas completado 100% de registro y recaudos"
            },
            "NO_APRUEBAN": {
                "question": "¿Qué hago si no me aprueban?",
                "answer": "Revisa que hayas completado 100% del registro y recaudos, sino contáctanos en SOPORTE"
            },
            "MENOR_EDAD": {
                "question": "¿Puedo invertir si soy menor de edad?",
                "answer": "Debes dirigirte a nuestras oficinas y registrarte con tu representante legal"
            },
            "MODIFICAR_DATOS": {
                "question": "¿Puedo modificar alguno de mis datos?",
                "answer": "Sí, pero por exigencia de la ley entras nuevamente en revisión"
            },
            "CUENTA_CVV": {
                "question": "¿Debo tener cuenta en la Caja Venezolana?",
                "answer": "No, para invertir en nuestro Fondo Mutual Abierto no es necesaria la cuenta en la CVV"
            },
            "COMO_SUSCRIBO": {
                "question": "¿Cómo suscribo (compro)?",
                "answer": "Haz click en Negociación > Suscripción > Monto a invertir > Suscribir > Método de Pago. Recuerda pagar desde TU cuenta bancaria y subir comprobante de pago"
            },
            "COMO_PAGO": {
                "question": "¿Cómo pago mi suscripción?",
                "answer": "Debes pagar desde TU cuenta bancaria vía Pago Móvil. Y recuerda subir comprobante. IMPORTANTE: no se aceptan pagos de terceros."
            },
            "PAGO_TERCEROS": {
                "question": "¿Puede pagar alguien por mí?",
                "answer": "No, la ley prohíbe los pagos de terceros. Siempre debes pagar desde tu cuenta bancaria."
            },
            "VER_INVERSION": {
                "question": "¿Cómo veo mi inversión?",
                "answer": "En el Home en la sección Mi Cuenta"
            },
            "CUANDO_VEO_INVERSION": {
                "question": "¿Cuándo veo mi inversión?",
                "answer": "Al cierre del sistema en días hábiles bancarios después del cierre de mercado y la publicación de tasas del Banco Central de Venezuela."
            },
            "COMISIONES": {
                "question": "¿Cuáles son las comisiones?",
                "answer": "3% flat Suscripción, 3% flat Rescate y 5% anual Administración"
            },
            "QUE_HACER_DESPUES": {
                "question": "¿Qué hago después de suscribir?",
                "answer": "Monitorea tu inversión desde el app"
            },
            "MISMO_MONTO": {
                "question": "¿Debo invertir siempre el mismo monto?",
                "answer": "No, puedes invertir el monto que desees"
            },
            "CUANDO_INVERTIR": {
                "question": "¿Puedo invertir cuando quiera?",
                "answer": "Sí, puedes invertir cuando quieras, las veces que quieras"
            },
            "COMO_RESCATO": {
                "question": "¿Cómo rescato (vendo)?",
                "answer": "Haz click en Negociación > Rescate > Unidades a Rescatar > Rescatar. Recuerda se enviarán fondos a TU cuenta bancaria"
            },
            "CUANDO_PAGAN": {
                "question": "¿Cuándo me pagan mis rescates?",
                "answer": "Al próximo día hábil bancario en horario de mercado"
            },
            "VER_SALDO": {
                "question": "¿Cómo veo el saldo de mi inversión?",
                "answer": "En el Home en la sección Mi Cuenta"
            },
            "CUANDO_RESCATAR": {
                "question": "¿Cuándo puedo Rescatar?",
                "answer": "Cuando tú quieras, y se liquida en días hábiles bancarios."
            },
            "ACTUALIZA_POSICION": {
                "question": "¿Cuándo se actualiza mi posición?",
                "answer": "Al cierre del sistema en días hábiles bancarios después del cierre de mercado y la publicación de tasas del Banco Central de Venezuela."
            },
            "VARIA_POSICION": {
                "question": "¿Por qué varía mi posición?",
                "answer": "Tu saldo y rendimiento sube si suben los precios de las inversiones del fondo, se reciben dividendos o cupones y bajan si estos precios caen."
            },
            "VER_HISTORICO": {
                "question": "¿Dónde veo mi histórico?",
                "answer": "En la sección Historial"
            },
            "VER_REPORTES": {
                "question": "¿Dónde veo reportes?",
                "answer": "En la sección Documentos > Reportes > Año > Trimestre"
            }
        }
    },
    "RIESGOS": {
        "title": "⚠️ Riesgos de Inversión",
        "questions": {
            "CUALES_RIESGOS": {
                "question": "¿Cuáles son los riesgos al invertir?",
                "answer": "Todas las inversiones están sujetas a riesgos y la pérdida de capital es posible. Algunos riesgos son: riesgo de mercado, riesgo país, riesgo cambiario, riesgo sector, entre otros."
            }
        }
    },
    "SOPORTE": {
        "title": "🆘 Soporte Técnico",
        "questions": {
            "EN_REVISION": {
                "question": "Estoy en revisión, ¿qué hago?",
                "answer": "Asegúrate de haber completado 100% datos y recaudos y espera tu aprobación. Si tarda más de lo habitual contáctanos en SOPORTE"
            },
            "NO_SMS": {
                "question": "No me llega el SMS",
                "answer": "Asegúrate de tener buena señal y de que hayas colocado correctamente un número telefónico venezolano"
            },
            "NO_CORREO": {
                "question": "No me llega el Correo",
                "answer": "Asegúrate de no dejar espacios al final cuando escribiste tu correo electrónico"
            },
            "NO_DESCARGA": {
                "question": "No logro descargar el App",
                "answer": "Asegúrate de que tu app store esté configurada en la región de Venezuela"
            },
            "NO_ABRE": {
                "question": "No me abre el App",
                "answer": "Asegúrate de tener la versión actualizada y que tu tienda de apps esté configurada en la región de Venezuela"
            },
            "RECUPERAR_CLAVE": {
                "question": "¿Cómo recupero mi clave?",
                "answer": "Seleccione Recuperar, te llegará una clave temporal para ingresar y luego actualiza tu nueva clave"
            }
        }
    }
}

# Estados de conversación
conversation_states: Dict[str, Dict[str, Any]] = {}
user_ratings: Dict[str, Dict[str, Any]] = {}

# ==================== UTILIDADES DE CONVERSACIÓN ====================

def detect_greeting(message: str) -> bool:
    """Detecta si el mensaje es un saludo inicial."""
    greetings = [
        'hola', 'hello', 'hi', 'buenas', 'saludos', 'buenos días',
        'buenas tardes', 'buenas noches', 'buen día', 'que tal',
        'hey', 'inicio', 'empezar', 'comenzar', 'start'
    ]
    message_lower = message.lower().strip()
    return any(greeting in message_lower for greeting in greetings)

def should_use_list_message(category_id: str) -> bool:
    """Determina si usar Interactive List o Reply Buttons basado en cantidad de preguntas."""
    question_count = len(QA_DATABASE[category_id]["questions"])
    return question_count >= 4

async def send_typing_indicator(to_msisdn: str) -> None:
    """Envía indicador de escritura."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "text",
        "text": {"body": "..."}  # Mensaje temporal que simula typing
    }
    try:
        await _post_messages(payload)
        await asyncio.sleep(1)  # Simular tiempo de escritura
    except Exception as e:
        logging.error(f"Error enviando typing indicator: {e}")

async def mark_as_read(message_id: str) -> None:
    """Marca el mensaje como leído."""
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id
    }
   
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
   
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, headers=headers, json=payload)
            logging.info(f"Mensaje marcado como leído: {message_id}")
    except Exception as e:
        logging.error(f"Error marcando mensaje como leído: {e}")

# ==================== FUNCIONES PARA ENVIAR MENSAJES ====================

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

async def send_text_message(to_msisdn: str, text: str) -> Dict[str, Any]:
    """Envía un mensaje de texto simple."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "text",
        "text": {"body": text}
    }
    return await _post_messages(payload)

async def send_welcome_message(to_msisdn: str) -> None:
    """Envía el mensaje de bienvenida inicial."""
    welcome_text = (
        "¡Hola! 👋 Bienvenido/a a *Per Capital*\n\n"
        "Soy tu asistente virtual y estoy aquí para ayudarte con todas tus consultas sobre:\n\n"
        "• 📊 Información sobre Per Capital\n"
        "• 💰 Fondo Mutual Abierto\n"
        "• 📱 Uso de la aplicación\n"
        "• ⚠️ Riesgos de inversión\n"
        "• 🆘 Soporte técnico\n\n"
        "Te voy a mostrar el menú de opciones disponibles..."
    )
    await send_text_message(to_msisdn, welcome_text)

async def send_main_menu_list(to_msisdn: str) -> Dict[str, Any]:
    """Envía el menú principal como Interactive List Message."""
    sections = []
   
    # Crear sección con todas las categorías
    rows = []
    for category_id, category_data in QA_DATABASE.items():
        rows.append({
            "id": category_id,
            "title": category_data["title"],
            "description": f"{len(category_data['questions'])} preguntas disponibles"
        })
   
    sections.append({
        "title": "Selecciona una categoría",
        "rows": rows
    })
   
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {
                "type": "text",
                "text": "📋 Menú de Consultas"
            },
            "body": {
                "text": "¿En qué puedo ayudarte hoy?\n\nSelecciona una categoría para ver las preguntas disponibles:"
            },
            "footer": {
                "text": "Per Capital - Tu socio de inversión"
            },
            "action": {
                "button": "Ver opciones",
                "sections": sections
            }
        }
    }
   
    return await _post_messages(payload)

async def send_category_menu(to_msisdn: str, category_id: str) -> Dict[str, Any]:
    """Envía el menú de preguntas para una categoría específica."""
    category_data = QA_DATABASE[category_id]
   
    if should_use_list_message(category_id):
        # Usar Interactive List para 4+ preguntas
        rows = []
        for question_id, question_data in category_data["questions"].items():
            rows.append({
                "id": question_id,
                "title": question_data["question"][:24],  # Límite de WhatsApp
                "description": question_data["question"][:72] if len(question_data["question"]) > 24 else ""
            })
       
        sections = [{
            "title": "Preguntas disponibles",
            "rows": rows
        }]
       
        payload = {
            "messaging_product": "whatsapp",
            "to": to_msisdn,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {
                    "type": "text",
                    "text": category_data["title"]
                },
                "body": {
                    "text": "Selecciona la pregunta que te interesa:"
                },
                "footer": {
                    "text": "Toca 'Ver preguntas' para comenzar"
                },
                "action": {
                    "button": "Ver preguntas",
                    "sections": sections
                }
            }
        }
    else:
        # Usar Reply Buttons para 3 o menos preguntas
        buttons = []
        for question_id, question_data in category_data["questions"].items():
            buttons.append({
                "type": "reply",
                "reply": {
                    "id": question_id,
                    "title": question_data["question"][:20]  # Límite más estricto para botones
                }
            })
       
        payload = {
            "messaging_product": "whatsapp",
            "to": to_msisdn,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "header": {
                    "type": "text",
                    "text": category_data["title"]
                },
                "body": {
                    "text": "Selecciona la pregunta que te interesa:"
                },
                "footer": {
                    "text": "Per Capital"
                },
                "action": {
                    "buttons": buttons
                }
            }
        }
   
    return await _post_messages(payload)

async def send_answer_and_followup(to_msisdn: str, question_id: str) -> None:
    """Envía la respuesta a una pregunta y el mensaje de seguimiento."""
    # Buscar la respuesta en la base de datos
    answer = None
    for category_data in QA_DATABASE.values():
        if question_id in category_data["questions"]:
            answer = category_data["questions"][question_id]["answer"]
            break
   
    if not answer:
        await send_text_message(to_msisdn, "Lo siento, no pude encontrar la respuesta a esa pregunta.")
        return
   
    # Enviar la respuesta
    response_text = f"✅ *Respuesta:*\n\n{answer}"
    await send_text_message(to_msisdn, response_text)
   
    # Pausa antes del mensaje de seguimiento
    await asyncio.sleep(1)
   
    # Enviar mensaje de seguimiento
    await send_followup_question(to_msisdn)

async def send_followup_question(to_msisdn: str) -> Dict[str, Any]:
    """Envía mensaje preguntando si necesita más ayuda."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": "¿Necesitas ayuda con alguna otra cosa?"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "YES",
                            "title": "Sí, por favor"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "NO",
                            "title": "No, gracias"
                        }
                    }
                ]
            }
        }
    }
   
    return await _post_messages(payload)

async def send_rating_request(to_msisdn: str) -> Dict[str, Any]:
    """Envía mensaje solicitando calificación del servicio."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "text",
                "text": "⭐ Califica nuestro servicio"
            },
            "body": {
                "text": "Nos encantaría conocer tu opinión sobre la atención recibida:"
            },
            "footer": {
                "text": "Tu opinión es muy importante para nosotros"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "RATE_EXCELLENT",
                            "title": "⭐⭐⭐ Excelente"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "RATE_GOOD",
                            "title": "⭐⭐ Bien"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "RATE_NEEDS_IMPROVEMENT",
                            "title": "⭐ Puede mejorar"
                        }
                    }
                ]
            }
        }
    }
   
    return await _post_messages(payload)

async def send_goodbye_message(to_msisdn: str, rating: str) -> None:
    """Envía mensaje de despedida después de la calificación."""
    rating_text = {
        "RATE_EXCELLENT": "¡Excelente! ⭐⭐⭐",
        "RATE_GOOD": "¡Bien! ⭐⭐",
        "RATE_NEEDS_IMPROVEMENT": "Puede mejorar ⭐"
    }.get(rating, "")
   
    goodbye_text = (
        f"Gracias por tu calificación: {rating_text}\n\n"
        "¡Ha sido un placer ayudarte! 😊\n\n"
        "Si necesitas ayuda en el futuro, no dudes en escribirnos nuevamente.\n\n"
        "🏦 *Per Capital* - Tu socio de inversión\n"
        "📧 Contáctanos también en: soporte@per-capital.com"
    )
   
    await send_text_message(to_msisdn, goodbye_text)

# ==================== PROCESAMIENTO DE MENSAJES ====================

async def process_text_message(from_msisdn: str, message_text: str, message_id: str) -> None:
    """Procesa mensajes de texto del usuario."""
    text_clean = message_text.strip()
   
    # Marcar como leído
    await mark_as_read(message_id)
   
    # Detectar saludo inicial
    if detect_greeting(text_clean):
        logging.info(f"👋 Saludo detectado de {from_msisdn}")
       
        # Enviar indicador de escritura
        await send_typing_indicator(from_msisdn)
       
        # Enviar mensaje de bienvenida
        await send_welcome_message(from_msisdn)
       
        # Pausa entre mensajes
        await asyncio.sleep(2)
       
        # Enviar menú principal
        await send_main_menu_list(from_msisdn)
       
        # Inicializar estado de conversación
        conversation_states[from_msisdn] = {
            "stage": "main_menu",
            "timestamp": datetime.now()
        }
       
        return
   
    # Para cualquier otro texto, enviar menú principal
    logging.info(f"📝 Mensaje de texto no reconocido de {from_msisdn}, enviando menú principal")
    await send_main_menu_list(from_msisdn)
    conversation_states[from_msisdn] = {
        "stage": "main_menu",
        "timestamp": datetime.now()
    }

async def process_interactive_message(from_msisdn: str, interactive_data: Dict[str, Any]) -> None:
    """Procesa mensajes interactivos (listas y botones)."""
    interactive_type = interactive_data.get("type")
   
    if interactive_type == "list_reply":
        # Respuesta de Interactive List
        list_reply = interactive_data.get("list_reply", {})
        selected_id = list_reply.get("id")
       
        logging.info(f"📋 Usuario {from_msisdn} seleccionó de lista: {selected_id}")
       
        if selected_id in QA_DATABASE:
            # Es una categoría del menú principal
            await send_category_menu(from_msisdn, selected_id)
            conversation_states[from_msisdn] = {
                "stage": "answered",
                "timestamp": datetime.now()
            }
   
    elif interactive_type == "button_reply":
        # Respuesta de Reply Button
        button_reply = interactive_data.get("button_reply", {})
        button_id = button_reply.get("id")
       
        logging.info(f"🔘 Usuario {from_msisdn} presionó botón: {button_id}")
       
        if button_id == "YES":
            # Usuario quiere más ayuda
            await send_text_message(from_msisdn, "¡Perfecto! Te muestro nuevamente el menú de opciones:")
            await asyncio.sleep(1)
            await send_main_menu_list(from_msisdn)
            conversation_states[from_msisdn] = {
                "stage": "main_menu",
                "timestamp": datetime.now()
            }
           
        elif button_id == "NO":
            # Usuario no necesita más ayuda
            await send_rating_request(from_msisdn)
            conversation_states[from_msisdn] = {
                "stage": "rating",
                "timestamp": datetime.now()
            }
           
        elif button_id.startswith("RATE_"):
            # Usuario está calificando el servicio
            rating_value = {
                "RATE_EXCELLENT": 5,
                "RATE_GOOD": 4,
                "RATE_NEEDS_IMPROVEMENT": 2
            }.get(button_id, 3)
           
            # Guardar calificación
            user_ratings[from_msisdn] = {
                "rating": rating_value,
                "rating_id": button_id,
                "timestamp": datetime.now()
            }
           
            logging.info(f"⭐ Usuario {from_msisdn} calificó con: {rating_value} estrellas")
           
            # Enviar mensaje de despedida
            await send_goodbye_message(from_msisdn, button_id)
           
            # Limpiar estado de conversación
            if from_msisdn in conversation_states:
                del conversation_states[from_msisdn]
               
        elif button_id in [q_id for category in QA_DATABASE.values() for q_id in category["questions"]]:
            # Es una pregunta específica de un Reply Button
            await send_answer_and_followup(from_msisdn, button_id)
            conversation_states[from_msisdn] = {
                "stage": "answered",
                "timestamp": datetime.now()
            }

# ==================== UTILIDADES DE VERIFICACIÓN ====================

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

# ==================== ENDPOINTS DE FASTAPI ====================

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
                   
                    if message_type == "text":
                        # Procesar mensajes de texto
                        text_data = message.get("text", {})
                        message_text = text_data.get("body", "")
                        await process_text_message(from_msisdn, message_text, message_id)
                       
                    elif message_type == "interactive":
                        # Procesar mensajes interactivos
                        interactive_data = message.get("interactive", {})
                        await process_interactive_message(from_msisdn, interactive_data)
                       
                    else:
                        # Para cualquier otro tipo de mensaje
                        logging.info(f"📎 Mensaje de tipo '{message_type}' recibido - Enviando menú principal")
                        await send_main_menu_list(from_msisdn)
                        conversation_states[from_msisdn] = {
                            "stage": "main_menu",
                            "timestamp": datetime.now()
                        }
       
        return Response(status_code=200)
       
    except json.JSONDecodeError:
        logging.error("❌ Error al decodificar JSON en la solicitud")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logging.error(f"❌ Error inesperado procesando webhook: {e}", exc_info=True)
        return Response(status_code=500, content="Internal Server Error")

# ==================== ENDPOINTS DE ESTADO Y MONITORING ====================

@app.get("/")
async def health_check():
    """Endpoint de salud para verificar que el servicio está funcionando."""
    return {
        "status": "ok",
        "service": "WhatsApp Business Bot - Per Capital",
        "version": "3.0",
        "categories": len(QA_DATABASE),
        "total_questions": sum(len(category["questions"]) for category in QA_DATABASE.values()),
        "active_conversations": len(conversation_states),
        "total_ratings": len(user_ratings)
    }

@app.get("/status")
async def status_endpoint():
    """Endpoint de estado detallado para monitoreo."""
    # Calcular estadísticas de calificaciones
    ratings_stats = {}
    if user_ratings:
        ratings_values = [rating["rating"] for rating in user_ratings.values()]
        ratings_stats = {
            "total_ratings": len(ratings_values),
            "average_rating": sum(ratings_values) / len(ratings_values),
            "rating_distribution": {
                "5_stars": sum(1 for r in ratings_values if r == 5),
                "4_stars": sum(1 for r in ratings_values if r == 4),
                "3_stars": sum(1 for r in ratings_values if r == 3),
                "2_stars": sum(1 for r in ratings_values if r == 2),
                "1_star": sum(1 for r in ratings_values if r == 1)
            }
        }
   
    return {
        "service_status": "running",
        "environment_variables": {
            "VERIFY_TOKEN": "✅" if VERIFY_TOKEN else "❌",
            "WHATSAPP_TOKEN": "✅" if WHATSAPP_TOKEN else "❌",
            "PHONE_NUMBER_ID": "✅" if PHONE_NUMBER_ID else "❌",
            "APP_SECRET": "✅" if APP_SECRET else "❌"
        },
        "database_stats": {
            "categories": len(QA_DATABASE),
            "total_questions": sum(len(category["questions"]) for category in QA_DATABASE.values()),
            "questions_by_category": {
                category_id: len(category["questions"])
                for category_id, category in QA_DATABASE.items()
            }
        },
        "conversation_stats": {
            "active_conversations": len(conversation_states),
            "conversation_stages": {
                stage: sum(1 for state in conversation_states.values() if state.get("stage") == stage)
                for stage in ["main_menu", "category_menu", "answered", "rating"]
            }
        },
        "ratings_stats": ratings_stats,
        "graph_api_version": GRAPH_API_VERSION
    }

@app.get("/conversations")
async def get_conversations():
    """Endpoint para ver conversaciones activas (útil para debugging)."""
    return {
        "active_conversations": len(conversation_states),
        "conversations": {
            phone: {
                "stage": state.get("stage"),
                "category": state.get("category"),
                "timestamp": state.get("timestamp").isoformat() if state.get("timestamp") else None
            }
            for phone, state in conversation_states.items()
        }
    }

@app.get("/ratings")
async def get_ratings():
    """Endpoint para ver las calificaciones recibidas."""
    return {
        "total_ratings": len(user_ratings),
        "ratings": {
            phone: {
                "rating": rating["rating"],
                "rating_id": rating["rating_id"],
                "timestamp": rating["timestamp"].isoformat()
            }
            for phone, rating in user_ratings.items()
        }
    }

@app.post("/clear-conversations")
async def clear_conversations():
    """Endpoint para limpiar todas las conversaciones activas."""
    global conversation_states
    count = len(conversation_states)
    conversation_states.clear()
    logging.info(f"🧹 Conversaciones limpiadas: {count}")
    return {
        "status": "success",
        "cleared_conversations": count,
        "message": f"Se limpiaron {count} conversaciones activas"
    }

@app.post("/clear-ratings")
async def clear_ratings():
    """Endpoint para limpiar todas las calificaciones."""
    global user_ratings
    count = len(user_ratings)
    user_ratings.clear()
    logging.info(f"🧹 Calificaciones limpiadas: {count}")
    return {
        "status": "success",
        "cleared_ratings": count,
        "message": f"Se limpiaron {count} calificaciones"
    }

# ==================== ENDPOINT DE TESTING ====================

@app.post("/test-message")
async def test_message(phone_number: str, test_type: str = "greeting"):
    """Endpoint para probar el bot enviando mensajes de prueba."""
    if not phone_number.startswith("58"):
        phone_number = f"58{phone_number}"
   
    try:
        if test_type == "greeting":
            await send_welcome_message(phone_number)
            await asyncio.sleep(2)
            await send_main_menu_list(phone_number)
           
        elif test_type == "menu":
            await send_main_menu_list(phone_number)
           
        elif test_type == "category":
            await send_category_menu(phone_number, "APP")
           
        elif test_type == "followup":
            await send_followup_question(phone_number)
           
        elif test_type == "rating":
            await send_rating_request(phone_number)
           
        return {
            "status": "success",
            "message": f"Mensaje de prueba '{test_type}' enviado a {phone_number}",
            "phone_number": phone_number
        }
       
    except Exception as e:
        logging.error(f"Error en mensaje de prueba: {e}")
        return {
            "status": "error",
            "message": str(e),
            "phone_number": phone_number
        }

# ==================== MANEJO DE ERRORES GLOBALES ====================

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

# ==================== FUNCIÓN DE LIMPIEZA AUTOMÁTICA ====================

async def cleanup_old_conversations():
    """Limpia conversaciones inactivas después de 24 horas."""
    while True:
        try:
            current_time = datetime.now()
            expired_conversations = []
           
            for phone, state in conversation_states.items():
                if state.get("timestamp"):
                    time_diff = current_time - state["timestamp"]
                    if time_diff.total_seconds() > 86400:  # 24 horas
                        expired_conversations.append(phone)
           
            for phone in expired_conversations:
                del conversation_states[phone]
                logging.info(f"🧹 Conversación expirada limpiada: {phone}")
           
            if expired_conversations:
                logging.info(f"🧹 Total conversaciones limpiadas: {len(expired_conversations)}")
           
            # Esperar 1 hora antes de la próxima limpieza
            await asyncio.sleep(3600)
           
        except Exception as e:
            logging.error(f"Error en limpieza automática: {e}")
            await asyncio.sleep(3600)

# ==================== INICIALIZACIÓN DE LA APLICACIÓN ====================

@app.on_event("startup")
async def startup_event():
    """Evento que se ejecuta al iniciar la aplicación."""
    logging.info("🚀 Iniciando WhatsApp Business Bot - Per Capital")
    logging.info(f"📊 Categorías cargadas: {len(QA_DATABASE)}")
   
    for category_id, category_data in QA_DATABASE.items():
        questions_count = len(category_data["questions"])
        logging.info(f"   • {category_data['title']}: {questions_count} preguntas")
   
    # Iniciar tarea de limpieza automática
    asyncio.create_task(cleanup_old_conversations())
    logging.info("✅ Bot listo para recibir mensajes!")

# ==================== MENSAJE DE INICIO ====================

if __name__ == "__main__":
    print("🚀 Iniciando WhatsApp Business Bot - Per Capital...")
    print(f"📊 Base de conocimiento cargada:")
    print(f"   • Categorías: {len(QA_DATABASE)}")
   
    total_questions = sum(len(category["questions"]) for category in QA_DATABASE.values())
    print(f"   • Total preguntas: {total_questions}")
   
    for category_id, category_data in QA_DATABASE.items():
        questions_count = len(category_data["questions"])
        print(f"   • {category_data['title']}: {questions_count} preguntas")
   
    print("\n📋 Endpoints disponibles:")
    print("   • GET  /          - Health check")
    print("   • GET  /status    - Estado detallado")
    print("   • GET  /conversations - Conversaciones activas")
    print("   • GET  /ratings   - Calificaciones recibidas")
    print("   • POST /test-message - Enviar mensaje de prueba")
    print("   • POST /clear-conversations - Limpiar conversaciones")
    print("   • POST /clear-ratings - Limpiar calificaciones")
   
    print("\n✅ Bot listo para recibir mensajes!")
    print("🔗 Configura tu webhook en: https://tu-dominio.com/webhook")