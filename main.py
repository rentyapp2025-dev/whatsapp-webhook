import os
import json
import time
import asyncio
import logging
import hmac
import hashlib
from typing import Dict, List, Optional, Any
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import httpx

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment variables
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "your_whatsapp_token_here")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "your_phone_number_id_here")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "your_verify_token_here")
APP_SECRET = os.getenv("APP_SECRET", "your_app_secret_here")

# WhatsApp API configuration
GRAPH_API_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_TOKEN}",
    "Content-Type": "application/json"
}

# Initialize FastAPI app
app = FastAPI(title="Per Capital WhatsApp Chatbot")

# Global state management (in production, use Redis or database)
user_sessions: Dict[str, Dict] = {}
user_ratings: List[Dict] = []

# ==================== DATA STRUCTURE ====================

KNOWLEDGE_BASE = {
    "PER_CAPITAL": {
        "id": "PER_CAPITAL",
        "title": "Per Capital",
        "questions": [
            {"id": "Q1_PC", "text": "¿Que es Per Capital?", "answer": "Es un grupo de empresas del Mercado de Valores Venezolano reguladas por la SUNAVAL."},
            {"id": "Q2_PC", "text": "¿Quien regula a PER CAPITAL?", "answer": "La SUNAVAL (Superintendencia Nacional de Valores)"},
            {"id": "Q3_PC", "text": "¿Que es la SUNAVAL?", "answer": "Es quien protege a inversionistas y regula a intermediarios y emisores del Mercado de Valores venezolano"},
            {"id": "Q4_PC", "text": "¿Que es la Bolsa de Valores de Caracas?", "answer": "Es el lugar donde se compran y venden bonos, acciones y otros instrumentos de manera ordenada a traves de las Casas de Bolsa y esta regulada por la SUNAVAL"},
            {"id": "Q5_PC", "text": "¿Como invierto?", "answer": "Para invertir en el Fondo Mutual Abierto de PER CAPITAL debes descargar el app, registrate, subir recaudos y colocar tus ordenes de compra."}
        ]
    },
    "FONDO_MUTUAL": {
        "id": "FONDO_MUTUAL",
        "title": "Fondo Mutual Abierto",
        "questions": [
            {"id": "Q1_FMA", "text": "¿Que es un Fondo Mutual?", "answer": "Es un instrumento de inversion en grupo donde varias personas ponen dinero en un fondo que es gestionado por expertos y esta disenado para ser diversificado, de bajo riesgo y dirigido a pequenos inversionistas con poca experiencia"},
            {"id": "Q2_FMA", "text": "¿Que es una Unidad de Inversion?", "answer": "Es una 'porcion' del fondo. Cuando inviertes adquieres unidades que representan tu parte del fondo."},
            {"id": "Q3_FMA", "text": "¿Que es el VUI?", "answer": "El Valor de la Unidad de Inversion (VUI) es el precio de una Unidad de Inversion. Si el VUI sube tu inversion gana valor. Se calcula diariamente al cierre del dia y depende del comportamiento de las inversiones del fondo."},
            {"id": "Q4_FMA", "text": "¿Como invierto?", "answer": "Descarga el app para Android y IOS, registrate, sube recaudos, acepta los contratos, espera tu aprobacion y suscribe Unidades de Inversion cuando quieras y cuantas veces desees"},
            {"id": "Q5_FMA", "text": "¿Cual es el monto minimo de inversion?", "answer": "1 Unidad de Inversion"},
            {"id": "Q6_FMA", "text": "¿Como gano?", "answer": "Ganas por apreciacion (subida del VUI) o por dividendo (en caso de que sea decretado)"},
            {"id": "Q7_FMA", "text": "¿En cuanto tiempo gano?", "answer": "Ganas a largo plazo, se recomienda medir resultados trimestralmente"},
            {"id": "Q8_FMA", "text": "¿Donde consigo mas informacion?", "answer": "En los prospectos y hojas de terminos en www.per-capital.com"}
        ]
    },
    "APP_GENERAL": {
        "id": "APP_GENERAL",
        "title": "Información General de la App",
        "questions": [
            {"id": "Q1_APP_GEN", "text": "¿Puedo comprar acciones y bonos?", "answer": "No, nuestra app es únicamente para invertir en nuestro Fondo Mutual Abierto. Pronto saldrá la nueva versión de nuestra app para negociar"}
        ]
    },
    "APP_REGISTRO": {
        "id": "APP_REGISTRO",
        "title": "Registro en la App",
        "questions": [
            {"id": "Q1_APP_REG", "text": "¿Como me registro?", "answer": "Descarga el app, completa 100% de los datos, acepta los contratos, sube tus recaudos como Cedula de Identidad y Selfie y espera tu aprobacion."},
            {"id": "Q2_APP_REG", "text": "¿Cuanto tarda mi aprobacion?", "answer": "De 2 a 5 dias habiles siempre que hayas completado 100% de registro y recaudos"},
            {"id": "Q3_APP_REG", "text": "¿Que hago si no me aprueban?", "answer": "Revisa que hayas completado 100% del registro y recaudos, sino contactanos en SOPORTE"},
            {"id": "Q4_APP_REG", "text": "¿Puedo invertir si soy menor de edad?", "answer": "Debes dirigirte a nuestras oficinas y registrarte con tu representante legal"},
            {"id": "Q5_APP_REG", "text": "¿Puedo modificar alguno de mis datos?", "answer": "Si, pero por exigencia del ley entras nuevamente en revision"},
            {"id": "Q6_APP_REG", "text": "¿Debo tener cuenta en la Caja Venezolana?", "answer": "No, para invertir en nuestro Fondo Mutual Abierto no es necesaria la cuenta en la CVV"}
        ]
    },
    "APP_SUSCRIPCION": {
        "id": "APP_SUSCRIPCION",
        "title": "Suscripción",
        "questions": [
            {"id": "Q1_APP_SUS", "text": "¿Como suscribo (compro)?", "answer": "Haz click en Negociacion > Suscripcion > Monto a invertir > Suscribir > Metodo de Pago. Recuerda pagar desde TU cuenta bancaria y subir comprobante de pago"},
            {"id": "Q2_APP_SUS", "text": "¿Como pago mi suscripcion?", "answer": "Debes pagar desde TU cuenta bancaria via Pago Movil. Y recuerda subir comprobante. IMPORTANTE: no se aceptan pagos de terceros."},
            {"id": "Q3_APP_SUS", "text": "¿Puede pagar alguien por mi?", "answer": "No, la ley prohibe los pagos de terceros. Siempre debes pagar desde tu cuenta bancaria."},
            {"id": "Q4_APP_SUS", "text": "¿Como veo mi inversion?", "answer": "En el Home en la seccion Mi Cuenta"},
            {"id": "Q5_APP_SUS", "text": "¿Cuando veo mi inversion?", "answer": "Al cierre del sistema en dias habiles bancarios despues del cierre de mercado y la publicacion de tasas del Banco Central de Venezuela."},
            {"id": "Q6_APP_SUS", "text": "¿Cuales son las comisiones?", "answer": "3% flat Suscripcion, 3% flat Rescate y 5% anual Administracion"},
            {"id": "Q7_APP_SUS", "text": "¿Que hago despues de suscribir?", "answer": "Monitorea tu inversion desde el app"},
            {"id": "Q8_APP_SUS", "text": "¿Debo invertir siempre el mismo monto?", "answer": "No, puedes invertir el monto que desees"},
            {"id": "Q9_APP_SUS", "text": "¿Puedo invertir cuando quiera?", "answer": "Si, puedes invertir cuando quieras, las veces que quieras"}
        ]
    },
    "APP_RESCATE": {
        "id": "APP_RESCATE",
        "title": "Rescate",
        "questions": [
            {"id": "Q1_APP_RES", "text": "¿Como rescato (vendo)?", "answer": "Haz click en Negociacion > Rescate > Unidades a Rescatar > Rescatar. Recuerda se enviaran fondos a TU cuenta bancaria"},
            {"id": "Q2_APP_RES", "text": "¿Cuando me pagan mis rescates (ventas)?", "answer": "Al proximo dia habil bancario en horario de mercado"},
            {"id": "Q3_APP_RES", "text": "¿Como veo el saldo de mi inversion?", "answer": "En el Home en la seccion Mi Cuenta"},
            {"id": "Q4_APP_RES", "text": "¿Cuando veo el saldo de mi inversion?", "answer": "Al cierre del sistema en dias habiles bancarios despues del cierre de mercado y la publicacion de tasas del Banco Central de Venezuela."},
            {"id": "Q5_APP_RES", "text": "¿Cuando puedo Rescatar?", "answer": "Cuando tu quieras, y se liquida en dias habiles bancarios."},
            {"id": "Q6_APP_RES", "text": "¿Cuales son las comisiones?", "answer": "3% flat Suscripcion, 3% flat Rescate y 5% anual Administracion"}
        ]
    },
    "APP_POSICION": {
        "id": "APP_POSICION",
        "title": "Posición (Saldo)",
        "questions": [
            {"id": "Q1_APP_POS", "text": "¿Cuando se actualiza mi posicion (saldo)?", "answer": "Al cierre del sistema en dias habiles bancarios despues del cierre de mercado y la publicacion de tasas del Banco Central de Venezuela."},
            {"id": "Q2_APP_POS", "text": "¿Por que varia mi posicion (saldo)?", "answer": "Tu saldo y rendimiento sube si suben los precios de las inversiones del fondo, se reciben dividendos o cupones y bajan si estos precios caen."},
            {"id": "Q3_APP_POS", "text": "¿Donde veo mi historico?", "answer": "En la seccion Historial"},
            {"id": "Q4_APP_POS", "text": "¿Donde veo reportes?", "answer": "En la seccion Documentos > Reportes > Año > Trimestre"}
        ]
    },
    "RIESGOS": {
        "id": "RIESGOS",
        "title": "Riesgos",
        "questions": [
            {"id": "Q1_RIE", "text": "¿Cuales son los riesgos al invertir?", "answer": "Todas las inversionbes estan sujetas a riesgos y la perdida de capital es posible. Agunos riesgos son: riesgo de mercado, riesgo pais, riesgo cambiario, riesgo sector, entre otros."}
        ]
    },
    "SOPORTE": {
        "id": "SOPORTE",
        "title": "Soporte",
        "questions": [
            {"id": "Q1_SOP", "text": "Estoy en revision, que hago?", "answer": "Asegurate de haber completado 100% datos y recaudos y espera tu aprobacion. Si tarda mas de lo habitual contactanos en SOPORTE"},
            {"id": "Q2_SOP", "text": "No me llega el SMS", "answer": "Asegurate de tener buena senal y de que hayas colocado correctamente un numero telefonico venezolano"},
            {"id": "Q3_SOP", "text": "No me llega el Correo", "answer": "Asegurate de no dejar espacios al final cuando escribiste tu correo electronico"},
            {"id": "Q4_SOP", "text": "No logro descargar el App", "answer": "Asegurate de que tu app store este configurada en la region de Venezuela"},
            {"id": "Q5_SOP", "text": "No me abre el App", "answer": "Asegurate de tener la version actualizada y que tu tienda de apps este configurada en la region de Venezuela"},
            {"id": "Q6_SOP", "text": "Como recupero mi clave", "answer": "Seleccione Recuperar, te legara una clave temporal para ingresar y luego actualiza tu nueva clave"}
        ]
    }
}

