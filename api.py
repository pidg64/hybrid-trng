import os
import io
import json
import time
import base64
import random
import sqlite3
import uvicorn

from PIL import Image
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

from randcrack import RandCrack

# Motor de entropía refactorizado (defense in depth: físico + urandom).
# capture_hybrid_entropy alimenta el dashboard de auditoría visual;
# HybridSource alimenta la generación de tokens (físico secundario + urandom).
from trng.physical import capture_hybrid_entropy, capture_entropy
from trng.sources import HybridSource
from trng.frames import ENV_VIDEO_URL

app = FastAPI(title="Hybrid-TRNG API")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Fuente combinada global: mezcla físico + urandom con BLAKE2b y expande vía DRBG.
# Su fuente física usa el live feed (ver trng.frames.default_provider).
secure_source = HybridSource()

# --- CONFIGURACIÓN CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


db = get_db()


def init_db():
    cursor = db.cursor()
    cursor.execute('''
        CREATE TABLE users (
            username TEXT PRIMARY KEY,
            password TEXT,
            reset_token TEXT
        )
    ''')
    cursor.execute("INSERT INTO users (username, password) VALUES ('admin', 'SuperSecret123')")
    cursor.execute("INSERT INTO users (username, password) VALUES ('hacker', 'hackerpass')")
    db.commit()


init_db()


class ChangePasswordRequest(BaseModel):
    username: str
    token: str
    new_password: str


class LoginRequest(BaseModel):
    username: str
    password: str


def generate_secure_token():
    # Defense in depth: el token sale del pipeline híbrido
    # seed = BLAKE2b(fisico || urandom) -> CSPRNG (modo contador).
    # Si la física falla o está congelada, urandom sostiene la garantía
    # criptográfica (Leftover Hash Lemma); nunca hay degradación catastrófica.
    token_bytes = secure_source.get_bytes(32)  # 256 bits
    secure_token_int = int.from_bytes(token_bytes, "big")
    print(f"Token seguro generado (fuente: {secure_source.health().value})")
    return str(secure_token_int)


# 1. ENDPOINT VULNERABLE
@app.post("/request_reset/{username}")
def request_reset(username: str):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    token_int = random.getrandbits(256)
    token = str(token_int)
    cursor.execute("UPDATE users SET reset_token = ? WHERE username = ?", (token, username))
    db.commit()
    return {"message": f"Token enviado al email de {username}", "token": token}

# 2. ENDPOINT SEGURO (NUEVO)
@app.post("/secure_request_reset/{username}")
def secure_request_reset(username: str):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    # Usamos nuestra propia fuente de entropía
    token = generate_secure_token()
    cursor.execute("UPDATE users SET reset_token = ? WHERE username = ?", (token, username))
    db.commit()
    return {"message": f"Token seguro enviado al email de {username}", "token": token}

# 3. ENDPOINT PARA DASHBOARD
@app.get("/raw_entropy")
def get_raw_entropy():
    # Consumimos la misma función base de forma 100% honesta
    x, y, r, g, b = capture_hybrid_entropy()

    return {
        "x_coord": x,
        "y_coord": y,
        "r": r,
        "g": g,
        "b": b
    }

# ENDPOINTS COMUNES (Cambio de pass y Login)
@app.post("/change_password")
def change_password(data: ChangePasswordRequest):
    cursor = db.cursor()
    cursor.execute("SELECT reset_token FROM users WHERE username = ?", (data.username,))
    row = cursor.fetchone()
    if not row or row['reset_token'] is None or row['reset_token'] != data.token:
        raise HTTPException(status_code=403, detail="Token inválido o expirado")
    cursor.execute("UPDATE users SET password = ?, reset_token = NULL WHERE username = ?", (data.new_password, data.username))
    db.commit()
    return {"message": "Contraseña actualizada exitosamente"}

@app.post("/login")
def login(data: LoginRequest):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ? AND password = ?", (data.username, data.password))
    if cursor.fetchone():
        return {"message": f"Login exitoso. Bienvenido {data.username}!"}
    raise HTTPException(status_code=401, detail="Credenciales incorrectas")


