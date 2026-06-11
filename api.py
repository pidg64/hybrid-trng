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
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from randcrack import RandCrack

# Motor de entropía (defense in depth: físico + urandom).
# capture_hybrid_entropy alimenta el dashboard de entropía cruda;
# get_quantum_random_int es el motor cuántico (Hadamard) que usa la pantalla de fondo;
# HybridSource alimenta la generación de tokens.
from trng.physical import capture_hybrid_entropy, get_quantum_random_int
from trng.sources import HybridSource
from trng.frames import ENV_VIDEO_URL

# PQC: ML-KEM-768 (FIPS 203) en puro Python, sembrado con entropía de MAELSTROM.
from kyber_py.ml_kem import ML_KEM_768

app = FastAPI(title="MAELSTROM API")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(BASE_DIR, "ui")

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
        raise HTTPException(status_code=404, detail="User not found")
    token_int = random.getrandbits(256)
    token = str(token_int)
    cursor.execute("UPDATE users SET reset_token = ? WHERE username = ?", (token, username))
    db.commit()
    return {"message": f"Token sent to {username}'s email", "token": token}

# 2. ENDPOINT SEGURO
@app.post("/secure_request_reset/{username}")
def secure_request_reset(username: str):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="User not found")
    token = generate_secure_token()
    cursor.execute("UPDATE users SET reset_token = ? WHERE username = ?", (token, username))
    db.commit()
    return {"message": f"Secure token sent to {username}'s email", "token": token}

# 3. ENDPOINT PARA DASHBOARD DE ENTROPÍA CRUDA
@app.get("/raw_entropy")
def get_raw_entropy():
    x, y, r, g, b = capture_hybrid_entropy()
    return {"x_coord": x, "y_coord": y, "r": r, "g": g, "b": b}

# ENDPOINTS COMUNES (Cambio de pass y Login)
@app.post("/change_password")
def change_password(data: ChangePasswordRequest):
    cursor = db.cursor()
    cursor.execute("SELECT reset_token FROM users WHERE username = ?", (data.username,))
    row = cursor.fetchone()
    if not row or row['reset_token'] is None or row['reset_token'] != data.token:
        raise HTTPException(status_code=403, detail="Invalid or expired token")
    cursor.execute("UPDATE users SET password = ?, reset_token = NULL WHERE username = ?", (data.new_password, data.username))
    db.commit()
    return {"message": "Password updated successfully"}

@app.post("/login")
def login(data: LoginRequest):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ? AND password = ?", (data.username, data.password))
    if cursor.fetchone():
        return {"message": f"Login successful. Welcome {data.username}!"}
    raise HTTPException(status_code=401, detail="Invalid credentials")


# ============================================================================
#  RUTAS DE LA UI  —  recorrido del usuario (todo vive en ui/)
# ============================================================================

@app.get("/")
def page_home():
    return RedirectResponse("/ui/index.html")

@app.get("/backstage")
def page_backstage():
    return RedirectResponse("/ui/backstage.html")

@app.get("/demo")
def page_demo():
    return RedirectResponse("/ui/demo.html")

@app.get("/brainrot")
def page_brainrot():
    return RedirectResponse("/ui/brainrot.html")


@app.get("/pqc")
def page_pqc():
    return RedirectResponse("/ui/pqc.html")


@app.get("/feed_url")
def feed_url():
    """URL del live feed para que el navegador muestre la cámara.
    Para la demo devolvemos la URL tal cual (servidor local de presentación)."""
    return {"url": ENV_VIDEO_URL}


# ============================================================================
#  DEMO DEL ATAQUE  (stream NDJSON en vivo)
# ============================================================================

def _reset_demo_state():
    cur = db.cursor()
    cur.execute("UPDATE users SET password='SuperSecret123', reset_token=NULL WHERE username='admin'")
    db.commit()


def _token_chunks(token_256: int):
    return [(token_256 >> (32 * i)) & 0xFFFFFFFF for i in range(8)]


