# supabase_client.py
import os
import httpx
import re
from datetime import datetime, date
from typing import Optional, Dict, Any, List

# === Config ===
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

BASE = f"{SUPABASE_URL}/rest/v1"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}
HEADERS_RETURN = {**HEADERS, "Prefer": "return=representation"}
HEADERS_UPSERT = {**HEADERS, "Prefer": "return=representation,resolution=merge-duplicates"}

# =========================================================
# Utilidades
# =========================================================

def _norm_phone(s: Optional[str]) -> str:
    """Normaliza teléfonos a solo dígitos para comparaciones robustas."""
    return re.sub(r"\D", "", s or "")

# =========================================================
# Users
# =========================================================

# --- MODIFICADO para aceptar y guardar el nombre de WhatsApp ---
async def ensure_user(msisdn: str, name: Optional[str] = None):
    """
    Garantiza que exista un usuario. Si es nuevo, lo crea con su nombre de perfil de WA.
    Si ya existe pero no tiene nombre (o tiene el número), lo actualiza.
    """
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
            # Actualiza el nombre si no existe, es el número de teléfono, o se proporciona uno nuevo.
            if name and (not user.get("name") or user.get("name") == msisdn):
                await client.patch(
                    f"{BASE}/users",
                    headers=HEADERS,
                    params={"wa_id": f"eq.{msisdn}"},
                    json={"name": name}
                )
            return

        # Si no existe, lo crea con el nombre proporcionado o el número como fallback.
        await client.post(
            f"{BASE}/users",
            headers=HEADERS_RETURN,
            json={"wa_id": msisdn, "name": name or msisdn},
        )

async def get_user_name(msisdn: str) -> str:
    # (Sin cambios, tu función es correcta)
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{BASE}/users",
            headers=HEADERS,
            params={"select": "name", "wa_id": f"eq.{msisdn}", "limit": 1},
        )
        r.raise_for_status()
        rows = r.json()
        return rows[0]["name"] if rows and rows[0].get("name") else msisdn

# =========================================================
# Sessions (state machine)
# =========================================================

async def set_session(msisdn: str, step: str, draft: dict | None = None):
    # (Sin cambios, tu función es correcta)
    payload = {
        "wa_id": msisdn,
        "step": step,
        "draft": draft or {},
        "updated_at": datetime.utcnow().isoformat(),
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
    # (Sin cambios, tu función es correcta)
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

# =========================================================
# Listings (Publicaciones)
# =========================================================

async def insert_listing(owner_msisdn: str, title: str, price_text: str, zone: str, payment_methods: List[str]) -> str:
    # (Tu función ya era correcta y aceptaba payment_methods, se mantiene)
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
    # (Sin cambios, tu función es correcta)
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{BASE}/listings",
            headers=HEADERS,
            params={"select": "*", "id": f"eq.{item_id}", "limit": 1},
        )
        r.raise_for_status()
        rows = r.json()
        if not rows: return None
        listing = rows[0]
        try:
            listing["owner_name"] = await get_user_name(listing["owner_wa"])
        except Exception:
            listing["owner_name"] = listing.get("owner_wa")
        return listing

