import re
import json
from typing import Dict, Any, Optional
from datetime import datetime, date

import httpx

from .state import Step, step_val, _extract_dates, _to_ve
from .wa_api import send_text, send_reply_buttons, send_list, send_main_menu
from .clients.llm_client import chat_completion
from .clients.supabase_client import (
    ensure_user, get_user_name,
    set_session, get_session,
    insert_listing, get_listing,
    upsert_consent, set_consent_flag, get_consent,
    create_rental_request, mark_introduced_once,
    get_active_rentals_for_item, update_listing_status,
    add_review, get_reviews_for_user,
    request_rental_cancellation,
    request_rental_extension,
    get_listings_for_user,
    get_rentals_for_user,
    confirm_rental_start,                 # Doble confirmación de inicio
    is_item_available,                    # NUEVO: disponibilidad por fechas
)

# === Helpers locales ===
def _validate_date_window(start_iso: str, end_iso: str) -> bool:
    """start >= hoy y end > start (comparación por fecha, sin horas)."""
    try:
        s = datetime.strptime(start_iso[:10], "%Y-%m-%d").date()
        e = datetime.strptime(end_iso[:10], "%Y-%m-%d").date()
        return s >= date.today() and e > s
    except Exception:
        return False


async def _send_post_agreement_menus(buyer_wa: str, seller_wa: str, item_id: str, rental_id: str):
    """
    Menú profesional post-acuerdo.
    """
    body = (
        f"Renta del artículo #{item_id}\n\n"
        "Estado actual: *PENDIENTE*.\n"
        "Para activar la renta, *ambas partes* deben confirmar el inicio.\n\n"
        "Opciones disponibles:"
        "\n• Confirmar inicio (activa la renta cuando los dos confirmen)"
        "\n• Cancelar (requiere confirmación de ambas partes)"
        "\n• Extender (proponer nueva fecha de fin; requiere confirmación de ambas partes)"
    )
    buttons = [
        {"id": f"rental_confirm_{rental_id}", "title": "✅ Confirmar inicio"},
        {"id": f"rental_cancel_{rental_id}", "title": "❌ Cancelar"},
        {"id": f"rental_extend_{rental_id}", "title": "🔄 Extender"},
    ]
    await send_reply_buttons(buyer_wa, "Gestión de Renta", body, buttons)
    await send_reply_buttons(seller_wa, "Gestión de Renta", body, buttons)

async def finalize_and_introduce(item_id: str, actor_msisdn: str):
    """
    Se llama cuando ambos dieron consentimiento.
    Crea la renta en estado PENDIENTE y presenta a las partes.
    """
    cons = await get_consent(item_id)
    if not cons:
        return

    buyer_wa, seller_wa = cons["buyer_wa"], cons["seller_wa"]
    st = await get_session(buyer_wa)
    draft = st.get("draft", {})

    rental_id_str = ""
    if 'start_iso' in draft and 'end_iso' in draft and 'selected_payment_method' in draft:
        # Revalidación final (por si algo cambió antes de escribir la renta)
        listing = await get_listing(str(item_id))
        if not listing or listing.get("status") != "active":
            await send_text(buyer_wa, "La publicación ya no está activa; no se pudo crear la renta.")
        elif not _validate_date_window(draft['start_iso'], draft['end_iso']):
            await send_text(buyer_wa, "Las fechas ya no son válidas. Intenta proponer un nuevo rango.")
        elif not await is_item_available(item_id, draft['start_iso'], draft['end_iso']):
            await send_text(buyer_wa, "Ese rango de fechas ya fue tomado. Propón nuevas fechas.")
        else:
            r = await create_rental_request(
                int(item_id), buyer_wa, draft['start_iso'], draft['end_iso'], draft['selected_payment_method']
            )
            if r.get("ok"):
                rental_id_str = str(r["row"]["id"])
        await set_session(buyer_wa, Step.IDLE, {})

    # Presentación (una sola vez) + aviso de estado
    if await mark_introduced_once(item_id):
        buyer_name, seller_name = await get_user_name(buyer_wa), await get_user_name(seller_wa)
        base_msg = (
            f"¡Acuerdo logrado para el artículo #{item_id}! "
            "Hemos compartido sus contactos para coordinar detalles."
        )
        await send_text(buyer_wa, f"{base_msg}\nVendedor: {seller_name} ({seller_wa})")
        await send_text(seller_wa, f"{base_msg}\nComprador: {buyer_name} ({buyer_wa})")

        if rental_id_str:
            info = (
                f"Se creó la *Renta #{rental_id_str}* en estado *PENDIENTE* "
                f"(del { _to_ve(draft['start_iso']) } al { _to_ve(draft['end_iso']) }).\n"
                "Pulsa *Confirmar inicio* cuando esté todo listo. La renta se activará cuando ambos confirmen."
            )
            await send_text(buyer_wa, info)
            await send_text(seller_wa, info)
            await _send_post_agreement_menus(buyer_wa, seller_wa, str(item_id), rental_id_str)

    # Limpiar sesión y menú para quien accionó
    if actor_msisdn != buyer_wa:
        await set_session(actor_msisdn, Step.IDLE, {})
    await send_main_menu(actor_msisdn)

