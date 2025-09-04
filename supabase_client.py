# supabase_client.py
import os
import httpx
import re
from datetime import datetime
from typing import Optional, Dict, Any

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
# Para upsert por clave única (por ej. sessions.wa_id)
HEADERS_UPSERT = {**HEADERS_RETURN, "Prefer": "resolution=merge-duplicates"}

# ===================== utilidades =====================

def _parse_price(text: str) -> float:
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

# ===================== tus funciones (sin cambios) =====================

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

# ===================== NUEVO: Users =====================

async def ensure_user(msisdn: str) -> Dict[str, Any]:
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

# ===================== NUEVO: Sessions (state machine) =====================

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

# ===================== NUEVO: Listings =====================

async def insert_listing(owner_msisdn: str, title: str, price_text: str, location: str) -> str:
    # Resuelve owner_id
    owner = await ensure_user(owner_msisdn)
    owner_id = owner["id"]
    price = _parse_price(price_text)
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{BASE}/listings",
            headers=HEADERS_RETURN,
            params={"select": "*"},
            json={
                "owner_id": owner_id,
                "title": title,
                "price_per_day": price,
                "location": location,
                "status": "active",
            },
        )
        r.raise_for_status()
        return str(r.json()[0]["id"])

async def get_listing(item_id: str) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        # Embebe datos del owner con la relación FK owner_id -> users.id
        r = await client.get(
            f"{BASE}/listings",
            headers=HEADERS,
            params={
                "select": "*,owner:owner_id(wa_id,name)",
                "id": f"eq.{item_id}",
                "limit": 1,
            },
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        listing = rows[0]
        owner = listing.get("owner") or {}
        listing["owner_wa"] = owner.get("wa_id")
        listing["owner_name"] = owner.get("name") or owner.get("wa_id")
        return listing

# ===================== NUEVO: Consents =====================

async def upsert_consent(listing_id: str, buyer_msisdn: str, seller_msisdn: str):
    """Haz GET y luego POST/PATCH según exista; así no dependes de un índice único en listing_id."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        g = await client.get(
            f"{BASE}/consents",
            headers=HEADERS,
            params={"select": "*", "listing_id": f"eq.{listing_id}", "limit": 1},
        )
        g.raise_for_status()
        rows = g.json()
        if rows:
            cid = rows[0]["id"]
            p = await client.patch(
                f"{BASE}/consents",
                headers=HEADERS_RETURN,
                params={"id": f"eq.{cid}", "select": "*"},
                json={"buyer_wa": buyer_msisdn, "seller_wa": seller_msisdn},
            )
            p.raise_for_status()
            return p.json()[0]
        else:
            c = await client.post(
                f"{BASE}/consents",
                headers=HEADERS_RETURN,
                params={"select": "*"},
                json={"listing_id": int(listing_id), "buyer_wa": buyer_msisdn, "seller_wa": seller_msisdn},
            )
            c.raise_for_status()
            return c.json()[0]

async def get_consent(listing_id: str) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{BASE}/consents",
            headers=HEADERS,
            params={"select": "*", "listing_id": f"eq.{listing_id}", "limit": 1},
        )
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None

async def set_consent_flag(listing_id: str, msisdn: str, ok: bool) -> Optional[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        g = await client.get(
            f"{BASE}/consents",
            headers=HEADERS,
            params={"select": "*", "listing_id": f"eq.{listing_id}", "limit": 1},
        )
        g.raise_for_status()
        rows = g.json()
        if not rows:
            return None
        row = rows[0]
        is_buyer = (msisdn == row.get("buyer_wa"))
        field = "buyer_ok" if is_buyer else "seller_ok"
        upd = await client.patch(
            f"{BASE}/consents",
            headers=HEADERS_RETURN,
            params={"id": f"eq.{row['id']}", "select": "*"},
            json={field: bool(ok)},
        )
        upd.raise_for_status()
        return upd.json()[0]

# ===================== NUEVO: Rentals =====================

async def create_rental_request(listing_id: int, renter_msisdn: str, start_iso: str, end_iso: str) -> Dict[str, Any]:
    renter = await ensure_user(renter_msisdn)
    start_s = _date_to_utc_iso(start_iso)
    end_s = _date_to_utc_iso(end_iso)
    period_literal = f"[{start_s},{end_s})"  # Postgres parsea tstzrange en texto

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.post(
                f"{BASE}/rentals",
                headers=HEADERS_RETURN,
                params={"select": "*"},
                json={
                    "listing_id": int(listing_id),
                    "renter_id": renter["id"],
                    "period": period_literal,
                    "status": "requested",
                },
            )
            r.raise_for_status()
            return {"ok": True, "rental": r.json()[0]}
        except httpx.HTTPStatusError as e:
            txt = e.response.text
            if "no_overlap_per_listing" in txt or "overlap" in txt:
                return {"ok": False, "error": "FECHAS_NO_DISPONIBLES"}
            return {"ok": False, "error": "DB_ERROR", "detail": txt}
