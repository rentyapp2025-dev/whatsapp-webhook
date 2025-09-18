import re
from enum import Enum
from typing import Any, Dict, Optional
from datetime import datetime, date

class Step(str, Enum):
    IDLE = "idle"
    PUBLISH_TITLE = "publish_title"
    PUBLISH_PRICE = "publish_price"
    PUBLISH_ZONE = "publish_zone"
    PUBLISH_PAYMENTS = "publish_payments"
    RENTAL_WAIT_DATES = "rental_wait_dates"
    RENTAL_WAIT_PAYMENT = "rental_wait_payment"
    RENTAL_EXTENSION_WAIT_DATES = "rental_extension_wait_dates"  
    RENTAL_VIEW_ONE = "rental_view_one"  
    
def step_val(st: Dict[str, Any] | None) -> str:
    v = (st or {}).get("step")
    return v.value if isinstance(v, Step) else v or Step.IDLE.value

# === Fechas / utilidades ===
def _parse_date_any(s: str) -> Optional[date]:
    if not s:
        return None
    s = s.strip()
    try:
        if m := re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", s):
            d, mth, y = map(int, m.groups())
            return date(y, mth, d)
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None

def _to_ve(d: date | str) -> str:
    d_obj = _parse_date_any(d) if isinstance(d, str) else d
    return d_obj.strftime("%d/%m/%Y") if d_obj else str(d)

def _extract_dates(text: str) -> Optional[tuple[str, str]]:
    dates = re.findall(r"\b(\d{2}/\d{2}/\d{4})\b", text)
    if len(dates) >= 2:
        d1, d2 = _parse_date_any(dates[0]), _parse_date_any(dates[1])
        if d1 and d2 and d1 <= d2:
            return d1.isoformat(), d2.isoformat()
    return None
