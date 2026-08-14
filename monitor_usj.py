import json
import os
import sys
from datetime import datetime

# Requiere 'pip install curl_cffi'
from curl_cffi import requests

# ============== CONFIGURACIÓN ==============

FECHA_OBJETIVO = "2026-10-13"

KLOOK_UNITS_URL = "https://www.klook.com/v1/experiencesrv/units/units_service/get_unit_list_by_schedule_id"

TARGETS = [
    {
        "nombre": "Studio Pass",
        "url_compra": "https://www.klook.com/activity/46604-universal-studios-japan-e-ticket-osaka-qr-code-direct-entry/",
        "referer": "https://www.klook.com/activity/46604-universal-studios-japan-e-ticket-osaka-qr-code-direct-entry/",
        "arrangement_id": "1792108800714034",
        "activo": True,
    },
    {
        "nombre": "Express Pass",
        "url_compra": "https://www.klook.com/activity/3407-universal-studios-japan-express-pass-osaka/",
        "referer": "https://www.klook.com/activity/3407-universal-studios-japan-express-pass-osaka/",
        "arrangement_id": "1792108800739977",
        "activo": True,
    },
]

TELEGRAM_TOKEN = "8874342950:AAE2uAgmqGE3cP7Mc_qOvZ_FPnxd06Y570A"
TELEGRAM_CHAT_IDS = [
    "6735724473",
    "1575450705",
]

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")

# =============================================


def enviar_telegram(mensaje: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            resp = requests.post(url, data={"chat_id": chat_id, "text": mensaje})
            if resp.status_code != 200:
                print(f"[ERROR] No se pudo enviar a Telegram ({chat_id}): {resp.text}", file=sys.stderr)
        except Exception as e:
            print(f"[ERROR] Excepción enviando Telegram: {e}", file=sys.stderr)


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
        print(f"[ERROR] Falló al guardar estado: {e}", file=sys.stderr)


def consultar_disponibilidad(target: dict) -> bool:
    params = {
        "k_lang": "en_BS",
        "k_currency": "USD",
        "arrangement_id": target["arrangement_id"],
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

    inventories = result.get("inventories") or {}
    if inventories:
        return sum(inventories.values()) > 0

    units = result.get("units") or result.get("unit_list") or []
    if units:
        for unit in units:
            stock = unit.get("stock", unit.get("inventory", 0))
            if stock > 0 or unit.get("is_sellable") or unit.get("available"):
                return True

    return False


def main():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] Revisando disponibilidad para {FECHA_OBJETIVO}...")

    disponibilidad_actual = {}
    for target in TARGETS:
        if not target["activo"]:
            continue
        try:
            disponibilidad_actual[target["nombre"]] = consultar_disponibilidad(target)
        except Exception as e:
            print(f"[WARN] No se pudo consultar {target['nombre']}: {e}", file=sys.stderr)

    if not disponibilidad_actual:
        print("[WARN] No se obtuvo respuesta válida.")
        return

    estado_anterior = cargar_estado_anterior()

    for target in TARGETS:
        nombre = target["nombre"]
        disponible_ahora = disponibilidad_actual.get(nombre, False)
        disponible_antes = estado_anterior.get(nombre, False)

        estado_texto = "DISPONIBLE" if disponible_ahora else "NO DISPONIBLE"
        print(f" -> {nombre}: {estado_texto}")

        if disponible_ahora and not disponible_antes:
            mensaje = (
                f"🎢 ¡Ya está disponible el {nombre} para el {FECHA_OBJETIVO}!\n\n"
                f"Compra directa aquí:\n{target['url_compra']}"
            )
            enviar_telegram(mensaje)
            print(f"[AVISO ENVIADO] Telegram notificado para {nombre}")

    estado_anterior.update(disponibilidad_actual)
    guardar_estado(estado_anterior)


if __name__ == "__main__":
    main()