# === INTERACTIVE ===
async def handle_interactive(msg: Dict[str, Any], st: Dict[str, Any], from_msisdn: str):
    s = step_val(st)
    interactive = msg["interactive"]
    itype = interactive["type"]

    if itype == "button_reply":
        btn_id = interactive["button_reply"]["id"]

        # CONSENTIMIENTO
        if btn_id.startswith("consent_"):
            answer, item_id = btn_id.split("_")[1], btn_id.split("_")[2]
            cons = await set_consent_flag(item_id, from_msisdn, ok=(answer == "yes"))
            if not cons:
                await send_text(from_msisdn, "No se encontró la solicitud.")
                return

            if cons.get("buyer_ok") and cons.get("seller_ok"):
                await finalize_and_introduce(item_id, from_msisdn)
            elif answer == "no":
                other = cons["seller_wa"] if from_msisdn == cons["buyer_wa"] else cons["buyer_wa"]
                await send_text(from_msisdn, "Entendido. Tu decisión fue registrada.")
                await send_text(other, "La otra parte ha rechazado la solicitud. La operación se canceló.")
                await set_session(from_msisdn, Step.IDLE, {})
            else:
                await send_text(from_msisdn, "Gracias. Esperamos la respuesta de la otra parte.")
            return

        # POST-ACUERDO
        if btn_id.startswith("rental_confirm_"):
            await handle_rental_confirmation(btn_id, from_msisdn)
            return

        if btn_id.startswith("rental_cancel_"):
            rental_id = btn_id.split("_")[-1]
            await handle_cancellation_request(rental_id, from_msisdn)
            return

        if btn_id.startswith("rental_extend_"):
            rental_id = btn_id.split("_")[-1]
            await set_session(from_msisdn, Step.RENTAL_EXTENSION_WAIT_DATES, {"rental_id": int(rental_id)})
            await send_text(from_msisdn, "Indica la *nueva fecha de fin* en formato: DD/MM/AAAA.")
            return

    if itype == "list_reply":
        row_id = interactive["list_reply"]["id"]
        row_title = interactive["list_reply"]["title"]

        # Método de pago → crea consentimiento (con revalidación previa)
        if s == Step.RENTAL_WAIT_PAYMENT:
            draft = st["draft"]
            start_iso, end_iso, item_id = draft['start_iso'], draft['end_iso'], str(draft['item_id'])

            listing = await get_listing(item_id)
            if not listing or listing.get("status") != "active":
                await send_text(from_msisdn, "La publicación ya no está activa.")
                await set_session(from_msisdn, Step.IDLE, {})
                await send_main_menu(from_msisdn)
                return

            if not _validate_date_window(start_iso, end_iso):
                await send_text(from_msisdn, "Las fechas no son válidas. Usa un inicio desde hoy y fin posterior al inicio.")
                await set_session(from_msisdn, Step.IDLE, {})
                await send_main_menu(from_msisdn)
                return

            if not await is_item_available(item_id, start_iso, end_iso):
                await send_text(from_msisdn, "Lo siento, esas fechas ya no están disponibles para este artículo.")
                await set_session(from_msisdn, Step.IDLE, {})
                await send_main_menu(from_msisdn)
                return

            # Si todo OK, persistimos y pedimos consentimientos
            draft["selected_payment_method"] = row_title
            await set_session(from_msisdn, s, draft)

            seller, buyer = listing["owner_wa"], from_msisdn
            await upsert_consent(item_id, buyer, seller)

            msg_to_seller = (f"¡Nueva solicitud para tu artículo #{item_id}!\n\n"
                             f"Fechas: del *{_to_ve(start_iso)}* al *{_to_ve(end_iso)}*\n"
                             f"Método de pago: *{row_title}*\n\n"
                             "¿Aceptas compartir tu contacto para coordinar?")
            seller_buttons = [{"id": f"consent_yes_{item_id}", "title": "Sí, acepto"},
                              {"id": f"consent_no_{item_id}", "title": "No, gracias"}]
            await send_reply_buttons(seller, "Confirmación de Alquiler", msg_to_seller, seller_buttons)

            await send_text(buyer, "¡Excelente! Hemos enviado tu solicitud al dueño. Para continuar, solo falta tu autorización final para compartir tu contacto.")
            buyer_buttons = [{"id": f"consent_yes_{item_id}", "title": "Sí, autorizo"},
                             {"id": f"consent_no_{item_id}", "title": "No autorizo"}]
            await send_reply_buttons(buyer, "Autorización Final", "¿Autorizas compartir tu contacto con el vendedor?", buyer_buttons)
            return

        # Menú principal
        if row_id == "menu_publish":
            await set_session(from_msisdn, Step.PUBLISH_TITLE, {})
            await send_text(from_msisdn, "¡Vamos a publicar! Primero, dime el *título* de tu artículo.")
        elif row_id == "menu_rent":
            await send_text(from_msisdn, "Para alquilar, escribe qué buscas o el ID del artículo. Ej: ALQUILAR #123")
        elif row_id == "menu_my_listings":
            await handle_text({"text": {"body": "MIS PUBLICACIONES"}}, st, from_msisdn)
        elif row_id == "menu_my_rentals":
            await handle_text({"text": {"body": "MIS ALQUILERES"}}, st, from_msisdn)
        elif row_id == "menu_my_reviews":
            await handle_text({"text": {"body": "MIS RESEÑAS"}}, st, from_msisdn)
        elif row_id == "menu_help":
            await send_text(from_msisdn, "Comandos útiles:\n- `ELIMINAR #ID`: Quita una publicación.\n- `CANCELAR RENTA #ID`: Cancela una renta.\n- `RESEÑA #ID_RENTA 1-5`: Deja una opinión.")

