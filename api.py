import random
import sqlite3
import uvicorn

from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# Motor de entropía refactorizado (defense in depth: físico + urandom).
# capture_hybrid_entropy alimenta el dashboard de auditoría visual;
# HybridSource alimenta la generación de tokens (físico secundario + urandom).
from trng.physical import capture_hybrid_entropy
from trng.sources import HybridSource

app = FastAPI(title="Hybrid-TRNG API")

# Fuente combinada global: mezcla físico + urandom con BLAKE2b y expande vía DRBG.
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

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)