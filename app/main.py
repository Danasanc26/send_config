import os
import asyncio
import datetime as dt
from uuid import uuid4
from typing import List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from asyncio_mqtt import Client, MqttError

from app.utils.log import get_logger
from app.utils.timezone import now_local_iso
from app.schemas import EPRequest, EPBatchRequest, EPAck
from app.services.ep import send_ep_cmd, wait_ep_ack, TIMEOUT_S

# Carga .env ANTES de leer variables o importar servicios que lean env
load_dotenv()

logger = get_logger("app_logger")

# ───────────── Configuración desde ENV ─────────────
MQTT_HOST  = os.getenv("MQTT_HOST")
MQTT_PORT  = int(os.getenv("MQTT_PORT", "1883"))
CLIENT_ID  = os.getenv("MQTT_CLIENT_ID", "api_config_ep")

MONGO_URI  = os.getenv("MONGO_URI")
MONGO_DB   = os.getenv("MONGO_DB", "test_db")
COL_EP     = os.getenv("MONGO_COLLECTION", "EP_REGISTER")
COL_ACK    = os.getenv("MONGO_ACK_COLLECTION", "ACK_REGISTER")

# Concurrencia para /ep/batch
CONCURRENCY_LIMIT = int(os.getenv("CONCURRENCY_LIMIT", "5"))
ACK_TIMEOUT_MULT  = float(os.getenv("ACK_TIMEOUT_MULT", "1"))

# ───────────── App + MQTT + Mongo (lifespan) ─────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    if not MQTT_HOST:
        raise RuntimeError("MQTT_HOST no definido")
    if not MONGO_URI:
        raise RuntimeError("MONGO_URI no definido")

    logger.info(f"Iniciando API -- conectando MQTT a {MQTT_HOST}:{MQTT_PORT} (client_id={CLIENT_ID})")
    mqtt_client = Client(MQTT_HOST, port=MQTT_PORT, client_id=CLIENT_ID)

    last_exc = None
    for attempt in range(1, 4):
        try:
            await mqtt_client.connect()
            logger.info(f"Conexion MQTT exitosa en intento {attempt}")
            app.state.mqtt = mqtt_client
            break
        except MqttError as e:
            last_exc = e
            logger.warning(f"Fallo de conexión MQTT (intento {attempt}/3): {e}")
            if attempt < 3:
                await asyncio.sleep(2.0)
    else:
        logger.error("No fue posible conectar al broker MQTT en el arranque")
        raise HTTPException(status_code=503, detail=f"MQTT no disponible: {last_exc}")

    mongo_client = AsyncIOMotorClient(MONGO_URI)
    app.state.mongo_db = mongo_client[MONGO_DB]
    logger.info(f"Conectado a MongoDB {MONGO_URI} -> DB '{MONGO_DB}'")

    try:
        yield
    finally:
        mongo_client.close()
        try:
            await mqtt_client.disconnect()
            logger.info("Desconectado de MQTT (shutdown)")
        except Exception as e:
            logger.warning(f"Error al desconectar MQTT en shutdown: {e}")

app = FastAPI(title="API Config Doran (EP) — asyncio-mqtt", lifespan=lifespan)

# ───────────── Utilidades Mongo ─────────────
async def mongo_insert(collection, doc: dict):
    try:
        result = await collection.insert_one(doc)
        logger.debug(f"Mongo insert _id={result.inserted_id}")
    except Exception:
        logger.exception("Error insertando en Mongo")
        raise

# ───────────── Endpoints ─────────────
@app.get("/health")
async def health():
    return {"status": "ok", "mqtt_host": MQTT_HOST, "mqtt_port": MQTT_PORT}

