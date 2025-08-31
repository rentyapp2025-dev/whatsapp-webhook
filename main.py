import os
import json
import time
import asyncio
import logging
import hmac
import hashlib
import re
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
            {"id": "Q1_PC", "text": "¿Que es Per Capital?", "answer": "Es un grupo de empresas del Mercado de Valores Venezolano reguladas por la SUNAVAL.", "short_title": "¿Qué es?"},
            {"id": "Q2_PC", "text": "¿Quien regula a PER CAPITAL?", "answer": "La SUNAVAL (Superintendencia Nacional de Valores)", "short_title": "Regulación"},
            {"id": "Q3_PC", "text": "¿Que es la SUNAVAL?", "answer": "Es quien protege a inversionistas y regula a intermediarios y emisores del Mercado de Valores venezolano", "short_title": "SUNAVAL"},
            {"id": "Q4_PC", "text": "¿Que es la Bolsa de Valores de Caracas?", "answer": "Es el lugar donde se compran y venden bonos, acciones y otros instrumentos de manera ordenada a traves de las Casas de Bolsa y esta regulada por la SUNAVAL", "short_title": "BVC"},
            {"id": "Q5_PC", "text": "¿Como invierto?", "answer": "Para invertir en el Fondo Mutual Abierto de PER CAPITAL debes descargar el app, registrate, subir recaudos y colocar tus ordenes de compra.", "short_title": "Cómo invertir"}
        ]
    },
    "FONDO_MUTUAL": {
        "id": "FONDO_MUTUAL",
        "title": "Fondo Mutual Abierto",
        "questions": [
            {"id": "Q1_FMA", "text": "¿Que es un Fondo Mutual?", "answer": "Es un instrumento de inversion en grupo donde varias personas ponen dinero en un fondo que es gestionado por expertos y esta disenado para ser diversificado, de bajo riesgo y dirigido a pequenos inversionistas con poca experiencia", "short_title": "Fondo Mutual"},
            {"id": "Q2_FMA", "text": "¿Que es una Unidad de Inversion?", "answer": "Es una 'porcion' del fondo. Cuando inviertes adquieres unidades que representan tu parte del fondo.", "short_title": "Unidad de Inversión"},
            {"id": "Q3_FMA", "text": "¿Que es el VUI?", "answer": "El Valor de la Unidad de Inversion (VUI) es el precio de una Unidad de Inversion. Si el VUI sube tu inversion gana valor. Se calcula diariamente al cierre del dia y depende del comportamiento de las inversiones del fondo.", "short_title": "Valor VUI"},
            {"id": "Q4_FMA", "text": "¿Como invierto?", "answer": "Descarga el app para Android y IOS, registrate, sube recaudos, acepta los contratos, espera tu aprobacion y suscribe Unidades de Inversion cuando quieras y cuantas veces desees", "short_title": "Cómo invertir"},
            {"id": "Q5_FMA", "text": "¿Cual es el monto minimo de inversion?", "answer": "1 Unidad de Inversion", "short_title": "Monto Mínimo"},
            {"id": "Q6_FMA", "text": "¿Como gano?", "answer": "Ganas por apreciacion (subida del VUI) o por dividendo (en caso de que sea decretado)", "short_title": "Cómo gano"},
            {"id": "Q7_FMA", "text": "¿En cuanto tiempo gano?", "answer": "Ganas a largo plazo, se recomienda medir resultados trimestralmente", "short_title": "Plazo"},
            {"id": "Q8_FMA", "text": "¿Donde consigo mas informacion?", "answer": "En los prospectos y hojas de terminos en www.per-capital.com", "short_title": "Más información"}
        ]
    },
    "APP_GENERAL": {
        "id": "APP_GENERAL",
        "title": "Información General App",
        "questions": [
            {"id": "Q1_APP_GEN", "text": "¿Puedo comprar acciones y bonos?", "answer": "No, nuestra app es únicamente para invertir en nuestro Fondo Mutual Abierto. Pronto saldrá la nueva versión de nuestra app para negociar", "short_title": "Acciones y bonos"}
        ]
    },
    "APP_REGISTRO": {
        "id": "APP_REGISTRO",
        "title": "Registro en la App",
        "questions": [
            {"id": "Q1_APP_REG", "text": "¿Como me registro?", "answer": "Descarga el app, completa 100% de los datos, acepta los contratos, sube tus recaudos como Cedula de Identidad y Selfie y espera tu aprobacion.", "short_title": "Cómo registrarme"},
            {"id": "Q2_APP_REG", "text": "¿Cuanto tarda mi aprobacion?", "answer": "De 2 a 5 dias habiles siempre que hayas completado 100% de registro y recaudos", "short_title": "Tiempo de aprobación"},
            {"id": "Q3_APP_REG", "text": "¿Que hago si no me aprueban?", "answer": "Revisa que hayas completado 100% del registro y recaudos, sino contactanos en SOPORTE", "short_title": "Si no me aprueban"},
            {"id": "Q4_APP_REG", "text": "¿Puedo invertir si soy menor de edad?", "answer": "Debes dirigirte a nuestras oficinas y registrarte con tu representante legal", "short_title": "Inversión menor de edad"},
            {"id": "Q5_APP_REG", "text": "¿Puedo modificar alguno de mis datos?", "answer": "Si, pero por exigencia del ley entras nuevamente en revision", "short_title": "Modificar datos"},
            {"id": "Q6_APP_REG", "text": "¿Debo tener cuenta en la Caja Venezolana?", "answer": "No, para invertir en nuestro Fondo Mutual Abierto no es necesaria la cuenta en la CVV", "short_title": "Cuenta CVV"}
        ]
    },
    "APP_SUSCRIPCION": {
        "id": "APP_SUSCRIPCION",
        "title": "Suscripción",
        "questions": [
            {"id": "Q1_APP_SUS", "text": "¿Como suscribo (compro)?", "answer": "Haz click en Negociacion > Suscripcion > Monto a invertir > Suscribir > Metodo de Pago. Recuerda pagar desde TU cuenta bancaria y subir comprobante de pago", "short_title": "Cómo suscribir"},
            {"id": "Q2_APP_SUS", "text": "¿Como pago mi suscripcion?", "answer": "Debes pagar desde TU cuenta bancaria via Pago Movil. Y recuerda subir comprobante. IMPORTANTE: no se aceptan pagos de terceros.", "short_title": "Cómo pagar"},
            {"id": "Q3_APP_SUS", "text": "¿Puede pagar alguien por mi?", "answer": "No, la ley prohibe los pagos de terceros. Siempre debes pagar desde tu cuenta bancaria.", "short_title": "Pago de terceros"},
            {"id": "Q4_APP_SUS", "text": "¿Como veo mi inversion?", "answer": "En el Home en la seccion Mi Cuenta", "short_title": "Ver inversión"},
            {"id": "Q5_APP_SUS", "text": "¿Cuando veo mi inversion?", "answer": "Al cierre del sistema en dias habiles bancarios despues del cierre de mercado y la publicacion de tasas del Banco Central de Venezuela.", "short_title": "Cuándo la veo"},
            {"id": "Q6_APP_SUS", "text": "¿Cuales son las comisiones?", "answer": "3% flat Suscripcion, 3% flat Rescate y 5% anual Administracion", "short_title": "Comisiones"},
            {"id": "Q7_APP_SUS", "text": "¿Que hago despues de suscribir?", "answer": "Monitorea tu inversion desde el app", "short_title": "Después de suscribir"},
            {"id": "Q8_APP_SUS", "text": "¿Debo invertir siempre el mismo monto?", "answer": "No, puedes invertir el monto que desees", "short_title": "Monto de inversión"},
            {"id": "Q9_APP_SUS", "text": "¿Puedo invertir cuando quiera?", "answer": "Si, puedes invertir cuando quieras, las veces que quieras", "short_title": "Invertir cuando quiera"}
        ]
    },
    "APP_RESCATE": {
        "id": "APP_RESCATE",
        "title": "Rescate",
        "questions": [
            {"id": "Q1_APP_RES", "text": "¿Como rescato (vendo)?", "answer": "Haz click en Negociacion > Rescate > Unidades a Rescatar > Rescatar. Recuerda se enviaran fondos a TU cuenta bancaria", "short_title": "Cómo rescatar"},
            {"id": "Q2_APP_RES", "text": "¿Cuando me pagan mis rescates (ventas)?", "answer": "Al proximo dia habil bancario en horario de mercado", "short_title": "Pago de rescate"},
            {"id": "Q3_APP_RES", "text": "¿Como veo el saldo de mi inversion?", "answer": "En el Home en la seccion Mi Cuenta", "short_title": "Ver saldo"},
            {"id": "Q4_APP_RES", "text": "¿Cuando veo el saldo de mi inversion?", "answer": "Al cierre del sistema en dias habiles bancarios despues del cierre de mercado y la publicacion de tasas del Banco Central de Venezuela.", "short_title": "Actualización de saldo"},
            {"id": "Q5_APP_RES", "text": "¿Cuando puedo Rescatar?", "answer": "Cuando tu quieras, y se liquida en dias habiles bancarios.", "short_title": "Cuándo rescatar"},
            {"id": "Q6_APP_RES", "text": "¿Cuales son las comisiones?", "answer": "3% flat Suscripcion, 3% flat Rescate y 5% anual Administracion", "short_title": "Comisiones"}
        ]
    },
    "APP_POSICION": {
        "id": "APP_POSICION",
        "title": "Posición (Saldo)",
        "questions": [
            {"id": "Q1_APP_POS", "text": "¿Cuando se actualiza mi posicion (saldo)?", "answer": "Al cierre del sistema en dias habiles bancarios despues del cierre de mercado y la publicacion de tasas del Banco Central de Venezuela.", "short_title": "Actualización de saldo"},
            {"id": "Q2_APP_POS", "text": "¿Por que varia mi posicion (saldo)?", "answer": "Tu saldo y rendimiento sube si suben los precios de las inversiones del fondo, se reciben dividendos o cupones y bajan si estos precios caen.", "short_title": "Variación de saldo"},
            {"id": "Q3_APP_POS", "text": "¿Donde veo mi historico?", "answer": "En la seccion Historial", "short_title": "Ver historial"},
            {"id": "Q4_APP_POS", "text": "¿Donde veo reportes?", "answer": "En la seccion Documentos > Reportes > Año > Trimestre", "short_title": "Ver reportes"}
        ]
    },
    "RIESGOS": {
        "id": "RIESGOS",
        "title": "Riesgos",
        "questions": [
            {"id": "Q1_RIE", "text": "¿Cuales son los riesgos al invertir?", "answer": "Todas las inversionbes estan sujetas a riesgos y la perdida de capital es posible. Agunos riesgos son: riesgo de mercado, riesgo pais, riesgo cambiario, riesgo sector, entre otros.", "short_title": "Riesgos de inversión"}
        ]
    },
    "SOPORTE": {
        "id": "SOPORTE",
        "title": "Soporte",
        "questions": [
            {"id": "Q1_SOP", "text": "Estoy en revision, que hago?", "answer": "Asegurate de haber completado 100% datos y recaudos y espera tu aprobacion. Si tarda mas de lo habitual contactanos en SOPORTE", "short_title": "En revisión"},
            {"id": "Q2_SOP", "text": "No me llega el SMS", "answer": "Asegurate de tener buena senal y de que hayas colocado correctamente un numero telefonico venezolano", "short_title": "Problema con SMS"},
            {"id": "Q3_SOP", "text": "No me llega el Correo", "answer": "Asegurate de no dejar espacios al final cuando escribiste tu correo electronico", "short_title": "Problema con correo"},
            {"id": "Q4_SOP", "text": "No logro descargar el App", "answer": "Asegurate de que tu app store este configurada en la region de Venezuela", "short_title": "No puedo descargar app"},
            {"id": "Q5_SOP", "text": "No me abre el App", "answer": "Asegurate de tener la version actualizada y que tu tienda de apps este configurada en la region de Venezuela", "short_title": "La app no abre"},
            {"id": "Q6_SOP", "text": "Como recupero mi clave", "answer": "Seleccione Recuperar, te legara una clave temporal para ingresar y luego actualiza tu nueva clave", "short_title": "Recuperar clave"}
        ]
    }
}

