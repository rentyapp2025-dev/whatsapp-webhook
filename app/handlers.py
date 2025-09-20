import re
import json
import uuid
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, date, timedelta

import httpx

from .state import Step, step_val, _extract_dates, _to_ve
from .wa_api import send_text, send_reply_buttons, send_list, send_main_menu
from .clients.llm_client import chat_completion
from .clients.supabase_client import (
    ensure_user, get_user_name,
    set_session, get_session,
    insert_listing, get_listing,
    # ---- Consents (por consent_id) ----
    upsert_consent, get_consent_by_id, set_consent_flag_by_id, mark_introduced_once_by_consent,
    # ---- Rentals & demás ----
    create_rental_request,
    get_active_rentals_for_item, update_listing_status,
    add_review, get_reviews_for_user,
    request_rental_cancellation,
    request_rental_extension,
    get_listings_for_user,
    get_rentals_for_user,
    confirm_rental_start,
    is_item_available,
    get_future_bookings,
    get_rental,
    _today_business,
    end_of_first_overlap,
)

# ======================
# Config UX / Paginación
# ======================
MYRENTALS_PAGE_SIZE = 10  # cantidad por página
BUSINESS_TZ = "America/Caracas"  # referencia comunicacional (guardas UTC en DB)

# =================
# Helpers de fechas
# =================
def _validate_date_window(start_iso: str, end_iso: str) -> bool:
    """start >= hoy (en TZ de negocio) y end > start (comparación por fecha, sin horas)."""
    try:
        s = datetime.strptime(start_iso[:10], "%Y-%m-%d").date()
        e = datetime.strptime(end_iso[:10], "%Y-%m-%d").date()
        return s >= _today_business() and e > s
    except Exception:
        return False


def _fmt_suggestions(sugs: List[tuple]) -> str:
    if not sugs:
        return ""
    lines = []
    for i, (s, e) in enumerate(sugs, 1):
        lines.append(f"  {i}. {_to_ve(s)} a {_to_ve(e)}")
    return "\n".join(lines)


