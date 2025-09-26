import os
import httpx
import re
from datetime import datetime, date, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple
from zoneinfo import ZoneInfo

# =========================
# Config & Consts
# =========================
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
# Límite de días por alquiler (opcional)
BUSINESS_TZ = os.environ.get("BUSINESS_TZ", "America/Caracas")
MIN_RENT_DAYS = int(os.environ.get("MIN_RENT_DAYS", "1"))
MAX_RENT_DAYS = int(os.environ.get("MAX_RENT_DAYS", "90"))
# Habilitar idempotencia (requiere tabla action_dedup)
ENABLE_IDEMPOTENCY = os.environ.get("ENABLE_IDEMPOTENCY", "true").lower() == "true"

BASE = f"{SUPABASE_URL}/rest/v1"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}
HEADERS_RETURN = {**HEADERS, "Prefer": "return=representation"}
HEADERS_UPSERT = {**HEADERS, "Prefer": "return=representation,resolution=merge-duplicates"}

# Estados permitidos (uniformiza y evita typos)
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"            # por si algún flujo futuro lo usa
STATUS_ACTIVE = "active"
STATUS_EXTENSION_PENDING = "extension_pending"
STATUS_CANCELLED = "cancelled"
STATUS_COMPLETED = "completed"

BLOCKING_STATUSES = (STATUS_PENDING, "requested", STATUS_APPROVED, STATUS_ACTIVE)

# =========================
# Utils
# =========================
def _today_business() -> date:
    """Devuelve 'hoy' según la zona horaria de negocio (no UTC)."""
    return datetime.now(ZoneInfo(BUSINESS_TZ)).date()

def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _norm_phone(s: Optional[str]) -> str:
    return re.sub(r"\D", "", s or "")

def _parse_iso_to_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()

def _valid_date_window(start_iso: str, end_iso: str) -> bool:
    """Inicio >= hoy (en tz de negocio) y fin > inicio."""
    try:
        s = date.fromisoformat(start_iso[:10])
        e = date.fromisoformat(end_iso[:10])
        return s >= _today_business() and e > s
    except Exception:
        return False

def _duration_days(start_iso: str, end_iso: str) -> int:
    s = _parse_iso_to_date(start_iso)
    e = _parse_iso_to_date(end_iso)
    return (e - s).days + 1

def _allowed_transition(old: str, new: str) -> bool:
    """Control básico de máquina de estados a nivel de cliente."""
    m = {
        STATUS_PENDING: {STATUS_ACTIVE, STATUS_CANCELLED, STATUS_EXTENSION_PENDING},  # (apertura o cancel)
        STATUS_ACTIVE: {STATUS_EXTENSION_PENDING, STATUS_COMPLETED, STATUS_CANCELLED},
        STATUS_EXTENSION_PENDING: {STATUS_ACTIVE, STATUS_CANCELLED},  # vuelve a active cuando aceptan
        STATUS_COMPLETED: set(),
        STATUS_CANCELLED: set(),
        STATUS_APPROVED: {STATUS_ACTIVE, STATUS_CANCELLED},
    }
    return new in m.get((old or "").lower(), set())