# ==================== NAME HELPERS ====================

def extract_first_name(full_name: str) -> str:
    if not full_name:
        return ""
    # limpia espacios y signos comunes
    name = re.sub(r'[^\wÁÉÍÓÚáéíóúÑñ\s\'.-]', '', full_name, flags=re.UNICODE).strip()
    # primer token significativo
    parts = [p for p in name.split() if p and p.lower() not in {"de", "del", "la", "el"}]
    if not parts:
        return name
    # capitaliza primera letra de cada parte corta (solo para estética básica)
    first = parts[0]
    return first[:1].upper() + first[1:]

def set_user_name(phone: str, full_name: str):
    if not phone or not full_name:
        return
    session = user_sessions.setdefault(phone, {})
    session["full_name"] = full_name.strip()
    session["first_name"] = extract_first_name(full_name)
    session["last_interaction"] = datetime.now().isoformat()
    logger.info(f"Saved name for {phone}: {session['first_name']} ({session['full_name']})")

def parse_and_set_name_from_text(phone: str, text: str) -> Optional[str]:
    """
    Detecta frases: 'me llamo X', 'mi nombre es X', 'soy X'
    Retorna el primer nombre si encontró uno y lo guarda.
    """
    if not text:
        return None
    patterns = [
        r"(?:^|\b)(?:me llamo|mi nombre es)\s+([A-Za-zÁÉÍÓÚáéíóúÑñ\'.\- ]{2,})",
        r"(?:^|\b)soy\s+([A-Za-zÁÉÍÓÚáéíóúÑñ\'.\- ]{2,})"
    ]
    lowered = text.strip()
    for pat in patterns:
        m = re.search(pat, lowered, flags=re.IGNORECASE | re.UNICODE)
        if m:
            candidate = m.group(1).strip().rstrip(".!,;:)")
            # evita capturar frases largas con más de 5 palabras (generalmente descripciones)
            if len(candidate.split()) > 5:
                continue
            set_user_name(phone, candidate)
            return user_sessions.get(phone, {}).get("first_name")
    return None