# === TEXT ===
async def handle_text(msg: Dict[str, Any], st: Dict[str, Any], from_msisdn: str):
    s = step_val(st)
    text = (msg.get("text", {}).get("body", "")).strip()
    if not text: return
    upper = text.upper()

    if upper in {"MENU", "MENÚ", "INICIO", "AYUDA"}:
        await set_session(from_msisdn, Step.IDLE, {})
        await send_main_menu(from_msisdn)
        return

    # ALQUILAR robusto
    m_cmd = re.search(r"\bALQUILAR\b", upper)
    m_id = re.search(r"[#№](\d+)", text)
    if m_cmd and m_id:
        item_id = m_id.group(1)
        listing = await get_listing(item_id)
        if not listing:
            await send_text(from_msisdn, f"No encontré un artículo con el ID #{item_id}.")
            return
        if listing['owner_wa'] == from_msisdn:
            await send_text(from_msisdn, "No puedes alquilar tu propio artículo.")
            return
        if listing.get("status") != "active":
            await send_text(from_msisdn, "Este artículo no está disponible para nuevas rentas.")
            return

        dates = _extract_dates(text)
        if dates:
            start_iso, end_iso = dates
            # Validaciones de fechas y disponibilidad antes de pasar a pagos
            if not _validate_date_window(start_iso, end_iso):
                await send_text(from_msisdn, "Las fechas no son válidas. Asegúrate de que el inicio sea desde hoy y el fin posterior.")
                return
            if not await is_item_available(item_id, start_iso, end_iso):
                await send_text(from_msisdn, "Lo siento, esas fechas ya están reservadas para este artículo.")
                return

            await set_session(from_msisdn, Step.RENTAL_WAIT_PAYMENT, {"item_id": int(item_id), "start_iso": start_iso, "end_iso": end_iso})
            payment_options = listing.get("payment_methods") or ["A convenir"]
            rows = [{"id": p.replace(" ", "_"), "title": p} for p in payment_options]
            await send_list(from_msisdn, f"Alquiler de #{item_id}", "Selecciona tu método de pago:", "Ver Pagos", rows)
        else:
            await set_session(from_msisdn, Step.RENTAL_WAIT_DATES, {"item_id": int(item_id)})
            await send_text(from_msisdn, f"Perfecto. Ahora, indica las *fechas* que necesitas para el artículo #{item_id} (formato: DD/MM/AAAA a DD/MM/AAAA).")
        return

    # Comandos directos
    if upper.startswith("RESEÑA #"):
        match = re.search(r"RESEÑA\s*#(\d+)\s*([1-5])\s*(.*)", text, re.IGNORECASE)
        if not match:
            await send_text(from_msisdn, "Formato incorrecto. Uso: RESEÑA #ID_RENTA CALIFICACIÓN COMENTARIO")
            return
        rental_id, rating, comment = match.groups()
        result = await add_review(int(rental_id), from_msisdn, int(rating), comment.strip())
        if result.get("ok"):
            await send_text(from_msisdn, "¡Gracias por tu reseña!")
        else:
            await send_text(from_msisdn, f"Error: {result.get('error', 'No se pudo guardar la reseña.')}")
        return

    if upper == "MIS RESEÑAS":
        reviews = await get_reviews_for_user(from_msisdn)
        if not reviews:
            await send_text(from_msisdn, "Aún no has recibido ninguna reseña.")
            return
        response = "Reseñas que te han dejado:\n\n" + "\n".join(
            [f"⭐️ {r['rating']}/5: \"{r['comment'] or 'Sin comentario.'}\" ({_to_ve(r['created_at'])})" for r in reviews]
        )
        await send_text(from_msisdn, response)
        return
        
    if upper == "MIS PUBLICACIONES":
        listings = await get_listings_for_user(from_msisdn)
        if not listings:
            await send_text(from_msisdn, "No tienes ningún artículo publicado. ¡Anímate a publicar algo con el menú principal!")
            return
        response = "Tus publicaciones:\n\n"
        for item in listings:
            status_emoji = "✅" if item['status'] == 'active' else "⏸️"
            response += f"{status_emoji} *#{item['id']}* - {item['title']} ({item['price']}) - Estado: {item['status']}\n"
        await send_text(from_msisdn, response)
        return

    if upper == "MIS ALQUILERES":
        rentals = await get_rentals_for_user(from_msisdn)
        if not rentals:
            await send_text(from_msisdn, "No tienes alquileres activos o pasados.")
            return
        response = "Tus alquileres:\n\n"
        for r in rentals:
            is_owner = r['seller_wa'] == from_msisdn
            role = "(Eres el dueño)" if is_owner else "(Eres el inquilino)"
            title = r.get('listing', {}).get('title', f"Artículo #{r['item_id']}")
            start, end = _to_ve(r['start_date']), _to_ve(r['end_date'])
            response += (f"📝 *Renta #{r['id']}* {role}\n"
                         f"   - Artículo: {title}\n"
                         f"   - Fechas: {start} a {end}\n"
                         f"   - Estado: *{r['status']}*\n\n")
        await send_text(from_msisdn, response)
        return

    if upper.startswith("ELIMINAR #"):
        item_id = text.split("#")[-1].strip()
        if not item_id.isdigit():
            await send_text(from_msisdn, "Proporciona un ID de artículo válido (solo números).")
            return
        try:
            if await get_active_rentals_for_item(item_id):
                await send_text(from_msisdn, f"No puedes eliminar la publicación #{item_id} porque tiene rentas activas o solicitadas.")
                return
            if await update_listing_status(item_id, from_msisdn, "inactive"):
                await send_text(from_msisdn, f"✅ Publicación #{item_id} eliminada con éxito.")
            else:
                await send_text(from_msisdn, "No se pudo eliminar. Asegúrate de que el ID sea correcto y que seas el dueño de la publicación.")
        except httpx.HTTPStatusError as e:
            print(f"Error de Supabase al eliminar item {item_id}: {e}")
            await send_text(from_msisdn, "Hubo un problema de comunicación con nuestros sistemas. Por favor, inténtalo de nuevo más tarde.")
        except Exception as e:
            print(f"Error inesperado al eliminar item {item_id}: {e}")
            await send_text(from_msisdn, "Ocurrió un error inesperado. Nuestro equipo ha sido notificado.")
        return

    if upper.startswith("CANCELAR RENTA #"):
        rental_id = text.split("#")[-1].strip()
        await handle_cancellation_request(rental_id, from_msisdn)
        return

    # Máquina de estados (Publicación)
    if s == Step.PUBLISH_TITLE:
        await set_session(from_msisdn, Step.PUBLISH_PRICE, {"title": text})
        await send_text(from_msisdn, "¡Bien! Ahora, indica el *precio por día* (ej: 10 USD).")
        return
    if s == Step.PUBLISH_PRICE:
        # (opcional) podríamos normalizar el monto a número aquí
        st["draft"]["price"] = text
        await set_session(from_msisdn, Step.PUBLISH_ZONE, st["draft"])
        await send_text(from_msisdn, "Ok. ¿En qué *zona* se encuentra? (ej: Chacao, Caracas)")
        return
    if s == Step.PUBLISH_ZONE:
        st["draft"]["zone"] = text
        await set_session(from_msisdn, Step.PUBLISH_PAYMENTS, st["draft"])
        await send_text(from_msisdn, "Casi listo. ¿Qué *métodos de pago* aceptas? (separados por coma)")
        return
    if s == Step.PUBLISH_PAYMENTS:
        pmts = [p.strip() for p in re.split(r"[,;]+", text) if p.strip()]
        # deduplicar y acotar
        pmts = list(dict.fromkeys(pmts))[:10]
        d = st["draft"]
        item_id = await insert_listing(from_msisdn, d["title"], d["price"], d["zone"], pmts)
        await set_session(from_msisdn, Step.IDLE, {})
        pagos = ", ".join(pmts) if pmts else "A convenir"
        await send_text(from_msisdn, f"¡Publicación creada! ID: *#{item_id}*\n- {d['title']}\n- Precio: {d['price']}\n- Zona: {d['zone']}\n- Pagos: {pagos}")
        await send_main_menu(from_msisdn)
        return

    # Máquina de estados (Alquiler)
    if s == Step.RENTAL_WAIT_DATES:
        dates = _extract_dates(text)
        if not dates:
            await send_text(from_msisdn, "Formato de fechas no válido. Ejemplo: 15/10/2025 a 20/10/2025")
            return
        start_iso, end_iso = dates
        item_id = st["draft"]["item_id"]

        listing = await get_listing(str(item_id))
        if not listing or listing.get("status") != "active":
            await send_text(from_msisdn, "Este artículo no está activo para alquiler.")
            await set_session(from_msisdn, Step.IDLE, {})
            await send_main_menu(from_msisdn)
            return

        if not _validate_date_window(start_iso, end_iso):
            await send_text(from_msisdn, "Fechas inválidas. Usa un rango desde hoy y con fin posterior al inicio.")
            return

        if not await is_item_available(item_id, start_iso, end_iso):
            await send_text(from_msisdn, "Esas fechas ya están reservadas. Prueba con un rango distinto.")
            return

        await set_session(from_msisdn, Step.RENTAL_WAIT_PAYMENT, {"item_id": item_id, "start_iso": start_iso, "end_iso": end_iso})
        payment_options = listing.get("payment_methods") or ["A convenir"]
        rows = [{"id": p.replace(" ", "_"), "title": p} for p in payment_options]
        await send_list(from_msisdn, f"Alquiler de #{item_id}", "¡Fechas guardadas! Ahora, selecciona tu método de pago.", "Ver Pagos", rows)
        return

    if s == Step.RENTAL_EXTENSION_WAIT_DATES:
        rental_id = st["draft"].get("rental_id")
        if not rental_id:
            await set_session(from_msisdn, Step.IDLE, {})
            await send_main_menu(from_msisdn)
            return
        dates = _extract_dates(text)
        end_iso = None
        if dates:
            _, end_iso = dates
        else:
            m1 = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", text)
            if m1:
                d, mth, y = map(int, m1.group(1).split("/")); end_iso = f"{y:04d}-{mth:02d}-{d:02d}"
        if not end_iso:
            await send_text(from_msisdn, "Formato de fecha no válido. Usa DD/MM/AAAA.")
            return
        result = await request_rental_extension(int(rental_id), from_msisdn, end_iso)
        status = result.get("status")
        if status == "EXTENSION_PENDING":
            other = result.get("other_party")
            await send_text(from_msisdn, f"Solicitud de extensión registrada hasta *{_to_ve(end_iso)}*. La otra parte debe confirmarla.")
            await send_text(other, f"El otro usuario solicita extender la renta #{rental_id} hasta *{_to_ve(end_iso)}*. Para aceptar, responde con el menú de la renta.")
        elif status == "EXTENDED":
            for party in result.get("parties", []):
                await send_text(party, f"¡Listo! La renta #{rental_id} fue extendida hasta *{_to_ve(end_iso)}* por mutuo acuerdo.")
        else:
            await send_text(from_msisdn, "No se pudo procesar la extensión.")
        await set_session(from_msisdn, Step.IDLE, {})
        await send_main_menu(from_msisdn)
        return

    # Fallback: IA (opcional)
    intent_data = await get_intent_from_llm(text)
    if intent_data and "intent" in intent_data:
        intent, params = intent_data.get("intent"), intent_data.get("parameters", {})
        if intent == "rent" and params.get("item_id"):
            item_id, dates_text = params["item_id"], params.get("dates_text", "")
            listing = await get_listing(item_id)
            if not listing:
                await send_text(from_msisdn, f"No encontré un artículo con el ID #{item_id}.")
                return
            if listing['owner_wa'] == from_msisdn:
                await send_text(from_msisdn, "No puedes alquilar tu propio artículo.")
                return
            if listing.get("status") != "active":
                await send_text(from_msisdn, "Este artículo no está disponible para nuevas rentas.")
                return
            dates = _extract_dates(dates_text) if dates_text else None
            if dates:
                start_iso, end_iso = dates
                if not _validate_date_window(start_iso, end_iso):
                    await send_text(from_msisdn, "Las fechas no son válidas. Inicio desde hoy y fin posterior.")
                    return
                if not await is_item_available(item_id, start_iso, end_iso):
                    await send_text(from_msisdn, "Ese rango ya está reservado para este artículo.")
                    return
                await set_session(from_msisdn, Step.RENTAL_WAIT_PAYMENT, {"item_id": int(item_id), "start_iso": start_iso, "end_iso": end_iso})
                payment_options = listing.get("payment_methods") or ["A convenir"]
                rows = [{"id": p.replace(" ", "_"), "title": p} for p in payment_options]
                await send_list(from_msisdn, f"Alquiler de #{item_id}", "Selecciona tu método de pago:", "Ver Pagos", rows)
            else:
                await set_session(from_msisdn, Step.RENTAL_WAIT_DATES, {"item_id": int(item_id)})
                await send_text(from_msisdn, f"Perfecto. Ahora, indica las *fechas* que necesitas para el artículo #{item_id} (formato: DD/MM/AAAA a DD/MM/AAAA).")
            return
        elif intent == "get_my_listings":
            await handle_text({"text": {"body": "MIS PUBLICACIONES"}}, st, from_msisdn)
            return
        elif intent == "get_my_rentals":
            await handle_text({"text": {"body": "MIS ALQUILERES"}}, st, from_msisdn)
            return
        elif intent == "greet":
            user_name = await get_user_name(from_msisdn)
            await send_text(from_msisdn, f"¡Hola, {user_name}! ¿En qué te puedo ayudar hoy?")

    await send_main_menu(from_msisdn)

