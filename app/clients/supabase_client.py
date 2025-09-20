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

# =========================
# Consents (NUEVO: por consent_id)
# =========================

async def create_consent(item_id: str, buyer_msisdn: str, seller_msisdn: str) -> Dict[str, Any]:
    """
    Crea SIEMPRE una nueva fila por solicitud.
    - Valida tipos y no vacíos.
    - Asegura existencia de users (buyer y seller) para evitar FK errors.
    - NO envía columnas inexistentes en tu tabla.
    """
    # Validaciones defensivas
    if item_id is None or str(item_id).strip() == "":
        raise ValueError("create_consent: item_id vacío")
    try:
        item_id_int = int(item_id)
    except Exception:
        raise ValueError(f"create_consent: item_id inválido: {item_id!r}")

    buyer_raw = (buyer_msisdn or "").strip()
    seller_raw = (seller_msisdn or "").strip()
    if not buyer_raw or not seller_raw:
        raise ValueError("create_consent: buyer_wa/seller_wa vacíos")
    if buyer_raw == seller_raw:
        raise ValueError("create_consent: buyer_wa y seller_wa no pueden ser iguales")

    # Asegurar existencia en users (FK)
    try:
        await ensure_user(buyer_raw)
    except Exception:
        pass
    try:
        await ensure_user(seller_raw)
    except Exception:
        pass

    payload = {
        "item_id": item_id_int,
        "buyer_wa": buyer_raw,    # SIN normalizar, para coincidir con FK/formatos existentes
        "seller_wa": seller_raw,
        "buyer_ok": False,
        "seller_ok": False,
        "introduced_at": None,
    }

    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(f"{BASE}/consents", headers=HEADERS_RETURN, json=payload)
        if r.status_code >= 400:
            # Log explícito para entender el 400 en runtime
            print("create_consent ERROR:", r.status_code, r.text, payload)
        r.raise_for_status()
        rows = r.json() or []
        return rows[0] if rows else {}

async def _get_latest_consent_for_triplet(item_id: str, buyer_msisdn: str, seller_msisdn: str) -> Optional[Dict[str, Any]]:
    """
    Último consent para (item_id, buyer, seller) usando los valores EXACTOS
    (sin normalizar) para que coincida con índices/FKs existentes.
    """
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

# -----------------------------------------------------------------
# COMPAT: función antigua que usa item_id (con fallback de 409)
# -----------------------------------------------------------------
async def upsert_consent(item_id: str, buyer_msisdn: str, seller_msisdn: str):
    """
    Crea un consent nuevo. Si hay 409 (índice único/FK), devuelve el último existente
    para (item_id, buyer, seller) usando los valores EXACTOS.
    Devuelve SIEMPRE {"row": {...}} o relanza el error si no hay fila.
    """
    try:
        row = await create_consent(item_id, buyer_msisdn, seller_msisdn)
        return {"row": row}
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 409:
            existing = await _get_latest_consent_for_triplet(item_id, buyer_msisdn, seller_msisdn)
            if existing:
                return {"row": existing}
        raise

async def set_consent_flag_by_id(consent_id: str, msisdn: str, ok: bool) -> Optional[Dict[str, Any]]:
    """
    Marca buyer_ok / seller_ok. Se permite match por igualdad exacta o
    por versión normalizada para tolerar formatos distintos.
    """
    actor = msisdn  # exacto; usamos normalización solo para comparar
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
            # no coincide: devolvemos lo que hay sin modificar
            return row

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


async def get_consent(item_id_or_consent_id: str) -> Optional[Dict[str, Any]]:
    """
    COMPAT: si recibe un número que existe como consent.id -> devuelve por id;
    si no, intenta devolver el último consent para ese item_id.
    """
    # Intentar por id exacto
    row = await get_consent_by_id(item_id_or_consent_id)
    if row:
        return row
    # Fallback: último por item_id
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
    """
    DEPRECATED COMPAT: si existe un consent con ese id exacto -> usa por id.
    Si no, toma el último consent del item_id dado y marca la bandera.
    """
    # por id
    row = await set_consent_flag_by_id(item_id_or_consent_id, msisdn, ok)
    if row:
        return row
    # por último del item
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
    """
    DEPRECATED COMPAT: si el id existe -> marca por id.
    Si no, marca introduced_at del consent más reciente del item_id.
    """
    # por id
    ok_by_id = await mark_introduced_once_by_consent(item_id_or_consent_id)
    if ok_by_id:
        return True
    # por último del item
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
            # Si no devolvió fila, versión cambió (race); reintentar lectura
            rows = r_upd.json() if r_upd.content else []
            if not rows:
                # estado cambió por carrera: leer y devolver
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
            # carrera
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
