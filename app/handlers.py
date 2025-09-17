import re
from typing import Dict, Any

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
    update_rental_status,
    request_rental_extension,
)

# ==============================
# Helpers de UX profesional
# ==============================
async def _send_post_agreement_menus(buyer_wa: str, seller_wa: str, item_id: str, rental_id: str):
    """
    Menú post-acuerdo para ambas partes.
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

async def _notify_rental_created(buyer_wa: str, seller_wa: str, rental_id: str, item_id: str, start_iso: str, end_iso: str):
    """
    Aviso explícito de renta creada (evita sensación de 'pegado').
    """
    pretty = f"Renta #{rental_id} – Artículo #{item_id}\nFechas: {_to_ve(start_iso)} a {_to_ve(end_iso)}"
    await send_text(buyer_wa, f"🎉 ¡Listo! {pretty}\nTus datos se compartieron con el vendedor para coordinar entrega y pago.")
    await send_text(seller_wa, f"🎉 ¡Listo! {pretty}\nEl comprador ya puede contactarte para coordinar la entrega y pago.")

# ==============================
# Finalización al tener doble OK
# ==============================
async def finalize_and_introduce(item_id: str, actor_msisdn: str):
    """
    Cuando ambas partes dieron consentimiento:
      - Crea la renta (active)
      - Presenta a las partes
      - Envía menú de gestión
      - Evita “quedarse pegado” aunque falte draft
    """
    cons = await get_consent(item_id)
    if not cons:
        await send_text(actor_msisdn, "No se encontró la solicitud. Intenta nuevamente.")
        return

    buyer_wa = cons["buyer_wa"]
    seller_wa = cons["seller_wa"]

    # Recuperar draft desde la sesión del comprador
    st_buyer = await get_session(buyer_wa)
    draft = (st_buyer or {}).get("draft", {}) or {}

    # Validación profesional: si faltan datos, avisa y no te quedes “pegado”.
    missing = []
    for k in ("start_iso", "end_iso", "selected_payment_method"):
        if k not in draft:
            missing.append(k)

    rental_id_str = ""
    if not missing:
        # Crear como ACTIVE (supabase_client ya crea con status="active")
        r = await create_rental_request(
            int(item_id), buyer_wa, draft["start_iso"], draft["end_iso"], draft["selected_payment_method"]
        )
        if r.get("ok"):
            rental_id_str = str(r["row"]["id"])
            # Notificar de forma explícita
            await _notify_rental_created(buyer_wa, seller_wa, rental_id_str, str(item_id), draft["start_iso"], draft["end_iso"])
            # Menú post-acuerdo
            await _send_post_agreement_menus(buyer_wa, seller_wa, str(item_id), rental_id_str)
        else:
            await send_text(buyer_wa, "Ocurrió un error creando la renta. Inténtalo más tarde.")
            await send_text(seller_wa, "Ocurrió un error creando la renta. Inténtalo más tarde.")
    else:
        # Fallback robusto: no bloquees al usuario, explícales qué falta
        faltan = ", ".join({
            "start_iso": "fecha inicio",
            "end_iso": "fecha fin",
            "selected_payment_method": "método de pago"
        }[k] for k in missing)
        await send_text(buyer_wa, f"Necesito completar tu solicitud: falta(n) {faltan}. Por favor indícame nuevamente las fechas y método de pago.")
        await set_session(buyer_wa, Step.RENTAL_WAIT_DATES, {"item_id": int(item_id)})
        await send_text(seller_wa, "Estamos pidiendo al comprador completar los datos para crear la renta.")
        # No retornamos error: dejamos el flujo encaminado

    # Presentación entre partes una sola vez
    if await mark_introduced_once(item_id):
        buyer_name = await get_user_name(buyer_wa)
        seller_name = await get_user_name(seller_wa)
        base_msg = f"¡Acuerdo logrado! Ya pueden coordinar el alquiler del artículo #{item_id}."
        await send_text(buyer_wa, f"{base_msg} Vendedor: {seller_name}")
        await send_text(seller_wa, f"{base_msg} Comprador: {buyer_name}")

    # Limpiar la sesión del actor que cerró el flujo y del comprador
    await set_session(buyer_wa, Step.IDLE, {})
    if actor_msisdn != buyer_wa:
        await set_session(actor_msisdn, Step.IDLE, {})
    await send_main_menu(actor_msisdn)

# ==============================
# INTERACTIVE
# ==============================
async def handle_interactive(msg: Dict[str, Any], st: Dict[str, Any], from_msisdn: str):
    s = step_val(st)
    interactive = msg["interactive"]
    itype = interactive["type"]

    if itype == "button_reply":
        btn_id = interactive["button_reply"]["id"]

        # CONSENTIMIENTO INICIAL
        if btn_id.startswith("consent_"):
            parts = btn_id.split("_")
            # Validaciones robustas
            if len(parts) < 3 or parts[1] not in {"yes", "no"} or not parts[2].isdigit():
                await send_text(from_msisdn, "Opción inválida. Intenta de nuevo desde el menú.")
                return

            answer, item_id = parts[1], parts[2]
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

        # POST-ACUERDO: CONFIRMAR / CANCELAR / EXTENDER
        if btn_id.startswith("rental_confirm_"):
            rental_id = btn_id.split("_")[-1]
            if not rental_id.isdigit():
                await send_text(from_msisdn, "ID de renta inválido.")
                return
            ok = await update_rental_status(int(rental_id), "active")
            await send_text(from_msisdn, "✅ Renta confirmada como *activa*." if ok else "No se pudo confirmar la renta. Inténtalo más tarde.")
            return

        if btn_id.startswith("rental_cancel_"):
            rental_id = btn_id.split("_")[-1]
            if not rental_id.isdigit():
                await send_text(from_msisdn, "ID de renta inválido.")
                return
            result = await request_rental_cancellation(int(rental_id), from_msisdn)
            status = result.get("status")
            if status == "CANCELLED":
                for party in result.get("parties", []):
                    await send_text(party, f"La renta #{rental_id} fue cancelada por mutuo acuerdo.")
            elif status == "WAITING_OTHER":
                await send_text(from_msisdn, "Solicitud de cancelación enviada. La otra parte debe confirmarla.")
                await send_text(result.get("other_party"), f"El otro usuario quiere cancelar la renta #{rental_id}. Para aceptar, escribe: CANCELAR RENTA #{rental_id}")
            else:
                await send_text(from_msisdn, "No se encontró la renta o no se puede cancelar.")
            return

        if btn_id.startswith("rental_extend_"):
            rental_id = btn_id.split("_")[-1]
            if not rental_id.isdigit():
                await send_text(from_msisdn, "ID de renta inválido.")
                return
            await set_session(from_msisdn, Step.RENTAL_EXTENSION_WAIT_DATES, {"rental_id": int(rental_id)})
            await send_text(from_msisdn, "Indica las *nuevas fechas* (puedes enviar solo la nueva fecha fin) en formato: DD/MM/AAAA o 'DD/MM/AAAA a DD/MM/AAAA'.")
            return

    # LIST REPLY (menú y pagos)
    if itype == "list_reply":
        row_id = interactive["list_reply"]["id"]
        row_title = interactive["list_reply"]["title"]

        # Selección del método de pago
        if s == Step.RENTAL_WAIT_PAYMENT:
            # Validación: que haya draft y fechas
            draft = st.get("draft") or {}
            if not all(k in draft for k in ("item_id", "start_iso", "end_iso")):
                await send_text(from_msisdn, "Perdí los datos de tu solicitud. Volvamos a indicar las fechas.")
                await set_session(from_msisdn, Step.RENTAL_WAIT_DATES, {"item_id": draft.get("item_id")})
                return

            draft["selected_payment_method"] = row_title
            await set_session(from_msisdn, s, draft)

            item_id, start_iso, end_iso = str(draft['item_id']), draft['start_iso'], draft['end_iso']
            listing = await get_listing(item_id)
            if not listing:
                await send_text(from_msisdn, "No encontré el artículo. Intenta de nuevo.")
                await set_session(from_msisdn, Step.IDLE, {})
                await send_main_menu(from_msisdn)
                return

            # Enviar consentimiento a vendedor y comprador
            seller, buyer = listing["owner_wa"], from_msisdn
            await upsert_consent(item_id, buyer, seller)

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

# ==============================
# TEXT
# ==============================
async def handle_text(msg: Dict[str, Any], st: Dict[str, Any], from_msisdn: str):
    s = step_val(st)
    text = (msg.get("text", {}).get("body", "")).strip()
    if not text:
        return
    upper = text.upper()

    # Menú
    if upper in {"MENU", "MENÚ", "INICIO"}:
        await set_session(from_msisdn, Step.IDLE, {})
        await send_main_menu(from_msisdn)
        return

    # Reseñas
    if upper.startswith("RESEÑA #"):
        match = re.search(r"RESEÑA\s*#(\d+)\s*([1-5])\s*(.*)", text, re.IGNORECASE)
        if not match:
            await send_text(from_msisdn, "Formato: RESEÑA #ID_RENTA CALIFICACIÓN COMENTARIO")
            return
        rental_id, rating, comment = match.groups()
        if not rental_id.isdigit():
            await send_text(from_msisdn, "ID de renta inválido.")
            return
        result = await add_review(int(rental_id), from_msisdn, int(rating), comment.strip())
        await send_text(from_msisdn, "¡Gracias por tu reseña!" if result.get("ok") else f"Error: {result.get('error', 'No se pudo guardar la reseña.')}")
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

    # Publicaciones
    if upper.startswith("ELIMINAR #"):
        item_id = text.split("#")[-1].strip()
        if not item_id.isdigit():
            await send_text(from_msisdn, "Proporciona un ID de artículo válido.")
            return
        if await get_active_rentals_for_item(item_id):
            await send_text(from_msisdn, f"No puedes eliminar la publicación #{item_id} porque tiene rentas activas.")
        elif await update_listing_status(item_id, from_msisdn, "inactive"):
            await send_text(from_msisdn, f"Publicación #{item_id} eliminada.")
        else:
            await send_text(from_msisdn, "No se pudo eliminar. Verifica el ID y que seas el dueño.")
        return

    if upper.startswith("CANCELAR RENTA #"):
        rental_id = text.split("#")[-1].strip()
        if not rental_id.isdigit():
            await send_text(from_msisdn, "Proporciona un ID de renta válido.")
            return
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

    # Máquina de estados: publicar
    if s == Step.PUBLISH_TITLE:
        if len(text) < 3:
            await send_text(from_msisdn, "El título es muy corto. Prueba con algo más descriptivo.")
            return
        await set_session(from_msisdn, Step.PUBLISH_PRICE, {"title": text})
        await send_text(from_msisdn, "¡Bien! Ahora, indica el *precio por día* (ej: 10 USD).")
        return

    if s == Step.PUBLISH_PRICE:
        # Validación simple: debe contener algún dígito
        if not re.search(r"\d", text):
            await send_text(from_msisdn, "Indica un precio válido (ej: 10 USD).")
            return
        st["draft"]["price"] = text
        await set_session(from_msisdn, Step.PUBLISH_ZONE, st["draft"])
        await send_text(from_msisdn, "Ok. ¿En qué *zona de Caracas* se encuentra? (ej: Chacao, El Paraíso)")
        return

    if s == Step.PUBLISH_ZONE:
        if len(text) < 3:
            await send_text(from_msisdn, "Zona inválida. Intenta con una zona conocida (ej: Chacao).")
            return
        st["draft"]["zone"] = text
        await set_session(from_msisdn, Step.PUBLISH_PAYMENTS, st["draft"])
        await send_text(from_msisdn, "Casi listo. ¿Qué *métodos de pago* aceptas? (separados por coma)")
        return

    if s == Step.PUBLISH_PAYMENTS:
        pmts = [p.strip() for p in re.split(r"[,;]+", text) if p.strip()]
        if not pmts:
            await send_text(from_msisdn, "Indica al menos un método de pago (ej: Zelle, Pago Móvil).")
            return
        d = st["draft"]
        item_id = await insert_listing(from_msisdn, d["title"], d["price"], d["zone"], pmts)
        await set_session(from_msisdn, Step.IDLE, {})
        pagos = ", ".join(pmts)
        await send_text(from_msisdn, f"¡Publicación creada! ID: *#{item_id}*\n- {d['title']}\n- Precio: {d['price']}\n- Zona: {d['zone']}\n- Pagos: {pagos}")
        await send_main_menu(from_msisdn)
        return

    # Solicitud de alquiler
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
        # Validación: que no exista ya una renta activa/pedida
        active = await get_active_rentals_for_item(item_id)
        if active:
            await send_text(from_msisdn, "Este artículo ya tiene una renta activa o en curso. Intenta con otro.")
            return

        dates = _extract_dates(text)
        if dates:
            start_iso, end_iso = dates
            if start_iso > end_iso:
                await send_text(from_msisdn, "El rango de fechas no es válido.")
                return
            await set_session(from_msisdn, Step.RENTAL_WAIT_PAYMENT, {"item_id": int(item_id), "start_iso": start_iso, "end_iso": end_iso})
            payment_options = listing.get("payment_methods") or ["A convenir"]
            rows = [{"id": p.replace(" ", "_"), "title": p} for p in payment_options][:10]
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
        if start_iso > end_iso:
            await send_text(from_msisdn, "El rango de fechas no es válido.")
            return
        item_id = st["draft"].get("item_id")
        if not item_id:
            await send_text(from_msisdn, "Perdí el ID del artículo. Intenta de nuevo: ALQUILAR #ID")
            await set_session(from_msisdn, Step.IDLE, {})
            await send_main_menu(from_msisdn)
            return

        listing = await get_listing(str(item_id))
        if not listing:
            await send_text(from_msisdn, "No encontré el artículo. Intenta de nuevo.")
            await set_session(from_msisdn, Step.IDLE, {})
            await send_main_menu(from_msisdn)
            return

        # Validación de bloqueos
        active = await get_active_rentals_for_item(str(item_id))
        if active:
            await send_text(from_msisdn, "Este artículo ya tiene una renta activa o en curso. Intenta con otro.")
            return

        await set_session(from_msisdn, Step.RENTAL_WAIT_PAYMENT, {"item_id": item_id, "start_iso": start_iso, "end_iso": end_iso})
        payment_options = listing.get("payment_methods") or ["A convenir"]
        rows = [{"id": p.replace(" ", "_"), "title": p} for p in payment_options][:10]
        await send_list(from_msisdn, f"Alquiler de #{item_id}", "¡Fechas guardadas! Ahora, selecciona tu método de pago.", "Ver Pagos", rows)
        return

    # Extensión de renta
    if s == Step.RENTAL_EXTENSION_WAIT_DATES:
        rental_id = (st.get("draft") or {}).get("rental_id")
        if not rental_id:
            await send_text(from_msisdn, "No encontré la solicitud de extensión. Intenta de nuevo desde el menú de la renta.")
            await set_session(from_msisdn, Step.IDLE, {})
            await send_main_menu(from_msisdn)
            return

        dates = _extract_dates(text)
        if not dates:
            m1 = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", text)
            if m1:
                d, mth, y = map(int, m1.group(1).split("/"))
                end_iso = f"{y:04d}-{mth:02d}-{d:02d}"
            else:
                await send_text(from_msisdn, "Formato de fecha no válido. Usa DD/MM/AAAA o un rango 'DD/MM/AAAA a DD/MM/AAAA'.")
                return
        else:
            _, end_iso = dates

        result = await request_rental_extension(int(rental_id), from_msisdn, end_iso)
        status = result.get("status")
        if status == "EXTENSION_PENDING":
            other = result.get("other_party")
            await send_text(from_msisdn, f"Solicitud de extensión registrada hasta *{_to_ve(end_iso)}*. La otra parte debe confirmarla.")
            await send_text(other, f"El otro usuario solicita extender la renta #{rental_id} hasta *{_to_ve(end_iso)}*. Para aceptar, responde: EXTENDER RENTA #{rental_id} {_to_ve(end_iso)}")
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

# ==============================
# ENTRADA PRINCIPAL (webhook)
# ==============================
async def handle_message(value: Dict[str, Any], msg: Dict[str, Any]):
    from_msisdn = msg["from"]
    profile_name = (value.get("contacts", [{}])[0].get("profile", {}) or {}).get("name")
    await ensure_user(from_msisdn, profile_name)

    st = await get_session(from_msisdn)

    if msg["type"] == "interactive":
        await handle_interactive(msg, st, from_msisdn)
    else:
        await handle_text(msg, st, from_msisdn)
