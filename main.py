import os
import hmac
import hashlib
import json
import re
from typing import Optional, Any, Dict
import logging

from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.responses import PlainTextResponse
import httpx

# Configuración de logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Variables de entorno
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "").encode("utf-8") if os.getenv("APP_SECRET") else b""
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Verificar configuración mínima
if not all([VERIFY_TOKEN, WHATSAPP_TOKEN, PHONE_NUMBER_ID]):
    logging.error("⚠️ Faltan variables de entorno obligatorias")

app = FastAPI(title="WhatsApp Cloud API Webhook - Per Capital")

# Base de conocimiento
QA_CATEGORIZED = {
    "1. Inversiones": {
        "1. ¿Cómo puedo invertir?": "Primero debe estar registrado...",
        "2. ¿Qué es el Fondo Mutual Abierto?": "El Fondo Mutual Abierto es una cesta..."
    },
    "2. Retiros y Transacciones": {
        "1. ¿Cómo hago un retiro?": "Selecciona rescate > ingresa las unidades...",
        "2. ¿Nunca he rescatado?": "Si usted no ha realizado algún rescate..."
    },
    "3. Problemas con la Cuenta": {
        "1. ¿Mi usuario está en revisión?": "Estimado inversionista por favor enviar cédula...",
        "2. ¿Cómo recupero la clave?": "Seleccione la opción recuperar..."
    }
}

conversation_state: Dict[str, str] = {}

# ==================== Utils WhatsApp ====================

def verify_signature(signature: Optional[str], body: bytes) -> bool:
    if not APP_SECRET:
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    their_signature = signature.split("sha256=")[-1].strip()
    mac = hmac.new(APP_SECRET, msg=body, digestmod=hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), their_signature)


async def _post_messages(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


async def send_text(to_msisdn: str, text: str) -> Dict[str, Any]:
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "text",
        "text": {"body": text}
    }
    return await _post_messages(payload)


async def send_initial_menu(to_msisdn: str):
    """
    Menú inicial con botones + lista de categorías
    """
    # Botones de bienvenida
    buttons_payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {"type": "text", "text": "🏦 Bienvenido a Per Capital"},
            "body": {
                "text": "¿Cómo quieres continuar?\n\n"
                        "👉 Usa el asistente virtual para respuestas rápidas\n"
                        "👉 O conecta con soporte humano"
            },
            "footer": {"text": "Selecciona una opción"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "bot_qa", "title": "🤖 Asistente Virtual"}},
                    {"type": "reply", "reply": {"id": "human_support", "title": "👨‍💼 Soporte Humano"}}
                ]
            }
        }
    }

    # Lista de categorías (list message)
    rows = []
    for idx, category in enumerate(QA_CATEGORIZED.keys(), 1):
        rows.append({
            "id": f"cat_{idx}",
            "title": category,
            "description": f"Ver preguntas sobre {category.split('. ', 1)[1]}"
        })

    list_payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "📋 Menú de Categorías"},
            "body": {"text": "Selecciona una categoría para ver las preguntas frecuentes:"},
            "footer": {"text": "Usa el menú para navegar"},
            "action": {
                "button": "Ver categorías",
                "sections": [{"title": "Categorías disponibles", "rows": rows}]
            }
        }
    }

    # Enviar botones primero, luego lista
    await _post_messages(buttons_payload)
    return await _post_messages(list_payload)


# ==================== Procesar mensajes ====================

async def process_interactive_message(from_msisdn: str, interactive_data: Dict[str, Any]) -> None:
    if interactive_data.get("type") == "button_reply":
        button_id = interactive_data["button_reply"]["id"]
        if button_id == "bot_qa":
            await send_text(from_msisdn, "Has elegido el *asistente virtual*. Ahora selecciona una categoría 👇")
            await send_initial_menu(from_msisdn)
        elif button_id == "human_support":
            await send_text(from_msisdn, "👨‍💼 Soporte humano activado. Un agente se pondrá en contacto contigo.")
            conversation_state.pop(from_msisdn, None)

    elif interactive_data.get("type") == "list_reply":
        list_id = interactive_data["list_reply"]["id"]
        if list_id.startswith("cat_"):
            cat_index = int(list_id.split("_")[1])
            category_name = list(QA_CATEGORIZED.keys())[cat_index - 1]
            questions = QA_CATEGORIZED[category_name]

            rows = []
            for i, q in enumerate(questions.keys(), 1):
                rows.append({
                    "id": f"q_{cat_index}_{i}",
                    "title": q,
                    "description": "Haz clic para ver respuesta"
                })

            payload = {
                "messaging_product": "whatsapp",
                "to": from_msisdn,
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "header": {"type": "text", "text": f"📂 {category_name}"},
                    "body": {"text": "Selecciona una pregunta 👇"},
                    "action": {
                        "button": "Ver preguntas",
                        "sections": [{"title": "Preguntas", "rows": rows}]
                    }
                }
            }
            await _post_messages(payload)

        elif list_id.startswith("q_"):
            _, cat_index, q_index = list_id.split("_")
            category_name = list(QA_CATEGORIZED.keys())[int(cat_index) - 1]
            question = list(QA_CATEGORIZED[category_name].keys())[int(q_index) - 1]
            answer = QA_CATEGORIZED[category_name][question]
            await send_text(from_msisdn, f"✅ *Respuesta:*\n\n{answer}")


# ==================== Endpoints ====================

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge or "", status_code=200)
    raise HTTPException(status_code=403, detail="Verification token mismatch")


@app.post("/webhook")
async def receive_webhook(request: Request):
    body_bytes = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_signature(signature, body_bytes):
        raise HTTPException(status_code=403, detail="Invalid signature")

    data = await request.json()
    logging.info(f"Webhook recibido: {json.dumps(data, indent=2)}")

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])
            for message in messages:
                from_msisdn = message.get("from")
                if message.get("type") == "interactive":
                    await process_interactive_message(from_msisdn, message["interactive"])
                else:
                    await send_initial_menu(from_msisdn)

    return Response(status_code=200)


@app.get("/")
async def health_check():
    return {"status": "ok", "categories": len(QA_CATEGORIZED)}


if __name__ == "__main__":
    print("🚀 Bot listo con menú inicial de botones y lista interactiva")