def get_first_name(phone: str) -> str:
    return user_sessions.get(phone, {}).get("first_name", "")

# ==================== UTILITY FUNCTIONS ====================

def truncate_text(text: str, max_length: int, add_ellipsis: bool = True) -> str:
    """Truncate text to specified length"""
    if len(text) <= max_length:
        return text
    if add_ellipsis and max_length > 3:
        return text[:max_length-3] + "..."
    else:
        return text[:max_length]

def format_question_for_list(question: Dict, index: int) -> Dict:
    """Format question for list message using short_title"""
    title = f"{index}. {question.get('short_title', truncate_text(question['text'], 20))}"
    if len(title) > 24:
        title = title[:24]
    description = truncate_text(question["text"], 72)
    return {"title": title, "description": description}

def format_question_for_button(question: Dict, index: int) -> str:
    """Format question for reply button using short_title"""
    short_title = question.get('short_title', truncate_text(question['text'], 15))
    title = f"{index}. {short_title}"
    if len(title) > 20:
        title = title[:20]
    return title

# ==================== MESSAGE BUILDERS ====================

def build_text_message(to: str, text: str) -> Dict:
    return {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}

def build_interactive_list_message(to: str, header: str, body: str, sections: List[Dict]) -> Dict:
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": header},
            "body": {"text": body},
            "footer": {"text": "Per Capital - Tu asistente virtual"},
            "action": {"button": "Ver opciones", "sections": sections}
        }
    }