async def _log_event(rental_id: int, evt_type: str, actor_wa: Optional[str] = None, payload: Optional[Dict[str, Any]] = None):
    """Auditoría mínima para depurar y compliance."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            await c.post(
                f"{BASE}/rental_events",
                headers=HEADERS,
                json={
                    "rental_id": rental_id,
                    "type": evt_type,
                    "actor_wa": actor_wa,
                    "payload_json": payload or {},
                    "created_at": _now_utc_iso(),
                },
            )
    except Exception:
        # No interrumpir el flujo por fallos de logging
        pass

async def _idempotent_check_and_register(token: Optional[str]) -> bool:
    """Devuelve True si puede continuar (no existe token o idempotencia desactivada).
       Si está habilitada, registra el token y devuelve False si ya existía (idempotente)."""
    if not ENABLE_IDEMPOTENCY or not token:
        return True
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            # Intentar insertar; si existe -> 409
            r = await c.post(
                f"{BASE}/action_dedup",
                headers=HEADERS_RETURN,
                json={"token": token, "created_at": _now_utc_iso()},
            )
            if r.status_code == 409:
                return False
            r.raise_for_status()
            return True
    except Exception:
        # Ante error, preferimos continuar (mejor no romper la UX)
        return True

# =========================
# Disponibilidad
# =========================
async def get_overlapping_rentals(item_id: int | str, start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
    """Rentas bloqueantes que se solapan con [start,end] (inclusive)."""
    start_d = start_iso[:10]
    end_d = end_iso[:10]
    async with httpx.AsyncClient(timeout=15.0) as c:
        params = {
            "item_id": f"eq.{item_id}",
            "status": f"in.({','.join(BLOCKING_STATUSES)})",
            "select": "id,start_date,end_date,status",
            "and": f"(start_date.lte.{end_d},end_date.gte.{start_d})",
            "order": "start_date.asc",
        }
        r = await c.get(f"{BASE}/rentals", headers=HEADERS, params=params)
        r.raise_for_status()
        return r.json()

async def is_item_available(item_id: int | str, start_iso: str, end_iso: str) -> bool:
    return len(await get_overlapping_rentals(item_id, start_iso, end_iso)) == 0

# Helpers de colisión (opcionales para usar en handlers)
def end_of_first_overlap(bookings: List[Tuple[date, date]], s: date, e: date) -> date:
    """Devuelve el fin de la primera reserva que solape con [s,e]."""
    for bs, be in bookings:
        if not (e < bs or s > be):  # hay solape
            return be
    cands = [be for _, be in bookings if be >= s]
    return max(cands) if cands else s

def days_left_until(d: date) -> int:
    """Días restantes hasta 'd' (incluyendo hoy) en tz de negocio."""
    return (d - _today_business()).days + 1

# =========================
# Calendario / sugerencias
# =========================
async def get_future_bookings(item_id: int | str, from_iso: Optional[str] = None) -> List[Tuple[date, date]]:
    """Reservas futuras (bloqueantes) como pares (start,end)."""
    if not from_iso:
        from_iso = _today_business().isoformat()

    async with httpx.AsyncClient(timeout=15.0) as c:
        params = {
            "item_id": f"eq.{item_id}",
            "status": f"in.({','.join(BLOCKING_STATUSES)})",
            "select": "start_date,end_date",
            "end_date": f"gte.{from_iso[:10]}",
            "order": "start_date.asc",
        }
        r = await c.get(f"{BASE}/rentals", headers=HEADERS, params=params)
        r.raise_for_status()
        rows = r.json()

    bookings: List[Tuple[date, date]] = []
    for rnt in rows:
        try:
            s = _parse_iso_to_date(rnt["start_date"])
            e = _parse_iso_to_date(rnt["end_date"])
            if s > e:
                continue
            bookings.append((s, e))
        except Exception:
            continue
    bookings.sort(key=lambda t: t[0])
    return bookings

def suggest_windows(
    bookings: List[Tuple[date, date]],
    requested_days: int,
    from_day: Optional[date] = None,
    horizon_days: int = 120,
    max_suggestions: int = 3,
) -> List[Tuple[date, date]]:
    """Huecos libres (>= requested_days) desde from_day (o hoy) hasta horizon."""
    if requested_days <= 0:
        requested_days = 1
    today = _today_business()
    cur = max(from_day or today, today)
    end_horizon = cur + timedelta(days=horizon_days)

    # Merge de solapes o adyacencias
    merged: List[Tuple[date, date]] = []
    for s, e in sorted(bookings, key=lambda t: t[0]):
        if e < cur:
            continue
        if not merged:
            merged.append((s, e))
        else:
            ps, pe = merged[-1]
            if s <= pe + timedelta(days=1):
                merged[-1] = (ps, max(pe, e))
            else:
                merged.append((s, e))

    suggestions: List[Tuple[date, date]] = []

    if not merged:
        end = min(end_horizon, cur + timedelta(days=requested_days - 1))
        suggestions.append((cur, end))
        return suggestions[:max_suggestions]

    first_s, _ = merged[0]
    if cur < first_s:
        gap_len = (first_s - cur).days
        if gap_len >= requested_days:
            suggestions.append((cur, cur + timedelta(days=requested_days - 1)))
            if len(suggestions) >= max_suggestions:
                return suggestions

    for (s1, e1), (s2, _e2) in zip(merged, merged[1:]):
        start_gap = e1 + timedelta(days=1)
        end_gap = min(s2 - timedelta(days=1), end_horizon)
        if start_gap <= end_gap:
            gap_len = (end_gap - start_gap).days + 1
            if gap_len >= requested_days:
                suggestions.append((start_gap, start_gap + timedelta(days=requested_days - 1)))
                if len(suggestions) >= max_suggestions:
                    return suggestions

    last_e = merged[-1][1]
    start_gap = max(last_e + timedelta(days=1), cur)
    if start_gap <= end_horizon and len(suggestions) < max_suggestions:
        suggestions.append((start_gap, start_gap + timedelta(days=requested_days - 1)))

    return suggestions[:max_suggestions]

async def suggest_availability_for_request(
    item_id: int | str,
    start_iso: str,
    end_iso: str,
    max_suggestions: int = 3,
) -> List[Tuple[str, str]]:
    """Sugerencias alternativas (mismo tamaño) si no hay disponibilidad."""
    try:
        s = _parse_iso_to_date(start_iso)
        e = _parse_iso_to_date(end_iso)
    except Exception:
        return []
    requested_days = (e - s).days + 1
    bookings = await get_future_bookings(item_id, from_iso=s.isoformat())
    sugg = suggest_windows(bookings, requested_days, from_day=s, max_suggestions=max_suggestions)
    return [(a.isoformat(), b.isoformat()) for a, b in sugg]

# =========================
# Users
# =========================
async def ensure_user(msisdn: str, name: Optional[str] = None):
    async with httpx.AsyncClient(timeout=15.0) as client:
        r_get = await client.get(
            f"{BASE}/users",
            headers=HEADERS,
            params={"select": "wa_id,name", "wa_id": f"eq.{msisdn}", "limit": 1},
        )
        r_get.raise_for_status()
        existing = r_get.json()

        if existing:
            user = existing[0]
            if name and (not user.get("name") or user.get("name") == msisdn):
                await client.patch(
                    f"{BASE}/users",
                    headers=HEADERS,
                    params={"wa_id": f"eq.{msisdn}"},
                    json={"name": name},
                )
            return

        await client.post(
            f"{BASE}/users",
            headers=HEADERS_RETURN,
            json={"wa_id": msisdn, "name": name or msisdn},
        )

async def get_user_name(msisdn: str) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{BASE}/users",
            headers=HEADERS,
            params={"select": "name", "wa_id": f"eq.{msisdn}", "limit": 1},
        )
        r.raise_for_status()
        rows = r.json()
        return rows[0]["name"] if rows and rows[0].get("name") else msisdn

# =========================
# Sessions
# =========================
async def set_session(msisdn: str, step: str, draft: dict | None = None):
    payload = {
        "wa_id": msisdn,
        "step": step,
        "draft": draft or {},
        "updated_at": _now_utc_iso(),
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            f"{BASE}/sessions",
            headers=HEADERS_UPSERT,
            params={"on_conflict": "wa_id"},
            json=payload,
        )
        r.raise_for_status()

async def get_session(msisdn: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{BASE}/sessions",
            headers=HEADERS,
            params={"select": "*", "wa_id": f"eq.{msisdn}", "limit": 1},
        )
        r.raise_for_status()
        rows = r.json()
        if rows:
            return {"step": rows[0].get("step", "idle"), "draft": rows[0].get("draft", {})}
        return {"step": "idle", "draft": {}}

# =========================
# Listings
# =========================
async def insert_listing(owner_msisdn: str, title: str, price_text: str, zone: str, payment_methods: List[str]) -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        payload = {
            "owner_wa": owner_msisdn,
            "title": title,
            "price": price_text,
            "zone": zone,
            "payment_methods": payment_methods or [],
            "status": "active",
        }
        r = await client.post(f"{BASE}/listings", headers=HEADERS_RETURN, json=payload)
        r.raise_for_status()
        return str(r.json()[0]["id"])

async def get_listing(item_id: str) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{BASE}/listings",
            headers=HEADERS,
            params={"select": "*", "id": f"eq.{item_id}", "limit": 1},
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        listing = rows[0]
        try:
            listing["owner_name"] = await get_user_name(listing["owner_wa"])
        except Exception:
            listing["owner_name"] = listing.get("owner_wa")
        return listing

async def update_listing_status(item_id: str, owner_wa: str, new_status: str) -> bool:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.patch(
            f"{BASE}/listings",
            headers=HEADERS_RETURN,
            params={"id": f"eq.{item_id}", "owner_wa": f"eq.{owner_wa}"},
            json={"status": new_status},
        )
        return bool(r.json())

async def get_listings_for_user(owner_wa: str) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10.0) as c:
        params = {
            "owner_wa": f"eq.{owner_wa}",
            "select": "id,title,status,price",
            "order": "created_at.desc",
        }
        r = await c.get(f"{BASE}/listings", headers=HEADERS, params=params)
        r.raise_for_status()
        return r.json()

# ========= NUEVO: Gestión segura de publicaciones =========

async def _get_current_item_booking(item_id: str) -> Optional[Dict[str, Any]]:
    """
    Devuelve la reserva que mantiene bloqueado el artículo HOY (si existe).
    """
    today = _today_business().isoformat()
    async with httpx.AsyncClient(timeout=10.0) as c:
        params = {
            "item_id": f"eq.{item_id}",
            "status": f"in.({','.join(BLOCKING_STATUSES)})",
            "select": "id,start_date,end_date,status",
            "and": f"(start_date.lte.{today},end_date.gte.{today})",
            "order": "end_date.asc",
            "limit": 1,
        }
        r = await c.get(f"{BASE}/rentals", headers=HEADERS, params=params)
        r.raise_for_status()
        rows = r.json() or []
        return rows[0] if rows else None

async def is_item_in_use_now(item_id: str) -> Tuple[bool, Optional[str], Optional[int]]:
    """
    ¿Está el artículo alquilado HOY?
    Retorna (en_uso, end_date_iso, dias_restantes)
    """
    row = await _get_current_item_booking(item_id)
    if not row:
        return False, None, None
    end_iso = (row.get("end_date") or "")[:10]
    try:
        end_d = _parse_iso_to_date(end_iso)
        return True, end_iso, max(1, days_left_until(end_d))
    except Exception:
        return True, end_iso, None

async def can_manage_listing(item_id: str) -> bool:
    """True si NO está alquilado hoy."""
    in_use, _, _ = await is_item_in_use_now(item_id)
    return not in_use

async def update_listing_fields(item_id: str, owner_wa: str, **fields) -> Dict[str, Any]:
    """
    Actualiza campos de la publicación si NO está alquilada HOY.
    Campos permitidos (si existen en tu tabla): title, price, description, zone, payment_methods, status.
    """
    if not await can_manage_listing(item_id):
        in_use, until_iso, days_left = await is_item_in_use_now(item_id)
        dias = "día" if (days_left or 0) == 1 else "días"
        return {"ok": False, "error": "IN_USE", "until": until_iso, "days_left": days_left, "message": f"Artículo alquilado hasta {until_iso} (faltan {days_left} {dias})."}

    allowed = {"title", "price", "description", "zone", "payment_methods", "status"}
    payload = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not payload:
        return {"ok": False, "error": "NO_FIELDS"}

    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.patch(
            f"{BASE}/listings",
            headers=HEADERS_RETURN,
            params={"id": f"eq.{item_id}", "owner_wa": f"eq.{owner_wa}"},
            json=payload,
        )
        if r.status_code >= 400:
            return {"ok": False, "error": "DB_ERROR", "status": r.status_code, "text": r.text}
        rows = r.json() or []
        return {"ok": bool(rows), "row": rows[0] if rows else None}

async def delete_listing(item_id: str, owner_wa: str, *, hard: bool = False) -> Dict[str, Any]:
    """
    Elimina una publicación. Si 'hard' es False, la marca como inactive.
    Bloquea si está alquilada HOY.
    """
    if not await can_manage_listing(item_id):
        in_use, until_iso, days_left = await is_item_in_use_now(item_id)
        dias = "día" if (days_left or 0) == 1 else "días"
        return {"ok": False, "error": "IN_USE", "until": until_iso, "days_left": days_left, "message": f"Artículo alquilado hasta {until_iso} (faltan {days_left} {dias})."}

    if not hard:
        ok = await update_listing_status(item_id, owner_wa, "inactive")
        return {"ok": bool(ok), "soft": True}

    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.delete(
            f"{BASE}/listings",
            headers=HEADERS,
            params={"id": f"eq.{item_id}", "owner_wa": f"eq.{owner_wa}"},
        )
        if r.status_code in (200, 204):
            return {"ok": True, "hard": True}
        # A veces por FK no deja borrar: devolvemos instrucción para soft delete
        return {"ok": False, "error": "DB_DELETE_FAILED", "status": r.status_code, "text": r.text}

# =========================
# Consents (SIEMPRE NUEVOS)
# =========================

async def create_consent(item_id: str, buyer_msisdn: str, seller_msisdn: str) -> Dict[str, Any]:
    """
    Crea SIEMPRE una nueva fila para cada solicitud.
    Importante: usamos los WA tal cual (sin normalizar) para respetar FK/formatos existentes.
    """
    payload = {
        "item_id": int(item_id),
        "buyer_wa": buyer_msisdn,
        "seller_wa": seller_msisdn,
        "buyer_ok": False,
        "seller_ok": False,
        "introduced_at": None,
    }
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(f"{BASE}/consents", headers=HEADERS_RETURN, json=payload)
        r.raise_for_status()
        rows = r.json() or []
        return rows[0] if rows else {}

# NUEVO: consentimiento con guardas de disponibilidad (para evitar flujos inválidos)
async def create_consent_guarded(item_id: str, buyer_msisdn: str, seller_msisdn: str, start_iso: str, end_iso: str) -> Dict[str, Any]:
    """
    Igual a create_consent, pero:
      - Verifica que el listing esté activo
      - Verifica ventana de fechas válida
      - Verifica disponibilidad exacta [start,end]
      - Si está ocupado, devuelve {"ok": False, "error": "ITEM_BUSY", "until": <YYYY-MM-DD>, "days_left": <int>}
    """
    listing = await get_listing(str(item_id))
    if not listing:
        return {"ok": False, "error": "LISTING_NOT_FOUND"}
    if listing.get("status") != "active":
        return {"ok": False, "error": "LISTING_INACTIVE"}

    if not _valid_date_window(start_iso, end_iso):
        return {"ok": False, "error": "INVALID_DATES"}

    if not await is_item_available(item_id, start_iso, end_iso):
        # calcular fin del primer solape y días restantes
        bookings = await get_future_bookings(item_id, from_iso=start_iso)
        try:
            s = _parse_iso_to_date(start_iso)
            e = _parse_iso_to_date(end_iso)
            be = end_of_first_overlap(bookings, s, e)
            return {"ok": False, "error": "ITEM_BUSY", "until": be.isoformat(), "days_left": days_left_until(be)}
        except Exception:
            return {"ok": False, "error": "ITEM_BUSY"}

    row = await create_consent(str(item_id), buyer_msisdn, seller_msisdn)
    return {"ok": True, "row": row}

# (Se deja este helper por si lo necesitas más adelante; no interfiere)
async def _get_latest_consent_for_triplet(item_id: str, buyer_msisdn: str, seller_msisdn: str) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as c:
        params = {
            "select": "*",
            "item_id": f"eq.{int(item_id)}",
            "buyer_wa": f"eq.{buyer_msisdn}",
            "seller_wa": f"eq.{seller_msisdn}",
            "order": "id.desc",
            "limit": 1,
        }
        r = await c.get(f"{BASE}/consents", headers=HEADERS, params=params)
        r.raise_for_status()
        rows = r.json() or []
        return rows[0] if rows else None

# Compat: algunos handlers llaman upsert_consent. Lo dejamos como wrapper.
async def upsert_consent(item_id: str, buyer_msisdn: str, seller_msisdn: str):
    """
    Compatibilidad: devuelve {"row": {...}} creando SIEMPRE un consent nuevo.
    (Asegúrate de haber eliminado el índice único para evitar 409.)
    """
    row = await create_consent(item_id, buyer_msisdn, seller_msisdn)
    return {"row": row}

async def set_consent_flag_by_id(consent_id: str, msisdn: str, ok: bool) -> Optional[Dict[str, Any]]:
    """
    Marca buyer_ok / seller_ok. Coincide por igualdad exacta o por versión normalizada
    solo para comparar (no para escribir).
    """
    actor = msisdn
    actor_norm = _norm_phone(msisdn)

    async with httpx.AsyncClient(timeout=20.0) as client:
        g = await client.get(
            f"{BASE}/consents",
            headers=HEADERS,
            params={"select": "id,buyer_wa,seller_wa,buyer_ok,seller_ok,introduced_at", "id": f"eq.{consent_id}", "limit": 1},
        )
        g.raise_for_status()
        rows = g.json()
        if not rows:
            return None

        row = rows[0]
        buyer_raw = row.get("buyer_wa") or ""
        seller_raw = row.get("seller_wa") or ""
        buyer_norm = _norm_phone(buyer_raw)
        seller_norm = _norm_phone(seller_raw)

        if actor == buyer_raw or actor_norm == buyer_norm:
            field = "buyer_ok"
        elif actor == seller_raw or actor_norm == seller_norm:
            field = "seller_ok"
        else:
            return row  # no corresponde a ninguna de las partes

        upd = await client.patch(
            f"{BASE}/consents",
            headers=HEADERS_RETURN,
            params={"id": f"eq.{row['id']}", "select": "*"},
            json={field: bool(ok)},
        )
        upd.raise_for_status()
        res = upd.json() or []
        return res[0] if res else None

async def get_consent_by_id(consent_id: str) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{BASE}/consents",
            headers=HEADERS,
            params={"select": "*", "id": f"eq.{consent_id}", "limit": 1},
        )
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None

async def mark_introduced_once_by_consent(consent_id: str) -> bool:
    """Marca introduced_at solo si estaba NULL para este consent_id (presentación 1 vez)."""
    ts = _now_utc_iso()
    async with httpx.AsyncClient(timeout=20.0) as client:
        upd = await client.patch(
            f"{BASE}/consents",
            headers=HEADERS_RETURN,
            params={"id": f"eq.{consent_id}", "introduced_at": "is.null", "select": "id,introduced_at"},
            json={"introduced_at": ts},
        )
        upd.raise_for_status()
        rows = upd.json() or []
        return len(rows) > 0

# Compat antiguos (se conservan; no toco su lógica)
async def get_consent(item_id_or_consent_id: str) -> Optional[Dict[str, Any]]:
    row = await get_consent_by_id(item_id_or_consent_id)
    if row:
        return row
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{BASE}/consents",
            headers=HEADERS,
            params={
                "select": "*",
                "item_id": f"eq.{item_id_or_consent_id}",
                "order": "id.desc",
                "limit": 1,
            },
        )
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None

async def set_consent_flag(item_id_or_consent_id: str, msisdn: str, ok: bool) -> Optional[Dict[str, Any]]:
    row = await set_consent_flag_by_id(item_id_or_consent_id, msisdn, ok)
    if row:
        return row
    actor = _norm_phone(msisdn)
    async with httpx.AsyncClient(timeout=20.0) as client:
        g = await client.get(
            f"{BASE}/consents",
            headers=HEADERS,
            params={
                "select": "id,buyer_wa,seller_wa,buyer_ok,seller_ok,updated_at",
                "item_id": f"eq.{item_id_or_consent_id}",
                "order": "id.desc",
                "limit": 1,
            },
        )
        g.raise_for_status()
        rows = g.json()
        if not rows:
            return None
        row = rows[0]
        buyer_n = _norm_phone(row.get("buyer_wa"))
        seller_n = _norm_phone(row.get("seller_wa"))
        if actor == buyer_n:
            field = "buyer_ok"
        elif actor == seller_n:
            field = "seller_ok"
        else:
            return row
        upd = await client.patch(
            f"{BASE}/consents",
            headers=HEADERS_RETURN,
            params={"id": f"eq.{row['id']}", "select": "*"},
            json={field: bool(ok), "updated_at": _now_utc_iso()},
        )
        upd.raise_for_status()
        return upd.json()[0]

async def mark_introduced_once(item_id_or_consent_id: str) -> bool:
    ok_by_id = await mark_introduced_once_by_consent(item_id_or_consent_id)
    if ok_by_id:
        return True
    ts = _now_utc_iso()
    async with httpx.AsyncClient(timeout=20.0) as client:
        upd = await client.patch(
            f"{BASE}/consents",
            headers=HEADERS_RETURN,
            params={
                "item_id": f"eq.{item_id_or_consent_id}",
                "introduced_at": "is.null",
                "order": "id.desc",
                "limit": 1,
                "select": "id,introduced_at",
            },
            json={"introduced_at": ts},
        )
        upd.raise_for_status()
        rows = upd.json() or []
        return len(rows) > 0

# =========================
# Gestión post-renta (reviews & issues) - NUEVO
# =========================

async def get_rental_by_id(rental_id: int) -> Optional[Dict[str, Any]]:
    """Carga mínima para validaciones de gestión post-renta."""
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(
            f"{BASE}/rentals",
            headers=HEADERS,
            params={"id": f"eq.{rental_id}", "select": "id,status,buyer_wa,seller_wa,start_date,end_date,tz,completed_at", "limit": 1},
        )
        r.raise_for_status()
        rows = r.json() or []
        return rows[0] if rows else None

async def get_user_role_in_rental(rental_id: int, wa: str) -> Optional[str]:
    """Retorna 'buyer' | 'seller' | None."""
    rental = await get_rental_by_id(rental_id)
    if not rental:
        return None
    if _norm_phone(wa) == _norm_phone(rental.get("buyer_wa")):
        return "buyer"
    if _norm_phone(wa) == _norm_phone(rental.get("seller_wa")):
        return "seller"
    return None

async def has_user_reviewed(rental_id: int, wa: str) -> bool:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(
            f"{BASE}/reviews",
            headers=HEADERS,
            params={"rental_id": f"eq.{rental_id}", "reviewer_wa": f"eq.{_norm_phone(wa)}", "select": "id", "limit": 1},
        )
        r.raise_for_status()
        return bool(r.json())

async def has_user_reported_issue(rental_id: int, wa: str) -> bool:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(
            f"{BASE}/rental_issues",
            headers=HEADERS,
            params={"rental_id": f"eq.{rental_id}", "reporter_wa": f"eq.{_norm_phone(wa)}", "select": "id", "limit": 1},
        )
        r.raise_for_status()
        return bool(r.json())

async def insert_review(rental_id: int, reviewer_wa: str, rating: int, comment: Optional[str] = None) -> Dict[str, Any]:
    """
    Inserta reseña mínima. Los triggers en DB validan:
    - rental status 'completed'
    - pertenencia (buyer/seller)
    - exclusión con issues
    - autocompletar reviewed_wa
    """
    payload = {
        "rental_id": rental_id,
        "reviewer_wa": _norm_phone(reviewer_wa),
        "rating": int(rating),
    }
    if comment:
        payload["comment"] = comment
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(
            f"{BASE}/reviews",
            headers=HEADERS_RETURN,
            json=payload,
            params={"select": "id,rental_id,reviewer_wa,reviewed_wa,rating,comment,created_at"},
        )
        r.raise_for_status()
        rows = r.json() or []
        return rows[0] if rows else {}

async def insert_issue(rental_id: int, reporter_wa: str, issue_type: str, notes: Optional[str] = None) -> Dict[str, Any]:
    """
    Inserta issue. Triggers validan rol/tipo y exclusión con reseñas.
    issue_type:
      - seller: 'no_entregado' | 'entregado_con_danos'
      - buyer : 'problema_general'
    """
    payload = {
        "rental_id": rental_id,
        "reporter_wa": _norm_phone(reporter_wa),
        "issue_type": issue_type,
    }
    if notes:
        payload["notes"] = notes
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(
            f"{BASE}/rental_issues",
            headers=HEADERS_RETURN,
            json=payload,
            params={"select": "id,rental_id,reporter_wa,issue_type,notes,created_at"},
        )
        r.raise_for_status()
        rows = r.json() or []
        return rows[0] if rows else {}

async def get_pending_feedback(wa: str) -> List[Dict[str, Any]]:
    """Usa la vista v_pending_feedback para listar rentals completados aún no gestionados por el usuario."""
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(
            f"{BASE}/v_pending_feedback",
            headers=HEADERS,
            params={"user_wa": f"eq.{_norm_phone(wa)}", "select": "*"},
        )
        r.raise_for_status()
        return r.json() or []

# =========================
# (Legacy) creación directa sin triggers
# =========================
async def create_review(rental_id: int, reviewer_wa: str, reviewed_wa: str, rating: int, comment: str | None):
    payload = {
        "rental_id": rental_id,
        "reviewer_wa": reviewer_wa,
        "reviewed_wa": reviewed_wa,
        "rating": rating,
        "comment": comment or None,
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{BASE}/reviews", headers=HEADERS, json=payload)
        r.raise_for_status()
        return r.json()

async def create_issue(rental_id: int, reporter_wa: str, issue_type: str, notes: str | None):
    payload = {
        "rental_id": rental_id,
        "reporter_wa": reporter_wa,
        "issue_type": issue_type,
        "notes": notes or None,
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{BASE}/rental_issues", headers=HEADERS, json=payload)
        r.raise_for_status()
        return r.json()

# =========================
# Rentals (endurecidos)
# =========================
async def create_rental_request(
    listing_id: int,
    renter_msisdn: str,
    start_iso: str,
    end_iso: str,
    payment_method: str,
    *,
    action_token: Optional[str] = None,
    tz: Optional[str] = None,
) -> Dict[str, Any]:
    """Crea la renta en estado PENDING con chequeos de negocio e idempotencia opcional."""
    if not await _idempotent_check_and_register(action_token):
        return {"ok": True, "idempotent": True}

    listing = await get_listing(str(listing_id))
    if not listing:
        return {"ok": False, "error": "LISTING_NOT_FOUND"}
    if listing.get("status") != "active":
        return {"ok": False, "error": "LISTING_INACTIVE"}

    if not _valid_date_window(start_iso, end_iso):
        return {"ok": False, "error": "INVALID_DATES"}

    dur = _duration_days(start_iso, end_iso)
    if dur < MIN_RENT_DAYS or dur > MAX_RENT_DAYS:
        return {"ok": False, "error": "INVALID_DURATION", "min": MIN_RENT_DAYS, "max": MAX_RENT_DAYS}

    # Anti-overbooking (backend)
    if not await is_item_available(listing_id, start_iso, end_iso):
        return {"ok": False, "error": "DATES_NOT_AVAILABLE"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        payload = {
            "item_id": listing_id,
            "buyer_wa": _norm_phone(renter_msisdn),
            "seller_wa": _norm_phone(listing["owner_wa"]),
            "start_date": start_iso[:10],
            "end_date": end_iso[:10],
            "status": STATUS_PENDING,
            "selected_payment_method": payment_method,
            "version": 1,
            "tz": tz or BUSINESS_TZ,
            "created_at": _now_utc_iso(),
            "updated_at": _now_utc_iso(),
        }
        r = await client.post(f"{BASE}/rentals", headers=HEADERS_RETURN, json=payload)
        if r.status_code >= 400:
            print("create_rental_request ERROR:", r.status_code, r.text)
            return {"ok": False, "error": "DB_ERROR"}
        row = r.json()[0]
        await _log_event(row["id"], "created", row["buyer_wa"], {"start": payload["start_date"], "end": payload["end_date"]})
        return {"ok": True, "row": row}

async def _load_rental_full(c: httpx.AsyncClient, rental_id: int) -> Optional[Dict[str, Any]]:
    r = await c.get(
        f"{BASE}/rentals",
        headers=HEADERS,
        params={"id": f"eq.{rental_id}", "select": "*", "limit": 1},
    )
    rows = r.json()
    return rows[0] if rows else None

async def confirm_rental_start(rental_id: int, actor_wa: str, *, action_token: Optional[str] = None) -> Dict[str, Any]:
    """
    Marca la confirmación del actor. Si ambas partes confirmaron y hoy ∈ [start,end], ACTIVA la renta
    usando concurrencia optimista con version.
    """
    if not await _idempotent_check_and_register(action_token):
        return {"status": "IDEMPOTENT_OK"}

    async with httpx.AsyncClient(timeout=20.0) as c:
        rental = await _load_rental_full(c, rental_id)
        if not rental:
            return {"status": "NOT_FOUND"}

        status = (rental.get("status") or "").lower()
        if status not in (STATUS_PENDING, STATUS_APPROVED):
            return {"status": "INVALID"}

        actor_n = _norm_phone(actor_wa)
        buyer_n = _norm_phone(rental["buyer_wa"])
        seller_n = _norm_phone(rental["seller_wa"])

        if actor_n == buyer_n:
            this_field = "buyer_confirm_start"
            other_field = "seller_confirm_start"
            other_party = rental["seller_wa"]
        elif actor_n == seller_n:
            this_field = "seller_confirm_start"
            other_field = "buyer_confirm_start"
            other_party = rental["buyer_wa"]
        else:
            return {"status": "INVALID", "reason": "NOT_AUTHORIZED"}

        # set my confirmation
        await c.patch(
            f"{BASE}/rentals",
            headers=HEADERS,
            params={"id": f"eq.{rental_id}"},
            json={this_field: True, "updated_at": _now_utc_iso()},
        )
        await _log_event(rental_id, "confirm_start", actor_wa, {"field": this_field})

        # reload to check both confirmations
        rental = await _load_rental_full(c, rental_id)
        if rental.get(other_field) is True:
            today = _today_business()
            s = _parse_iso_to_date(rental["start_date"])
            e = _parse_iso_to_date(rental["end_date"])
            if not (s <= today <= e):
                return {"status": "INVALID", "reason": "OUT_OF_WINDOW"}

            # Optimistic concurrency: require current version
            expected_version = rental.get("version") or 1
            if not _allowed_transition(rental["status"], STATUS_ACTIVE):
                return {"status": "INVALID", "reason": "BAD_TRANSITION"}

            r_upd = await c.patch(
                f"{BASE}/rentals",
                headers=HEADERS_RETURN,
                params={"id": f"eq.{rental_id}", "version": f"eq.{expected_version}"},
                json={"status": STATUS_ACTIVE, "started_at": _now_utc_iso(), "version": expected_version + 1, "updated_at": _now_utc_iso()},
            )
            rows = r_upd.json() if r_upd.content else []
            if not rows:
                rental = await _load_rental_full(c, rental_id)
                if (rental or {}).get("status") == STATUS_ACTIVE:
                    return {"status": "ACTIVATED", "parties": [rental["buyer_wa"], rental["seller_wa"]]}
                return {"status": "INVALID", "reason": "STALE_VERSION"}
            await _log_event(rental_id, "activated", actor_wa)
            return {"status": "ACTIVATED", "parties": [rental["buyer_wa"], rental["seller_wa"]]}

        return {"status": "WAITING_OTHER", "other_party": other_party}

async def get_active_rentals_for_item(item_id: str) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10.0) as c:
        params = {"item_id": f"eq.{item_id}", "status": f"in.({','.join(BLOCKING_STATUSES)})"}
        r = await c.get(f"{BASE}/rentals", headers=HEADERS, params=params)
        r.raise_for_status()
        return r.json()

async def update_rental_status(rental_id: int, new_status: str) -> bool:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.patch(
            f"{BASE}/rentals",
            headers=HEADERS_RETURN,
            params={"id": f"eq.{rental_id}"},
            json={"status": new_status, "updated_at": _now_utc_iso()},
        )
        return bool(r.json())

async def request_rental_cancellation(rental_id: int, requester_wa: str, *, action_token: Optional[str] = None) -> Dict[str, Any]:
    if not await _idempotent_check_and_register(action_token):
        return {"status": "IDEMPOTENT_OK"}

    async with httpx.AsyncClient(timeout=15.0) as c:
        rental = await _load_rental_full(c, rental_id)
        if not rental:
            return {"status": "NOT_FOUND"}

        if rental.get("status") in (STATUS_CANCELLED, STATUS_COMPLETED):
            return {"status": "INVALID"}

        is_buyer = _norm_phone(requester_wa) == _norm_phone(rental["buyer_wa"])
        flag_field = "buyer_wants_cancel" if is_buyer else "seller_wants_cancel"
        other_party_wa = rental["seller_wa"] if is_buyer else rental["buyer_wa"]

        await c.patch(
            f"{BASE}/rentals",
            headers=HEADERS,
            params={"id": f"eq.{rental_id}"},
            json={flag_field: True, "updated_at": _now_utc_iso()},
        )
        await _log_event(rental_id, "request_cancel", requester_wa, {"flag": flag_field})

        rental2 = await _load_rental_full(c, rental_id)
        if rental2.get("buyer_wants_cancel") and rental2.get("seller_wants_cancel"):
            expected_version = rental2.get("version") or 1
            if not _allowed_transition(rental2["status"], STATUS_CANCELLED):
                return {"status": "INVALID", "reason": "BAD_TRANSITION"}
            r_upd = await c.patch(
                f"{BASE}/rentals",
                headers=HEADERS_RETURN,
                params={"id": f"eq.{rental_id}", "version": f"eq.{expected_version}"},
                json={"status": STATUS_CANCELLED, "version": expected_version + 1, "updated_at": _now_utc_iso()},
            )
            rows = r_upd.json() if r_upd.content else []
            if not rows:
                rental3 = await _load_rental_full(c, rental_id)
                if (rental3 or {}).get("status") == STATUS_CANCELLED:
                    return {"status": "CANCELLED", "parties": [rental2["buyer_wa"], rental2["seller_wa"]]}
                return {"status": "INVALID", "reason": "STALE_VERSION"}
            await _log_event(rental_id, "cancelled", requester_wa)
            return {"status": "CANCELLED", "parties": [rental2["buyer_wa"], rental2["seller_wa"]]}
        else:
            return {"status": "WAITING_OTHER", "other_party": other_party_wa}

async def request_rental_extension(
    rental_id: int,
    requester_wa: str,
    new_end_iso: str,
    *,
    action_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Extensión robusta:
      - status debe ser ACTIVE (o EXTENSION_PENDING para el mismo end propuesto).
      - new_end > end_date actual y > hoy.
      - Sin solapes en (end_actual+1 .. new_end).
      - Usa version para concurrencia.
    """
    # 🔧 Fix: nombre correcto de la función de idempotencia
    if not await _idempotent_check_and_register(action_token):
        return {"status": "IDEMPOTENT_OK"}

    try:
        new_end = _parse_iso_to_date(new_end_iso)
    except Exception:
        return {"status": "INVALID", "reason": "BAD_DATE"}

    if new_end <= _today_business():
        return {"status": "INVALID", "reason": "END_NOT_AFTER_TODAY"}

    async with httpx.AsyncClient(timeout=20.0) as c:
        rental = await _load_rental_full(c, rental_id)
        if not rental:
            return {"status": "NOT_FOUND"}

        status = (rental.get("status") or "").lower()
        if status not in (STATUS_ACTIVE, STATUS_EXTENSION_PENDING):
            return {"status": "INVALID", "reason": "NOT_ACTIVE"}

        old_end = _parse_iso_to_date(rental["end_date"])
        if new_end <= old_end:
            return {"status": "INVALID", "reason": "END_NOT_AFTER_CURRENT"}

        # verificar solapes solo del tramo extra
        item_id = rental["item_id"]
        extra_start = (old_end + timedelta(days=1)).isoformat()
        new_end_str = new_end.isoformat()

        # obtener reservas y descartar la propia
        overlaps = await get_overlapping_rentals(item_id, extra_start, new_end_str)
        overlaps = [o for o in overlaps if str(o.get("id")) != str(rental_id)]
        if overlaps:
            return {"status": "INVALID", "reason": "OVERLAP"}

        req_is_buyer = _norm_phone(requester_wa) == _norm_phone(rental["buyer_wa"])
        flag_field = "buyer_wants_extension" if req_is_buyer else "seller_wants_extension"
        other_flag = "seller_wants_extension" if req_is_buyer else "buyer_wants_extension"
        other_party = rental["seller_wa"] if req_is_buyer else rental["buyer_wa"]

        # registrar propuesta + mi intención
        await c.patch(
            f"{BASE}/rentals",
            headers=HEADERS,
            params={"id": f"eq.{rental_id}"},
            json={
                "proposed_end_date": new_end_str,
                flag_field: True,
                "status": STATUS_EXTENSION_PENDING,
                "updated_at": _now_utc_iso(),
            },
        )
        await _log_event(rental_id, "request_extension", requester_wa, {"proposed_end": new_end_str, "flag": flag_field})

        curr = await _load_rental_full(c, rental_id)
        # Si la otra parte ya estaba de acuerdo y la propuesta coincide, aplicar extensión con version check
        if curr.get(other_flag) and curr.get("proposed_end_date") == new_end_str:
            expected_version = curr.get("version") or 1
            # transición de extension_pending -> active con end_date actualizado
            if not _allowed_transition(curr["status"], STATUS_ACTIVE):
                return {"status": "INVALID", "reason": "BAD_TRANSITION"}

            r_upd = await c.patch(
                f"{BASE}/rentals",
                headers=HEADERS_RETURN,
                params={"id": f"eq.{rental_id}", "version": f"eq.{expected_version}"},
                json={
                    "end_date": new_end_str,
                    "proposed_end_date": None,
                    "buyer_wants_extension": False,
                    "seller_wants_extension": False,
                    "status": STATUS_ACTIVE,
                    "version": expected_version + 1,
                    "updated_at": _now_utc_iso(),
                },
            )
            rows = r_upd.json() if r_upd.content else []
            if not rows:
                latest = await _load_rental_full(c, rental_id)
                if (latest or {}).get("end_date") == new_end_str:
                    await _log_event(rental_id, "extended", requester_wa, {"new_end": new_end_str, "race": True})
                    return {"status": "EXTENDED", "parties": [curr["buyer_wa"], curr["seller_wa"]]}
                return {"status": "INVALID", "reason": "STALE_VERSION"}

            await _log_event(rental_id, "extended", requester_wa, {"new_end": new_end_str})
            return {"status": "EXTENDED", "parties": [curr["buyer_wa"], curr["seller_wa"]]}

        return {"status": "EXTENSION_PENDING", "other_party": other_party}

