import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

from curl_cffi.requests import AsyncSession

# ============== CONFIGURACIÓN =================

FECHA_OBJETIVO = os.getenv("FECHA_OBJETIVO", "2027-05-17")

KLOOK_UNITS_URL = "https://www.klook.com/v1/experiencesrv/units/units_service/get_unit_list_by_schedule_id"

TARGETS = [
    {
        "nombre": "Studio Pass",
        "url_compra": "https://www.klook.com/activity/46604-universal-studios-japan-e-ticket-osaka-qr-code-direct-entry/",
        "referer": "https://www.klook.com/activity/46604-universal-studios-japan-e-ticket-osaka-qr-code-direct-entry/",
        "sufijo_id": "714034",
        "activo": True,
    },
    {
        "nombre": "Express Pass",
        "url_compra": "https://www.klook.com/activity/3407-universal-studios-japan-express-pass-osaka/",
        "referer": "https://www.klook.com/activity/3407-universal-studios-japan-express-pass-osaka/",
        "sufijo_id": "739977",
        "activo": True,
    },
]

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_IDS = [c.strip() for c in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

TIMEOUT = 15
MAX_INTENTOS = 3          # reintentos ante errores transitorios (red, 429, 5xx, bloqueos)
IMPERSONATE = "chrome124"

# Headers comunes construidos una sola vez
HEADERS_BASE = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
    "origin": "https://www.klook.com",
}

# =============================================


def log(msg: str, error: bool = False):
    print(msg, file=sys.stderr if error else sys.stdout, flush=True)


def generar_arrangement_id(fecha_str: str, sufijo: str) -> str:
    """Timestamp Unix UTC (medianoche) de la fecha + sufijo del producto."""
    dt = datetime.strptime(fecha_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return f"{int(dt.timestamp())}{sufijo}"


def _num(valor) -> float:
    """Convierte a número de forma segura (None, '', strings raros -> 0)."""
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def hay_disponibilidad(data: dict) -> bool:
    if not data.get("success"):
        return False

    result = data.get("result") or {}

    # 1. Inventarios directos
    inventories = result.get("inventories")
    if isinstance(inventories, dict) and any(_num(v) > 0 for v in inventories.values()):
        return True

    # 2. Unidades habilitadas
    units = result.get("units") or result.get("unit_list") or []
    return any(
        _num(u.get("stock", u.get("inventory"))) > 0 or u.get("is_sellable") or u.get("available")
        for u in units
        if isinstance(u, dict)
    )


async def consultar_disponibilidad(session: AsyncSession, target: dict, arr_id: str) -> bool:
    params = {
        "k_lang": "en_BS",
        "k_currency": "USD",
        "arrangement_id": arr_id,
        "translation": "0",
        "preview": "0",
    }
    headers = {**HEADERS_BASE, "referer": target["referer"]}

    for intento in range(1, MAX_INTENTOS + 1):
        try:
            resp = await session.get(KLOOK_UNITS_URL, params=params, headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()
            return hay_disponibilidad(resp.json())
        except Exception as e:
            if intento == MAX_INTENTOS:
                raise
            espera = 2 ** intento
            log(f"[WARN] {target['nombre']}: intento {intento} falló ({e}); reintentando en {espera}s", error=True)
            await asyncio.sleep(espera)
    return False  # inalcanzable


async def enviar_telegram(session: AsyncSession, mensaje: str) -> bool:
    """Envía a todos los chats en paralelo. Devuelve True si al menos uno lo recibió."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_IDS:
        log("[WARN] Telegram omitido: faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_IDS.", error=True)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    async def _enviar(chat_id: str) -> bool:
        try:
            r = await session.post(url, json={"chat_id": chat_id, "text": mensaje}, timeout=TIMEOUT)
            if r.status_code == 200:
                return True
            log(f"[ERROR] Telegram ({chat_id}): {r.text}", error=True)
        except Exception as e:
            log(f"[ERROR] Excepción enviando Telegram a {chat_id}: {e}", error=True)
        return False

    resultados = await asyncio.gather(*(_enviar(c) for c in TELEGRAM_CHAT_IDS))
    return any(resultados)


def cargar_estado() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def guardar_estado(estado: dict):
    """Escritura atómica: nunca deja un state.json corrupto a medias."""
    try:
        directorio = os.path.dirname(STATE_FILE)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directorio, delete=False, suffix=".tmp") as tmp:
            json.dump(estado, tmp, ensure_ascii=False, indent=2)
        os.replace(tmp.name, STATE_FILE)
    except Exception as e:
        log(f"[ERROR] No se pudo guardar estado: {e}", error=True)


def mensaje_alerta(target: dict, fecha: str) -> str:
    return (
        f"🎢 ¡ALERTA USJ! 🎢\n\n"
        f"¡Ya hay boletos para {target['nombre']} en la fecha {fecha}!\n\n"
        f"Compra directa aquí:\n{target['url_compra']}"
    )


async def main():
    activos = [t for t in TARGETS if t["activo"]]
    if not activos:
        log("[WARN] No hay objetivos activos.")
        return

    try:
        arr_ids = {t["nombre"]: generar_arrangement_id(FECHA_OBJETIVO, t["sufijo_id"]) for t in activos}
    except ValueError:
        log(f"[ERROR] FECHA_OBJETIVO inválida: '{FECHA_OBJETIVO}' (formato esperado AAAA-MM-DD)", error=True)
        sys.exit(1)

    ahora = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    log(f"[{ahora}] Monitoreando disponibilidad para {FECHA_OBJETIVO}...")

    # Una sola sesión: reutiliza conexiones TLS y huella de navegador para todas las peticiones
    async with AsyncSession(impersonate=IMPERSONATE) as session:
        resultados = await asyncio.gather(
            *(consultar_disponibilidad(session, t, arr_ids[t["nombre"]]) for t in activos),
            return_exceptions=True,
        )

        # La clave incluye la fecha: si cambias FECHA_OBJETIVO, el estado viejo no bloquea alertas nuevas
        clave = lambda t: f"{t['nombre']}|{FECHA_OBJETIVO}"

        actual = {}
        for t, r in zip(activos, resultados):
            if isinstance(r, Exception):
                log(f"[WARN] No se pudo consultar {t['nombre']}: {r}", error=True)
                continue
            actual[clave(t)] = r
            log(f" -> {t['nombre']}: {'¡DISPONIBLE!' if r else 'NO DISPONIBLE'}")

        if not actual:
            log("[WARN] No se obtuvo respuesta de ningún objetivo.")
            return

        anterior = cargar_estado()
        nuevos = [t for t in activos if actual.get(clave(t)) and not anterior.get(clave(t))]

        if nuevos:
            enviados = await asyncio.gather(*(enviar_telegram(session, mensaje_alerta(t, FECHA_OBJETIVO)) for t in nuevos))
            for t, ok in zip(nuevos, enviados):
                if ok:
                    log(f"[AVISO ENVIADO] Telegram notificado para {t['nombre']}")
                else:
                    # No se marca como notificado: se reintentará en la próxima ejecución
                    actual[clave(t)] = False

    nuevo_estado = {**anterior, **actual}
    if nuevo_estado != anterior:
        guardar_estado(nuevo_estado)


if __name__ == "__main__":
    asyncio.run(main())