# === ENTRADA PRINCIPAL ===
async def handle_message(value: Dict[str, Any], msg: Dict[str, Any]):
    from_msisdn = msg["from"]
    profile_name = (value.get("contacts", [{}])[0].get("profile", {}) or {}).get("name")
    await ensure_user(from_msisdn, profile_name)
    st = await get_session(from_msisdn)
    msg_type = msg.get("type")

    if msg_type == "interactive":
        await handle_interactive(msg, st, from_msisdn)
    elif msg_type == "text":
        await handle_text(msg, st, from_msisdn)
    else:
        await send_text(from_msisdn, "Solo puedo procesar mensajes de texto y botones. Por favor, usa el menú.")

# === AUXILIARES ===
async def get_intent_from_llm(text: str) -> Optional[Dict[str, Any]]:
    system_prompt = (
        "Eres un asistente experto para un bot de WhatsApp llamado Renty. "
        "Responde SOLO con un JSON. Intenciones: 'rent', 'get_my_listings', 'get_my_rentals', 'greet', 'unknown'. "
        "Para 'rent', extrae 'item_id' y 'dates_text'."
    )
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": text}]
    try:
        response_text = chat_completion(messages, temperature=0.2, max_tokens=200)
        match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if match: return json.loads(match.group(0))
        return None
    except Exception as e:
        print(f"Error al contactar el LLM: {e}")
        return None