def _attack_events(target: str):
    """Live attack (NDJSON). Self-contained: operates directly on the RNG and the 'db'.
    target='vulnerable' -> random.getrandbits (Mersenne Twister) -> PWNED.
    target='secure'     -> HybridSource (MAELSTROM)              -> MITIGATED.
    """
    secure = (target == "secure")

    def ev(level, msg, **extra):
        return json.dumps({"level": level, "msg": msg, **extra}) + "\n"

    label = ("SECURE — MAELSTROM: camera + quantum + urandom (BLAKE2b)"
             if secure else
             "VULNERABLE — random.getrandbits() → Mersenne Twister")

    _reset_demo_state()
    yield ev("title", f"TARGET: {label} endpoint")
    yield ev("info", "State reset: admin / SuperSecret123.")
    time.sleep(0.25)

    yield ev("phase", "PHASE 1 · Collection — the attacker requests 78 reset tokens (they're public).")
    yield ev("dim", "   78 tokens × 8 blocks of 32 bits = 624 integers (what RandCrack needs).")
    rc = RandCrack()
    ingest_failed = False
    for i in range(78):
        token = int(generate_secure_token()) if secure else random.getrandbits(256)
        for chunk in _token_chunks(token):
            try:
                rc.submit(chunk)
            except ValueError as e:
                yield ev("warn", f"   RandCrack rejected a value: {e}")
                ingest_failed = True
                break
        if ingest_failed:
            break
        if (i + 1) % 13 == 0 or (i + 1) == 78:
            yield ev("info", f"   collected {i + 1}/78 tokens…")

    if ingest_failed:
        yield ev("good", "🛡️  MITIGATED: the output doesn't even have the shape the predictor expects.")
        yield ev("done", "Attack failed.", success=False, target=target)
        return

    time.sleep(0.3)
    yield ev("phase", "PHASE 2 · Analysis — feeding RandCrack (the Mersenne Twister cloner).")
    time.sleep(0.4)
    if secure:
        yield ev("info", "   RandCrack ingested the 624 values, but the MT state won't sync with real noise.")
    else:
        yield ev("crit", "   ✓ MT19937 internal state RECONSTRUCTED. The generator is now cloned.")

    time.sleep(0.3)
    yield ev("phase", "PHASE 3 · Prediction — computing the NEXT reset token.")
    try:
        predicted = 0
        for i in range(8):
            predicted |= rc.predict_getrandbits(32) << (32 * i)
    except Exception as e:
        yield ev("good", f"🛡️  MITIGATED: no structure means no prediction is possible ({e}).")
        yield ev("done", "Attack failed.", success=False, target=target)
        return
    yield ev("dim", f"   predicted token: {str(predicted)[:46]}…")

    time.sleep(0.3)
    yield ev("phase", "PHASE 4 · Takeover — firing the REAL 'admin' reset and using the predicted token.")
    actual = int(generate_secure_token()) if secure else random.getrandbits(256)
    cur = db.cursor()
    cur.execute("UPDATE users SET reset_token=? WHERE username='admin'", (str(actual),))
    db.commit()
    cur.execute("SELECT reset_token FROM users WHERE username='admin'")
    real_token = cur.fetchone()["reset_token"]
    time.sleep(0.3)

    if str(predicted) == real_token:
        cur.execute("UPDATE users SET password='pwned_by_maelstrom', reset_token=NULL WHERE username='admin'")
        db.commit()
        yield ev("crit", "🔓  PWNED: the predicted token MATCHED. Changed admin's password.")
        time.sleep(0.25)
        yield ev("crit", "🔓  Logged in as admin SUCCESSFULLY → account fully hijacked.")
        yield ev("done", "Account compromised.", success=True, target=target)
    else:
        yield ev("good", "   ✗ The predicted token does NOT match the real one.")
        yield ev("dim", f"   predicted …{str(predicted)[-14:]}  ≠  real …{real_token[-14:]}")
        yield ev("good", "🛡️  MITIGATED: the token comes from BLAKE2b(camera‖quantum‖urandom). No pattern to clone.")
        yield ev("done", "Attack failed.", success=False, target=target)


@app.get("/demo/attack")
def demo_attack(target: str = "vulnerable"):
    if target not in ("vulnerable", "secure"):
        raise HTTPException(status_code=400, detail="target must be 'vulnerable' or 'secure'")
    return StreamingResponse(_attack_events(target), media_type="application/x-ndjson")


# ============================================================================
#  PANTALLA DE FONDO  —  selección cuántica por grilla (fiel a seleccion_qiskit.py)
# ============================================================================
#  Sobre el frame actual: tomo una VENTANA CHICA Y ALEATORIA de GRID×GRID píxeles
#  (así cada celda de la grilla = 1 píxel real), y qiskit (Hadamard sobre QUBITS
#  qubits) elige N_SELECT índices con Rejection Sampling sin reposición. Para cada
#  celda elegida muestro el pixel crudo, la máscara cuántica y el XOR (whitening).
#  NOTA: esto es solo la visualización didáctica; el motor de tokens no cambia.

GRID = 30                 # 30x30 = 900 celdas; cada celda = 1 píxel real de la ventana
N_SELECT = 32             # cuántas celdas elige qiskit
QUBITS = 10               # 2^10 = 1024 >= 900
MAX_INDEX = GRID * GRID   # 900
MAX_ROLLS = 400           # tope de tiradas (incluye rechazos) para no colgarse
_viz_rng = random.Random()  # RNG propio para sortear la posición de la ventana
                            # (NO toca el random global que usa el ataque)


