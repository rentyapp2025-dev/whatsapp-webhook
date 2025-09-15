# supabase_client.py
import os
import httpx
import re
from datetime import datetime
from typing import Optional, Dict, Any

# === Config ===
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

BASE = f"{SUPABASE_URL}/rest/v1"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}
# Para que PostgREST devuelva la fila insertada/actualizada
HEADERS_RETURN = {**HEADERS, "Prefer": "return=representation"}
# Para upsert por clave única (por ej. sessions.wa_id o consents.item_id)
HEADERS_UPSERT = {
    **HEADERS,
    "Prefer": "return=representation,resolution=merge-duplicates",
}

# =========================================================
# Utilidades
# =========================================================

def _parse_price(text: str) -> float:
    """
    Extrae el primer número del texto y lo devuelve como float.
    Útil si decides guardar un 'price_num' adicional en el futuro.
    """
    if not text:
        return 0.0
    m = re.search(r"(\d+(?:[.,]\d+)?)", text)
    return float(m.group(1).replace(",", ".")) if m else 0.0

def _date_to_utc_iso(s: str) -> str:
    """Acepta 'YYYY-MM-DD' o ISO; devuelve ISO con 'Z' cuando aplica."""
    s = s.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return f"{s}T00:00:00Z"
    # Si ya viene con hora, asumimos que es ISO válido; si no trae tz, Postgres hará conversión según config
    return s

def _norm_phone(s: Optional[str]) -> str:
    """Normaliza teléfonos a solo dígitos para comparaciones robustas."""
    return re.sub(r"\D", "", s or "")

# =========================================================
# Ratings / Orders (sin cambios funcionales)
# =========================================================

async def insert_rating(phone: str, name: str, rating: str):
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{BASE}/ratings",
            headers=HEADERS_RETURN,
            json={"phone": phone, "name": name or "", "rating": rating},
            params={"select": "*"},
        )
        r.raise_for_status()

async def insert_order(order: dict):
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{BASE}/orders",
            headers=HEADERS_RETURN,
            json=order,
            params={"select": "*"},
        )
        r.raise_for_status()

async def get_rating_counts():
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{BASE}/ratings_counts",
            headers=HEADERS,
            params={"select": "*"},
        )
        r.raise_for_status()
        return r.json()[0]

# =========================================================
# Users
# =========================================================

async def ensure_user(msisdn: str) -> Dict[str, Any]:
    """
    Garantiza que exista un usuario con wa_id = msisdn.
    Si no existe, lo crea con name igual al msisdn.
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
        # Busca por wa_id
        r = await client.get(
            f"{BASE}/users",
            headers=HEADERS,
            params={"select": "*", "wa_id": f"eq.{msisdn}", "limit": 1},
        )
        r.raise_for_status()
        rows = r.json()
        if rows:
            return rows[0]
        # Crea
        r2 = await client.post(
            f"{BASE}/users",
            headers=HEADERS_RETURN,
            params={"select": "*"},
            json={"wa_id": msisdn, "name": msisdn},
        )
        r2.raise_for_status()
        return r2.json()[0]

async def get_user_name(msisdn: str) -> str:
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{BASE}/users",
            headers=HEADERS,
            params={"select": "name", "wa_id": f"eq.{msisdn}", "limit": 1},
        )
        r.raise_for_status()
        rows = r.json()
        if rows and rows[0].get("name"):
            return rows[0]["name"]
        return msisdn

# =========================================================
# Sessions (state machine)
# =========================================================

async def set_session(msisdn: str, step: str, draft: dict | None = None):
    payload = {
        "wa_id": msisdn,
        "step": step,
        "draft": draft or {},
        "updated_at": datetime.utcnow().isoformat(),
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        # Upsert por PK/unique wa_id (asegúrate de que wa_id sea PK o unique en sessions)
        r = await client.post(
            f"{BASE}/sessions",
            headers=HEADERS_UPSERT,
            params={"on_conflict": "wa_id", "select": "*"},
            json=payload,
        )
        r.raise_for_status()

async def get_session(msisdn: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{BASE}/sessions",
            headers=HEADERS,
            params={"select": "*", "wa_id": f"eq.{msisdn}", "limit": 1},
        )
        r.raise_for_status()
        rows = r.json()
        if rows:
            row = rows[0]
            return {"step": row.get("step", "idle"), "draft": row.get("draft", {})}
        return {"step": "idle", "draft": {}}

# =========================================================
# Listings (owner_wa + price TEXT)
# =========================================================

async def insert_listing(owner_msisdn: str, title: str, price_text: str, location: str) -> str:
    """
    Inserta una publicación.
    Esquema esperado en 'listings':
      - owner_wa TEXT
      - title TEXT
      - price TEXT (p.ej. "10 USD/día")
      - location TEXT
      - status TEXT
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{BASE}/listings",
            headers=HEADERS_RETURN,
            params={"select": "*"},
            json={
                "owner_wa": owner_msisdn,  # número E.164 sin '+'
                "title": title,
                "price": price_text,
                "location": location,
                "status": "active",
            },
        )
        if r.status_code >= 400:
            print("insert_listing ERROR:", r.status_code, r.text)
        r.raise_for_status()
        return str(r.json()[0]["id"])

