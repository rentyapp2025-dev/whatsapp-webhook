import re
from enum import Enum
from typing import Any, Dict, Optional, Tuple
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


# ===========================
# Fechas / utilidades robustas
# ===========================
# Aceptamos separadores comunes día/mes/año
_DATE_SEP = r"[\/\-.]"  # /  -  .
# Patron DD{sep}MM{sep}YYYY con ceros a la izquierda
_DDMMYYYY = rf"(\d{{2}}){_DATE_SEP}(\d{{2}}){_DATE_SEP}(\d{{4}})"

# Separadores de rango comunes: 'a', 'al', 'hasta', '-', '–'
_RANGE_SEP = r"(?:\s*(?:a|al|hasta|–|-)\s*)"

def _parse_date_any(s: str) -> Optional[date]:
    """
    Convierte una cadena a date aceptando:
      - DD/MM/AAAA
      - DD-MM-AAAA
      - DD.MM.AAAA
      - YYYY-MM-DD (ISO)
    """
    if not s:
        return None
    s = s.strip()
    # ISO directo
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s[:10]):
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        pass

    # DD{sep}MM{sep}YYYY
    m = re.fullmatch(_DDMMYYYY, s)
    if m:
        d, mth, y = map(int, m.groups())
        try:
            return date(y, mth, d)
        except Exception:
            return None

    # Último intento: truncar a 10 y probar ISO
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _to_ve(d: date | str) -> str:
    """
    Formatea a DD/MM/AAAA aceptando date o cadena (ISO o DD/MM/AAAA).
    """
    d_obj = _parse_date_any(d) if isinstance(d, str) else d
    return d_obj.strftime("%d/%m/%Y") if d_obj else str(d)


def _extract_dates(text: str) -> Optional[Tuple[str, str]]:
    """
    Extrae el primer rango de fechas del texto y lo devuelve como (start_iso, end_iso).
    Acepta:
      - "DD/MM/AAAA a DD/MM/AAAA"
      - "DD-MM-AAAA al DD-MM-AAAA"
      - "DD.MM.AAAA hasta DD.MM.AAAA"
      - "DD/MM/AAAA - DD/MM/AAAA" (guion normal o en dash)
    También soporta si el usuario envía dos fechas separadas por espacio.
    Devuelve None si no encuentra al menos dos fechas válidas.
    """
    if not text:
        return None
    t = text.strip()

    # 1) Intento con separador de rango explícito (a|al|hasta|-|–)
    #    Permitimos texto alrededor.
    range_pattern = rf"{_DDMMYYYY}{_RANGE_SEP}{_DDMMYYYY}"
    m = re.search(range_pattern, t, flags=re.IGNORECASE)
    if m:
        d1 = _parse_date_any(m.group(1) + "/" + m.group(2) + "/" + m.group(3))
        d2 = _parse_date_any(m.group(4) + "/" + m.group(5) + "/" + m.group(6))
        if d1 and d2:
            start, end = (d1, d2) if d1 <= d2 else (d2, d1)
            return start.isoformat(), end.isoformat()

    # 2) Si no hubo separador de rango, busquemos todas las fechas DD{sep}MM{sep}YYYY
    dates = re.findall(_DDMMYYYY, t)
    if len(dates) >= 2:
        d1 = _parse_date_any("/".join(dates[0]))
        d2 = _parse_date_any("/".join(dates[1]))
        if d1 and d2:
            start, end = (d1, d2) if d1 <= d2 else (d2, d1)
            return start.isoformat(), end.isoformat()

    # 3) Fallback: mezclar con posibles ISO ya formateadas en el texto
    #    Buscar "YYYY-MM-DD" también.
    iso_matches = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", t)
    ddmm_matches = re.findall(_DDMMYYYY, t)
    pool: list[date] = []
    for im in iso_matches:
        d = _parse_date_any(im)
        if d:
            pool.append(d)
    for tup in ddmm_matches:
        d = _parse_date_any("/".join(tup))
        if d:
            pool.append(d)
    if len(pool) >= 2:
        pool.sort()
        return pool[0].isoformat(), pool[1].isoformat()

    return None