def _b64_jpeg(im, quality=82):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def _grid_select(provider):
    img, w, h, px = provider.get_frame()

    # Ventana CHICA y ALEATORIA del frame (la "partecita"): GRID×GRID píxeles,
    # así cada celda de la grilla mapea a 1 píxel real. La posición se sortea con
    # un RNG propio (no el global) para no interferir con el ataque.
    win = min(GRID, w, h)
    cx0 = _viz_rng.randint(0, max(0, w - win))
    cy0 = _viz_rng.randint(0, max(0, h - win))
    cell = win / GRID

    rolls, selected, seen = [], [], set()
    n_rolls = 0
    while len(selected) < N_SELECT and n_rolls < MAX_ROLLS:
        n_rolls += 1
        idx = get_quantum_random_int(num_bits=QUBITS)   # medición cuántica real (Hadamard)
        bits = format(idx, f"0{QUBITS}b")
        if idx >= MAX_INDEX:
            rolls.append({"bits": bits, "index": idx, "ok": False, "reason": "out of range"})
            continue
        if idx in seen:
            rolls.append({"bits": bits, "index": idx, "ok": False, "reason": "repeat"})
            continue
        seen.add(idx)
        rolls.append({"bits": bits, "index": idx, "ok": True, "reason": ""})
        fila, col = idx // GRID, idx % GRID
        sx = min(w - 1, int(cx0 + (col + 0.5) * cell))
        sy = min(h - 1, int(cy0 + (fila + 0.5) * cell))
        raw = px[sx, sy]
        mask = (get_quantum_random_int(num_bits=8),
                get_quantum_random_int(num_bits=8),
                get_quantum_random_int(num_bits=8))
        white = (raw[0] ^ mask[0], raw[1] ^ mask[1], raw[2] ^ mask[2])
        selected.append({
            "index": idx, "fila": fila, "col": col, "x": sx, "y": sy,
            "raw": [raw[0], raw[1], raw[2]],
            "mask": list(mask),
            "white": list(white),
        })

    disp = img.copy(); disp.thumbnail((560, 560))
    crop = img.crop((cx0, cy0, cx0 + win, cy0 + win)).resize((420, 420), Image.NEAREST)

    return {
        "connected": bool(getattr(provider, "connected", True)),
        "frame": _b64_jpeg(disp),
        "orig_width": w, "orig_height": h,
        "disp_width": disp.width, "disp_height": disp.height,
        "crop_box": {"x": cx0, "y": cy0, "w": win, "h": win},
        "crop": _b64_jpeg(crop),
        "grid": GRID, "n_select": N_SELECT, "qubits": QUBITS, "max_index": MAX_INDEX,
        "rolls": rolls,
        "selected": selected,
        "health": secure_source.health().value,
    }


@app.get("/demo/quantum_select")
def demo_quantum_select():
    """Frame + crop + grilla 30×30 + 32 píxeles que eligió qiskit (Hadamard) + XOR."""
    provider = secure_source.physical._provider
    try:
        return _grid_select(provider)
    except Exception as e:
        return {"connected": False, "error": str(e), "health": secure_source.health().value}


# ============================================================================
#  PQC  —  ML-KEM-768 (FIPS 203) sembrado con entropía de MAELSTROM
# ============================================================================
#  Punto clave: PQC arregla el ALGORITMO (resistente a Shor), pero igual necesita
#  un RNG fuerte para generar las claves. Acá la clave nace de los bytes de
#  MAELSTROM: seed (64B) -> _keygen_internal(d, z); después un round-trip
#  encaps/decaps prueba que el shared secret coincide en ambos lados.

def _pqc_handshake(seed64: bytes, m32: bytes):
    d, z = seed64[:32], seed64[32:64]
    ek, dk = ML_KEM_768._keygen_internal(d, z)            # clave pública + privada
    ss_send, ct = ML_KEM_768._encaps_internal(ek, m32)    # emisor: secreto + ciphertext
    ss_recv = ML_KEM_768.decaps(dk, ct)                   # receptor recupera el secreto
    return {
        "algo": "ML-KEM-768 (NIST FIPS 203)",
        "seed_hex": seed64.hex(),
        "ek_hex": ek.hex(), "ek_size": len(ek),
        "dk_size": len(dk),
        "ct_hex": ct.hex(), "ct_size": len(ct),
        "ss_send": ss_send.hex(), "ss_recv": ss_recv.hex(), "ss_size": len(ss_send),
        "match": ss_send == ss_recv,
    }


@app.get("/demo/pqc_keygen")
def demo_pqc_keygen():
    """Genera un par ML-KEM-768 sembrado por MAELSTROM y prueba un encaps/decaps."""
    seed = secure_source.get_bytes(64)   # entropía MAELSTROM -> seed de keygen (d||z)
    m = secure_source.get_bytes(32)      # entropía MAELSTROM -> randomness de encaps
    data = _pqc_handshake(seed, m)
    data["health"] = secure_source.health().value
    return data


# Servir la UI estática (debe ir al final, después de las rutas de la API).
app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