# =========================
# Queries de usuario
# =========================
async def get_rentals_for_user(wa_id: str, *, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15.0) as c:
        params = {
            "or": f"(buyer_wa.eq.{_norm_phone(wa_id)},seller_wa.eq.{_norm_phone(wa_id)})",
            "select": "*,listing:listings(title)",
            "order": "start_date.desc",
            "limit": str(limit),
            "offset": str(offset),
        }
        r = await c.get(f"{BASE}/rentals", headers=HEADERS, params=params)
        r.raise_for_status()
        return r.json()

async def get_rental(rental_id: int) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(
            f"{BASE}/rentals",
            headers=HEADERS,
            params={"id": f"eq.{rental_id}", "select": "*,listing:listings(title)", "limit": 1},
        )
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None

# =========================
# Extensión: rechazo explícito
# =========================
async def reject_rental_extension(rental_id: int, actor_wa: str, *, action_token: Optional[str] = None) -> Dict[str, Any]:
    """Rechaza una extensión en estado extension_pending y limpia flags."""
    if not await _idempotent_check_and_register(action_token):
        return {"status": "IDEMPOTENT_OK"}

    async with httpx.AsyncClient(timeout=15.0) as c:
        rental = await _load_rental_full(c, rental_id)
        if not rental:
            return {"status": "NOT_FOUND"}

        if rental.get("status") != STATUS_EXTENSION_PENDING:
            return {"status": "INVALID"}

        expected_version = rental.get("version") or 1
        r_upd = await c.patch(
            f"{BASE}/rentals",
            headers=HEADERS_RETURN,
            params={"id": f"eq.{rental_id}", "version": f"eq.{expected_version}"},
            json={
                "proposed_end_date": None,
                "buyer_wants_extension": False,
                "seller_wants_extension": False,
                "status": STATUS_ACTIVE,
                "version": expected_version + 1,
                "updated_at": _now_utc_iso(),
            },
        )
        rows = r_upd.json() if r_upd.content else []
        if not rows:
            latest = await _load_rental_full(c, rental_id)
            if (latest or {}).get("status") == STATUS_ACTIVE and (latest or {}).get("proposed_end_date") is None:
                await _log_event(rental_id, "extension_rejected", actor_wa, {"race": True})
                return {"status": "REJECTED", "parties": [rental["buyer_wa"], rental["seller_wa"]]}
            return {"status": "INVALID", "reason": "STALE_VERSION"}

        await _log_event(rental_id, "extension_rejected", actor_wa)
        return {"status": "REJECTED", "parties": [rental["buyer_wa"], rental["seller_wa"]]}

