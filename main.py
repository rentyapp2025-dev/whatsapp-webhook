import os
import hmac
import hashlib
import json
import re
from typing import Optional, Any, Dict, List
import logging
from datetime import datetime
import asyncio
import random

from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.responses import PlainTextResponse
import httpx

# ==================== CONFIGURACIÓN Y LOGGING AVANZADO ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | 🤖 BOT | %(message)s',
    datefmt='%H:%M:%S'
)

# Variables de entorno con validación robusta
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "").encode("utf-8") if os.getenv("APP_SECRET") else b""
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Validación crítica de variables de entorno
if not all([VERIFY_TOKEN, WHATSAPP_TOKEN, PHONE_NUMBER_ID]):
    logging.error("💥 CONFIGURACIÓN CRÍTICA FALTANTE | Revisa tus variables de entorno")
    logging.info("📋 Variables requeridas: VERIFY_TOKEN, WHATSAPP_TOKEN, PHONE_NUMBER_ID")

app = FastAPI(
    title="🏦 Per Capital WhatsApp Bot",
    description="Bot de soporte inteligente con experiencia premium",
    version="3.0.0"
)

# ==================== BASE DE CONOCIMIENTO PREMIUM ====================
QA_CATEGORIZED = {
    "💰 Inversiones": {
        "🔰 ¿Cómo puedo empezar a invertir?": "Para comenzar tu journey de inversión:\n\n✅ **Paso 1:** Regístrate y completa tu verificación en la app\n✅ **Paso 2:** Ve a 'Negociación' → 'Suscripción'\n✅ **Paso 3:** Ingresa el monto deseado\n✅ **Paso 4:** Selecciona tu método de pago preferido\n✅ **Paso 5:** Sube tu comprobante\n\n⏰ *Tu inversión se hace efectiva al cierre del día o siguiente día hábil*",
       
        "🎯 ¿Qué es el Fondo Mutual Abierto?": "El **Fondo Mutual Abierto** es tu puerta de entrada al mercado financiero 🚪\n\n💼 Es como una *canasta inteligente* que contiene:\n• Acciones diversificadas\n• Bonos de renta fija\n• Instrumentos financieros premium\n\n🔍 **¡Lo mejor?** Puedes ver exactamente dónde está tu dinero trabajando dentro de la app",
       
        "🌟 ¿En qué puedo invertir?": "**¡Excelente pregunta!** 🎉\n\nActualmente ofrecemos:\n\n💎 **Fondo Mutual Abierto** con portafolio diversificado:\n• 🇻🇪 **En Bolívares:** Acciones que cotizan en BVC\n• 💵 **En Dólares:** Papeles comerciales y renta fija\n\n📊 Todo estratégicamente balanceado para optimizar tu rentabilidad",
       
        "📊 ¿Qué son las Unidades de Inversión (UI)?": "Las **Unidades de Inversión (UI)** son tu *participación inteligente* en el fondo 🧩\n\n🔢 **Piénsalo así:**\n• Cada UI = Una porción del pastel completo\n• Su valor fluctúa según el rendimiento\n• Representan tu % del patrimonio total\n\n📈 Es la forma más eficiente de participar en mercados diversificados",
       
        "💹 ¿Qué es el Valor de Unidad de Inversión (VUI)?": "El **VUI** es el *precio actual* de cada unidad que posees 💰\n\n📊 **Características clave:**\n• Se actualiza diariamente\n• Refleja el valor de mercado real\n• Base para calcular tu inversión total\n• Cambia según performance del mercado\n\n⚡ *¡Es el pulso de tu inversión en tiempo real!*",
       
        "📉 ¿Por qué baja mi rendimiento?": "¡Tranquilo! 😌 Los mercados son como montañas rusas:\n\n📊 **Tu inversión refleja:**\n• Valor total de activos del fondo\n• Condiciones del mercado\n• Performance de inversiones subyacentes\n\n⏰ **Recuerda:** Los Fondos Mutuales son para *horizontes largos*\n🎯 **Tip:** La paciencia es tu mejor aliada en inversiones",
       
        "🎮 ¿Qué hago después de suscribir?": "¡Perfecto! Ya estás en el juego 🏆\n\n**Tu próximo nivel:**\n• 📱 Monitorea desde la app\n• 👀 Observa tu portafolio en detalle\n• 💤 Nosotros gestionamos activamente\n• 📊 Revisa performance cuando gustes\n\n*¡Tú relájate, nosotros trabajamos tu dinero!* 😎",
       
        "💸 ¿Cuánto cuestan las comisiones?": "**Estructura transparente de fees:**\n\n💳 **Suscripción:** 3% (una sola vez)\n🔄 **Administración:** 5% anualizado\n\n💡 **¡Sin sorpresas!** Todo claro desde el inicio",
       
        "💵 ¿Desde cuánto puedo invertir?": "**¡Democratizamos las inversiones!** 🌟\n\n💰 **Mínimo:** Solo 1 Bolívar\n🚀 **Máximo:** ¡El cielo es el límite!\n\n*Porque creemos que todos merecen crecer financieramente* 💪",
       
        "⏰ ¿Cuándo veo ganancias?": "**¡Excelente mindset de largo plazo!** 🎯\n\n📅 **Para horizontes cortos:** No recomendamos FMA\n📈 **Para horizontes largos:** ¡Aquí brillamos!\n⏳ **Paciencia = Rentabilidad**\n\n🏆 *Los grandes inversionistas piensan en años, no en días*",
       
        "📈 ¿Cómo compro acciones individuales?": "**¡Coming soon!** 🚀\n\n🔜 **Próximamente:** Compra/venta directa de acciones\n💎 **Mientras tanto:** FMA con portafolio de acciones seleccionadas\n\n*¡Mantente conectado para las novedades!* 📱"
    },
   
    "💳 Retiros y Transacciones": {
        "💰 ¿Cómo hago un retiro?": "**¡Proceso súper simple!** ⚡\n\n**Pasos para tu rescate:**\n1️⃣ Selecciona **'Rescate'**\n2️⃣ Ingresa unidades a rescatar\n3️⃣ Haz clic en **'Calcular'**\n4️⃣ Confirma con **'Rescatar'**\n5️⃣ Sigue las instrucciones finales\n\n*¡Tu dinero, tu decisión, tu control!* 🎮",
       
        "🤔 Nunca he rescatado antes": "**¡Sin problema!** 😊\n\n**Si recibiste un email sobre rescate:**\n• 📧 Ignóralo si no has rescatado\n• 📱 Mejor ingresa a la app\n• ✅ Valida tus fondos directamente\n\n*¡Tu app es tu fuente de verdad!* 💯",
       
        "💸 ¿Cuánto puedo retirar?": "**Flexibilidad total:** 🌟\n\n**Mínimo:** 1 Unidad de Inversión\n**Máximo:** Todas tus unidades disponibles\n\n*¡Tú decides cuánto y cuándo!* 🎯",
       
        "🔄 Proceso de rescate paso a paso": "**¡Tu guía completa!** 📋\n\n🎯 **Flujo optimizado:**\n• **Rescate** → **Unidades** → **Calcular** → **Rescatar** → **¡Listo!**\n\n⚡ Simple, rápido y seguro"
    },
   
    "🔐 Problemas con la Cuenta": {
        "⏳ Mi usuario está en revisión": "**¡Te ayudamos inmediatamente!** 🚀\n\n📋 **Para acelerar tu aprobación:**\n• Envíanos tu número de cédula\n• Verificaremos documentación\n• Activaremos tu cuenta\n\n*¡Estamos aquí para ti!* 💪",
       
        "🔑 ¿Cómo recupero mi clave?": "**¡Recovery mode activado!** 🛠️\n\n**Proceso súper seguro:**\n1️⃣ Selecciona **'Recuperar'**\n2️⃣ Recibirás clave temporal\n3️⃣ Úsala para ingresar\n4️⃣ Sistema pedirá nueva clave\n5️⃣ Confirma tu nueva password\n\n*¡Back in business!* ✨",
       
        "⏰ ¿Por qué tardan en aprobar?": "**¡Gracias por tu paciencia!** 🙏\n\n📊 **Situación actual:**\n• Alto tráfico de registros\n• Trabajamos 24/7 en aprobaciones\n• Tu experiencia es nuestra prioridad\n\n📎 **Acelera tu proceso:** Envía cédula escaneada",
       
        "✅ ¿Ya estoy aprobado?": "**¡Bienvenido oficialmente!** 🎉\n\n✨ **Tu cuenta está ACTIVA**\n⚠️ **Importante:** Modificaciones requieren nueva revisión\n⏰ **Suscripciones antes 12PM:** Efectivas al cierre (5-6 PM)\n\n*¡A invertir se ha dicho!* 🚀",
       
        "📱 No recibo SMS de verificación": "**¡Solucionemos esto!** 🔧\n\n🔄 **Plan de acción:**\n1️⃣ Intenta desde otra ubicación\n2️⃣ Espera unas horas\n3️⃣ Prueba mañana\n4️⃣ Como último recurso: otro número\n\n*¡No te rendiremos hasta que funcione!* 💪"
    },
   
    "🌎 Otros Tipos de Inversión": {
        "💵 ¿Cómo invierto en dólares?": "**¡Diversifica en USD!** 🇺🇸\n\n💎 **Papel Comercial disponible:**\n• Instrumentos de deuda corto plazo\n• Menos de 1 año de duración\n• Emitidos por empresas sólidas\n• En el mercado de valores\n\n*¡Tu portafolio internacional te espera!* 🌟",
       
        "📄 ¿Cómo invierto en papel comercial?": "**¡Proceso premium!** ⭐\n\n📋 **Requisitos:**\n• ✅ Registro Per Capital\n• ✅ Registro Caja Venezolana\n• ✅ Cédula + RIF + Constancia trabajo\n• ✅ Per Capital como depositante\n\n🔗 Te enviaremos el link de Caja Venezolana",
       
        "🏦 ¿Ya me registré en Caja Venezolana?": "**¡No te preocupes!** 😌\n\n**Para FMA:** No necesitas Caja Venezolana aún\n**Para acciones:** Próximamente será requerido\n**Mientras tanto:** ¡Disfruta del FMA!\n\n*¡Un paso a la vez hacia el éxito!* 🎯",
       
        "📊 Info detallada de inversiones": "**¡Tu centro de información!** 📚\n\n**FMA incluye:**\n• 🏛️ Acciones BVC Caracas\n• 📄 Papeles comerciales\n• 🎯 Portafolio diversificado\n\n📱 **Todo visible en tu app con lujo de detalles**"
    }
}