async def handle_cancellation_request(rental_id_str: str, requester_wa: str):
    if not rental_id_str.isdigit():
        await send_text(requester_wa, "Proporciona un ID de renta válido.")
        return
    try:
        result = await request_rental_cancellation(int(rental_id_str), requester_wa)
        status = result.get("status")
        if status == "CANCELLED":
            for party in result.get("parties", []):
                await send_text(party, f"✅ La renta #{rental_id_str} ha sido cancelada por mutuo acuerdo.")
        elif status == "WAITING_OTHER":
            await send_text(requester_wa, "👍 Solicitud de cancelación enviada. La otra parte debe confirmarla para que sea efectiva.")
            msg_to_other = (f"⚠️ El otro usuario ha solicitado cancelar la renta #{rental_id_str}.\n\n"
                            f"Para aceptar, responde: CANCELAR RENTA #{rental_id_str}")
            await send_text(result.get("other_party"), msg_to_other)
        elif status == "NOT_FOUND":
             await send_text(requester_wa, f"No se encontró una renta con el ID #{rental_id_str}.")
        else:
            await send_text(requester_wa, "No se pudo procesar tu solicitud. La renta podría no ser cancelable en este momento.")
    except Exception as e:
        print(f"Error en handle_cancellation_request para rental {rental_id_str}: {e}")
        await send_text(requester_wa, "Ocurrió un error al procesar tu solicitud de cancelación.")