# =========================
# Reviews
# =========================
async def add_review(rental_id: int, reviewer_wa: str, rating: int, comment: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as c:
        r_get = await c.get(
            f"{BASE}/rentals",
            headers=HEADERS,
            params={"select": "buyer_wa,seller_wa,status", "id": f"eq.{rental_id}", "limit": 1},
        )
        rows = r_get.json()
        if not rows:
            return {"ok": False, "error": "RENTAL_NOT_FOUND"}

        rental = rows[0]
        reviewer_norm = _norm_phone(reviewer_wa)
        buyer_norm = _norm_phone(rental["buyer_wa"])
        seller_norm = _norm_phone(rental["seller_wa"])

        if reviewer_norm not in [buyer_norm, seller_norm]:
            return {"ok": False, "error": "NOT_PART_OF_RENTAL"}

        if rental.get("status") != STATUS_COMPLETED:
            return {"ok": False, "error": "RENTAL_NOT_COMPLETED"}

        try:
            r_int = int(rating)
        except Exception:
            return {"ok": False, "error": "INVALID_RATING"}
        if not (1 <= r_int <= 5):
            return {"ok": False, "error": "INVALID_RATING"}

        reviewed_wa = rental["seller_wa"] if reviewer_norm == buyer_norm else rental["buyer_wa"]

        payload = {
            "rental_id": rental_id,
            "reviewer_wa": reviewer_wa,
            "reviewed_wa": reviewed_wa,
            "rating": r_int,
            "comment": comment,
        }
        r_post = await c.post(f"{BASE}/reviews", headers=HEADERS_RETURN, json=payload)
        if r_post.status_code == 409:
            return {"ok": False, "error": "ALREADY_REVIEWED"}
        r_post.raise_for_status()

        # Recalcular reputación del usuario reseñado (si no usas trigger en DB)
        try:
            await recalc_reputation(reviewed_wa)
        except Exception:
            pass

        return {"ok": True, "data": r_post.json()}

async def get_reviews_for_user(wa_id: str) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10.0) as c:
        params = {
            "select": "rating,comment,created_at",
            "reviewed_wa": f"eq.{_norm_phone(wa_id)}",
            "order": "created_at.desc",
        }
        r = await c.get(f"{BASE}/reviews", headers=HEADERS, params=params)
        r.raise_for_status()
        return r.json()