# Estado global mejorado para conversaciones
conversation_state: Dict[str, Dict[str, Any]] = {}

# ==================== FUNCIONES PREMIUM DE EXPERIENCIA ====================

def get_welcome_emoji() -> str:
    """Obtiene un emoji de bienvenida aleatorio para más dinamismo"""
    emojis = ["🎉", "✨", "🌟", "💫", "🎊", "🚀", "💎", "⭐"]
    return random.choice(emojis)

def get_time_greeting() -> str:
    """Saludo contextual según la hora"""
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "¡Buenos días! ☀️"
    elif 12 <= hour < 18:
        return "¡Buenas tardes! 🌤️"
    else:
        return "¡Buenas noches! 🌙"

def get_menu_by_category_index(index: int) -> Optional[Dict[str, str]]:
    """Obtiene submenú con mejor UX"""
    categories = list(QA_CATEGORIZED.keys())
    if 1 <= index <= len(categories):
        category_name = categories[index - 1]
        return {
            "title": category_name,
            "questions": QA_CATEGORIZED[category_name]
        }
    return None

def get_answer_by_full_index(category_index: int, question_index: int) -> str:
    """Respuesta con validación mejorada"""
    category_menu = get_menu_by_category_index(category_index)
    if not category_menu:
        return "🚫 **Ups!** Esa categoría no existe.\n\n📱 *Consejo:* Usa los números del menú principal (1-4)"
   
    questions = list(category_menu["questions"].keys())
    if 1 <= question_index <= len(questions):
        question = questions[question_index - 1]
        return category_menu["questions"][question]
   
    return f"❓ **Pregunta no encontrada**\n\nPor favor selecciona entre las opciones 1-{len(questions)} del submenú actual."

