import re
from typing import Dict, Any, List, Optional

from .state import Step, step_val, _extract_dates, _to_ve
from .wa_api import send_text, send_reply_buttons, send_list, send_main_menu
from .clients.supabase_client import (
    ensure_user, get_user_name,
    set_session, get_session,
    insert_listing, get_listing,
    upsert_consent, set_consent_flag, get_consent,
    create_rental_request, mark_introduced_once,
    get_active_rentals_for_item, update_listing_status,
    add_review, get_reviews_for_user,
    request_rental_cancellation,
    update_rental_status,          # NUEVO
    request_rental_extension,      # NUEVO
)

async def _send_post_agreement_menus(buyer_wa: str, seller_wa: str, item_id: str, rental_id: str):
    """
    Envía a ambas partes el menú profesional post-acuerdo con Confirmar/Cancelar/Extender.
    """
    body = (
        f"Opciones para la renta del artículo #{item_id}.\n\n"
        "Puedes confirmar el inicio, cancelar antes de empezar o solicitar una extensión."
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
    Lógica centralizada para finalizar una solicitud (cuando ambas partes dieron consentimiento).
    """
    cons = await get_consent(item_id)
    if not cons:
        return

    buyer_wa = cons["buyer_wa"]
    seller_wa = cons["seller_wa"]
    st = await get_session(buyer_wa)
    draft = st.get("draft", {})

    # 1) Crear la renta si tenemos todos los datos
    rental_id_str = ""
    if 'start_iso' in draft and 'end_iso' in draft and 'selected_payment_method' in draft:
        r = await create_rental_request(
            int(item_id), buyer_wa, draft['start_iso'], draft['end_iso'], draft['selected_payment_method']
        )
        if r.get("ok"):
            rental_id_str = str(r["row"]["id"])
        # Limpiar sesión del comprador
        await set_session(buyer_wa, Step.IDLE, {})

    # 2) Presentar a las partes una sola vez
    if await mark_introduced_once(item_id):
        buyer_name = await get_user_name(buyer_wa)
        seller_name = await get_user_name(seller_wa)
        base_msg = f"¡Acuerdo logrado! Ya pueden coordinar el alquiler del artículo #{item_id}."
        await send_text(buyer_wa, f"{base_msg} Vendedor: {seller_name}")
        await send_text(seller_wa, f"{base_msg} Comprador: {buyer_name}")

        # Menú profesional post-acuerdo
        if rental_id_str:
            await _send_post_agreement_menus(buyer_wa, seller_wa, str(item_id), rental_id_str)

    # 3) Limpiar sesión y mandar menú al actor final
    if actor_msisdn != buyer_wa:  # evitar doble menú
        await set_session(actor_msisdn, Step.IDLE, {})
    await send_main_menu(actor_msisdn)

# === INTERACTIVE ===
async def handle_interactive(msg: Dict[str, Any], st: Dict[str, Any], from_msisdn: str):
    s = step_val(st)
    interactive = msg["interactive"]
    itype = interactive["type"]

    # Botones de consentimiento (flujo inicial)
    if itype == "button_reply":
        btn_id = interactive["button_reply"]["id"]

        # CONSENTIMIENTO INICIAL
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
                await set_session(from_msisdn, Step.IDLE, {})  # limpiar
            else:
                await send_text(from_msisdn, "Gracias. Esperamos la respuesta de la otra parte.")
            return

        # POST-ACUERDO: CONFIRMAR / CANCELAR / EXTENDER
        if btn_id.startswith("rental_confirm_"):
            rental_id = btn_id.split("_")[-1]
            ok = await update_rental_status(int(rental_id), "active")
            if ok:
                await send_text(from_msisdn, f"✅ Renta #{rental_id} confirmada como *activa*.")
            else:
                await send_text(from_msisdn, "No se pudo confirmar la renta. Inténtalo más tarde.")
            return

        if btn_id.startswith("rental_cancel_"):
            rental_id = btn_id.split("_")[-1]
            result = await request_rental_cancellation(int(rental_id), from_msisdn)
            status = result.get("status")
            if status == "CANCELLED":
                for party in result.get("parties", []):
                    await send_text(party, f"La renta #{rental_id} fue cancelada por mutuo acuerdo.")
            elif status == "WAITING_OTHER":
                await send_text(from_msisdn, f"Solicitud de cancelación enviada. La otra parte debe confirmarla.")
                await send_text(result.get("other_party"), f"El otro usuario quiere cancelar la renta #{rental_id}. Para aceptar, escribe: CANCELAR RENTA #{rental_id}")
            else:
                await send_text(from_msisdn, "No se encontró la renta o no se puede cancelar.")
            return

        if btn_id.startswith("rental_extend_"):
            rental_id = btn_id.split("_")[-1]
            # Pasamos a pedir nuevas fechas (solo cambiaremos fin, pero aceptamos rango)
            await set_session(from_msisdn, Step.RENTAL_EXTENSION_WAIT_DATES, {"rental_id": int(rental_id)})
            await send_text(from_msisdn, "Indica las *nuevas fechas* (puedes enviar solo la nueva fecha fin) en formato: DD/MM/AAAA o 'DD/MM/AAAA a DD/MM/AAAA'.")
            return

    # Selecciones de lista (menú / pagos)
    if itype == "list_reply":
        row_id = interactive["list_reply"]["id"]
        row_title = interactive["list_reply"]["title"]

        # Flujo: método de pago
        if s == Step.RENTAL_WAIT_PAYMENT:
            draft = st["draft"]
            draft["selected_payment_method"] = row_title
            await set_session(from_msisdn, s, draft)

            item_id, start_iso, end_iso = str(draft['item_id']), draft['start_iso'], draft['end_iso']
            listing = await get_listing(item_id)
            seller, buyer = listing["owner_wa"], from_msisdn

            await upsert_consent(item_id, buyer, seller)

            # Vendedor
            msg_to_seller = (
                f"¡Nueva solicitud para tu artículo #{item_id}!\n\n"
                f"Fechas: del *{_to_ve(start_iso)}* al *{_to_ve(end_iso)}*\n"
                f"Método de pago: *{row_title}*\n\n"
                "¿Aceptas compartir tu contacto para coordinar?"
            )
            seller_buttons = [
                {"id": f"consent_yes_{item_id}", "title": "Sí, acepto"},
                {"id": f"consent_no_{item_id}", "title": "No, gracias"}
            ]
            await send_reply_buttons(seller, "Confirmación de Alquiler", msg_to_seller, seller_buttons)

            # Comprador
            await send_text(buyer, "¡Excelente! Hemos enviado tu solicitud al dueño. Para continuar, solo falta tu autorización final para compartir tu contacto.")
            buyer_buttons = [
                {"id": f"consent_yes_{item_id}", "title": "Sí, autorizo"},
                {"id": f"consent_no_{item_id}", "title": "No autorizo"}
            ]
            await send_reply_buttons(buyer, "Autorización Final", "¿Autorizas compartir tu contacto con el vendedor?", buyer_buttons)
            return

        # Menú principal
        if row_id == "menu_publish":
            await set_session(from_msisdn, Step.PUBLISH_TITLE, {})
            await send_text(from_msisdn, "¡Vamos a publicar! Primero, dime el *título* de tu artículo.")
        elif row_id == "menu_rent":
            await send_text(from_msisdn, "Para alquilar, escribe: ALQUILAR #ID (ej: ALQUILAR #123)")
        elif row_id == "menu_my_reviews":
            await send_text(from_msisdn, "Para ver las reseñas que te han dejado, escribe: MIS RESEÑAS")
        elif row_id == "menu_help":
            await send_text(from_msisdn, "Comandos útiles:\n- `ELIMINAR #ID`: Quita una publicación.\n- `CANCELAR RENTA #ID`: Cancela una renta.\n- `RESEÑA #ID_RENTA 1-5`: Deja una opinión.")

# === TEXT ===
async def handle_text(msg: Dict[str, Any], st: Dict[str, Any], from_msisdn: str):
    s = step_val(st)
    text = (msg.get("text", {}).get("body", "")).strip()
    if not text:
        return
    upper = text.upper()

    if upper in {"MENU", "MENÚ", "INICIO"}:
        await set_session(from_msisdn, Step.IDLE, {})
        await send_main_menu(from_msisdn)
        return

    # Comandos directos
    if upper.startswith("RESEÑA #"):
        match = re.search(r"RESEÑA\s*#(\d+)\s*([1-5])\s*(.*)", text, re.IGNORECASE)
        if not match:
            await send_text(from_msisdn, "Formato: RESEÑA #ID_RENTA CALIFICACIÓN COMENTARIO")
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
            [f"⭐️ {r['rating']}/5: \"{r['comment'] or 'Sin comentario.'}\"" for r in reviews]
        )
        await send_text(from_msisdn, response)
        return

    if upper.startswith("ELIMINAR #"):
        item_id = text.split("#")[-1].strip()
        if item_id.isdigit():
            if await get_active_rentals_for_item(item_id):
                await send_text(from_msisdn, f"No puedes eliminar la publicación #{item_id} porque tiene rentas activas.")
            elif await update_listing_status(item_id, from_msisdn, "inactive"):
                await send_text(from_msisdn, f"Publicación #{item_id} eliminada.")
            else:
                await send_text(from_msisdn, "No se pudo eliminar. Asegúrate de que el ID sea correcto y seas el dueño.")
        else:
            await send_text(from_msisdn, "Proporciona un ID de artículo válido.")
        return

    if upper.startswith("CANCELAR RENTA #"):
        rental_id = text.split("#")[-1].strip()
        if rental_id.isdigit():
            result = await request_rental_cancellation(int(rental_id), from_msisdn)
            status = result.get("status")
            if status == "CANCELLED":
                for party in result.get("parties", []):
                    await send_text(party, f"La renta #{rental_id} fue cancelada por mutuo acuerdo.")
            elif status == "WAITING_OTHER":
                await send_text(from_msisdn, f"Solicitud de cancelación enviada. La otra parte debe confirmarla.")
                await send_text(result.get("other_party"), f"El otro usuario quiere cancelar la renta #{rental_id}. Para aceptar, escribe: CANCELAR RENTA #{rental_id}")
            else:
                await send_text(from_msisdn, "No se encontró la renta o no se puede cancelar.")
        else:
            await send_text(from_msisdn, "Proporciona un ID de renta válido.")
        return

    # Máquina de estados
    if s == Step.PUBLISH_TITLE:
        await set_session(from_msisdn, Step.PUBLISH_PRICE, {"title": text})
        await send_text(from_msisdn, "¡Bien! Ahora, indica el *precio por día* (ej: 10 USD).")
        return

    if s == Step.PUBLISH_PRICE:
        st["draft"]["price"] = text
        await set_session(from_msisdn, Step.PUBLISH_ZONE, st["draft"])
        await send_text(from_msisdn, "Ok. ¿En qué *zona de Caracas* se encuentra? (ej: Chacao, El Paraíso)")
        return

    if s == Step.PUBLISH_ZONE:
        st["draft"]["zone"] = text
        await set_session(from_msisdn, Step.PUBLISH_PAYMENTS, st["draft"])
        await send_text(from_msisdn, "Casi listo. ¿Qué *métodos de pago* aceptas? (separados por coma)")
        return

    if s == Step.PUBLISH_PAYMENTS:
        pmts = [p.strip() for p in re.split(r"[,;]+", text) if p.strip()]
        d = st["draft"]
        item_id = await insert_listing(from_msisdn, d["title"], d["price"], d["zone"], pmts)
        await set_session(from_msisdn, Step.IDLE, {})
        pagos = ", ".join(pmts) if pmts else "A convenir"
        await send_text(from_msisdn, f"¡Publicación creada! ID: *#{item_id}*\n- {d['title']}\n- Precio: {d['price']}\n- Zona: {d['zone']}\n- Pagos: {pagos}")
        await send_main_menu(from_msisdn)
        return

    if upper.startswith("ALQUILAR"):
        m = re.search(r"#(\d+)", text)
        if not m:
            await send_text(from_msisdn, "Formato incorrecto. Usa: ALQUILAR #ID")
            return
        item_id = m.group(1)
        listing = await get_listing(item_id)
        if not listing:
            await send_text(from_msisdn, "No encontré un artículo con ese ID.")
            return
        if listing['owner_wa'] == from_msisdn:
            await send_text(from_msisdn, "No puedes alquilar tu propio artículo.")
            return

        dates = _extract_dates(text)
        if dates:
            start_iso, end_iso = dates
            await set_session(from_msisdn, Step.RENTAL_WAIT_PAYMENT, {"item_id": int(item_id), "start_iso": start_iso, "end_iso": end_iso})
            payment_options = listing.get("payment_methods") or ["A convenir"]
            rows = [{"id": p.replace(" ", "_"), "title": p} for p in payment_options]
            await send_list(from_msisdn, f"Alquiler de #{item_id}", "Selecciona tu método de pago:", "Ver Pagos", rows)
        else:
            await set_session(from_msisdn, Step.RENTAL_WAIT_DATES, {"item_id": int(item_id)})
            await send_text(from_msisdn, f"Perfecto. Ahora, indica las *fechas* que necesitas para el artículo #{item_id} (formato: DD/MM/AAAA a DD/MM/AAAA).")
        return

    if s == Step.RENTAL_WAIT_DATES:
        dates = _extract_dates(text)
        if not dates:
            await send_text(from_msisdn, "Formato de fechas no válido. Ejemplo: 15/10/2025 a 20/10/2025")
            return
        start_iso, end_iso = dates
        item_id = st["draft"]["item_id"]
        await set_session(from_msisdn, Step.RENTAL_WAIT_PAYMENT, {"item_id": item_id, "start_iso": start_iso, "end_iso": end_iso})
        listing = await get_listing(str(item_id))
        payment_options = listing.get("payment_methods") or ["A convenir"]
        rows = [{"id": p.replace(" ", "_"), "title": p} for p in payment_options]
        await send_list(from_msisdn, f"Alquiler de #{item_id}", "¡Fechas guardadas! Ahora, selecciona tu método de pago.", "Ver Pagos", rows)
        return

    # NUEVO: Extensión de renta (solo pedimos nueva fecha fin; si el usuario manda rango, se usa el fin)
    if s == Step.RENTAL_EXTENSION_WAIT_DATES:
        rental_id = st["draft"].get("rental_id")
        if not rental_id:
            await send_text(from_msisdn, "No encontré la solicitud de extensión. Intenta de nuevo desde el menú de la renta.")
            await set_session(from_msisdn, Step.IDLE, {})
            await send_main_menu(from_msisdn)
            return

        # Aceptamos una fecha o rango. Si es rango, tomamos el fin.
        dates = _extract_dates(text)
        if not dates:
            # Intentar capturar una sola fecha DD/MM/AAAA
            m1 = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", text)
            if m1:
                end_iso = "/".join(m1.group(1).split("/")[::-1])  # dd/mm/yyyy -> yyyy/mm/dd (pero lo convertimos abajo en supabase si hace falta)
                # Mejor convertir correctamente a ISO
                d, mth, y = map(int, m1.group(1).split("/"))
                end_iso = f"{y:04d}-{mth:02d}-{d:02d}"
            else:
                await send_text(from_msisdn, "Formato de fecha no válido. Usa DD/MM/AAAA o un rango 'DD/MM/AAAA a DD/MM/AAAA'.")
                return
        else:
            # Usar la segunda como nueva fecha fin
            _, end_iso = dates

        result = await request_rental_extension(int(rental_id), from_msisdn, end_iso)
        status = result.get("status")
        if status == "EXTENSION_PENDING":
            other = result.get("other_party")
            await send_text(from_msisdn, f"Solicitud de extensión registrada hasta *{_to_ve(end_iso)}*. La otra parte debe confirmarla.")
            await send_text(other, f"El otro usuario solicita extender la renta #{rental_id} hasta *{_to_ve(end_iso)}*. Para aceptar, responde: EXTENDER RENTA #{rental_id} { _to_ve(end_iso) }")
        elif status == "EXTENDED":
            for party in result.get("parties", []):
                await send_text(party, f"¡Listo! La renta #{rental_id} fue extendida hasta *{_to_ve(end_iso)}* por mutuo acuerdo.")
        else:
            await send_text(from_msisdn, "No se pudo procesar la extensión. Verifica el ID o inténtalo más tarde.")
        await set_session(from_msisdn, Step.IDLE, {})
        await send_main_menu(from_msisdn)
        return

    # Estado idle u otros → menú
    await send_main_menu(from_msisdn)

# === ENTRADA PRINCIPAL (desde el webhook) ===
async def handle_message(value: Dict[str, Any], msg: Dict[str, Any]):
    from_msisdn = msg["from"]
    profile_name = (value.get("contacts", [{}])[0].get("profile", {}) or {}).get("name")
    await ensure_user(from_msisdn, profile_name)

    st = await get_session(from_msisdn)

    if msg["type"] == "interactive":
        await handle_interactive(msg, st, from_msisdn)
    else:
        await handle_text(msg, st, from_msisdn)
