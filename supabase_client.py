import os, httpx

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

async def insert_rating(phone: str, name: str, rating: str):
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/ratings",
            headers=HEADERS,
            json={"phone": phone, "name": name or "", "rating": rating},
            params={"select": "*"},
            timeout=20.0
        )
        r.raise_for_status()

async def insert_order(order: dict):
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/orders",
            headers=HEADERS,
            json=order,
            params={"select": "*"},
            timeout=20.0
        )
        r.raise_for_status()

async def get_rating_counts():
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/ratings_counts",
            headers=HEADERS,
            params={"select": "*"},
            timeout=20.0
        )
        r.raise_for_status()
        return r.json()[0]