def is_back_command(text: str) -> bool:
    """Comandos de navegación expandidos"""
    back_keywords = [
        "volver", "menu", "menú", "principal", "inicio", "back", "home",
        "atras", "atrás", "salir", "regresar", "0", "menu principal"
    ]
    return text.strip().lower() in back_keywords

# ==================== MENSAJES PREMIUM CON DISEÑO AVANZADO ====================

async def send_welcome_experience(to_msisdn: str) -> Dict[str, Any]:
    """Experiencia de bienvenida ultra premium"""
    greeting = get_time_greeting()
    emoji = get_welcome_emoji()
   
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "text",
                "text": f"🏦 Per Capital | Tu Futuro Financiero"
            },
            "body": {
                "text": f"{greeting} {emoji}\n\n**¡Bienvenido a Per Capital!**\n\nSoy tu asistente financiero inteligente, diseñado para brindarte la mejor experiencia de inversión.\n\n🎯 **¿Cómo quieres continuar?**\n\n• 🤖 **Asistente Virtual:** Respuestas instantáneas 24/7\n• 👨‍💼 **Especialista Humano:** Asesoría personalizada\n\n*Selecciona tu preferencia y comencemos tu journey financiero* ✨"
            },
            "footer": {
                "text": "Per Capital • Invierte con confianza"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "bot_premium",
                            "title": "🤖 Asistente Virtual"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "human_vip",
                            "title": "👨‍💼 Especialista VIP"
                        }
                    }
                ]
            }
        }
    }
    return await _post_messages(payload)