@app.post("/ep", response_model=EPAck, status_code=202)
async def ep_single(req: EPRequest, request: Request):
    db   = request.app.state.mongo_db
    mqtt = request.app.state.mqtt

    # Guarda la solicitud original
    doc_ep = req.model_dump(mode="json") | {"timestamp": now_local_iso()}
    await mongo_insert(db[COL_EP], doc_ep)

    rid = str(req.rid or uuid4())
    logger.info(f"[{req.id}] /ep recibido (rid={rid}) prod_id={req.payload.prod_id}")

    await send_ep_cmd(req.id, rid, req.payload, mqtt)
    ack = await wait_ep_ack(req.id, rid, mqtt)
    data = ack.get("data", {})

    resp = EPAck(
        id_balanza=req.id,
        rid=rid,
        ack=data.get("ack", ""),
        sent_len=data.get("sent_len"),
        sent_hex=data.get("sent_hex"),
        updated_at=dt.datetime.utcnow().isoformat() + "Z"
    )
    logger.info(f"[{req.id}] /ep completado (rid={rid}) ack={resp.ack}")
    await mongo_insert(db[COL_ACK], resp.model_dump(mode="json"))

    return resp

@app.post("/ep/batch")
async def ep_batch(req: EPBatchRequest, request: Request):
    """
    Parametriza VARIAS balanzas en paralelo y registra:
      • Solicitud original  → EP_REGISTER
      • Cada ACK recibido   → ACK_REGISTER
    """
    db   = request.app.state.mongo_db
    mqtt = request.app.state.mqtt

    # Guarda la solicitud original
    doc_ep = req.model_dump(mode="json") | {"timestamp": now_local_iso()}
    await mongo_insert(db[COL_EP], doc_ep)

    op_rid = str(req.rid or uuid4())
    ids = list(dict.fromkeys([i.strip() for i in req.ids if i and i.strip()]))

    logger.info(f"[BATCH] op_rid={op_rid}  ids={len(ids)}  concurr={CONCURRENCY_LIMIT}")

    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)
    results:  List[Dict[str, Any]] = []
    missing:  List[str]            = []
    ack_docs: List[Dict[str, Any]] = []

    async def one_balance(bal_id: str):
        try:
            async with sem:
                await send_ep_cmd(bal_id, op_rid, req.payload, mqtt)
                # Timeout externo por tarea (además del interno en wait_ep_ack)
                ack = await asyncio.wait_for(
                    wait_ep_ack(bal_id, op_rid, mqtt),
                    timeout=TIMEOUT_S * ACK_TIMEOUT_MULT
                )
            data = ack.get("data", {})
            item = {
                "id_balanza": bal_id,
                "ack":        data.get("ack", ""),
                "sent_len":   data.get("sent_len"),
                "sent_hex":   data.get("sent_hex"),
                "timestamp":  now_local_iso()
            }
            results.append(item)
            ack_docs.append({**item, "rid": op_rid})
            logger.info(f"[BATCH][{bal_id}] OK (rid={op_rid})")
        except HTTPException as e:
            missing.append(bal_id)
            logger.warning(f"[BATCH][{bal_id}] FALLÓ (rid={op_rid}) status={e.status_code} detail={e.detail}")
        except Exception as e:
            missing.append(bal_id)
            logger.warning(f"[BATCH][{bal_id}] FALLÓ (rid={op_rid}) error={e}")

    await asyncio.gather(*(one_balance(bid) for bid in ids))

    if ack_docs:
        try:
            await db[COL_ACK].insert_many(ack_docs, ordered=False)
            logger.debug(f"[BATCH] Insertados {len(ack_docs)} ACKs en Mongo")
        except Exception as e:
            logger.error(f"[BATCH] Error al insertar ACKs en Mongo: {e}")

    resp = {
        "statusCode": 200,
        "rid": op_rid,
        "message": "parametrizacion",
        "data": {
            "total": len(results),
            "list": results,
            "missing_ids": missing
        },
        "echo": {"payload": req.payload.model_dump(mode="json")}
    }
    logger.info(f"[BATCH] Fin op_rid={op_rid} ok={len(results)} fail={len(missing)}")
    return resp