def _overlaps(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    return a_start <= b_end and b_start <= a_end


def _safe_date(d: str) -> Optional[date]:
    try:
        return datetime.strptime(d[:10], "%Y-%m-%d").date()
    except Exception:
        return None


# ============================
# Helpers de UI / Idempotencia
# ============================
def _new_token() -> str:
    """Token de idempotencia simple para botones (previene doble tap)."""
    return uuid.uuid4().hex[:12]


def _card_for_rental(r: Dict[str, Any], you_msisdn: str) -> str:
    is_owner = r.get("seller_wa") == you_msisdn
    role = "(Eres el dueño)" if is_owner else "(Eres el inquilino)"
    title = (r.get("listing") or {}).get("title", f"Artículo #{r['item_id']}")
    start, end = _to_ve(r['start_date']), _to_ve(r['end_date'])
    lines = [
        f"📝 *Renta #{r['id']}* {role}",
        f"   - Artículo: {title}",
        f"   - Fechas: {start} a {end}",
        f"   - Estado: *{r['status']}*",
    ]
    # Si tu backend adjunta información adicional (precio, política, versión) la mostramos
    if r.get("price_per_day"):
        lines.append(f"   - Tarifa: {r['price_per_day']} por día")
    if r.get("policy"):
        lines.append(f"   - Política: {r['policy']}")
    return "\n".join(lines)


def _format_collision_message(overlap_end: date) -> str:
    """Mensaje de colisión: fin de la renta activa y días restantes."""
    fin = overlap_end.strftime("%d/%m/%Y")
    left = (overlap_end - _today_business()).days + 1
    if left < 1:
        left = 1
    dias = "día" if left == 1 else "días"
    return (
        f"Esas fechas chocan con una renta activa que finaliza el *{fin}* "
        f"(faltan *{left} {dias}*).\n\n"
        "Envía un nuevo rango que *no* solape con el formato: *DD/MM/AAAA a DD/MM/AAAA*."
    )


async def _send_rental_management_menu(target_wa: str, rental: Dict[str, Any]):
    """
    Muestra botones de acción según estado de la renta.
    - pending  -> Confirmar / Cancelar
    - active   -> Extender / Cancelar
    - finished/cancelled/others -> solo info
    Incluye token anti-tap y, si existe en el objeto, el versioning esperado.
    """
    rid = rental["id"]
    status = (rental.get("status") or "").lower()
    body = _card_for_rental(rental, target_wa)

    token = _new_token()
    expected_version = rental.get("version")  # si lo expones desde backend
    ver_suffix = f"_{expected_version}" if expected_version is not None else ""

    if status == "pending":
        buttons = [
            {"id": f"rental_confirm_{rid}_{token}{ver_suffix}", "title": "✅ Confirmar inicio"},
            {"id": f"rental_cancel_{rid}_{token}{ver_suffix}", "title": "❌ Cancelar"},
        ]
        await send_reply_buttons(target_wa, "Gestión de Renta", body + "\n\n¿Qué deseas hacer?", buttons)

    elif status == "active":
        buttons = [
            {"id": f"rental_extend_{rid}_{token}{ver_suffix}", "title": "🔄 Extender"},
            {"id": f"rental_cancel_{rid}_{token}{ver_suffix}", "title": "❌ Cancelar"},
        ]
        await send_reply_buttons(target_wa, "Gestión de Renta", body + "\n\n¿Qué deseas hacer?", buttons)

    else:
        await send_text(target_wa, body)


async def _render_my_rentals_page(user_wa: str, rentals: List[Dict[str, Any]], offset: int):
    """
    Renderiza una página de 'Mis Alquileres' con paginación básica.
    """
    if not rentals:
        await send_text(user_wa, "No tienes alquileres activos o pasados.")
        return

    end_index = min(offset + MYRENTALS_PAGE_SIZE, len(rentals))
    page = rentals[offset:end_index]

    chunks = []
    for r in page:
        chunks.append(_card_for_rental(r, user_wa))
    txt = "Tus alquileres:\n\n" + "\n\n".join(chunks)
    await send_text(user_wa, txt)

    # Botones de paginación
    buttons = []
    if offset > 0:
        prev_off = max(0, offset - MYRENTALS_PAGE_SIZE)
        buttons.append({"id": f"myrentals_page_{prev_off}", "title": "⬅️ Anteriores"})
    if end_index < len(rentals):
        next_off = end_index
        buttons.append({"id": f"myrentals_page_{next_off}", "title": "➡️ Siguientes"})
    # Ayuda de gestión directa
    buttons.append({"id": "myrentals_one", "title": "🔎 Ver uno (#ID)"})

    await send_reply_buttons(user_wa, "Mis Alquileres", "Navega tus rentas o gestiona una en particular:", buttons)


async def _send_post_agreement_menus(buyer_wa: str, seller_wa: str, item_id: str, rental_id: str):
    """
    Menús post-acuerdo para estado PENDIENTE (sin 'Extender').
    """
    body_common = (
        f"Renta del artículo #{item_id}\n\n"
        "Estado actual: *PENDIENTE*.\n"
        "Para activar la renta, *ambas partes* deben confirmar el inicio.\n\n"
        "Opciones disponibles:"
    )

    # Arrendatario
    body_buyer = (
        body_common
        + "\n• Confirmar inicio (activa la renta cuando los dos confirmen)"
          "\n• Cancelar (requiere confirmación de ambas partes)"
    )
    tok_b = _new_token()
    buttons_buyer = [
        {"id": f"rental_confirm_{rental_id}_{tok_b}", "title": "✅ Confirmar inicio"},
        {"id": f"rental_cancel_{rental_id}_{tok_b}", "title": "❌ Cancelar"},
    ]
    await send_reply_buttons(buyer_wa, "Gestión de Renta", body_buyer, buttons_buyer)

    # Arrendador
    body_seller = (
        body_common
        + "\n• Confirmar inicio (activa la renta cuando los dos confirmen)"
          "\n• Cancelar (requiere confirmación de ambas partes)"
    )
    tok_s = _new_token()
    buttons_seller = [
        {"id": f"rental_confirm_{rental_id}_{tok_s}", "title": "✅ Confirmar inicio"},
        {"id": f"rental_cancel_{rental_id}_{tok_s}", "title": "❌ Cancelar"},
    ]
    await send_reply_buttons(seller_wa, "Gestión de Renta", body_seller, buttons_seller)


# =========================
# Flujo de creación inicial
# =========================
async def finalize_and_introduce(consent_id: str, actor_msisdn: str):
    """
    Se llama cuando ambos dieron consentimiento.
    Crea la renta en estado PENDIENTE y presenta a las partes.
    Trabaja por consent_id (una solicitud independiente).
    """
    cons = await get_consent_by_id(consent_id)
    if not cons:
        return

    item_id = str(cons["item_id"])
    buyer_wa, seller_wa = cons["buyer_wa"], cons["seller_wa"]
    st = await get_session(buyer_wa)
    draft = st.get("draft", {})

    rental_id_str = ""
    if 'start_iso' in draft and 'end_iso' in draft and 'selected_payment_method' in draft:
        # Revalidación final
        listing = await get_listing(item_id)
        if not listing or listing.get("status") != "active":
            await send_text(buyer_wa, "La publicación ya no está activa; no se pudo crear la renta.")
        elif not _validate_date_window(draft['start_iso'], draft['end_iso']):
            await send_text(buyer_wa, "Las fechas ya no son válidas. Intenta proponer un nuevo rango.")
        elif not await is_item_available(item_id, draft['start_iso'], draft['end_iso']):
            # Colisión
            bookings = await get_future_bookings(item_id, from_iso=draft['start_iso'])
            s_d = _safe_date(draft['start_iso']); e_d = _safe_date(draft['end_iso'])
            if s_d and e_d and bookings:
                overlap_end = end_of_first_overlap(bookings, s_d, e_d)
                await send_text(buyer_wa, _format_collision_message(overlap_end))
            else:
                await send_text(buyer_wa, "Ese rango ya fue tomado. Propón nuevas fechas.")
        else:
            r = await create_rental_request(
                int(item_id), buyer_wa, draft['start_iso'], draft['end_iso'], draft['selected_payment_method']
            )
            if r.get("ok"):
                rental_id_str = str(r["row"]["id"])
        # limpiar sesión del comprador
        await set_session(buyer_wa, Step.IDLE, {})

    # Presentación (una sola vez por consent_id) + aviso de estado
    if await mark_introduced_once_by_consent(consent_id):
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
            await _send_post_agreement_menus(buyer_wa, seller_wa, item_id, rental_id_str)

    # Limpiar sesión y menú para quien accionó
    if actor_msisdn != buyer_wa:
        await set_session(actor_msisdn, Step.IDLE, {})
    await send_main_menu(actor_msisdn)


# ================
# INTERACTIVE FLOW
# ================
async def handle_interactive(msg: Dict[str, Any], st: Dict[str, Any], from_msisdn: str):
    s = step_val(st)
    interactive = msg["interactive"]
    itype = interactive["type"]

    if itype == "button_reply":
        btn_id = interactive["button_reply"]["id"]

        # CONSENTIMIENTO (por consent_id)
        if btn_id.startswith("consent_"):
            # consent_yes_{consentId} | consent_no_{consentId}
            parts = btn_id.split("_")
            answer, consent_id = parts[1], parts[2]
            cons = await set_consent_flag_by_id(consent_id, from_msisdn, ok=(answer == "yes"))
            if not cons:
                await send_text(from_msisdn, "No se encontró la solicitud.")
                return

            if cons.get("buyer_ok") and cons.get("seller_ok"):
                await finalize_and_introduce(consent_id, from_msisdn)
            elif answer == "no":
                other = cons["seller_wa"] if from_msisdn == cons["buyer_wa"] else cons["buyer_wa"]
                await send_text(from_msisdn, "Entendido. Tu decisión fue registrada.")
                await send_text(other, "La otra parte ha rechazado la solicitud. La operación se canceló.")
                await set_session(from_msisdn, Step.IDLE, {})
            else:
                await send_text(from_msisdn, "Gracias. Esperamos la respuesta de la otra parte.")
            return

        # POST-ACUERDO (confirmar/cancelar/extender)
        if btn_id.startswith("rental_confirm_"):
            # rental_confirm_{rid}_{token}[_version]
            await handle_rental_confirmation(btn_id, from_msisdn)
            return

        if btn_id.startswith("rental_cancel_"):
            rid = btn_id.split("_")[2]
            await handle_cancellation_request(rid, from_msisdn)
            return

        if btn_id.startswith("rental_extend_"):
            rid = btn_id.split("_")[2]
            await set_session(from_msisdn, Step.RENTAL_EXTENSION_WAIT_DATES, {"rental_id": int(rid)})
            await send_text(from_msisdn, "Indica la *nueva fecha de fin* en formato: DD/MM/AAAA.")
            return

        # Aceptar extensión
        # Formato: rental_ext_accept_{rentalId}_{YYYY-MM-DD}
        if btn_id.startswith("rental_ext_accept_"):
            try:
                _, _, rid, end_iso = btn_id.split("_", 3)
                rid_int = int(rid)
                res = await request_rental_extension(rid_int, from_msisdn, end_iso)
                if res.get("status") == "EXTENDED":
                    for wa in res.get("parties", []):
                        await send_text(wa, f"✅ La renta #{rid_int} fue *extendida* hasta *{_to_ve(end_iso)}* por mutuo acuerdo.")
                elif res.get("status") == "EXTENSION_PENDING":
                    await send_text(from_msisdn, "Tu respuesta fue registrada. Falta la confirmación de la otra parte.")
                else:
                    reason = res.get("reason") or "Verifica que la renta esté activa y la fecha sea válida."
                    await send_text(from_msisdn, f"No se pudo confirmar la extensión. {reason}")
            except Exception:
                await send_text(from_msisdn, "No se pudo procesar el botón de extensión.")
            return

        # Paginación de "Mis Alquileres"
        if btn_id.startswith("myrentals_page_"):
            try:
                off = int(btn_id.split("_")[-1])
            except Exception:
                off = 0
            rentals = await get_rentals_for_user(from_msisdn)
            await _render_my_rentals_page(from_msisdn, rentals or [], off)
            return

        # Botones del submenú "Mis Alquileres"
        if btn_id == "myrentals_all":
            rentals = await get_rentals_for_user(from_msisdn)
            await _render_my_rentals_page(from_msisdn, rentals or [], 0)
            return

        if btn_id == "myrentals_one":
            await set_session(from_msisdn, Step.RENTAL_VIEW_ONE, {})
            await send_text(from_msisdn, "Escribe el *número de renta* (ej: `#123` o `123`).")
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
                # Colisión
                s_d = _safe_date(start_iso); e_d = _safe_date(end_iso)
                bookings = await get_future_bookings(item_id, from_iso=start_iso)
                if s_d and e_d and bookings:
                    overlap_end = end_of_first_overlap(bookings, s_d, e_d)
                    await send_text(from_msisdn, _format_collision_message(overlap_end))
                else:
                    await send_text(from_msisdn, "Lo siento, esas fechas ya no están disponibles para este artículo.")
                await set_session(from_msisdn, Step.RENTAL_WAIT_DATES, {"item_id": int(item_id)})
                return

            # Si todo OK, persistimos y pedimos consentimientos
            draft["selected_payment_method"] = row_title

            seller, buyer = listing["owner_wa"], from_msisdn
            cons_res = await upsert_consent(item_id, buyer, seller)

            # soporta que upsert_consent devuelva {"row": {...}} o directamente {...}
            row = cons_res.get("row", cons_res) if cons_res else {}
            consent_id = str(row["id"])

            # guardamos consent_id en el draft (para fallback textual)
            new_draft = {**draft, "consent_id": consent_id}
            await set_session(from_msisdn, s, new_draft)

            msg_to_seller = (f"¡Nueva solicitud para tu artículo #{item_id}!\n\n"
                             f"Fechas: del *{_to_ve(start_iso)}* al *{_to_ve(end_iso)}*\n"
                             f"Método de pago: *{row_title}*\n\n"
                             "¿Aceptas compartir tu contacto para coordinar?")
            seller_buttons = [{"id": f"consent_yes_{consent_id}", "title": "Sí, acepto"},
                              {"id": f"consent_no_{consent_id}", "title": "No, gracias"}]
            await send_reply_buttons(seller, "Confirmación de Alquiler", msg_to_seller, seller_buttons)

            await send_text(buyer, "¡Excelente! Hemos enviado tu solicitud al dueño. Para continuar, solo falta tu autorización final para compartir tu contacto.")
            buyer_buttons = [{"id": f"consent_yes_{consent_id}", "title": "Sí, autorizo"},
                             {"id": f"consent_no_{consent_id}", "title": "No autorizo"}]
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
            # Mostrar submenú con opciones
            body = "¿Qué te gustaría ver?"
            buttons = [
                {"id": "myrentals_all", "title": "📋 Ver todos"},
                {"id": "myrentals_one", "title": "🔎 Ver uno (#ID)"},
            ]
            await send_reply_buttons(from_msisdn, "Mis Alquileres", body, buttons)
        elif row_id == "menu_my_reviews":
            await handle_text({"text": {"body": "MIS RESEÑAS"}}, st, from_msisdn)
        elif row_id == "menu_help":
            await send_text(
                from_msisdn,
                "Comandos útiles:\n"
                "- `ELIMINAR #ID`: Quita una publicación.\n"
                "- `CANCELAR RENTA #ID`: Cancela una renta.\n"
                "- `RESEÑA #ID_RENTA 1-5`: Deja una opinión."
            )

# =========
# TEXT FLOW
# =========
async def handle_text(msg: Dict[str, Any], st: Dict[str, Any], from_msisdn: str):
    s = step_val(st)
    text = (msg.get("text", {}).get("body", "")).strip()
    if not text:
        return
    upper = text.upper()

    if upper in {"MENU", "MENÚ", "INICIO", "AYUDA"}:
        await set_session(from_msisdn, Step.IDLE, {})
        await send_main_menu(from_msisdn)
        return

    # === Fallback textual para consentimiento final (usa consent_id del draft) ===
    if s == Step.RENTAL_WAIT_PAYMENT and st.get("draft", {}).get("consent_id"):
        consent_id = str(st["draft"]["consent_id"])
        if upper in {"SI, AUTORIZO", "SÍ, AUTORIZO", "SI AUTORIZO", "SÍ AUTORIZO"}:
            cons = await set_consent_flag_by_id(consent_id, from_msisdn, ok=True)
            if not cons:
                await send_text(from_msisdn, "No se encontró la solicitud.")
                return
            if cons.get("buyer_ok") and cons.get("seller_ok"):
                await finalize_and_introduce(consent_id, from_msisdn)
            else:
                await send_text(from_msisdn, "Gracias. Esperamos la respuesta de la otra parte.")
            return
        if upper in {"NO AUTORIZO", "NO, AUTORIZO", "NO", "NO ACEPTO"}:
            cons = await set_consent_flag_by_id(consent_id, from_msisdn, ok=False)
            if cons:
                other = cons["seller_wa"] if from_msisdn == cons["buyer_wa"] else cons["buyer_wa"]
                await send_text(from_msisdn, "Entendido. Tu decisión fue registrada.")
                await send_text(other, "La otra parte ha rechazado la solicitud. La operación se canceló.")
                await set_session(from_msisdn, Step.IDLE, {})
                await send_main_menu(from_msisdn)
            else:
                await send_text(from_msisdn, "No se encontró la solicitud.")
            return
    # === FIN fallback textual consentimiento ===

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
                # Colisión → informar fin y días restantes, mantener el paso para reintento
                s_d = _safe_date(start_iso); e_d = _safe_date(end_iso)
                bookings = await get_future_bookings(item_id, from_iso=start_iso)
                if s_d and e_d and bookings:
                    overlap_end = end_of_first_overlap(bookings, s_d, e_d)
                    await send_text(from_msisdn, _format_collision_message(overlap_end))
                else:
                    await send_text(from_msisdn, "Lo siento, ese rango está ocupado para este artículo.")
                await set_session(from_msisdn, Step.RENTAL_WAIT_DATES, {"item_id": int(item_id)})
                return

            await set_session(from_msisdn, Step.RENTAL_WAIT_PAYMENT, {"item_id": int(item_id), "start_iso": start_iso, "end_iso": end_iso})
            payment_options = listing.get("payment_methods") or ["A convenir"]
            rows = [{"id": p.replace(" ", "_"), "title": p} for p in payment_options]
            await send_list(from_msisdn, f"Alquiler de #{item_id}", "Selecciona tu método de pago:", "Ver Pagos", rows)
        else:
            await set_session(from_msisdn, Step.RENTAL_WAIT_DATES, {"item_id": int(item_id)})
            await send_text(from_msisdn, f"Perfecto. Ahora, indica las *fechas* que necesitas para el artículo #{item_id} (formato: DD/MM/AAAA a DD/MM/AAAA).")
        return

    # Ver una renta específica directamente: "ALQUILER #123" o "Renta #123"
    if re.search(r"\b(ALQUILER|RENTA)\b", upper) and re.search(r"[#№](\d+)", text):
        rid = int(re.search(r"[#№](\d+)", text).group(1))
        r = await get_rental(rid)
        if not r:
            await send_text(from_msisdn, f"No encontré la renta #{rid}.")
            return
        await send_text(from_msisdn, _card_for_rental(r, from_msisdn))
        # Menú de acciones según estado
        await _send_rental_management_menu(from_msisdn, r)
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
        await _render_my_rentals_page(from_msisdn, rentals or [], 0)
        return

    # Atajo: "MIS ALQUILERES #123"
    if upper.startswith("MIS ALQUILERES #") or re.match(r"^MIS\s+ALQUILERES\s*[#№]\d+", upper):
        rid = int(re.search(r"[#№](\d+)", text).group(1))
        r = await get_rental(rid)
        if not r:
            await send_text(from_msisdn, f"No encontré la renta #{rid}.")
            return
        await send_text(from_msisdn, _card_for_rental(r, from_msisdn))
        await _send_rental_management_menu(from_msisdn, r)
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
            # Colisión → informar fin y días restantes, mantener el paso para reintento
            s_d = _safe_date(start_iso); e_d = _safe_date(end_iso)
            bookings = await get_future_bookings(item_id, from_iso=start_iso)
            if s_d and e_d and bookings:
                overlap_end = end_of_first_overlap(bookings, s_d, e_d)
                await send_text(from_msisdn, _format_collision_message(overlap_end))
            else:
                await send_text(from_msisdn, "Esas fechas ya están reservadas. Prueba un rango distinto.")
            # seguimos esperando fechas
            await set_session(from_msisdn, Step.RENTAL_WAIT_DATES, {"item_id": item_id})
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

        # Obtener la renta actual para validar estado y fecha fin
        r = await get_rental(int(rental_id))
        if not r:
            await send_text(from_msisdn, f"No encontré la renta #{rental_id}.")
            await set_session(from_msisdn, Step.IDLE, {})
            await send_main_menu(from_msisdn)
            return

        status_r = (r.get("status") or "").lower()
        if status_r != "active":
            await send_text(from_msisdn, "Solo puedes extender una renta *activa*.")
            await set_session(from_msisdn, Step.IDLE, {})
            await send_main_menu(from_msisdn)
            return

        # Parseo de fecha: admitimos "DD/MM/AAAA" o "DD/MM/AAAA a DD/MM/AAAA"
        dates = _extract_dates(text)
        end_iso = None
        if dates:
            _, end_iso = dates
        else:
            m1 = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", text)
            if m1:
                d_, mth, y = map(int, m1.group(1).split("/"))
                end_iso = f"{y:04d}-{mth:02d}-{d_:02d}"

        if not end_iso:
            await send_text(from_msisdn, "Formato de fecha no válido. Usa *DD/MM/AAAA* para la nueva fecha de fin.")
            return

        # Validaciones de ventana (nueva fecha fin > hoy y > fecha fin actual)
        new_end = _safe_date(end_iso)
        current_end = _safe_date(r["end_date"])
        if not (new_end and current_end):
            await send_text(from_msisdn, "No pude interpretar la fecha. Usa *DD/MM/AAAA*.")
            return

        today = _today_business()
        if new_end <= today:
            await send_text(from_msisdn, "La nueva fecha de fin debe ser *posterior a hoy*.")
            return
        if new_end <= current_end:
            await send_text(from_msisdn, f"La nueva fecha de fin debe ser *posterior a { _to_ve(r['end_date']) }*.")
            return

        # Disponibilidad del TRAMO EXTRA: (current_end + 1) .. new_end
        item_id = r["item_id"]
        try:
            # get_future_bookings devuelve List[Tuple[date, date]]
            bookings = await get_future_bookings(str(item_id)) or []
            extra_start = current_end + timedelta(days=1)
            extra_end = new_end
            conflict = any(_overlaps(extra_start, extra_end, bs, be) for (bs, be) in bookings)
            if conflict:
                await send_text(from_msisdn, "No es posible extender: el tramo adicional *se solapa* con otra reserva.")
                return
        except Exception:
            # Si falla la comprobación, continuamos y dejamos la decisión al backend (que valida).
            pass

        # Solicitud de extensión (requiere confirmación de la otra parte)
        result = await request_rental_extension(int(rental_id), from_msisdn, end_iso)
        status = result.get("status")

        if status == "EXTENSION_PENDING":
            other = result.get("other_party")
            await send_text(from_msisdn, f"Solicitud de extensión registrada hasta *{_to_ve(end_iso)}*. La otra parte debe confirmarla.")
            buttons = [{"id": f"rental_ext_accept_{rental_id}_{end_iso[:10]}", "title": "✅ Aceptar extensión"}]
            body = (f"El otro usuario solicita *extender* la renta #{rental_id} hasta *{_to_ve(end_iso)}*.\n\nElige una opción:")
            await send_reply_buttons(other, "Extensión de Renta", body, buttons)
            await set_session(from_msisdn, Step.IDLE, {})
            await send_main_menu(from_msisdn)

        elif status == "EXTENDED":
            for party in result.get("parties", []):
                await send_text(party, f"¡Listo! La renta #{rental_id} fue extendida hasta *{_to_ve(end_iso)}* por mutuo acuerdo.")
            await set_session(from_msisdn, Step.IDLE, {})
            await send_main_menu(from_msisdn)

        else:
            reason = result.get("reason") or "No se pudo procesar la extensión."
            await send_text(from_msisdn, f"No se pudo procesar la extensión. {reason}")
            await set_session(from_msisdn, Step.IDLE, {})
            await send_main_menu(from_msisdn)
        return

    # Estado: pedir un ID de renta específico
    if s == Step.RENTAL_VIEW_ONE:
        m = re.search(r"(\d+)", text)
        if not m:
            await send_text(from_msisdn, "Por favor envía un número de renta válido. Ej: `#123` o `123`.")
            return
        rid = int(m.group(1))
        r = await get_rental(rid)
        await set_session(from_msisdn, Step.IDLE, {})
        if not r:
            await send_text(from_msisdn, f"No encontré la renta #{rid}.")
            await send_main_menu(from_msisdn)
            return
        await send_text(from_msisdn, _card_for_rental(r, from_msisdn))
        await _send_rental_management_menu(from_msisdn, r)
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
                    # Colisión -> informar fin y días restantes y mantener paso
                    s_d = _safe_date(start_iso); e_d = _safe_date(end_iso)
                    bookings = await get_future_bookings(item_id, from_iso=start_iso)
                    if s_d and e_d and bookings:
                        overlap_end = end_of_first_overlap(bookings, s_d, e_d)
                        await send_text(from_msisdn, _format_collision_message(overlap_end))
                    else:
                        await send_text(from_msisdn, "Ese rango ya está reservado para este artículo.")
                    await set_session(from_msisdn, Step.RENTAL_WAIT_DATES, {"item_id": int(item_id)})
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


# =================
# ENTRADA PRINCIPAL
# =================
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


# ===========
# AUXILIARES
# ===========
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
        if match:
            return json.loads(match.group(0))
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
    Admite sufijos de token/version: rental_confirm_{rid}_{token}[_version]
    """
    try:
        parts = btn_id.split("_")
        rental_id = int(parts[2])
        # Si adjuntas expected_version, lo puedes pasar a tu API confirm_rental_start vía header/campo adicional
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