async def get_listing(item_id: str) -> Optional[Dict[str, Any]]:
    """
    Devuelve la fila de listings y añade:
      - owner_name (consultado en users por owner_wa)
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
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

# =========================================================
# Consents (1 fila por item_id; requiere UNIQUE en consents.item_id)
# =========================================================

async def upsert_consent(item_id: str, buyer_msisdn: str, seller_msisdn: str):
    """
    Upsert por item_id (requiere UNIQUE en consents.item_id).
    Siempre guarda los teléfonos normalizados (solo dígitos).
    """
    buyer_n = _norm_phone(buyer_msisdn)
    seller_n = _norm_phone(seller_msisdn)
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{BASE}/consents",
            headers=HEADERS_UPSERT,
            params={"on_conflict": "item_id", "select": "*"},
            json={
                "item_id": int(item_id),
                "buyer_wa": buyer_n,
                "seller_wa": seller_n,
            },
        )
        r.raise_for_status()
        return r.json()[0]

async def get_consent(item_id: str) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{BASE}/consents",
            headers=HEADERS,
            params={
                "select": "*",
                "item_id": f"eq.{item_id}",
                "order": "id.desc",
                "limit": 1,
            },
        )
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None

async def set_consent_flag(item_id: str, msisdn: str, ok: bool) -> Optional[Dict[str, Any]]:
    """
    Marca buyer_ok o seller_ok en la fila más reciente de ese item_id.
    Determina el rol comparando teléfonos normalizados.
    """
    actor = _norm_phone(msisdn)

    async with httpx.AsyncClient(timeout=20.0) as client:
        # Leer la fila más reciente del item
        g = await client.get(
            f"{BASE}/consents",
            headers=HEADERS,
            params={
                "select": "id,buyer_wa,seller_wa,buyer_ok,seller_ok,updated_at",
                "item_id": f"eq.{item_id}",
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
            # No coincide con ninguno → no tocamos nada para no corromper el estado
            return row

        upd = await client.patch(
            f"{BASE}/consents",
            headers=HEADERS_RETURN,
            params={"id": f"eq.{row['id']}", "select": "*"},
            json={field: bool(ok), "updated_at": datetime.utcnow().isoformat()},
        )
        upd.raise_for_status()
        return upd.json()[0]

# Idempotencia: marcar que ya se presentaron los contactos para este item
async def mark_introduced_once(item_id: str) -> bool:
    """
    Devuelve True si marcó 'introduced_at' (aún no estaba marcada).
    Devuelve False si ya estaba marcada previamente.
    Requiere columna introduced_at timestamptz NULL en consents.
    """
    ts = datetime.utcnow().isoformat()
    async with httpx.AsyncClient(timeout=20.0) as client:
        # Solo actualizar si introduced_at IS NULL
        upd = await client.patch(
            f"{BASE}/consents",
            headers=HEADERS_RETURN,
            params={
                "item_id": f"eq.{item_id}",
                "introduced_at": "is.null",
                "select": "id,introduced_at",
            },
            json={"introduced_at": ts},
        )
        upd.raise_for_status()
        rows = upd.json() or []
        return len(rows) > 0

# =========================================================
# Rentals (simple: buyer_wa/seller_wa + fechas)
# =========================================================

async def create_rental_request(listing_id: int, renter_msisdn: str, start_iso: str, end_iso: str) -> Dict[str, Any]:
    """
    Crea una solicitud en 'rentals' con:
      - item_id INT
      - buyer_wa TEXT (quien alquila)
      - seller_wa TEXT (dueño del listing)
      - start_date TEXT/DATE (YYYY-MM-DD)
      - end_date TEXT/DATE (YYYY-MM-DD)
      - status TEXT ('requested')
    """
    listing = await get_listing(str(listing_id))
    if not listing:
        return {"ok": False, "error": "LISTING_NOT_FOUND"}

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{BASE}/rentals",
            headers=HEADERS_RETURN,
            params={"select": "*"},
            json={
                "item_id": int(listing_id),
                "buyer_wa": renter_msisdn,
                "seller_wa": listing["owner_wa"],
                "start_date": start_iso,  # "YYYY-MM-DD"
                "end_date": end_iso,      # "YYYY-MM-DD"
                "status": "requested",
            },
        )
        if r.status_code >= 400:
            txt = r.text
            print("create_rental_request ERROR:", r.status_code, txt)
            if "overlap" in txt or "no_overlap" in txt:
                return {"ok": False, "error": "FECHAS_NO_DISPONIBLES"}
        r.raise_for_status()
        return {"ok": True, "row": r.json()[0]}