# ============================================================================
#  DEMO / UI  —  pantallas didácticas para la presentación al jurado
# ============================================================================

@app.get("/")
@app.get("/demo")
def page_demo():
    """Pantalla 1: demo del ataque en tiempo real."""
    return FileResponse(os.path.join(BASE_DIR, "demo.html"))


@app.get("/backstage")
def page_backstage():
    """Pantalla 2: funcionamiento de fondo (frame + píxeles que elige qiskit)."""
    return FileResponse(os.path.join(BASE_DIR, "backstage.html"))


@app.get("/feed_url")
def feed_url():
    """URL del live feed para que el navegador muestre la cámara.

    OJO: para la demo devolvemos la URL tal cual (puede incluir token). Es un
    servidor local de presentación, no producción.
    """
    return {"url": ENV_VIDEO_URL}


def _reset_demo_state():
    """Deja a 'admin' en estado inicial para poder repetir la demo."""
    cur = db.cursor()
    cur.execute("UPDATE users SET password='SuperSecret123', reset_token=NULL WHERE username='admin'")
    db.commit()


def _token_chunks(token_256: int):
    """Parte un token de 256 bits en 8 enteros de 32 bits (orden del MT)."""
    return [(token_256 >> (32 * i)) & 0xFFFFFFFF for i in range(8)]


def _attack_events(target: str):
    """Generador del ataque en vivo (NDJSON). Self-contained: no hace HTTP a sí
    mismo, opera directo sobre el RNG y la 'base de datos'.

    target='vulnerable' -> tokens de random.getrandbits (Mersenne Twister) -> PWNED.
    target='secure'     -> tokens de HybridSource (LavaRand)               -> MITIGADO.
    """
    secure = (target == "secure")

    def ev(level, msg, **extra):
        return json.dumps({"level": level, "msg": msg, **extra}) + "\n"

    label = ("SEGURO — LavaRand: cámara + cuántico + urandom (BLAKE2b)"
             if secure else
             "VULNERABLE — random.getrandbits() → Mersenne Twister")

    _reset_demo_state()
    yield ev("title", f"OBJETIVO: endpoint {label}")
    yield ev("info", "Estado reseteado: admin / SuperSecret123.")
    time.sleep(0.25)

    yield ev("phase", "FASE 1 · Recolección — el atacante pide 78 tokens de reseteo (son públicos).")
    yield ev("dim", "   78 tokens × 8 bloques de 32 bits = 624 enteros (lo que RandCrack necesita).")
    rc = RandCrack()
    ingest_failed = False
    for i in range(78):
        token = int(generate_secure_token()) if secure else random.getrandbits(256)
        for chunk in _token_chunks(token):
            try:
                rc.submit(chunk)
            except ValueError as e:
                yield ev("warn", f"   RandCrack rechazó un valor: {e}")
                ingest_failed = True
                break
        if ingest_failed:
            break
        if (i + 1) % 13 == 0 or (i + 1) == 78:
            yield ev("info", f"   recolectados {i + 1}/78 tokens…")

    if ingest_failed:
        yield ev("good", "🛡️  MITIGADO: la salida ni siquiera tiene la forma esperada por el predictor.")
        yield ev("done", "Ataque fallido.", success=False, target=target)
        return

    time.sleep(0.3)
    yield ev("phase", "FASE 2 · Análisis — alimentando RandCrack (clonador del Mersenne Twister).")
    time.sleep(0.4)
    if secure:
        yield ev("info", "   RandCrack ingirió los 624 valores, pero el estado del MT no sincroniza con ruido real.")
    else:
        yield ev("crit", "   ✓ Estado interno del MT19937 RECONSTRUIDO. El generador quedó clonado.")

    time.sleep(0.3)
    yield ev("phase", "FASE 3 · Predicción — calculando el PRÓXIMO token de reseteo.")
    try:
        predicted = 0
        for i in range(8):
            predicted |= rc.predict_getrandbits(32) << (32 * i)
    except Exception as e:
        yield ev("good", f"🛡️  MITIGADO: sin estructura no hay predicción posible ({e}).")
        yield ev("done", "Ataque fallido.", success=False, target=target)
        return
    yield ev("dim", f"   token predicho: {str(predicted)[:46]}…")

    time.sleep(0.3)
    yield ev("phase", "FASE 4 · Secuestro — disparo el reseteo REAL de 'admin' y uso el token predicho.")
    actual = int(generate_secure_token()) if secure else random.getrandbits(256)
    cur = db.cursor()
    cur.execute("UPDATE users SET reset_token=? WHERE username='admin'", (str(actual),))
    db.commit()
    cur.execute("SELECT reset_token FROM users WHERE username='admin'")
    real_token = cur.fetchone()["reset_token"]
    time.sleep(0.3)

    if str(predicted) == real_token:
        cur.execute("UPDATE users SET password='pwned_by_lavarand', reset_token=NULL WHERE username='admin'")
        db.commit()
        yield ev("crit", "🔓  PWNED: el token predicho COINCIDIÓ. Cambié la contraseña de admin.")
        time.sleep(0.25)
        yield ev("crit", "🔓  Login como admin EXITOSO → cuenta secuestrada por completo.")
        yield ev("done", "Cuenta comprometida.", success=True, target=target)
    else:
        yield ev("good", "   ✗ El token predicho NO coincide con el real.")
        yield ev("dim", f"   predicho …{str(predicted)[-14:]}  ≠  real …{real_token[-14:]}")
        yield ev("good", "🛡️  MITIGADO: el token sale de BLAKE2b(cámara‖cuántico‖urandom). No hay patrón que clonar.")
        yield ev("done", "Ataque fallido.", success=False, target=target)