# ==================== MESSAGE BUILDERS ====================

def build_text_message(to: str, text: str) -> Dict:
    """Build a text message payload"""
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }

def build_interactive_list_message(to: str, header: str, body: str, sections: List[Dict]) -> Dict:
    """Build an interactive list message payload"""
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": header},
            "body": {"text": body},
            "footer": {"text": "Per Capital - Tu asistente virtual"},
            "action": {
                "button": "Ver opciones",
                "sections": sections
            }
        }
    }

def build_reply_button_message(to: str, body: str, buttons: List[Dict]) -> Dict:
    """Build a reply button message payload"""
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "footer": {"text": "Per Capital - Tu asistente virtual"},
            "action": {"buttons": buttons}
        }
    }

def build_read_receipt(message_id: str) -> Dict:
    """Build a read receipt payload"""
    return {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id
    }

def build_typing_indicator(to: str) -> Dict:
    """Build typing indicator payload"""
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": "typing..."}
    }

# ==================== WHATSAPP API FUNCTIONS ====================

async def send_message(payload: Dict) -> bool:
    """Send message to WhatsApp API"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(GRAPH_API_URL, headers=HEADERS, json=payload, timeout=30.0)
            response.raise_for_status()
            logger.info(f"Message sent successfully to {payload.get('to')}")
            return True
    except httpx.RequestError as e:
        logger.error(f"Request error sending message: {e}")
        return False
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error sending message: {e.response.status_code} - {e.response.text}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending message: {e}")
        return False

async def send_typing_indicator_and_wait(to: str, seconds: float = 2.0):
    """Send typing indicator and wait"""
    try:
        # Mark as read first (simulate natural behavior)
        await asyncio.sleep(0.5)
        
        # Wait for the specified time (simulating typing)
        await asyncio.sleep(seconds)
        
    except Exception as e:
        logger.error(f"Error in typing indicator: {e}")

async def send_welcome_sequence(to: str):
    """Send welcome message sequence with typing indicators"""
    # Welcome message
    welcome_text = (
        "¡Hola! 👋 Bienvenido a Per Capital\n\n"
        "Soy tu asistente virtual y estoy aquí para ayudarte con todas tus consultas "
        "sobre inversiones, nuestra app y servicios financieros.\n\n"
        "¿Cómo puedo ayudarte hoy?"
    )
    
    # Send typing indicator and wait
    await send_typing_indicator_and_wait(to, 1.5)
    
    # Send welcome message
    await send_message(build_text_message(to, welcome_text))
    
    # Wait a bit more before sending options
    await asyncio.sleep(1.0)
    
    # Send main menu
    await send_main_menu(to)

async def send_main_menu(to: str):
    """Send main interactive menu"""
    sections = [{
        "title": "Categorías disponibles",
        "rows": [
            {"id": "PER_CAPITAL", "title": "Per Capital", "description": "Información general de la empresa"},
            {"id": "FONDO_MUTUAL", "title": "Fondo Mutual Abierto", "description": "Todo sobre nuestro fondo de inversión"},
            {"id": "APP_MAIN", "title": "App Per Capital", "description": "Registro, suscripción, rescate y más"},
            {"id": "RIESGOS", "title": "Riesgos de Inversión", "description": "Información sobre riesgos al invertir"},
            {"id": "SOPORTE", "title": "Soporte Técnico", "description": "Ayuda con problemas técnicos"},
        ]
    }]
    
    payload = build_interactive_list_message(
        to=to,
        header="Menú Principal",
        body="Selecciona la categoría sobre la que necesitas información:",
        sections=sections
    )
    
    await send_message(payload)
    
    # Update user session
    user_sessions[to] = {
        "state": "main_menu",
        "last_interaction": datetime.now().isoformat()
    }

async def send_app_submenu(to: str):
    """Send App submenu"""
    sections = [{
        "title": "Opciones de la App",
        "rows": [
            {"id": "APP_GENERAL", "title": "Información General", "description": "Funciones generales de la app"},
            {"id": "APP_REGISTRO", "title": "Registro", "description": "Cómo registrarse y aprobación"},
            {"id": "APP_SUSCRIPCION", "title": "Suscripción", "description": "Cómo invertir y procesos de pago"},
            {"id": "APP_RESCATE", "title": "Rescate", "description": "Cómo retirar inversiones"},
            {"id": "APP_POSICION", "title": "Posición y Saldo", "description": "Consultar saldos y reportes"},
        ]
    }]
    
    payload = build_interactive_list_message(
        to=to,
        header="App Per Capital",
        body="¿Sobre qué aspecto de la app necesitas información?",
        sections=sections
    )
    
    await send_message(payload)
    
    # Update user session
    user_sessions[to] = {
        "state": "app_submenu",
        "last_interaction": datetime.now().isoformat()
    }

async def send_category_questions(to: str, category_id: str):
    """Send questions for a specific category"""
    category = KNOWLEDGE_BASE.get(category_id)
    if not category:
        await send_message(build_text_message(to, "Lo siento, no pude encontrar esa categoría."))
        await send_main_menu(to)
        return
    
    questions = category["questions"]
    
    if len(questions) <= 3:
        # Use reply buttons for 3 or fewer questions
        buttons = []
        for i, q in enumerate(questions[:3]):
            buttons.append({
                "type": "reply",
                "reply": {
                    "id": q["id"],
                    "title": f"{i+1}. {q['text'][:20]}..."  # Truncate title if too long
                }
            })
        
        payload = build_reply_button_message(
            to=to,
            body=f"*{category['title']}*\n\nSelecciona tu pregunta:",
            buttons=buttons
        )
    else:
        # Use interactive list for 4+ questions
        rows = []
        for i, q in enumerate(questions):
            rows.append({
                "id": q["id"],
                "title": f"{i+1}. {q['text'][:24]}",  # Truncate title if too long
                "description": q["text"][:72] + "..." if len(q["text"]) > 72 else q["text"]
            })
        
        sections = [{"title": category["title"], "rows": rows}]
        
        payload = build_interactive_list_message(
            to=to,
            header=category["title"],
            body="Selecciona tu pregunta:",
            sections=sections
        )
    
    await send_message(payload)
    
    # Update user session
    user_sessions[to] = {
        "state": "questions_menu",
        "category": category_id,
        "last_interaction": datetime.now().isoformat()
    }

async def send_answer(to: str, question_id: str):
    """Send answer for a specific question"""
    # Find the question in the knowledge base
    answer = None
    for category in KNOWLEDGE_BASE.values():
        for question in category["questions"]:
            if question["id"] == question_id:
                answer = question["answer"]
                break
        if answer:
            break
    
    if not answer:
        await send_message(build_text_message(to, "Lo siento, no pude encontrar la respuesta a esa pregunta."))
        await send_main_menu(to)
        return
    
    # Send typing indicator
    await send_typing_indicator_and_wait(to, 1.0)
    
    # Send the answer
    answer_text = f"📝 *Respuesta:*\n\n{answer}"
    await send_message(build_text_message(to, answer_text))
    
    # Wait a moment before asking for more help
    await asyncio.sleep(1.5)
    
    # Ask if they need more help
    await send_more_help_options(to)

async def send_more_help_options(to: str):
    """Send options to continue or finish conversation"""
    buttons = [
        {
            "type": "reply",
            "reply": {"id": "YES", "title": "Sí, por favor"}
        },
        {
            "type": "reply",
            "reply": {"id": "NO", "title": "No, gracias"}
        }
    ]
    
    payload = build_reply_button_message(
        to=to,
        body="¿Necesitas ayuda con alguna otra cosa?",
        buttons=buttons
    )
    
    await send_message(payload)
    
    # Update user session
    user_sessions[to] = {
        "state": "more_help",
        "last_interaction": datetime.now().isoformat()
    }

async def send_rating_request(to: str):
    """Send rating options"""
    buttons = [
        {
            "type": "reply",
            "reply": {"id": "RATE_EXCELLENT", "title": "Excelente"}
        },
        {
            "type": "reply",
            "reply": {"id": "RATE_GOOD", "title": "Bien"}
        },
        {
            "type": "reply",
            "reply": {"id": "RATE_NEEDS_IMPROVEMENT", "title": "Necesita mejorar"}
        }
    ]
    
    payload = build_reply_button_message(
        to=to,
        body="¡Gracias por usar nuestro asistente virtual! 😊\n\n¿Cómo calificarías la ayuda recibida?",
        buttons=buttons
    )
    
    await send_message(payload)
    
    # Update user session
    user_sessions[to] = {
        "state": "rating",
        "last_interaction": datetime.now().isoformat()
    }

async def handle_rating(to: str, rating_id: str):
    """Handle user rating"""
    rating_map = {
        "RATE_EXCELLENT": "Excelente",
        "RATE_GOOD": "Bien", 
        "RATE_NEEDS_IMPROVEMENT": "Necesita mejorar"
    }
    
    rating = rating_map.get(rating_id, "Desconocida")
    
    # Store rating
    user_ratings.append({
        "user": to,
        "rating": rating,
        "timestamp": datetime.now().isoformat()
    })
    
    thank_you_text = (
        f"¡Gracias por tu calificación: *{rating}*! 🙏\n\n"
        "Tu opinión es muy importante para nosotros y nos ayuda a mejorar nuestro servicio.\n\n"
        "Si necesitas más ayuda en el futuro, no dudes en escribirnos. "
        "¡Que tengas un excelente día! 😊"
    )
    
    await send_message(build_text_message(to, thank_you_text))
    
    # Clean up user session
    if to in user_sessions:
        del user_sessions[to]
    
    logger.info(f"User {to} rated the service as: {rating}")

# ==================== MESSAGE PROCESSING ====================

def is_greeting(text: str) -> bool:
    """Check if message is a greeting"""
    greetings = [
        "hola", "hello", "hi", "buenas", "buenos dias", "buenas tardes", 
        "buenas noches", "saludos", "que tal", "hey", "inicio"
    ]
    return text.lower().strip() in greetings

async def process_text_message(from_number: str, text: str, message_id: str):
    """Process incoming text message"""
    logger.info(f"Processing text message from {from_number}: {text}")
    
    # Check if it's a greeting
    if is_greeting(text):
        await send_welcome_sequence(from_number)
        return
    
    # Check current user state
    user_state = user_sessions.get(from_number, {}).get("state", "new")
    
    if user_state == "new":
        # New user, send welcome
        await send_welcome_sequence(from_number)
    else:
        # User sent text while in middle of flow, redirect to main menu
        redirect_text = (
            "Para brindarte la mejor ayuda, por favor utiliza los botones y opciones del menú. "
            "Te muestro nuevamente las opciones disponibles:"
        )
        await send_message(build_text_message(from_number, redirect_text))
        await asyncio.sleep(1.0)
        await send_main_menu(from_number)

async def process_interactive_message(from_number: str, interactive_data: Dict):
    """Process interactive message (button/list replies)"""
    message_type = interactive_data.get("type")
    
    if message_type == "list_reply":
        list_reply = interactive_data.get("list_reply", {})
        selection_id = list_reply.get("id")
        
        logger.info(f"List reply from {from_number}: {selection_id}")
        
        if selection_id == "APP_MAIN":
            await send_app_submenu(from_number)
        elif selection_id in KNOWLEDGE_BASE:
            await send_category_questions(from_number, selection_id)
        else:
            # It might be a question ID
            await send_answer(from_number, selection_id)
    
    elif message_type == "button_reply":
        button_reply = interactive_data.get("button_reply", {})
        button_id = button_reply.get("id")
        
        logger.info(f"Button reply from {from_number}: {button_id}")
        
        if button_id == "YES":
            await send_main_menu(from_number)
        elif button_id == "NO":
            await send_rating_request(from_number)
        elif button_id.startswith("RATE_"):
            await handle_rating(from_number, button_id)
        else:
            # It might be a question ID
            await send_answer(from_number, button_id)

# ==================== WEBHOOK VERIFICATION ====================

def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Verify webhook signature"""
    if not APP_SECRET:
        logger.warning("APP_SECRET not set, skipping signature verification")
        return True
    
    expected_signature = hmac.new(
        APP_SECRET.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(f"sha256={expected_signature}", signature)

# ==================== FASTAPI ENDPOINTS ====================

@app.get("/webhook")
async def verify_webhook(request: Request):
    """Verify webhook for WhatsApp"""
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token") 
    hub_challenge = request.query_params.get("hub.challenge")
    
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return JSONResponse(content=int(hub_challenge))
    
    logger.error("Webhook verification failed")
    raise HTTPException(status_code=403, detail="Forbidden")

@app.post("/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle incoming WhatsApp messages"""
    try:
        # Get request body and signature
        body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")
        
        # Verify signature
        if not verify_webhook_signature(body, signature):
            logger.error("Invalid webhook signature")
            raise HTTPException(status_code=403, detail="Invalid signature")
        
        # Parse webhook data
        data = json.loads(body.decode())
        
        # Process webhook entry
        if data.get("object") == "whatsapp_business_account":
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    
                    # Process messages
                    if "messages" in value:
                        for message in value["messages"]:
                            # Process in background to avoid blocking
                            background_tasks.add_task(process_message, message)
                    
                    # Process message status updates
                    if "statuses" in value:
                        for status in value["statuses"]:
                            logger.info(f"Message status update: {status}")
        
        return JSONResponse(content={"status": "success"})
    
    except json.JSONDecodeError:
        logger.error("Invalid JSON in webhook")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

async def process_message(message: Dict):
    """Process individual message"""
    try:
        from_number = message.get("from")
        message_id = message.get("id")
        message_type = message.get("type")
        
        logger.info(f"Processing message {message_id} from {from_number}, type: {message_type}")
        
        if message_type == "text":
            text_data = message.get("text", {})
            text_body = text_data.get("body", "")
            await process_text_message(from_number, text_body, message_id)
            
        elif message_type == "interactive":
            interactive_data = message.get("interactive", {})
            await process_interactive_message(from_number, interactive_data)
            
        elif message_type in ["image", "document", "audio", "video", "sticker"]:
            # Handle media messages by redirecting to main menu
            media_response = (
                "He recibido tu archivo multimedia. "
                "Para brindarte la mejor ayuda, por favor utiliza el menú de opciones:"
            )
            await send_message(build_text_message(from_number, media_response))
            await asyncio.sleep(1.0)
            await send_main_menu(from_number)
            
        else:
            # Handle other message types
            logger.info(f"Unsupported message type: {message_type}")
            await send_main_menu(from_number)
            
    except Exception as e:
        logger.error(f"Error processing message: {e}")

@app.get("/")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "Per Capital WhatsApp Chatbot",
        "version": "1.0.0",
        "active_sessions": len(user_sessions),
        "total_ratings": len(user_ratings)
    }

@app.get("/stats")
async def get_stats():
    """Get chatbot statistics"""
    rating_counts = {}
    for rating_data in user_ratings:
        rating = rating_data["rating"]
        rating_counts[rating] = rating_counts.get(rating, 0) + 1
    
    return {
        "active_sessions": len(user_sessions),
        "total_ratings": len(user_ratings),
        "rating_breakdown": rating_counts,
        "knowledge_base_categories": len(KNOWLEDGE_BASE),
        "total_questions": sum(len(cat["questions"]) for cat in KNOWLEDGE_BASE.values())
    }

@app.post("/send-message")
async def send_manual_message(request: Request):
    """Manual message sending endpoint for testing"""
    try:
        data = await request.json()
        to = data.get("to")
        message = data.get("message")
        message_type = data.get("type", "text")
        
        if not to or not message:
            raise HTTPException(status_code=400, detail="Missing 'to' or 'message' fields")
        
        if message_type == "text":
            payload = build_text_message(to, message)
        else:
            raise HTTPException(status_code=400, detail="Only text messages supported in manual send")
        
        success = await send_message(payload)
        
        if success:
            return {"status": "success", "message": "Message sent"}
        else:
            raise HTTPException(status_code=500, detail="Failed to send message")
            
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

@app.delete("/sessions/{phone_number}")
async def clear_user_session(phone_number: str):
    """Clear a specific user's session"""
    if phone_number in user_sessions:
        del user_sessions[phone_number]
        return {"status": "success", "message": f"Session cleared for {phone_number}"}
    else:
        raise HTTPException(status_code=404, detail="Session not found")

@app.delete("/sessions")
async def clear_all_sessions():
    """Clear all user sessions"""
    count = len(user_sessions)
    user_sessions.clear()
    return {"status": "success", "message": f"Cleared {count} sessions"}

# ==================== STARTUP VALIDATION ====================

@app.on_event("startup")
async def startup_event():
    """Validate environment variables on startup"""
    required_vars = {
        "WHATSAPP_TOKEN": WHATSAPP_TOKEN,
        "PHONE_NUMBER_ID": PHONE_NUMBER_ID,
        "VERIFY_TOKEN": VERIFY_TOKEN
    }
    
    missing_vars = []
    placeholder_vars = []
    
    for var_name, var_value in required_vars.items():
        if not var_value:
            missing_vars.append(var_name)
        elif "your_" in var_value.lower() and "_here" in var_value.lower():
            placeholder_vars.append(var_name)
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
    
    if placeholder_vars:
        logger.warning(f"Please update placeholder values for: {', '.join(placeholder_vars)}")
    
    logger.info("Per Capital WhatsApp Chatbot started successfully!")
    logger.info(f"Knowledge base loaded with {len(KNOWLEDGE_BASE)} categories")
    total_questions = sum(len(cat["questions"]) for cat in KNOWLEDGE_BASE.values())
    logger.info(f"Total questions available: {total_questions}")

# ==================== ERROR HANDLERS ====================

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=404,
        content={"error": "Endpoint not found", "detail": "The requested endpoint does not exist"}
    )