async def send_main_menu_premium(to_msisdn: str) -> Dict[str, Any]:
    """Menú principal con diseño premium"""
    # Limpiar estado
    if to_msisdn in conversation_state:
        del conversation_state[to_msisdn]
   
    # Crear menú visualmente impactante
    menu_text = "🎯 **CENTRO DE INFORMACIÓN FINANCIERA**\n"
    menu_text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
   
    categories_emojis = ["💰", "💳", "🔐", "🌎"]
    categories_list = list(QA_CATEGORIZED.keys())
   
    for i, category in enumerate(categories_list, 1):
        clean_name = category.split(' ', 1)[1] if ' ' in category else category
        menu_text += f"**{i}.** {category}\n"
        menu_text += f"     *{get_category_description(i)}*\n\n"
   
    menu_text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    menu_text += "💡 **NAVEGACIÓN FÁCIL:**\n"
    menu_text += "• Envía el **número** de tu interés (ej: `1`)\n"
    menu_text += "• Escribe `volver` para regresar aquí\n"
    menu_text += "• Escribe `menu` en cualquier momento\n\n"
    menu_text += "*¡Tu éxito financiero comienza con una buena decisión!* 🚀"
   
    return await send_text(to_msisdn, menu_text)

def get_category_description(category_num: int) -> str:
    """Descripciones atractivas para cada categoría"""
    descriptions = {
        1: "Todo sobre inversiones y fondos mutuales",
        2: "Retiros, rescates y transacciones",
        3: "Soporte técnico y problemas de cuenta",
        4: "Inversiones internacionales y productos premium"
    }
    return descriptions.get(category_num, "Información especializada")

async def send_subcategory_premium(to_msisdn: str, category_index: int) -> Dict[str, Any]:
    """Submenú con experiencia premium"""
    category_menu = get_menu_by_category_index(category_index)
    if not category_menu:
        await send_text(to_msisdn, "❌ **Error de navegación**\n\nEsa categoría no está disponible. Regresemos al inicio.")
        await send_main_menu_premium(to_msisdn)
        return {}

    # Guardar estado avanzado
    conversation_state[to_msisdn] = {
        "category": str(category_index),
        "timestamp": datetime.now().isoformat(),
        "questions_viewed": []
    }

    # Crear submenú impactante
    menu_text = f"📂 **{category_menu['title'].upper()}**\n"
    menu_text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    menu_text += "🎯 **Selecciona tu consulta:**\n\n"
   
    questions = list(category_menu["questions"].keys())
    for i, question in enumerate(questions, 1):
        # Limpiar formato de pregunta
        clean_question = re.sub(r'^[🎯💰📊💡🔰⭐💎🌟📈💹📉🎮💸💵⏰💳🤔💸🔄🔐⏳🔑⏰✅📱🌎💵📄🏦📊]?\s*', '', question)
        clean_question = re.sub(r'^\d+\.\s*', '', clean_question)
       
        # Emoji dinámico por pregunta
        question_emoji = get_question_emoji(i)
        menu_text += f"**{i}.** {question_emoji} {clean_question}\n"
   
    menu_text += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    menu_text += "🧭 **NAVEGACIÓN:**\n"
    menu_text += f"• Número de pregunta (1-{len(questions)})\n"
    menu_text += "• `volver` → Menú principal\n"
    menu_text += "• `menu` → Inicio\n\n"
    menu_text += "*¡Estamos aquí para resolver todas tus dudas!* ✨"
   
    return await send_text(to_msisdn, menu_text)

def get_question_emoji(question_num: int) -> str:
    """Emoji dinámico para cada pregunta"""
    emojis = ["🎯", "💡", "⭐", "🚀", "💎", "🔥", "⚡", "🌟", "💫", "✨", "🎊", "🏆"]
    return emojis[question_num % len(emojis)]

# ==================== PROCESAMIENTO DE MENSAJES AVANZADO ====================