# ============================================================
# Fin de alquileres: búsqueda, marcado y reputación (NUEVO)
# ============================================================
async def get_rentals_ended_since(since_iso: str) -> List[Dict[str, Any]]:
    """
    Devuelve rentas con end_date <= since_iso y que aún no tienen ended_notified_at.
    Incluye estados que razonablemente pueden llegar al final del periodo sin cerrarse.
    """
    allowed = [STATUS_ACTIVE, STATUS_PENDING, STATUS_APPROVED, STATUS_EXTENSION_PENDING]
    async with httpx.AsyncClient(timeout=15.0) as c:
        params = {
            "select": "id,item_id,buyer_wa,seller_wa,end_date,status,ended_notified_at",
            "end_date": f"lte.{since_iso[:10]}",
            "ended_notified_at": "is.null",
            "status": f"in.({','.join(allowed)})",
            "order": "end_date.asc",
        }
        r = await c.get(f"{BASE}/rentals", headers=HEADERS, params=params)
        r.raise_for_status()
        return r.json() or []

async def mark_rental_end_notified(rental_id: int, *, set_completed: bool = True) -> bool:
    """
    Marca una renta como notificada al finalizar su periodo.
    Opcionalmente, cambia el estado a 'completed' para permitir reseñas.
    """
    payload = {"ended_notified_at": _now_utc_iso(), "updated_at": _now_utc_iso()}
    if set_completed:
        payload["status"] = STATUS_COMPLETED

    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.patch(
            f"{BASE}/rentals",
            headers=HEADERS_RETURN,
            params={"id": f"eq.{rental_id}"},
            json=payload,
        )
        return bool(r.json())

async def recalc_reputation(user_wa: str) -> float:
    """
    Recalcula reputation = avg(rating) para users. Si no hay reseñas, 0.0.
    """
    norm = _norm_phone(user_wa)
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(
            f"{BASE}/reviews",
            headers=HEADERS,
            params={"select": "rating", "reviewed_wa": f"eq.{norm}"},
        )
        r.raise_for_status()
        ratings = [row.get("rating") for row in (r.json() or []) if row.get("rating") is not None]
        avg = round(sum(ratings) / len(ratings), 2) if ratings else 0.0
        await c.patch(
            f"{BASE}/users",
            headers=HEADERS_RETURN,
            params={"wa_id": f"eq.{norm}"},
            json={"reputation": avg},
        )
        return avg