def build_reply_button_message(to: str, body: str, buttons: List[Dict]) -> Dict:
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
    return {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}

# ==================== WHATSAPP API FUNCTIONS ====================

async def send_message(payload: Dict) -> bool:
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
    try:
        await asyncio.sleep(0.5)
        await asyncio.sleep(seconds)
    except Exception as e:
        logger.error(f"Error in typing indicator: {e}")

# ==================== CONVERSATION FLOWS ====================

async def send_welcome_sequence(to: str):
    """Welcome message personalized with first name if available"""
    name = get_first_name(to)
    saludo = f"¡Hola, {name}! 👋" if name else "¡Hola! 👋"
    welcome_text = (
        f"{saludo} Bienvenido a Per Capital\n\n"
        "Soy tu asistente virtual y estoy aquí para ayudarte con todas tus consultas "
        "sobre inversiones, nuestra app y servicios financieros.\n\n"
        "¿Cómo puedo ayudarte hoy?"
    )
    await send_typing_indicator_and_wait(to, 1.5)
    await send_message(build_text_message(to, welcome_text))
    await asyncio.sleep(1.0)
    await send_main_menu(to)

async def send_main_menu(to: str):
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
    user_sessions.setdefault(to, {})
    user_sessions[to].update({
        "state": "main_menu",
        "last_interaction": datetime.now().isoformat()
    })

