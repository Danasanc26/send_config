import os
import json
import time
import asyncio
from typing import Dict, Any
from fastapi import HTTPException
from asyncio_mqtt import Client, MqttError
from app.utils.log import get_logger

logger = get_logger("app_logger")

# Config locales del módulo (lee .env previamente cargado por main)
MAX_RETRIES   = int(os.getenv("MAX_RETRIES", "3"))
RETRY_DELAY_S = float(os.getenv("RETRY_DELAY_S", "2.0"))
TIMEOUT_S     = float(os.getenv("ACK_TIMEOUT_S", "3"))

SEPARATOR = "*" * 30

def log_section(title: str):
    logger.info(f"{SEPARATOR}  {title}  {SEPARATOR}")

async def wait_ep_ack(id_balanza: str, rid: str, mqtt: Client) -> Dict[str, Any]:
    """
    Espera doran/{id}/ack con:
      { "rid": rid, "ok": true, "data": {"ack":"*","sent_len":...,"sent_hex":"..."} }
    Ignora retained y rids que no coinciden.
    """
    if mqtt is None:
        logger.error(f"[{id_balanza}] Cliente MQTT no disponible para ACK")
        raise HTTPException(503, "MQTT no disponible")

    ack_topic = f"doran/{id_balanza}/ack"
    logger.info(f"[{id_balanza}] Esperando ACK en topic: {ack_topic} (rid={rid})")

    try:
        async with mqtt.filtered_messages(ack_topic) as msgs:
            await mqtt.subscribe(ack_topic, qos=1)
            logger.debug(f"[{id_balanza}] Suscripción exitosa al tópico de ACK")
            deadline = time.monotonic() + TIMEOUT_S
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(f"[{id_balanza}] Timeout esperando ACK (rid={rid})")
                    raise HTTPException(504, f"sin ACK de {id_balanza} en {TIMEOUT_S}s")

                try:
                    msg = await asyncio.wait_for(msgs.__anext__(), timeout=remaining)
                except asyncio.TimeoutError:
                    logger.warning(f"[{id_balanza}] Timeout al esperar mensaje MQTT (rid={rid})")
                    raise HTTPException(504, f"sin ACK de {id_balanza} en {TIMEOUT_S}s")

                if getattr(msg, "retain", False):
                    logger.debug(f"[{id_balanza}] Mensaje retained ignorado")
                    continue

                try:
                    ack = json.loads(msg.payload.decode())
                    logger.debug(f"[{id_balanza}] ACK recibido: {ack}")
                except Exception as e:
                    logger.error(f"[{id_balanza}] Error al parsear ACK: {e}")
                    continue

                if ack.get("rid") != rid:
                    logger.debug(f"[{id_balanza}] RID no coincide (esperado={rid}, recibido={ack.get('rid')})")
                    continue

                if not ack.get("ok", False):
                    logger.error(f"[{id_balanza}] ACK recibido con ok=false")
                    raise HTTPException(502, f"ACK ok=false de {id_balanza}")

                data = ack.get("data", {})
                if data.get("ack") != "*":
                    logger.error(f"[{id_balanza}] ACK inválido: {data.get('ack')!r}")
                    raise HTTPException(502, f"ACK inesperado de {id_balanza}: {data.get('ack')!r}")

                logger.info(f"[{id_balanza}] ACK válido recibido (rid={rid})")
                return ack
    except MqttError as e:
        logger.exception(f"[{id_balanza}] Error MQTT al esperar ACK")
        raise HTTPException(500, f"MQTT error: {e}")
    finally:
        try:
            await mqtt.unsubscribe(ack_topic)
            logger.debug(f"[{id_balanza}] Unsubscribe de {ack_topic}")
        except Exception:
            pass

async def send_ep_cmd(id_balanza: str, rid: str, params, mqtt: Client):
    """
    Publica 'config' con category 'ep' a doran/{id}/cmd. Reintenta si falla.
    """
    if mqtt is None:
        logger.error(f"[{id_balanza}] MQTT no disponible para publicar comando EP")
        raise HTTPException(503, "MQTT no disponible")

    cmd_topic = f"doran/{id_balanza}/cmd"
    payload = {
        "rid": rid,
        "cmd": "config",
        "payload": {
            "category": "ep",
            "prod_id": params.prod_id,
            "under": params.under,
            "over": params.over,
            "tare": params.tare,
            "unit": params.unit,
            "motion": params.motion,
            "threshold": params.threshold,
            "outputs": params.outputs,
            "check_entry": params.check_entry
        }
    }
    message = json.dumps(payload)

    log_section(f"NEW EP CMD  id={id_balanza}  rid={rid}")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"[{id_balanza}] Intento {attempt}: publicando EP a {cmd_topic}")
            await mqtt.publish(cmd_topic, message, qos=1, retain=False)
            logger.info(f"[{id_balanza}] EP publicado exitosamente (intento {attempt})")
            return
        except MqttError as e:
            logger.warning(f"[{id_balanza}] Error MQTT al publicar EP (intento {attempt}): {e}")
            if attempt == MAX_RETRIES:
                logger.error(f"[{id_balanza}] Fallo tras {MAX_RETRIES} intentos de publicar EP")
                raise HTTPException(502, f"Fallo al publicar en MQTT tras {MAX_RETRIES} intentos: {e}") from e
            await asyncio.sleep(RETRY_DELAY_S)
        except Exception as e:
            logger.exception(f"[{id_balanza}] Error inesperado al publicar EP")
            raise HTTPException(500, f"Error inesperado al publicar en MQTT: {e}") from e
