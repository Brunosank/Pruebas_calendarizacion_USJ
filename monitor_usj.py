import json
import os
import sys
from datetime import datetime, timezone
from curl_cffi import requests

# ============== CONFIGURACIÓN =================

# Fecha objetivo (puedes cambiar el valor por defecto si lo corres localmente)
FECHA_OBJETIVO = os.getenv("FECHA_OBJETIVO", "2026-10-17")

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

# Lectura 100% segura mediante Variables de Entorno (Secrets)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_IDS_RAW = os.getenv("TELEGRAM_CHAT_IDS", "")
TELEGRAM_CHAT_IDS = [cid.strip() for cid in TELEGRAM_CHAT_IDS_RAW.split(",") if cid.strip()]

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")

# =============================================


def generar_arrangement_id(fecha_str: str, sufijo: str) -> str:
    """Calcula el arrangement_id concatenando el Timestamp Unix UTC y el sufijo."""
    dt = datetime.strptime(fecha_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    ts = int(dt.timestamp())
    return f"{ts}{sufijo}"


def enviar_telegram(mensaje: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_IDS:
        print("[WARN] Se omitió el envío a Telegram: TELEGRAM_TOKEN o TELEGRAM_CHAT_IDS no están definidos.", file=sys.stderr)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            resp = requests.post(url, data={"chat_id": chat_id, "text": mensaje})
            if resp.status_code != 200:
                print(f"[ERROR] No se pudo enviar a Telegram ({chat_id}): {resp.text}", file=sys.stderr)
        except Exception as e:
            print(f"[ERROR] Excepción enviando Telegram a {chat_id}: {e}", file=sys.stderr)


def cargar_estado_anterior() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def guardar_estado(estado: dict):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] No se pudo guardar estado: {e}", file=sys.stderr)


def consultar_disponibilidad(target: dict, fecha_str: str) -> bool:
    arr_id = generar_arrangement_id(fecha_str, target["sufijo_id"])

    params = {
        "k_lang": "en_BS",
        "k_currency": "USD",
        "arrangement_id": arr_id,
        "translation": "0",
        "preview": "0",
    }

    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
        "origin": "https://www.klook.com",
        "referer": target["referer"],
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    }

    resp = requests.get(
        KLOOK_UNITS_URL,
        params=params,
        headers=headers,
        impersonate="chrome124",
        timeout=15,
    )

    resp.raise_for_status()
    data = resp.json()

    if not data.get("success"):
        return False

    result = data.get("result") or {}

    # 1. Verificar inventarios directos
    inventories = result.get("inventories") or {}
    if inventories and sum(inventories.values()) > 0:
        return True

    # 2. Verificar unidades habilitadas
    units = result.get("units") or result.get("unit_list") or []
    for unit in units:
        stock = unit.get("stock", unit.get("inventory", 0))
        if stock > 0 or unit.get("is_sellable") or unit.get("available"):
            return True

    return False


def main():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] Monitoreando disponibilidad para {FECHA_OBJETIVO}...")

    disponibilidad_actual = {}
    for target in TARGETS:
        if not target["activo"]:
            continue
        try:
            disponibilidad_actual[target["nombre"]] = consultar_disponibilidad(target, FECHA_OBJETIVO)
        except Exception as e:
            print(f"[WARN] No se pudo consultar {target['nombre']}: {e}", file=sys.stderr)

    if not disponibilidad_actual:
        print("[WARN] No se obtuvo respuesta de ningún objetivo.")
        return

    estado_anterior = cargar_estado_anterior()

    for target in TARGETS:
        nombre = target["nombre"]
        disponible_ahora = disponibilidad_actual.get(nombre, False)
        disponible_antes = estado_anterior.get(nombre, False)

        estado_texto = "¡DISPONIBLE!" if disponible_ahora else "NO DISPONIBLE"
        print(f" -> {nombre}: {estado_texto}")

        if disponible_ahora and not disponible_antes:
            mensaje = (
                f"🎢 ¡ALERTA USJ! 🎢\n\n"
                f"¡Ya hay boletos para {nombre} en la fecha {FECHA_OBJETIVO}!\n\n"
                f"Compra directa aquí:\n{target['url_compra']}"
            )
            enviar_telegram(mensaje)
            print(f"[AVISO ENVIADO] Telegram notificado para {nombre}")

    estado_anterior.update(disponibilidad_actual)
    guardar_estado(estado_anterior)


if __name__ == "__main__":
    main()