@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception):
    logger.error(f"Internal server error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": "An unexpected error occurred"}
    )

# ==================== UTILITY FUNCTIONS ====================

def get_question_by_id(question_id: str) -> Optional[Dict]:
    """Get question data by ID"""
    for category in KNOWLEDGE_BASE.values():
        for question in category["questions"]:
            if question["id"] == question_id:
                return question
    return None

def get_user_session_info(phone_number: str) -> Dict:
    """Get user session information"""
    session = user_sessions.get(phone_number, {})
    return {
        "exists": phone_number in user_sessions,
        "state": session.get("state", "new"),
        "last_interaction": session.get("last_interaction", "never"),
        "category": session.get("category", None)
    }

# ==================== MAIN EXECUTION ====================

if __name__ == "__main__":
    import uvicorn
    
    print("Starting Per Capital WhatsApp Chatbot...")
    print(f"Environment check:")
    print(f"  WHATSAPP_TOKEN: {'✓' if WHATSAPP_TOKEN and 'your_' not in WHATSAPP_TOKEN.lower() else '✗'}")
    print(f"  PHONE_NUMBER_ID: {'✓' if PHONE_NUMBER_ID and 'your_' not in PHONE_NUMBER_ID.lower() else '✗'}")
    print(f"  VERIFY_TOKEN: {'✓' if VERIFY_TOKEN and 'your_' not in VERIFY_TOKEN.lower() else '✗'}")
    print(f"  APP_SECRET: {'✓' if APP_SECRET and 'your_' not in APP_SECRET.lower() else '✗ (optional)'}")
    
    uvicorn.run(
        "main:app",  # Assuming this file is named main.py
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )