import os
import hmac
import hashlib
import json
import re
from typing import Optional, Any, Dict, List
import logging
from datetime import datetime
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.responses import PlainTextResponse
import httpx
import asyncio

# Configurar el logging para ver mensajes detallados
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ==================== VARIABLES DE ENTORNO ====================
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "").encode("utf-8") if os.getenv("APP_SECRET") else b""
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Verificar variables de entorno cruciales
if not all([VERIFY_TOKEN, WHATSAPP_TOKEN, PHONE_NUMBER_ID]):
    logging.error("Faltan variables de entorno cruciales: VERIFY_TOKEN, WHATSAPP_TOKEN, o PHONE_NUMBER_ID no están configuradas.")
    logging.info("Asegúrate de configurar estas variables en tu entorno de despliegue.")

# ==================== CONFIGURACIÓN DE BASE DE DATOS ====================
DATABASE_PATH = os.getenv("DATABASE_PATH", "whatsapp_bot.db")

def init_database():
    """Inicializa la base de datos con las tablas necesarias."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    # Tabla para usuarios
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_number TEXT UNIQUE NOT NULL,
            first_name TEXT,
            last_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_interaction TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE
        )
    """)
   
    # Tabla para estados de conversación
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversation_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_number TEXT UNIQUE NOT NULL,
            current_category TEXT,
            current_state TEXT DEFAULT 'main_menu',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
   
    # Tabla para mensajes
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_number TEXT NOT NULL,
            message_id TEXT UNIQUE,
            message_type TEXT NOT NULL,
            message_content TEXT,
            direction TEXT NOT NULL,  -- 'incoming' o 'outgoing'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
   
    # Tabla para categorías y preguntas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS qa_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            display_order INTEGER NOT NULL,
            is_active BOOLEAN DEFAULT TRUE
        )
    """)
   
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS qa_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            display_order INTEGER NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            FOREIGN KEY (category_id) REFERENCES qa_categories (id)
        )
    """)
   
    # Tabla para estadísticas de uso
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usage_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_number TEXT NOT NULL,
            category_id INTEGER,
            question_id INTEGER,
            action_type TEXT NOT NULL,  -- 'category_view', 'question_view', 'answer_view'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
   
    conn.commit()
    conn.close()
    logging.info("✅ Base de datos inicializada correctamente")

def populate_qa_data():
    """Pobla la base de datos con las preguntas y respuestas."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    # Verificar si ya hay datos
    cursor.execute("SELECT COUNT(*) FROM qa_categories")
    if cursor.fetchone()[0] > 0:
        conn.close()
        return
   
    # Datos de preguntas y respuestas
    qa_data = {
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
   
    # Insertar categorías y preguntas
    for i, (category_name, questions) in enumerate(qa_data.items(), 1):
        # Insertar categoría
        cursor.execute("""
            INSERT INTO qa_categories (name, display_order, is_active)
            VALUES (?, ?, ?)
        """, (category_name, i, True))
       
        category_id = cursor.lastrowid
       
        # Insertar preguntas de la categoría
        for j, (question, answer) in enumerate(questions.items(), 1):
            cursor.execute("""
                INSERT INTO qa_questions (category_id, question, answer, display_order, is_active)
                VALUES (?, ?, ?, ?, ?)
            """, (category_id, question, answer, j, True))
   
    conn.commit()
    conn.close()
    logging.info("✅ Datos de Q&A poblados en la base de datos")

# ==================== FUNCIONES DE BASE DE DATOS ====================
def get_or_create_user(phone_number: str) -> int:
    """Obtiene o crea un usuario en la base de datos."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    # Intentar obtener el usuario
    cursor.execute("SELECT id FROM users WHERE phone_number = ?", (phone_number,))
    user = cursor.fetchone()
   
    if user:
        # Actualizar última interacción
        cursor.execute("""
            UPDATE users SET last_interaction = CURRENT_TIMESTAMP
            WHERE phone_number = ?
        """, (phone_number,))
        user_id = user[0]
    else:
        # Crear nuevo usuario
        cursor.execute("""
            INSERT INTO users (phone_number) VALUES (?)
        """, (phone_number,))
        user_id = cursor.lastrowid
   
    conn.commit()
    conn.close()
    return user_id

def get_conversation_state(phone_number: str) -> Dict[str, Any]:
    """Obtiene el estado actual de la conversación."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    cursor.execute("""
        SELECT current_category, current_state FROM conversation_states
        WHERE phone_number = ?
    """, (phone_number,))
   
    result = cursor.fetchone()
    conn.close()
   
    if result:
        return {
            "current_category": result[0],
            "current_state": result[1]
        }
    return {"current_category": None, "current_state": "main_menu"}

def update_conversation_state(phone_number: str, category: str = None, state: str = "main_menu"):
    """Actualiza el estado de la conversación."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    cursor.execute("""
        INSERT OR REPLACE INTO conversation_states
        (phone_number, current_category, current_state, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    """, (phone_number, category, state))
   
    conn.commit()
    conn.close()

def log_message(phone_number: str, message_id: str, message_type: str,
                content: str, direction: str):
    """Registra un mensaje en la base de datos."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    try:
        cursor.execute("""
            INSERT INTO messages (phone_number, message_id, message_type, message_content, direction)
            VALUES (?, ?, ?, ?, ?)
        """, (phone_number, message_id, message_type, content, direction))
        conn.commit()
    except sqlite3.IntegrityError:
        # Mensaje ya existe
        pass
    finally:
        conn.close()

def log_usage_stat(phone_number: str, action_type: str, category_id: int = None, question_id: int = None):
    """Registra estadísticas de uso."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    cursor.execute("""
        INSERT INTO usage_stats (phone_number, category_id, question_id, action_type)
        VALUES (?, ?, ?, ?)
    """, (phone_number, category_id, question_id, action_type))
   
    conn.commit()
    conn.close()

def get_categories() -> List[Dict[str, Any]]:
    """Obtiene todas las categorías activas."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    cursor.execute("""
        SELECT id, name, display_order
        FROM qa_categories
        WHERE is_active = TRUE
        ORDER BY display_order
    """)
   
    categories = []
    for row in cursor.fetchall():
        categories.append({
            "id": row[0],
            "name": row[1],
            "display_order": row[2]
        })
   
    conn.close()
    return categories

def get_questions_by_category(category_id: int) -> List[Dict[str, Any]]:
    """Obtiene todas las preguntas de una categoría."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    cursor.execute("""
        SELECT id, question, answer, display_order
        FROM qa_questions
        WHERE category_id = ? AND is_active = TRUE
        ORDER BY display_order
    """, (category_id,))
   
    questions = []
    for row in cursor.fetchall():
        questions.append({
            "id": row[0],
            "question": row[1],
            "answer": row[2],
            "display_order": row[3]
        })
   
    conn.close()
    return questions

def get_answer_by_question_id(question_id: int) -> Optional[str]:
    """Obtiene la respuesta por ID de pregunta."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    cursor.execute("""
        SELECT answer FROM qa_questions
        WHERE id = ? AND is_active = TRUE
    """, (question_id,))
   
    result = cursor.fetchone()
    conn.close()
   
    return result[0] if result else None

# ==================== FUNCIONES DE UTILIDAD ====================
def is_back_command(text: str) -> bool:
    """Verifica si el mensaje es un comando para volver al menú principal."""
    back_keywords = ["volver", "menu", "menú", "principal", "inicio", "back", "0"]
    return text.strip().lower() in back_keywords

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
           
            # Registrar mensaje enviado
            log_message(
                phone_number=payload.get('to'),
                message_id=f"out_{datetime.now().isoformat()}",
                message_type=payload.get('type', 'text'),
                content=json.dumps(payload),
                direction='outgoing'
            )
           
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

async def send_welcome_menu(to_msisdn: str) -> Dict[str, Any]:
    """Envía el menú de bienvenida."""
    welcome_text = """🏦 *Bienvenido a Per Capital*

¡Hola! Gracias por contactarnos.

Soy tu asistente virtual y estoy aquí para ayudarte con todas tus dudas sobre inversiones y servicios financieros.

📋 *Opciones disponibles:*

*1.* Ver preguntas frecuentes
*2.* Hablar con soporte humano

💡 *Instrucciones:*
• Envía el número de la opción que prefieras (1 o 2)
• Escribe "volver" en cualquier momento para regresar a este menú"""

    # Actualizar estado de conversación
    update_conversation_state(to_msisdn, state="welcome_menu")
   
    return await send_text(to_msisdn, welcome_text)

async def send_main_menu(to_msisdn: str) -> Dict[str, Any]:
    """Envía el menú principal de categorías."""
    categories = get_categories()
   
    menu_text = "📋 *Menú Principal - Per Capital*\n\n"
    menu_text += "Selecciona una categoría enviando el número correspondiente:\n\n"
   
    for i, category in enumerate(categories, 1):
        menu_text += f"*{i}.* {category['name']}\n"
   
    menu_text += "\n💡 *Instrucciones:*\n"
    menu_text += "• Envía solo el número de la categoría (ej. '1')\n"
    menu_text += "• Escribe 'volver' para regresar al menú de bienvenida"
   
    # Limpiar el estado de la conversación
    update_conversation_state(to_msisdn, state="main_menu")
   
    return await send_text(to_msisdn, menu_text)

async def send_category_menu(to_msisdn: str, category_id: int) -> Dict[str, Any]:
    """Envía el menú de preguntas para una categoría específica."""
    categories = get_categories()
    category = next((c for c in categories if c['id'] == category_id), None)
   
    if not category:
        await send_text(to_msisdn, "❌ Categoría no válida. Por favor, envía un número de categoría válido.")
        return await send_main_menu(to_msisdn)
   
    questions = get_questions_by_category(category_id)
   
    if not questions:
        await send_text(to_msisdn, "❌ No hay preguntas disponibles en esta categoría.")
        return await send_main_menu(to_msisdn)
   
    menu_text = f"📂 *{category['name']}*\n\n"
    menu_text += "Selecciona una pregunta enviando el número correspondiente:\n\n"
   
    for i, question in enumerate(questions, 1):
        # Limpiar la pregunta de numeración previa
        clean_question = re.sub(r'^\d+\.\s*', '', question['question'])
        menu_text += f"*{i}.* {clean_question}\n"
   
    menu_text += f"\n💡 *Opciones:*\n"
    menu_text += "• Envía el número de la pregunta (ej. '1')\n"
    menu_text += "• Escribe 'volver' para regresar al menú principal"
   
    # Guardar el estado de la categoría actual
    update_conversation_state(to_msisdn, category=str(category_id), state="category_menu")
   
    # Registrar estadística de visualización de categoría
    log_usage_stat(to_msisdn, "category_view", category_id=category_id)
   
    return await send_text(to_msisdn, menu_text)

# ==================== PROCESAMIENTO DE MENSAJES ====================
async def process_text_message(from_msisdn: str, message_text: str, message_id: str) -> None:
    """Procesa los mensajes de texto del usuario según el flujo de conversación."""
    text_clean = message_text.strip()
   
    logging.info(f"📝 Procesando mensaje de texto de {from_msisdn}: '{text_clean}'")
   
    # Registrar mensaje recibido
    log_message(from_msisdn, message_id, "text", text_clean, "incoming")
   
    # Asegurar que el usuario existe en la base de datos
    get_or_create_user(from_msisdn)
   
    # Obtener estado actual de la conversación
    conversation_state = get_conversation_state(from_msisdn)
    current_state = conversation_state.get("current_state", "main_menu")
    current_category = conversation_state.get("current_category")
   
    # Verificar si es un comando para volver
    if is_back_command(text_clean):
        logging.info(f"🔄 Usuario {from_msisdn} solicitó volver")
        if current_state == "category_menu":
            await send_main_menu(from_msisdn)
        else:
            await send_welcome_menu(from_msisdn)
        return
   
    # Intentar interpretar el mensaje como un número
    try:
        choice = int(text_clean)
       
        if current_state == "welcome_menu":
            # Procesando selección del menú de bienvenida
            logging.info(f"🏠 Usuario {from_msisdn} en menú de bienvenida, opción {choice}")
           
            if choice == 1:
                # Ir a preguntas frecuentes
                await send_main_menu(from_msisdn)
            elif choice == 2:
                # Contactar soporte humano
                await send_text(from_msisdn, """👨‍💼 *Soporte Humano Activado*

Gracias por contactarnos. Un miembro especializado de nuestro equipo de Per Capital se pondrá en contacto contigo a la brevedad posible.

📞 También puedes llamarnos directamente si tu consulta es urgente.

Esta conversación automática ha finalizado. ¡Que tengas un excelente día! 🙋‍♀️""")
                # Limpiar estado de conversación
                update_conversation_state(from_msisdn, state="ended")
            else:
                await send_text(from_msisdn, "❌ Opción no válida. Por favor, elige 1 o 2.")
                await send_welcome_menu(from_msisdn)
       
        elif current_state == "main_menu":
            # Procesando selección de categoría
            categories = get_categories()
            logging.info(f"🗂️ Usuario {from_msisdn} seleccionó categoría {choice}")
           
            if 1 <= choice <= len(categories):
                category_id = categories[choice - 1]['id']
                await send_category_menu(from_msisdn, category_id)
            else:
                await send_text(from_msisdn, f"❌ Opción no válida. Por favor, elige un número entre 1 y {len(categories)}.")
                await send_main_menu(from_msisdn)
       
        elif current_state == "category_menu" and current_category:
            # Procesando selección de pregunta
            category_id = int(current_category)
            questions = get_questions_by_category(category_id)
           
            logging.info(f"❓ Usuario {from_msisdn} seleccionó pregunta {choice} de categoría {category_id}")
           
            if 1 <= choice <= len(questions):
                question = questions[choice - 1]
                answer = question['answer']
               
                # Registrar estadística de visualización de respuesta
                log_usage_stat(from_msisdn, "answer_view", category_id=category_id, question_id=question['id'])
               
                # Enviar la respuesta
                await send_text(from_msisdn, f"✅ *Respuesta:*\n\n{answer}")
               
                # Pequeña pausa antes de enviar el menú
                await asyncio.sleep(1)
               
                # Volver al menú principal después de dar la respuesta
                await send_text(from_msisdn, "📋 ¿Tienes alguna otra consulta?")
                await send_main_menu(from_msisdn)
            else:
                await send_text(from_msisdn, f"❌ Opción no válida. Por favor, elige un número entre 1 y {len(questions)}.")
                await send_category_menu(from_msisdn, category_id)
   
    except (ValueError, IndexError):
        # El input no es un número válido
        logging.info(f"⚠️ Entrada no numérica de {from_msisdn}: '{text_clean}'")
       
        if current_state == "category_menu" and current_category:
            # Si está en un submenú, reenviar el submenú con instrucciones
            await send_text(from_msisdn, "⚠️ Por favor, envía solo el número de la pregunta que te interesa.")
            await send_category_menu(from_msisdn, int(current_category))
        elif current_state == "main_menu":
            # Si está en el menú principal, reenviar con instrucciones
            await send_text(from_msisdn, "⚠️ Por favor, envía solo el número de la categoría que te interesa.")
            await send_main_menu(from_msisdn)
        else:
            # Si no hay estado o estado desconocido, enviar menú de bienvenida
            logging.info(f"🔄 Enviando menú de bienvenida a {from_msisdn}")
            await send_welcome_menu(from_msisdn)

# ==================== CONTEXT MANAGER PARA FASTAPI ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicialización al arranque
    logging.info("🚀 Inicializando WhatsApp Bot Per Capital...")
    init_database()
    populate_qa_data()
    logging.info("✅ Bot listo para recibir mensajes!")
    yield
    # Limpieza al cierre (si es necesario)
    logging.info("🛑 Cerrando WhatsApp Bot Per Capital...")

# Crear la aplicación FastAPI
app = FastAPI(
    title="WhatsApp Cloud API Webhook (Render/FastAPI)",
    lifespan=lifespan
)

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
                    else:
                        # Para cualquier otro tipo de mensaje, enviar menú de bienvenida
                        logging.info(f"📎 Mensaje de tipo '{message_type}' recibido - Enviando menú de bienvenida")
                        log_message(from_msisdn, message_id, message_type, f"Mensaje tipo: {message_type}", "incoming")
                        await send_welcome_menu(from_msisdn)
       
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
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    # Obtener estadísticas básicas
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
   
    cursor.execute("SELECT COUNT(*) FROM messages")
    total_messages = cursor.fetchone()[0]
   
    cursor.execute("SELECT COUNT(*) FROM qa_categories WHERE is_active = TRUE")
    total_categories = cursor.fetchone()[0]
   
    cursor.execute("SELECT COUNT(*) FROM qa_questions WHERE is_active = TRUE")
    total_questions = cursor.fetchone()[0]
   
    cursor.execute("SELECT COUNT(DISTINCT phone_number) FROM conversation_states WHERE current_state != 'ended'")
    active_conversations = cursor.fetchone()[0]
   
    conn.close()
   
    return {
        "status": "ok",
        "service": "WhatsApp Bot Per Capital",
        "version": "3.0",
        "database": {
            "total_users": total_users,
            "total_messages": total_messages,
            "total_categories": total_categories,
            "total_questions": total_questions,
            "active_conversations": active_conversations
        }
    }

@app.get("/status")
async def status_endpoint():
    """Endpoint de estado detallado para monitoreo."""
    categories = get_categories()
   
    return {
        "service_status": "running",
        "environment_variables": {
            "VERIFY_TOKEN": "✅" if VERIFY_TOKEN else "❌",
            "WHATSAPP_TOKEN": "✅" if WHATSAPP_TOKEN else "❌",
            "PHONE_NUMBER_ID": "✅" if PHONE_NUMBER_ID else "❌",
            "APP_SECRET": "✅" if APP_SECRET else "❌"
        },
        "database_status": "connected",
        "qa_categories": [cat["name"] for cat in categories],
        "graph_api_version": GRAPH_API_VERSION
    }

@app.get("/admin/users")
async def get_users():
    """Endpoint administrativo para obtener usuarios."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    cursor.execute("""
        SELECT phone_number, created_at, last_interaction, is_active
        FROM users
        ORDER BY last_interaction DESC
        LIMIT 100
    """)
   
    users = []
    for row in cursor.fetchall():
        users.append({
            "phone_number": row[0],
            "created_at": row[1],
            "last_interaction": row[2],
            "is_active": bool(row[3])
        })
   
    conn.close()
    return {"users": users}

@app.get("/admin/stats")
async def get_usage_stats():
    """Endpoint administrativo para obtener estadísticas de uso."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    # Estadísticas por categoría
    cursor.execute("""
        SELECT c.name, COUNT(us.id) as views
        FROM qa_categories c
        LEFT JOIN usage_stats us ON c.id = us.category_id AND us.action_type = 'category_view'
        WHERE c.is_active = TRUE
        GROUP BY c.id, c.name
        ORDER BY views DESC
    """)
   
    category_stats = []
    for row in cursor.fetchall():
        category_stats.append({
            "category": row[0],
            "views": row[1]
        })
   
    # Estadísticas por día
    cursor.execute("""
        SELECT DATE(created_at) as date, COUNT(*) as total_interactions
        FROM usage_stats
        WHERE created_at >= datetime('now', '-30 days')
        GROUP BY DATE(created_at)
        ORDER BY date DESC
        LIMIT 30
    """)
   
    daily_stats = []
    for row in cursor.fetchall():
        daily_stats.append({
            "date": row[0],
            "interactions": row[1]
        })
   
    # Preguntas más vistas
    cursor.execute("""
        SELECT q.question, COUNT(us.id) as views
        FROM qa_questions q
        LEFT JOIN usage_stats us ON q.id = us.question_id AND us.action_type = 'answer_view'
        WHERE q.is_active = TRUE
        GROUP BY q.id, q.question
        ORDER BY views DESC
        LIMIT 10
    """)
   
    popular_questions = []
    for row in cursor.fetchall():
        popular_questions.append({
            "question": row[0],
            "views": row[1]
        })
   
    conn.close()
   
    return {
        "category_stats": category_stats,
        "daily_stats": daily_stats,
        "popular_questions": popular_questions
    }

@app.post("/admin/broadcast")
async def broadcast_message(request: Request):
    """Endpoint administrativo para enviar mensajes masivos."""
    try:
        data = await request.json()
        message = data.get("message")
        target_filter = data.get("filter", "all")  # "all", "active", "recent"
       
        if not message:
            raise HTTPException(status_code=400, detail="Message is required")
       
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
       
        # Construir query según el filtro
        if target_filter == "active":
            cursor.execute("""
                SELECT DISTINCT phone_number FROM conversation_states
                WHERE current_state != 'ended' AND updated_at >= datetime('now', '-7 days')
            """)
        elif target_filter == "recent":
            cursor.execute("""
                SELECT phone_number FROM users
                WHERE last_interaction >= datetime('now', '-1 day')
            """)
        else:  # all
            cursor.execute("SELECT phone_number FROM users WHERE is_active = TRUE")
       
        phone_numbers = [row[0] for row in cursor.fetchall()]
        conn.close()
       
        success_count = 0
        error_count = 0
       
        for phone_number in phone_numbers:
            try:
                await send_text(phone_number, f"📢 *Mensaje de Per Capital*\n\n{message}")
                success_count += 1
                await asyncio.sleep(0.1)  # Rate limiting
            except Exception as e:
                logging.error(f"Error enviando mensaje a {phone_number}: {e}")
                error_count += 1
       
        return {
            "status": "completed",
            "total_recipients": len(phone_numbers),
            "successful_sends": success_count,
            "failed_sends": error_count
        }
   
    except Exception as e:
        logging.error(f"Error en broadcast: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/admin/clear-conversations")
async def clear_conversations():
    """Endpoint para limpiar todas las conversaciones activas."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    cursor.execute("SELECT COUNT(*) FROM conversation_states")
    count = cursor.fetchone()[0]
   
    cursor.execute("DELETE FROM conversation_states")
    conn.commit()
    conn.close()
   
    logging.info(f"🧹 Conversaciones limpiadas: {count}")
   
    return {
        "status": "success",
        "cleared_conversations": count,
        "message": f"Se limpiaron {count} conversaciones activas"
    }

@app.get("/admin/categories")
async def get_all_categories():
    """Endpoint administrativo para obtener todas las categorías con sus preguntas."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    cursor.execute("""
        SELECT c.id, c.name, c.display_order, c.is_active,
               q.id as question_id, q.question, q.answer, q.display_order as question_order
        FROM qa_categories c
        LEFT JOIN qa_questions q ON c.id = q.category_id AND q.is_active = TRUE
        WHERE c.is_active = TRUE
        ORDER BY c.display_order, q.display_order
    """)
   
    categories_dict = {}
    for row in cursor.fetchall():
        cat_id = row[0]
        if cat_id not in categories_dict:
            categories_dict[cat_id] = {
                "id": row[0],
                "name": row[1],
                "display_order": row[2],
                "is_active": bool(row[3]),
                "questions": []
            }
       
        if row[4]:  # question_id exists
            categories_dict[cat_id]["questions"].append({
                "id": row[4],
                "question": row[5],
                "answer": row[6],
                "display_order": row[7]
            })
   
    conn.close()
   
    return {"categories": list(categories_dict.values())}

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

# ==================== MENSAJE DE INICIO DEL SERVIDOR ====================
if __name__ == "__main__":
    print("🚀 Iniciando WhatsApp Bot Per Capital...")
   
    # Inicializar base de datos
    init_database()
    populate_qa_data()
   
    # Mostrar estadísticas de arranque
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
   
    cursor.execute("SELECT COUNT(*) FROM qa_categories WHERE is_active = TRUE")
    categories_count = cursor.fetchone()[0]
   
    cursor.execute("SELECT COUNT(*) FROM qa_questions WHERE is_active = TRUE")
    questions_count = cursor.fetchone()[0]
   
    cursor.execute("SELECT COUNT(*) FROM users")
    users_count = cursor.fetchone()[0]
   
    conn.close()
   
    print(f"📊 Base de datos inicializada:")
    print(f"  • Categorías de Q&A: {categories_count}")
    print(f"  • Preguntas totales: {questions_count}")
    print(f"  • Usuarios registrados: {users_count}")
    print("✅ Bot listo para recibir mensajes!")
   
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)