async def handle_rental_confirmation(btn_id: str, from_msisdn: str):
    """
    Doble confirmación de inicio:
      - Registra la confirmación del actor
      - Si la otra parte ya confirmó -> activa la renta (status=active) siempre que hoy ∈ [start,end]
      - Si no -> avisa que falta la otra parte
    """
    try:
        rental_id = int(btn_id.split("_")[-1])
        result = await confirm_rental_start(rental_id, from_msisdn)
        status = result.get("status")
        if status == "ACTIVATED":
            for wa in result.get("parties", []):
                await send_text(wa, f"✅ Renta #{rental_id} *confirmada por ambas partes*. Estado: *ACTIVA*.")
        elif status == "WAITING_OTHER":
            other = result.get("other_party")
            await send_text(from_msisdn, "👍 Tu confirmación fue registrada. Falta la otra parte.")
            await send_text(other, f"⚠️ La otra parte confirmó el inicio de la renta #{rental_id}. Entra a *Gestión de Renta* y pulsa *Confirmar inicio* para activarla.")
        elif status == "INVALID" and result.get("reason") == "OUT_OF_WINDOW":
            await send_text(from_msisdn, "Aún no puede activarse: solo se activa dentro del rango de fechas acordado.")
        elif status == "INVALID":
            await send_text(from_msisdn, "Esta renta no puede confirmarse (posiblemente ya está activa o fue cancelada).")
        else:  # NOT_FOUND u otra
            await send_text(from_msisdn, "No se encontró la renta.")
    except Exception as e:
        print(f"Error en handle_rental_confirmation para botón {btn_id}: {e}")
        await send_text(from_msisdn, "Ocurrió un error al confirmar la renta.")
