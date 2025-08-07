import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

def get_local_tz():
    """Devuelve la zona horaria a partir de API_TIMEZONE; fallback a -05:00."""
    tzname = os.getenv("API_TIMEZONE", "America/Guayaquil")
    try:
        return ZoneInfo(tzname)
    except ZoneInfoNotFoundError:
        return timezone(-timedelta(hours=5))

def now_local_iso() -> str:
    """Fecha/hora local en ISO con milisegundos."""
    return datetime.now(get_local_tz()).isoformat(timespec="milliseconds")