async def send_app_submenu(to: str):
    sections = [{
        "title": "Opciones de la App",
        "rows": [
            {"id": "APP_GENERAL", "title": "Info General", "description": "Funciones generales de la app"},
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
    user_sessions.setdefault(to, {})
    user_sessions[to].update({
        "state": "app_submenu",
        "last_interaction": datetime.now().isoformat()
    })

async def send_category_questions(to: str, category_id: str):
    category = KNOWLEDGE_BASE.get(category_id)
    if not category:
        await send_message(build_text_message(to, "Lo siento, no pude encontrar esa categoría."))
        await send_main_menu(to)
        return
    questions = category["questions"]
    if len(questions) <= 3:
        buttons = []
        for i, q in enumerate(questions[:3]):
            formatted_title = format_question_for_button(q, i+1)
            buttons.append({"type": "reply", "reply": {"id": q["id"], "title": formatted_title}})
        payload = build_reply_button_message(
            to=to,
            body=f"*{category['title']}*\n\nSelecciona tu pregunta:",
            buttons=buttons
        )
    else:
        rows = []
        for i, q in enumerate(questions[:10]):
            formatted_q = format_question_for_list(q, i+1)
            rows.append({"id": q["id"], "title": formatted_q["title"], "description": formatted_q["description"]})
        sections = [{"title": category["title"], "rows": rows}]
        payload = build_interactive_list_message(
            to=to,
            header=category["title"],
            body="Selecciona tu pregunta:",
            sections=sections
        )
    await send_message(payload)
    user_sessions.setdefault(to, {})
    user_sessions[to].update({
        "state": "questions_menu",
        "category": category_id,
        "last_interaction": datetime.now().isoformat()
    })

async def send_answer(to: str, question_id: str):
    answer = None
    question_text = None
    for category in KNOWLEDGE_BASE.values():
        for question in category["questions"]:
            if question["id"] == question_id:
                answer = question["answer"]
                question_text = question["text"]
                break
        if answer:
            break
    if not answer:
        await send_message(build_text_message(to, "Lo siento, no pude encontrar la respuesta a esa pregunta."))
        await send_main_menu(to)
        return
    await send_typing_indicator_and_wait(to, 1.5)
    name = get_first_name(to)
    header = f"📋 *Pregunta*{f' ({name})' if name else ''}:\n"
    answer_text = f"{header}{question_text}\n\n💡 *Respuesta:*\n{answer}"
    await send_message(build_text_message(to, answer_text))
    await asyncio.sleep(1.5)
    await send_more_help_options(to)

async def send_more_help_options(to: str):
    name = get_first_name(to)
    body = f"¿Algo más{', ' + name if name else ''}? 👋"
    buttons = [
        {"type": "reply", "reply": {"id": "HELP_YES", "title": "Sí, por favor"}},
        {"type": "reply", "reply": {"id": "HELP_NO", "title": "No, gracias"}}
    ]
    payload = build_reply_button_message(to=to, body=body, buttons=buttons)
    await send_message(payload)
    user_sessions.setdefault(to, {})
    user_sessions[to].update({
        "state": "more_help",
        "last_interaction": datetime.now().isoformat()
    })

async def send_rating_request(to: str):
    name = get_first_name(to)
    pref = f"¡Gracias{', ' + name if name else ''} por usar nuestro asistente virtual! 😊"
    body = (
        f"{pref}\n\n"
        "Por favor, califica la atención recibida para ayudarnos a mejorar:"
    )
    buttons = [
        {"type": "reply", "reply": {"id": "RATE_EXCELLENT", "title": "⭐⭐⭐ Excelente"}},
        {"type": "reply", "reply": {"id": "RATE_GOOD", "title": "⭐⭐ Bueno"}},
        {"type": "reply", "reply": {"id": "RATE_POOR", "title": "⭐ Necesita mejorar"}}
    ]
    payload = build_reply_button_message(to=to, body=body, buttons=buttons)
    await send_message(payload)
    user_sessions.setdefault(to, {})
    user_sessions[to].update({
        "state": "rating",
        "last_interaction": datetime.now().isoformat()
    })

async def handle_rating(to: str, rating_id: str):
    rating_map = {
        "RATE_EXCELLENT": "Excelente ⭐⭐⭐",
        "RATE_GOOD": "Bueno ⭐⭐",
        "RATE_POOR": "Necesita mejorar ⭐"
    }
    rating = rating_map.get(rating_id, "Desconocida")
    user_ratings.append({"user": to, "rating": rating, "timestamp": datetime.now().isoformat()})
    name = get_first_name(to)
    thank_you_text = (
        f"¡Muchas gracias{', ' + name if name else ''} por tu calificación: *{rating}*! 🙏\n\n"
        "Tu opinión es muy valiosa para nosotros y nos ayuda a mejorar continuamente nuestro servicio."
    )
    await send_message(build_text_message(to, thank_you_text))
    await asyncio.sleep(2.0)
    await send_conversation_end(to)

async def send_conversation_end(to: str):
    name = get_first_name(to)
    end_message = (
        f"🔚 *Esta conversación ha terminado*{f', {name}' if name else ''}\n\n"
        "Si necesitas más ayuda en el futuro, no dudes en escribirnos nuevamente. "
        "Estaremos aquí para asistirte.\n\n"
        "¡Que tengas un excelente día! 😊\n\n"
        "_Per Capital - Invirtiendo en tu futuro_"
    )
    await send_message(build_text_message(to, end_message))
    user_sessions.setdefault(to, {})
    user_sessions[to].update({
        "state": "finished",
        "last_interaction": datetime.now().isoformat()
    })
    logger.info(f"Conversation ended for user {to}")

# ==================== MESSAGE PROCESSING ====================

def is_greeting(text: str) -> bool:
    greetings = [
        "hola", "hello", "hi", "buenas", "buenos dias", "buenas tardes",
        "buenas noches", "saludos", "que tal", "hey", "inicio", "empezar",
        "comenzar", "start"
    ]
    return text.lower().strip() in greetings

def is_negative_response(text: str) -> bool:
    negative_responses = [
        "no", "no gracias", "no, gracias", "nada más", "nada mas",
        "ya no", "suficiente", "está bien", "esta bien", "listo",
        "perfecto", "ok", "vale"
    ]
    return text.lower().strip() in negative_responses

async def process_text_message(from_number: str, text: str, message_id: str):
    logger.info(f"Processing text message from {from_number}: {text}")

    # intenta capturar y guardar nombre si el usuario lo declara
    parsed_first = parse_and_set_name_from_text(from_number, text)

    if is_greeting(text):
        await send_welcome_sequence(from_number)
        return

    user_state = user_sessions.get(from_number, {}).get("state", "new")

    if user_state in ["new", "finished"] or from_number not in user_sessions:
        await send_welcome_sequence(from_number)
        return

    if user_state == "more_help" and is_negative_response(text):
        await send_rating_request(from_number)
        return

    # Si el usuario nos dijo su nombre fuera de flujo, agradécele y muestra menú
    if parsed_first and user_state not in ["main_menu", "app_submenu", "questions_menu"]:
        await send_message(build_text_message(
            from_number,
            f"¡Encantado, {parsed_first}! He guardado tu nombre. Te muestro el menú principal:"
        ))
        await asyncio.sleep(0.8)
        await send_main_menu(from_number)
        return

    redirect_text = (
        "Para brindarte la mejor ayuda, por favor utiliza los botones y opciones del menú. "
        "Te muestro nuevamente las opciones disponibles:"
    )
    await send_message(build_text_message(from_number, redirect_text))
    await asyncio.sleep(1.0)
    await send_main_menu(from_number)

async def process_interactive_message(from_number: str, interactive_data: Dict):
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
            await send_answer(from_number, selection_id)

    elif message_type == "button_reply":
        button_reply = interactive_data.get("button_reply", {})
        button_id = button_reply.get("id")
        logger.info(f"Button reply from {from_number}: {button_id}")

        if button_id == "HELP_YES":
            await send_main_menu(from_number)
        elif button_id == "HELP_NO":
            await send_rating_request(from_number)
        elif button_id.startswith("RATE_"):
            await handle_rating(from_number, button_id)
        else:
            await send_answer(from_number, button_id)

# ==================== WEBHOOK VERIFICATION ====================

def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    if not APP_SECRET:
        logger.warning("APP_SECRET not set, skipping signature verification")
        return True
    expected_signature = hmac.new(APP_SECRET.encode('utf-8'), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected_signature}", signature)

# ==================== FASTAPI ENDPOINTS ====================

@app.get("/webhook")
async def verify_webhook(request: Request):
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")

    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        # Devuelve el challenge como número o texto; aquí lo retornamos tal cual lo envía Meta
        try:
            return JSONResponse(content=int(hub_challenge))
        except Exception:
            return JSONResponse(content=hub_challenge)

    logger.error("Webhook verification failed")
    raise HTTPException(status_code=403, detail="Forbidden")

@app.post("/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")

        if not verify_webhook_signature(body, signature):
            logger.error("Invalid webhook signature")
            raise HTTPException(status_code=403, detail="Invalid signature")

        data = json.loads(body.decode())

        if data.get("object") == "whatsapp_business_account":
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})

                    # === NUEVO: capturar nombres desde contacts ===
                    # Ejemplo: "contacts": [{"profile": {"name": "Juan Pérez"}, "wa_id": "58412..."}]
                    for contact in value.get("contacts", []) or []:
                        wa_id = contact.get("wa_id")
                        profile_name = ((contact.get("profile") or {}).get("name") or "").strip()
                        if wa_id and profile_name:
                            set_user_name(wa_id, profile_name)

                    if "messages" in value:
                        for message in value["messages"]:
                            background_tasks.add_task(process_message, message)

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
    try:
        from_number = message.get("from")
        message_id = message.get("id")
        message_type = message.get("type")

        # Si el payload de este mensaje trae nombre (caso raro), también lo guardamos
        maybe_name = ((message.get("profile") or {}).get("name") or "").strip()
        if from_number and maybe_name:
            set_user_name(from_number, maybe_name)

        logger.info(f"Processing message {message_id} from {from_number}, type: {message_type}")

        if message_type == "text":
            text_data = message.get("text", {})
            text_body = text_data.get("body", "")
            await process_text_message(from_number, text_body, message_id)

        elif message_type == "interactive":
            interactive_data = message.get("interactive", {})
            await process_interactive_message(from_number, interactive_data)

        elif message_type in ["image", "document", "audio", "video", "sticker"]:
            media_response = (
                "He recibido tu archivo multimedia. "
                "Para brindarte la mejor ayuda, por favor utiliza el menú de opciones:"
            )
            await send_message(build_text_message(from_number, media_response))
            await asyncio.sleep(1.0)
            await send_main_menu(from_number)

        else:
            logger.info(f"Unsupported message type: {message_type}")
            await send_main_menu(from_number)

    except Exception as e:
        logger.error(f"Error processing message: {e}")

@app.get("/")
async def health_check():
    return {
        "status": "healthy",
        "service": "Per Capital WhatsApp Chatbot",
        "version": "2.1.0",
        "active_sessions": len(user_sessions),
        "total_ratings": len(user_ratings)
    }

@app.get("/stats")
async def get_stats():
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
    if phone_number in user_sessions:
        del user_sessions[phone_number]
        return {"status": "success", "message": f"Session cleared for {phone_number}"}
    else:
        raise HTTPException(status_code=404, detail="Session not found")

@app.delete("/sessions")
async def clear_all_sessions():
    count = len(user_sessions)
    user_sessions.clear()
    return {"status": "success", "message": f"Cleared {count} sessions"}

# ==================== STARTUP VALIDATION ====================

@app.on_event("startup")
async def startup_event():
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

# ==================== MAIN EXECUTION ====================

if __name__ == "__main__":
    import uvicorn
    print("Starting Per Capital WhatsApp Chatbot...")
    print(f"Environment check:")
    print(f"  WHATSAPP_TOKEN: {'✓' if WHATSAPP_TOKEN and 'your_' not in WHATSAPP_TOKEN.lower() else '✗'}")
    print(f"  PHONE_NUMBER_ID: {'✓' if PHONE_NUMBER_ID and 'your_' not in PHONE_NUMBER_ID.lower() else '✗'}")
    print(f"  VERIFY_TOKEN: {'✓' if VERIFY_TOKEN and 'your_' not in VERIFY_TOKEN.lower() else '✗'}")
    print(f"  APP_SECRET: {'✓' if APP_SECRET and 'your_' not in APP_SECRET.lower() else '✗ (optional)'}")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
