import random
import sqlite3
import hashlib
import uvicorn

from PIL import Image
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# Importaciones Cuánticas
from qiskit_aer import AerSimulator
from qiskit import QuantumCircuit, transpile

app = FastAPI(title="Hybrid-TRNG API")

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


def get_quantum_random_int(num_bits=12):
    qc = QuantumCircuit(num_bits, num_bits)
    qc.h(range(num_bits))
    qc.measure(range(num_bits), range(num_bits))
    
    simulator = AerSimulator()
    compiled_circuit = transpile(qc, simulator)
    job = simulator.run(compiled_circuit, shots=1)
    
    binary_string = list(job.result().get_counts().keys())[0]
    return int(binary_string, 2)


def capture_hybrid_entropy(image_path="frame.png"):
    try:
        img = Image.open(image_path).convert("RGB")
        width, height = img.size
    except FileNotFoundError:
        width, height = 800, 600
        img = Image.new('RGB', (width, height), color='black')
    # 1. Entropía para Coordenadas
    raw_x = get_quantum_random_int(num_bits=12)
    raw_y = get_quantum_random_int(num_bits=12)
    x = raw_x % width
    y = raw_y % height
    pixel_color = img.getpixel((x, y))
    # 2. Entropía para Blanqueamiento
    color_mask = get_quantum_random_int(num_bits=24)
    mask_r = (color_mask >> 16) & 0xFF
    mask_g = (color_mask >> 8) & 0xFF
    mask_b = color_mask & 0xFF
    # 3. Blanqueamiento XOR
    r = pixel_color[0] ^ mask_r
    g = pixel_color[1] ^ mask_g
    b = pixel_color[2] ^ mask_b    
    return x, y, r, g, b


def generate_secure_token():
    # Consumimos la función base
    x, y, r, g, b = capture_hybrid_entropy()
    raw_data = f"X:{x}-Y:{y}-R:{r}-G:{g}-B:{b}"
    secure_seed_hex = hashlib.sha256(raw_data.encode('utf-8')).hexdigest()
    secure_token_int = int(secure_seed_hex, 16)
    print(f"Entropía capturada -> Coordenadas: ({x}, {y}) | RGB blanqueado: ({r}, {g}, {b})")
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