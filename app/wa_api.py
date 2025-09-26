import hmac
import hashlib
from typing import Optional, Dict, Any, List
import httpx

from .config import GRAPH_BASE, WHATSAPP_TOKEN, PHONE_NUMBER_ID, APP_SECRET_RAW

APP_SECRET = APP_SECRET_RAW.encode("utf-8") if APP_SECRET_RAW else b""


# ===========================
# Seguridad (Webhook verify)
# ===========================
def verify_signature(signature: Optional[str], body: bytes) -> bool:
    if not APP_SECRET:
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    their_sig = signature.split("sha256=")[-1].strip()
    mac = hmac.new(APP_SECRET, msg=body, digestmod=hashlib.sha256)
    my_sig = mac.hexdigest()
    return hmac.compare_digest(my_sig, their_sig)


# ===========================
# Cliente HTTP (Graph API)
# ===========================
async def _post_messages(payload: Dict[str, Any]):
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers=headers, json=payload)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            print(f"[GraphAPI] {e.response.status_code} - {e.response.text}")
            raise


# ===========================
# Primitivas de envío
# ===========================
async def send_text(to_msisdn: str, text: str):
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "text",
        "text": {"body": text}
    }
    await _post_messages(payload)


async def send_reply_buttons(
    to_msisdn: str, header: str, body: str, buttons: List[Dict], footer: str = ""
):
    """
    buttons: lista de dicts con forma {"id": "...", "title": "..."}
    (máximo 3 botones por limitación de WhatsApp).
    """
    action = {"buttons": [{"type": "reply", "reply": b} for b in buttons][:3]}
    interactive = {
        "type": "button",
        "header": {"type": "text", "text": header},
        "body": {"text": body},
        "action": action
    }
    if footer:
        interactive["footer"] = {"text": footer}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": interactive
    }
    await _post_messages(payload)


async def send_list(
    to_msisdn: str, header: str, body: str, button_text: str, rows: List[Dict],
    footer: str = "", section_title: str = "Opciones"
):
    """
    rows: [{"id": "...", "title": "...", "description": "..."}]
    Máximo 10 filas por sección.
    """
    action = {"button": button_text, "sections": [{"title": section_title, "rows": rows[:10]}]}
    interactive = {
        "type": "list",
        "header": {"type": "text", "text": header},
        "body": {"text": body},
        "action": action
    }
    if footer:
        interactive["footer"] = {"text": footer}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_msisdn,
        "type": "interactive",
        "interactive": interactive
    }
    await _post_messages(payload)


# ===========================
# Menús de la app
# ===========================
async def send_main_menu(to_msisdn: str):
    rows = [
        {"id": "menu_publish", "title": "➕ Publicar un artículo", "description": "Ofrece algo en alquiler."},
        {"id": "menu_rent", "title": "🔍 Alquilar por ID", "description": "Inicia una solicitud de alquiler."},
        {"id": "menu_my_listings", "title": "📚 Mis Publicaciones", "description": "Gestiona los artículos que ofreces."},
        {"id": "menu_my_rentals", "title": "📋 Mis Alquileres", "description": "Revisa tus alquileres actuales y pasados."},
        {"id": "menu_my_reviews", "title": "⭐️ Ver mis reseñas", "description": "Lo que otros opinan de ti."},
        {"id": "menu_help", "title": "❓ Ayuda y Comandos", "description": "Descubre más funciones."},
    ]
    await send_list(
        to_msisdn,
        "Menú de Renty",
        "¿Qué te gustaría hacer?",
        "Ver Opciones",
        rows,
        footer="Escribe MENU para volver aquí",
    )


# ==========================================================
# Helpers específicos del flujo: Gestionar rental completado
# ==========================================================
async def send_manage_menu(to_msisdn: str, rental_id: int | str, role: str):
    """
    Muestra el menú de gestión post-renta:
      - ⭐ Dejar reseña
      - ⚠️ Reportar problema
    Incluye el rental_id codificado en el id del botón para parseo simple en handlers.
    """
    role_txt = "dueño" if role == "seller" else "cliente"
    header = f"Gestionar alquiler #{rental_id}"
    body = (
        f"Eres {role_txt} en este alquiler. ¿Qué deseas hacer?\n\n"
        "• ⭐ Dejar una única reseña\n"
        "• ⚠️ Reportar un problema"
    )
    buttons = [
        {"id": f"review::{rental_id}", "title": "⭐ Reseñar"},
        {"id": f"issue::{rental_id}", "title": "⚠️ Reportar problema"},
    ]
    await send_reply_buttons(to_msisdn, header, body, buttons, footer="Solo una acción por participante")


async def send_issue_type_buttons(to_msisdn: str, role: str, rental_id: int | str):
    """
    Muestra tipos de problema permitidos según rol:
      seller -> no_entregado | entregado_con_danos
      buyer  -> problema_general
    """
    header = "Selecciona el tipo de problema"
    if role == "seller":
        body = "Como dueño del artículo, elige el tipo de problema:"
        buttons = [
            {"id": f"issue_type::no_entregado::{rental_id}", "title": "🚫 No entregado"},
            {"id": f"issue_type::entregado_con_danos::{rental_id}", "title": "🛠️ Entregado con daños"},
        ]
    else:
        body = "Como cliente, elige el tipo de problema:"
        buttons = [
            {"id": f"issue_type::problema_general::{rental_id}", "title": "⚠️ Problema general"},
        ]
    await send_reply_buttons(to_msisdn, header, body, buttons)


async def send_rating_buttons(to_msisdn: str, rental_id: int | str):
    """
    Solicita una calificación 1..5 (entero). El id del botón codifica la puntuación y el rental.
    """
    header = "Calificación"
    body = "¿Cómo calificas la experiencia? (1 = muy mala, 5 = excelente)"
    buttons = [
        {"id": f"rating::1::{rental_id}", "title": "★ 1"},
        {"id": f"rating::2::{rental_id}", "title": "★ 2"},
        {"id": f"rating::3::{rental_id}", "title": "★ 3"},
    ]
    # En WhatsApp hay máximo 3 botones; enviamos 1–3 y luego 4–5 en un segundo mensaje.
    await send_reply_buttons(to_msisdn, header, body, buttons)

    buttons2 = [
        {"id": f"rating::4::{rental_id}", "title": "★ 4"},
        {"id": f"rating::5::{rental_id}", "title": "★ 5"},
    ]
    await send_reply_buttons(to_msisdn, "Calificación (continuación)", "Elige 4 o 5 si lo prefieres:", buttons2)