@app.get("/demo/attack")
def demo_attack(target: str = "vulnerable"):
    if target not in ("vulnerable", "secure"):
        raise HTTPException(status_code=400, detail="target debe ser 'vulnerable' o 'secure'")
    return StreamingResponse(_attack_events(target), media_type="application/x-ndjson")


def _analyze_provider(provider, n: int):
    """Toma el frame actual del provider y muestra qué píxeles eligió qiskit."""
    img, w, h, px = provider.get_frame()

    samples = []
    for _ in range(n):
        x, y, r, g, b = capture_entropy(px, w, h)
        raw = px[x, y]
        samples.append({
            "x": x, "y": y,
            "raw": [raw[0], raw[1], raw[2]],   # píxel crudo del frame
            "white": [r, g, b],                # tras whitening cuántico (XOR)
        })

    # Frame para mostrar (downscale para que pese poco).
    disp = img.copy()
    disp.thumbnail((640, 640))
    buf = io.BytesIO()
    disp.save(buf, "JPEG", quality=80)
    frame_b64 = base64.b64encode(buf.getvalue()).decode()

    # Ventana de píxeles alrededor del ÚLTIMO elegido (magnificada, nearest).
    lx, ly = samples[-1]["x"], samples[-1]["y"]
    s = 24
    left, top = max(0, lx - s), max(0, ly - s)
    right, bottom = min(w, lx + s + 1), min(h, ly + s + 1)
    crop = img.crop((left, top, right, bottom)).resize((260, 260), Image.NEAREST)
    zbuf = io.BytesIO()
    crop.save(zbuf, "JPEG", quality=85)
    zoom_b64 = base64.b64encode(zbuf.getvalue()).decode()

    return {
        "connected": bool(getattr(provider, "connected", True)),
        "frame": frame_b64,
        "orig_width": w, "orig_height": h,
        "disp_width": disp.width, "disp_height": disp.height,
        "samples": samples,
        "zoom": zoom_b64,
        "zoom_box": {"x": left, "y": top, "w": right - left, "h": bottom - top},
        "health": secure_source.health().value,
    }


@app.get("/demo/analyze")
def demo_analyze(n: int = 6):
    """Frame que se está analizando + píxeles que qiskit eligió + ventana ampliada."""
    provider = secure_source.physical._provider
    try:
        return _analyze_provider(provider, n)
    except Exception as e:
        return {"connected": False, "error": str(e), "health": secure_source.health().value}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