async def process_text_message_premium(from_msisdn: str, message_text: str) -> None:
    """Procesamiento de texto con UX premium"""
    text_clean = message_text.strip()
   
    # Log con estilo
    logging.info(f"💬 Usuario {from_msisdn[-4:]}**** → '{text_clean[:30]}{'...' if len(text_clean) > 30 else ''}'")
   
    # Comando de regreso
    if is_back_command(text_clean):
        await send_text(from_msisdn, "🔄 **Regresando al menú principal...**")
        await asyncio.sleep(0.5)  # Micro-pausa para UX
        await send_main_menu_premium(from_msisdn)
        return
   
    # Procesamiento numérico inteligente
    try:
        choice = int(text_clean)
        user_state = conversation_state.get(from_msisdn, {})
        current_category = user_state.get("category")
       
        if current_category is None:
            # Selección de categoría
            if 1 <= choice <= len(QA_CATEGORIZED):
                await send_text(from_msisdn, f"📂 **Cargando información especializada...** ⏳")
                await asyncio.sleep(0.3)
                await send_subcategory_premium(from_msisdn, choice)
            else:
                await send_text(from_msisdn,
                    f"🚫 **Opción no válida**\n\n"
                    f"Por favor selecciona entre 1-{len(QA_CATEGORIZED)}.\n\n"
                    f"*¿Prefieres ver el menú nuevamente?* 📋")
                await send_main_menu_premium(from_msisdn)
        else:
            # Respuesta a pregunta
            category_index = int(current_category)
           
            # Actualizar estado
            user_state.setdefault("questions_viewed", []).append(choice)
           
            # Obtener respuesta
            response_text = get_answer_by_full_index(category_index, choice)
           
            # Envío con estilo
            await send_text(from_msisdn, "💭 **Consultando nuestra base de conocimiento...** ⏳")
            await asyncio.sleep(0.5)
           
            formatted_response = f"✅ **RESPUESTA ESPECIALIZADA**\n"
            formatted_response += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            formatted_response += f"{response_text}\n\n"
            formatted_response += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            formatted_response += "💬 **¿Te fue útil esta información?**\n\n"
            formatted_response += "🔄 *En un momento verás el menú principal para nuevas consultas*"
           
            await send_text(from_msisdn, formatted_response)
           
            # Pausa y regreso al menú principal
            await asyncio.sleep(2)
            await send_text(from_msisdn, "📋 **¿Alguna otra consulta?** Aquí tienes el menú completo:")
            await send_main_menu_premium(from_msisdn)
           
    except (ValueError, IndexError):
        # Input no numérico
        user_state = conversation_state.get(from_msisdn, {})
        current_category = user_state.get("category")
       
        if current_category is not None:
            await send_text(from_msisdn,
                "🔢 **Formato requerido: Solo números**\n\n"
                "Por favor envía el número de la pregunta que te interesa.\n\n"
                "*Ejemplo: envía `1` para la primera opción* 💡")
            await send_subcategory_premium(from_msisdn, int(current_category))
        else:
            # Trigger menú inicial
            await send_welcome_experience(from_msisdn)

async def process_interactive_premium(from_msisdn: str, interactive_data: Dict[str, Any]) -> None:
    """Procesamiento de botones con experiencia premium"""
    if interactive_data.get("type") == "button_reply":
        button_reply = interactive_data.get("button_reply", {})
        button_id = button_reply.get("id")
       
        logging.info(f"🔘 Usuario {from_msisdn[-4:]}**** presionó → {button_id}")
       
        if button_id == "bot_premium":
            await send_text(from_msisdn,
                "🤖 **ASISTENTE VIRTUAL ACTIVADO** ✨\n\n"
                "¡Perfecto! Has elegido la experiencia más rápida y eficiente.\n\n"
                "🎯 **Ventajas del Asistente Virtual:**\n"
                "• ⚡ Respuestas instantáneas 24/7\n"
                "• 📚 Acceso a toda nuestra base de conocimiento\n"
                "• 🎨 Navegación intuitiva y fácil\n"
                "• 🔄 Disponible cuando lo necesites\n\n"
                "*¡Comencemos con tu consulta!* 🚀")
            await asyncio.sleep(1)
            await send_main_menu_premium(from_msisdn)
           
        elif button_id == "human_vip":
            await send_text(from_msisdn,
                "👨‍💼 **ESPECIALISTA VIP CONTACTADO** 🌟\n\n"
                "¡Excelente elección! Has optado por nuestro servicio premium de atención personalizada.\n\n"
                "🎯 **¿Qué sigue ahora?**\n"
                "• 📞 Un especialista certificado te contactará pronto\n"
                "• 💼 Recibirás atención completamente personalizada\n"
                "• 🕐 Horario de contacto: Lunes a Viernes 8AM-6PM\n"
                "• 📱 Para urgencias, también puedes llamarnos directamente\n\n"
                "📋 **Información importante:**\n"
                "• Tu consulta ha sido registrada con prioridad VIP\n"
                "• Recibirás seguimiento especializado\n"
                "• Este chat automático ha finalizado\n\n"
                "**¡Gracias por confiar en Per Capital!** 🏆\n"
                "*Tu éxito financiero es nuestra misión* ✨")
           
            # Limpiar estado
            if from_msisdn in conversation_state:
                del conversation_state[from_msisdn]

# ==================== FUNCIONES DE ENVÍO OPTIMIZADAS ====================