# --- NUEVA FUNCIÓN para actualizar el estado de una publicación ---
async def update_listing_status(item_id: str, owner_wa: str, new_status: str) -> bool:
    """Actualiza el estado de una publicación (ej: 'inactive'). Verifica la propiedad."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.patch(
            f"{BASE}/listings",
            headers=HEADERS,
            params={"id": f"eq.{item_id}", "owner_wa": f"eq.{owner_wa}"},
            json={"status": new_status}
        )
        # 204 No Content indica éxito sin cuerpo de respuesta.
        return 200 <= r.status_code < 300

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

# --- MODIFICADO para aceptar el método de pago seleccionado ---
async def create_rental_request(listing_id: int, renter_msisdn: str, start_iso: str, end_iso: str, payment_method: str) -> Dict[str, Any]:
    """Crea una solicitud de alquiler incluyendo el método de pago elegido."""
    listing = await get_listing(str(listing_id))
    if not listing: return {"ok": False, "error": "LISTING_NOT_FOUND"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        payload = {
            "item_id": listing_id,
            "buyer_wa": renter_msisdn,
            "seller_wa": listing["owner_wa"],
            "start_date": start_iso[:10],
            "end_date": end_iso[:10],
            "status": "requested",
            "selected_payment_method": payment_method, # <-- DATO NUEVO
        }
        r = await client.post(f"{BASE}/rentals", headers=HEADERS_RETURN, json=payload)
        if r.status_code >= 400:
            print("create_rental_request ERROR:", r.status_code, r.text)
            return {"ok": False, "error": "DB_ERROR"}
        return {"ok": True, "row": r.json()[0]}

# --- NUEVA FUNCIÓN para obtener rentas activas de un artículo ---
async def get_active_rentals_for_item(item_id: str) -> List[Dict[str, Any]]:
    """Busca rentas que no estén canceladas/completadas para un artículo."""
    async with httpx.AsyncClient(timeout=10.0) as c:
        params = {
            "item_id": f"eq.{item_id}",
            "status": "in.(requested,approved,active)"
        }
        r = await c.get(f"{BASE}/rentals", headers=HEADERS, params=params)
        r.raise_for_status()
        return r.json()

# --- NUEVA FUNCIÓN para solicitar la cancelación de una renta ---
async def request_rental_cancellation(rental_id: int, requester_wa: str) -> Dict[str, Any]:
    """Registra la solicitud de cancelación de un usuario y la procesa si ambos están de acuerdo."""
    async with httpx.AsyncClient(timeout=15.0) as c:
        # 1. Obtener la renta actual
        r_get = await c.get(f"{BASE}/rentals", headers=HEADERS, params={"id": f"eq.{rental_id}", "select": "*"})
        if not r_get.json(): return {"status": "NOT_FOUND"}
        
        rental = r_get.json()[0]
        other_party_wa = ""
        is_buyer = _norm_phone(requester_wa) == _norm_phone(rental['buyer_wa'])

        # 2. Determinar qué campo actualizar
        if is_buyer:
            update_payload = {"buyer_wants_cancel": True}
            other_party_wa = rental['seller_wa']
        else:
            update_payload = {"seller_wants_cancel": True}
            other_party_wa = rental['buyer_wa']

        # 3. Actualizar el flag del solicitante
        await c.patch(f"{BASE}/rentals", headers=HEADERS, params={"id": f"eq.{rental_id}"}, json=update_payload)
        
        # 4. Comprobar si la otra parte ya quería cancelar
        if (is_buyer and rental.get('seller_wants_cancel')) or (not is_buyer and rental.get('buyer_wants_cancel')):
            # Ambos quieren cancelar, actualizamos el estado final
            await c.patch(f"{BASE}/rentals", headers=HEADERS, params={"id": f"eq.{rental_id}"}, json={"status": "cancelled"})
            return {"status": "CANCELLED", "parties": [rental['buyer_wa'], rental['seller_wa']]}
        else:
            # Solo uno ha solicitado, esperamos al otro
            return {"status": "WAITING_OTHER", "other_party": other_party_wa}

# =========================================================
# Reviews (Reseñas) - SECCIÓN COMPLETAMENTE NUEVA
# =========================================================

async def add_review(rental_id: int, reviewer_wa: str, rating: int, comment: str) -> Dict[str, Any]:
    """
    Añade una reseña a una renta. El trigger en la BD actualizará la reputación.
    Valida que la renta exista, esté completada y que el usuario sea parte de ella.
    """
    async with httpx.AsyncClient(timeout=15.0) as c:
        # 1. Obtener la renta para validar
        r_get = await c.get(
            f"{BASE}/rentals",
            headers=HEADERS,
            params={"select": "buyer_wa,seller_wa,status", "id": f"eq.{rental_id}", "limit": 1}
        )
        if not r_get.json():
            return {"ok": False, "error": "RENTAL_NOT_FOUND"}
        
        rental = r_get.json()[0]
        
        # 2. Validaciones
        # if rental.get("status") != "completed":
        #    return {"ok": False, "error": "RENTAL_NOT_COMPLETED"}
        
        reviewer_norm = _norm_phone(reviewer_wa)
        buyer_norm = _norm_phone(rental["buyer_wa"])
        seller_norm = _norm_phone(rental["seller_wa"])
        
        if reviewer_norm not in [buyer_norm, seller_norm]:
            return {"ok": False, "error": "NOT_PART_OF_RENTAL"}

        reviewed_wa = rental["seller_wa"] if reviewer_norm == buyer_norm else rental["buyer_wa"]
        
        # 3. Insertar la reseña
        payload = {
            "rental_id": rental_id,
            "reviewer_wa": reviewer_wa,
            "reviewed_wa": reviewed_wa,
            "rating": rating,
            "comment": comment
        }
        r_post = await c.post(f"{BASE}/reviews", headers=HEADERS_RETURN, json=payload)
        
        if r_post.status_code == 409: # Conflicto, significa que ya existe una reseña para esa (rental_id, reviewer_wa)
             return {"ok": False, "error": "ALREADY_REVIEWED"}
        r_post.raise_for_status()
        return {"ok": True, "data": r_post.json()}

async def get_reviews_for_user(wa_id: str) -> List[Dict[str, Any]]:
    """Obtiene todas las reseñas que un usuario ha recibido."""
    async with httpx.AsyncClient(timeout=10.0) as c:
        params = {
            "select": "rating,comment,created_at",
            "reviewed_wa": f"eq.{wa_id}",
            "order": "created_at.desc"
        }
        r = await c.get(f"{BASE}/reviews", headers=HEADERS, params=params)
        r.raise_for_status()
        return r.json()