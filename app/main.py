from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
import uuid

from .config import VERIFY_TOKEN
from .wa_api import verify_signature, send_reply_buttons, send_text
from .handlers import handle_message
from .clients.supabase_client import update_rental_status  # usamos esto para pasar active -> completed

app = FastAPI(title="WhatsApp Cloud API Webhook for Renty")

# ========= utilidades locales p/cron (no alteran el resto) =========
BUSINESS_TZ = os.environ.get("BUSINESS_TZ", "America/Caracas")
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
CRON_SECRET = os.environ.get("CRON_SECRET")  # opcional, recomendado para proteger el endpoint

BASE = f"{SUPABASE_URL}/rest/v1"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

def _today_business_str() -> str:
    return datetime.now(ZoneInfo(BUSINESS_TZ)).date().isoformat()

def _to_ve(d_iso: str) -> str:
    # d_iso = "YYYY-MM-DD"
    try:
        y, m, d = map(int, d_iso[:10].split("-"))
        return f"{d:02d}/{m:02d}/{y:04d}"
    except Exception:
        return d_iso

def _new_token() -> str:
    return uuid.uuid4().hex[:10]

# ==================== Webhook de Meta ====================
@app.get("/webhook")
async def verify_webhook(req: Request):
    if req.query_params.get("hub.mode") == "subscribe" and req.query_params.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(req.query_params.get("hub.challenge"))
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhook")
async def receive_webhook(request: Request):
    body_bytes = await request.body()
    if not verify_signature(request.headers.get("X-Hub-Signature-256"), body_bytes):
        raise HTTPException(status_code=403, detail="Invalid signature")

    data = await request.json()
    if data.get("object") != "whatsapp_business_account":
        return Response(status_code=200)

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages")
            if not messages:
                continue
            for msg in messages:
                await handle_message(value, msg)

    return Response(status_code=200)

@app.get("/")
async def health_check():
    return {"status": "ok"}


# ==================== Cron: fin de alquileres ====================
@app.post("/cron/end-rentals")
async def cron_end_rentals(request: Request):
    """
    Llamar 1 vez al día (ej: 00:05 hora de negocio) con un secreto:
      curl -X POST https://tu-app/cron/end-rentals -H "X-Cron-Secret: <CRON_SECRET>"
    Hace:
      - Busca rentas con status=active y end_date == hoy (en tz de negocio)
      - Notifica a dueño e inquilino con botones: reseñar / reportar
      - Marca la renta como completed (habilita reseñas)
    """
    # Seguridad básica (opcional pero recomendado)
    if CRON_SECRET:
        provided = request.headers.get("X-Cron-Secret")
        if not provided or provided != CRON_SECRET:
            raise HTTPException(status_code=403, detail="Forbidden")

    today_iso = _today_business_str()

    # 1) Traer rentas que finalizan hoy y están activas
    async with httpx.AsyncClient(timeout=20.0) as c:
        params = {
            "select": "id,item_id,buyer_wa,seller_wa,start_date,end_date,status",
            "status": "eq.active",
            "end_date": f"eq.{today_iso}",
        }
        r = await c.get(f"{BASE}/rentals", headers=HEADERS, params=params)
        r.raise_for_status()
        rows = r.json() or []

    processed = []
    errors = []

    # 2) Notificar y marcar como completed
    for row in rows:
        try:
            rid = row["id"]
            buyer = row["buyer_wa"]
            seller = row["seller_wa"]
            item_id = row["item_id"]
            start_ve = _to_ve(row["start_date"])
            end_ve = _to_ve(row["end_date"])
            tok_b = _new_token()
            tok_s = _new_token()

            # Mensaje base
            body_common = (
                f"🕓 Tu alquiler #{rid} del artículo #{item_id} finalizó hoy "
                f"({start_ve} → {end_ve}).\n\n"
                "Cuéntanos cómo te fue:"
            )
            # Botones (handlers debe manejar ids: review_start_{rid}_*, report_start_{rid}_*)
            btns = [
                {"id": f"review_start_{rid}_{tok_b}", "title": "⭐️ Dejar reseña"},
                {"id": f"report_start_{rid}_{tok_b}", "title": "⚠️ Reportar problema"},
            ]

            # Enviar a comprador y dueño
            await send_reply_buttons(buyer, "Fin del alquiler", body_common + "\n• Califica al dueño o reporta un inconveniente.", btns)
            await send_reply_buttons(seller, "Fin del alquiler", body_common + "\n• Califica al inquilino o reporta un inconveniente.", [
                {"id": f"review_start_{rid}_{tok_s}", "title": "⭐️ Dejar reseña"},
                {"id": f"report_start_{rid}_{tok_s}", "title": "⚠️ Reportar problema"},
            ])

            # Fallback textual (por si el cliente no ve botones)
            tips = (
                f"También puedes escribir:\n"
                f" • RESEÑA #{rid} 1-5 Tu comentario\n"
                f" • Para reportar: escribe 'REPORTAR #{rid} ...' (detalla el problema)"
            )
            await send_text(buyer, tips)
            await send_text(seller, tips)

            # Marcar como COMPLETED (habilita reseñas en tu lógica actual)
            ok = await update_rental_status(int(rid), "completed")
            if not ok:
                # si no se pudo por concurrencia, igual seguimos (ya se notificó)
                pass

            processed.append(rid)
        except Exception as e:
            errors.append({"rental_id": row.get("id"), "error": str(e)})

    return {
        "date": today_iso,
        "found": len(rows),
        "processed": processed,
        "errors": errors,
    }