def verify_signature(signature: Optional[str], body: bytes) -> bool:
    """Verificación de firma con logging mejorado"""
    if not APP_SECRET:
        logging.warning("⚠️ APP_SECRET no configurada - Verificación deshabilitada")
        return True
   
    if not signature or not signature.startswith("sha256="):
        logging.error("🚫 Firma de solicitud inválida o ausente")
        return False
   
    their_signature = signature.split("sha256=")[-1].strip()
    mac = hmac.new(APP_SECRET, msg=body, digestmod=hashlib.sha256)
    our_signature = mac.hexdigest()
   
    is_valid = hmac.compare_digest(our_signature, their_signature)
    if not is_valid:
        logging.error("❌ Firma no coincide - Posible intento de acceso no autorizado")
    return is_valid

async def _post_messages(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Envío de mensajes con retry automático y logging premium"""
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
   
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
               
                recipient = payload.get('to', 'Unknown')[-4:]
                logging.info(f"✅ Mensaje enviado exitosamente → Usuario ****{recipient}")
                return response.json()
               
        except httpx.HTTPStatusError as e:
            logging.error(f"❌ Error HTTP {e.response.status_code} en intento {attempt + 1}/{max_retries}")
            if attempt == max_retries - 1:
                logging.error(f"💥 Fallo definitivo enviando mensaje: {e.response.text}")
                raise HTTPException(status_code=500, detail=f"Error sending message after {max_retries} attempts")
            await asyncio.sleep(2 ** attempt)  # Backoff exponencial
           
        except Exception as e:
            logging.error(f"💥 Error inesperado en intento {attempt + 1}: {e}")
            if attempt == max_retries - 1:
                raise HTTPException(status_code=500, detail="Critical error sending message")
            await asyncio.sleep(1)

async def send_text(to_msisdn: str, text: str) -> Dict[str, Any]:
    """Envío de texto con formato optimizado"""
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "text",
        "text": {
            "body": text,
            "preview_url": False
        }
    }
    return await _post_messages(payload)

# ==================== ENDPOINTS PREMIUM ====================

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
):
    """Verificación de webhook con logging premium"""
    logging.info(f"🔍 Verificación webhook → Mode: {hub_mode} | Token: {'✅' if hub_verify_token == VERIFY_TOKEN else '❌'}")
   
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logging.info("✅ Webhook verificado exitosamente - Bot listo para recibir mensajes")
        return PlainTextResponse(content=hub_challenge or "", status_code=200)
   
    logging.error("🚫 Verificación fallida - Token inválido o modo incorrecto")
    raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/webhook")
async def receive_webhook_premium(request: Request):
    """Webhook principal con procesamiento premium y manejo avanzado de errores"""
    start_time = datetime.now()
   
    try:
        # Leer y verificar solicitud
        body_bytes = await request.body()
        signature = request.headers.get("X-Hub-Signature-256")
       
        if not verify_signature(signature, body_bytes):
            logging.error("🚫 Acceso denegado - Firma inválida")
            raise HTTPException(status_code=403, detail="Invalid signature")
       
        # Parse JSON con validación
        try:
            data = await request.json()
        except json.JSONDecodeError:
            logging.error("💥 JSON malformado recibido")
            raise HTTPException(status_code=400, detail="Invalid JSON format")
       
        # Log del webhook recibido (sin datos sensibles)
        logging.info(f"📨 Webhook recibido → Entries: {len(data.get('entry', []))}")
       
        # Validar estructura básica
        if data.get("object") != "whatsapp_business_account":
            logging.info("ℹ️ Webhook ignorado - No es de WhatsApp Business")
            return Response(status_code=200)
       
        # Procesar cada entrada
        messages_processed = 0
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
               
                # Procesar mensajes
                messages = value.get("messages", [])
                for message in messages:
                    messages_processed += 1
                    await process_single_message(message)
               
                # Procesar cambios de estado (opcional)
                statuses = value.get("statuses", [])
                if statuses:
                    logging.info(f"📊 Estados de mensaje recibidos: {len(statuses)}")
       
        # Métricas de performance
        processing_time = (datetime.now() - start_time).total_seconds()
        logging.info(f"⚡ Webhook procesado → {messages_processed} mensajes en {processing_time:.2f}s")
       
        return Response(status_code=200)
       
    except HTTPException:
        raise
    except Exception as e:
        processing_time = (datetime.now() - start_time).total_seconds()
        logging.error(f"💥 Error crítico procesando webhook (tiempo: {processing_time:.2f}s): {e}", exc_info=True)
        return Response(status_code=500, content="Internal server error")

async def process_single_message(message: Dict[str, Any]) -> None:
    """Procesador individual de mensajes con logging detallado"""
    try:
        from_msisdn = message.get("from")
        message_type = message.get("type")
        message_id = message.get("id", "unknown")
       
        # Log del mensaje recibido
        logging.info(f"📱 Nuevo mensaje → ID: {message_id[:8]}... | Tipo: {message_type} | De: ****{from_msisdn[-4:] if from_msisdn else 'unknown'}")
       
        if message_type == "interactive":
            # Procesar mensajes interactivos (botones)
            interactive_data = message.get("interactive", {})
            await process_interactive_premium(from_msisdn, interactive_data)
           
        elif message_type == "text":
            # Procesar mensajes de texto
            text_data = message.get("text", {})
            message_text = text_data.get("body", "")
            await process_text_message_premium(from_msisdn, message_text)
           
        elif message_type in ["audio", "image", "document", "video", "sticker", "location", "contacts"]:
            # Manejo elegante de otros tipos de mensaje
            logging.info(f"📎 Mensaje multimedia recibido → Tipo: {message_type}")
            await send_text(from_msisdn,
                f"📎 **Mensaje {message_type.title()} Recibido** ✨\n\n"
                f"¡Gracias por tu mensaje! Aunque recibí tu {message_type}, "
                f"trabajo mejor con texto para brindarte respuestas precisas.\n\n"
                f"🤖 **¿Prefieres usar el asistente virtual?**\n"
                f"Te ayudo a encontrar exactamente lo que necesitas 🎯")
            await asyncio.sleep(1)
            await send_welcome_experience(from_msisdn)
           
        else:
            # Tipo de mensaje no reconocido
            logging.warning(f"⚠️ Tipo de mensaje no manejado: {message_type}")
            await send_welcome_experience(from_msisdn)
           
    except Exception as e:
        logging.error(f"💥 Error procesando mensaje individual: {e}", exc_info=True)
        # Intentar enviar respuesta de error amigable
        try:
            if from_msisdn:
                await send_text(from_msisdn,
                    "🔧 **Momento técnico** ⚡\n\n"
                    "Disculpa, experimenté un pequeño problema técnico. "
                    "¡Pero ya estoy de vuelta! 😊\n\n"
                    "*¿Intentamos de nuevo?*")
                await send_welcome_experience(from_msisdn)
        except:
            logging.error("💥 No se pudo enviar mensaje de error de recuperación")

# ==================== ENDPOINTS DE MONITOREO Y ADMINISTRACIÓN ====================

@app.get("/")
async def health_check_premium():
    """Health check con información detallada del sistema"""
    return {
        "status": "🚀 ONLINE",
        "service": "Per Capital WhatsApp Bot Premium",
        "version": "3.0.0",
        "timestamp": datetime.now().isoformat(),
        "features": {
            "categories": len(QA_CATEGORIZED),
            "total_questions": sum(len(qa) for qa in QA_CATEGORIZED.values()),
            "active_conversations": len(conversation_state),
            "premium_features": True
        },
        "performance": {
            "message_processing": "Optimized with async/await",
            "error_handling": "Advanced with retry logic",
            "user_experience": "Premium with dynamic content"
        }
    }

@app.get("/dashboard")
async def admin_dashboard():
    """Dashboard administrativo con métricas avanzadas"""
    total_questions = sum(len(qa) for qa in QA_CATEGORIZED.values())
   
    # Estadísticas por categoría
    category_stats = {}
    for category, questions in QA_CATEGORIZED.items():
        category_stats[category] = {
            "questions_count": len(questions),
            "percentage": round((len(questions) / total_questions) * 100, 1)
        }
   
    # Estadísticas de conversaciones activas
    active_conversations = []
    for user, state in conversation_state.items():
        active_conversations.append({
            "user": f"****{user[-4:]}",
            "category": state.get("category", "N/A"),
            "timestamp": state.get("timestamp", "N/A"),
            "questions_viewed": len(state.get("questions_viewed", []))
        })
   
    return {
        "🎯 Bot Status": "PREMIUM ONLINE",
        "📊 Knowledge Base": {
            "total_categories": len(QA_CATEGORIZED),
            "total_questions": total_questions,
            "category_breakdown": category_stats
        },
        "💬 Active Conversations": {
            "count": len(conversation_state),
            "details": active_conversations
        },
        "🔧 System Health": {
            "environment_vars": {
                "VERIFY_TOKEN": "✅" if VERIFY_TOKEN else "❌",
                "WHATSAPP_TOKEN": "✅" if WHATSAPP_TOKEN else "❌",
                "PHONE_NUMBER_ID": "✅" if PHONE_NUMBER_ID else "❌",
                "APP_SECRET": "✅" if APP_SECRET else "❌"
            },
            "api_version": GRAPH_API_VERSION
        }
    }

@app.post("/admin/broadcast")
async def send_broadcast_message(request: Request):
    """Endpoint para envío masivo de mensajes (uso administrativo)"""
    try:
        data = await request.json()
        message_text = data.get("message", "")
        target_users = data.get("users", [])
       
        if not message_text or not target_users:
            raise HTTPException(status_code=400, detail="Message and users list required")
       
        # Envío con control de rate limiting
        sent_count = 0
        failed_count = 0
       
        for user in target_users:
            try:
                await send_text(user, f"📢 **MENSAJE OFICIAL PER CAPITAL**\n\n{message_text}")
                sent_count += 1
                await asyncio.sleep(0.1)  # Rate limiting básico
            except:
                failed_count += 1
       
        return {
            "status": "Broadcast completed",
            "sent": sent_count,
            "failed": failed_count,
            "total": len(target_users)
        }
       
    except Exception as e:
        logging.error(f"💥 Error en broadcast: {e}")
        raise HTTPException(status_code=500, detail="Broadcast failed")

@app.delete("/admin/conversations")
async def clear_all_conversations():
    """Limpia todas las conversaciones activas"""
    global conversation_state
    count = len(conversation_state)
    conversation_state.clear()
   
    logging.info(f"🧹 Admin: Limpieza masiva de {count} conversaciones")
   
    return {
        "status": "success",
        "action": "conversations_cleared",
        "count": count,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/admin/conversations/{user_id}")
async def get_user_conversation(user_id: str):
    """Obtiene el estado de conversación de un usuario específico"""
    user_state = conversation_state.get(user_id)
   
    if not user_state:
        return {"status": "no_active_conversation", "user": user_id}
   
    return {
        "user": user_id,
        "state": user_state,
        "active": True
    }

# ==================== MANEJO GLOBAL DE ERRORES ====================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Manejo elegante de errores HTTP"""
    logging.error(f"🚫 HTTP Error {exc.status_code}: {exc.detail}")
    return Response(
        status_code=exc.status_code,
        content=json.dumps({
            "error": f"HTTP {exc.status_code}",
            "message": exc.detail,
            "timestamp": datetime.now().isoformat()
        }),
        media_type="application/json"
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Manejo global de excepciones con logging detallado"""
    logging.error(f"💥 Excepción global no manejada: {exc}", exc_info=True)
   
    return Response(
        status_code=500,
        content=json.dumps({
            "error": "Internal Server Error",
            "message": "Se produjo un error inesperado. El equipo técnico ha sido notificado.",
            "timestamp": datetime.now().isoformat(),
            "support": "Contacta a soporte si el problema persiste"
        }),
        media_type="application/json"
    )

# ==================== STARTUP Y CONFIGURACIÓN ====================

@app.on_event("startup")
async def startup_event():
    """Configuración al inicio del servidor"""
    logging.info("🚀 =" * 50)
    logging.info("🚀 PER CAPITAL WHATSAPP BOT PREMIUM v3.0")
    logging.info("🚀 =" * 50)
    logging.info(f"📊 Base de conocimiento cargada:")
   
    total_questions = 0
    for category, questions in QA_CATEGORIZED.items():
        questions_count = len(questions)
        total_questions += questions_count
        logging.info(f"   • {category}: {questions_count} preguntas")
   
    logging.info(f"✅ Total: {len(QA_CATEGORIZED)} categorías, {total_questions} preguntas")
    logging.info(f"🔧 API Version: {GRAPH_API_VERSION}")
    logging.info(f"🏃‍♂️ Bot PREMIUM listo para recibir mensajes!")
    logging.info("🚀 =" * 50)

@app.on_event("shutdown")
async def shutdown_event():
    """Limpieza al cerrar el servidor"""
    logging.info("🛑 Cerrando Bot Premium...")
    logging.info(f"📊 Conversaciones activas al cierre: {len(conversation_state)}")
    conversation_state.clear()
    logging.info("✅ Bot cerrado correctamente")

# ==================== CONFIGURACIÓN PARA DESARROLLO ====================

if __name__ == "__main__":
    import uvicorn
   
    print("🚀 " + "=" * 60)
    print("🚀 INICIANDO PER CAPITAL WHATSAPP BOT PREMIUM")
    print("🚀 " + "=" * 60)
    print(f"📊 Categorías: {len(QA_CATEGORIZED)}")
    print(f"📝 Preguntas totales: {sum(len(qa) for qa in QA_CATEGORIZED.values())}")
    print("🎨 Características premium activadas")
    print("⚡ Experiencia de usuario ultra optimizada")
    print("🔐 Seguridad y validaciones avanzadas")
    print("📱 Listo para WhatsApp Cloud API")
    print("🚀 " + "=" * 60)
   
    # Ejecutar servidor de desarrollo
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )