# Cómo se usa qiskit en MAELSTROM

Este documento explica **qué hace qiskit** en el proyecto y **cómo se integra** al
código. Spoiler: qiskit es el motor que produce los **bits aleatorios cuánticos**
que se usan para (1) elegir qué píxeles del frame mirar y (2) blanquear esos
píxeles. No genera el token entero — es una de las fuentes de entropía física.

---

## ⚠️ Honestidad primero: es un SIMULADOR

qiskit acá corre sobre **`AerSimulator`** ([qiskit-aer](https://github.com/Qiskit/qiskit-aer)),
que **simula** un circuito cuántico en tu CPU clásica. **No** es una QPU real.

- La aleatoriedad que sale es estadísticamente correcta (reproduce las
  probabilidades cuánticas), pero el azar de fondo lo aporta el RNG del simulador,
  no un fenómeno cuántico real.
- Para la **seguridad** del sistema esto no importa: el token final mezcla esta
  fuente con `os.urandom` vía BLAKE2b, y por el *Leftover Hash Lemma* el resultado
  es tan fuerte como urandom aunque la parte "cuántica" fuera floja.
- Para la **demo/narrativa**: decí "aleatoriedad cuántica **simulada**", no
  "entropía de hardware cuántico". Más abajo está cómo pasarlo a una QPU real.

---

## El circuito cuántico (Hadamard → medición)

Toda la magia está en un circuito mínimo: **N qubits, una compuerta Hadamard en
cada uno, y medición**.

```
            ┌───┐┌─┐
q_0: ──|0>──┤ H ├┤M├      H lleva |0>  ->  (|0> + |1>)/√2
            ├───┤└╥┘       medir  ->  0 ó 1 con prob. 50/50
q_1: ──|0>──┤ H ├─╫─┤M├
            └───┘ ║ └╥┘    N qubits independientes  ->  entero uniforme
                  ▼  ▼         en [0, 2^N - 1]
                 bits medidos
```

- `|0> --H--> (|0>+|1>)/√2`: la Hadamard pone cada qubit en **superposición
  pareja**. Al medir, colapsa a `0` o `1` con probabilidad 50/50.
- N qubits independientes → un número **uniforme** entre `0` y `2^N − 1`.
- Eso es exactamente lo que necesitamos como fuente de bits aleatorios crudos.

---

## El motor: `get_quantum_random_int()`

Todo qiskit vive en un solo lugar: [`trng/physical.py`](trng/physical.py).

```python
from qiskit_aer import AerSimulator
from qiskit import QuantumCircuit, transpile

_simulator = AerSimulator()              # 1 sola instancia global
_compiled_circuits: dict[int, object] = {}  # cache de circuitos transpilados

def _get_compiled_circuit(num_bits):
    qc = QuantumCircuit(num_bits, num_bits)
    qc.h(range(num_bits))                # Hadamard en TODOS los qubits
    qc.measure(range(num_bits), range(num_bits))
    return transpile(qc, _simulator)     # se transpila una vez por tamaño

def get_quantum_random_int(num_bits=12):
    compiled = _get_compiled_circuit(num_bits)
    job = _simulator.run(compiled, shots=1, memory=True)
    binary_string = job.result().get_memory()[0]   # ej. "0110101011"
    return int(binary_string, 2)                    # -> entero en [0, 2^num_bits)
```

`get_quantum_random_int(n)` = "tirá una moneda cuántica `n` veces y devolveme el
número". Es la **única** función que toca qiskit; todo lo demás la llama.

### Performance (no cambia la entropía)
- **Simulador único** (`_simulator` global): no se re-instancia por tirada.
- **Circuito cacheado por tamaño**: `transpile()` es caro (corre todo el pass
  manager); se hace **una vez por cantidad de qubits** y se reusa. Antes se
  transpilaba en cada medición → era la causa de la lentitud y del spam de logs.

---

## Dónde se integra (3 usos)

### 1. Elegir el píxel: coordenadas con *Rejection Sampling*
En [`capture_entropy`](trng/physical.py) (el pipeline real de tokens), qiskit
elige las coordenadas `(x, y)` del píxel a leer:

```python
bits_x = width.bit_length()            # cuántos qubits para cubrir el ancho
while True:
    x = get_quantum_random_int(num_bits=bits_x)
    if x < width:                      # si cae fuera, se descarta y re-tira
        break
```

El **rejection sampling** (descartar y re-tirar si `x ≥ width`) evita el
**modulo bias**: si hiciéramos `x % width` los primeros valores saldrían más
seguido. Así la coordenada es **uniforme de verdad**.

### 2. Blanquear el píxel: máscara cuántica + XOR
El píxel crudo `(R,G,B)` tiene sesgo (hay colores más comunes). qiskit genera
**3 máscaras de 8 bits** y se XOR-ean con cada canal → **ruido blanco**:

```python
mask_r = get_quantum_random_int(num_bits=8)   # 0..255 cuántico
# ...
r = pixel_color[0] ^ mask_r                   # whitening (anti histogram bias)
```

Estos `r, g, b` blanqueados son los **bytes de entropía física** que produce la
fuente.

### 3. La pantalla de fondo (grilla 30×30)
En [`api.py`](api.py) (`_grid_select`, lo que muestra `/backstage`) se usa el
mismo motor para la visualización didáctica: una grilla de 30×30 = 900 celdas,
y qiskit elige 32 con **10 qubits** (`2^10 = 1024 ≥ 900`) + rejection sampling:

```python
idx = get_quantum_random_int(num_bits=10)   # 0..1023
# si idx >= 900 o ya salió -> se descarta (rejection sampling sin reposición)
```

(Esto es **solo visualización**; el motor de tokens no cambió.)

---

## Cómo encaja en el pipeline completo

```
qiskit (Hadamard+medición)                         os.urandom
        │                                               │
        ├─ coords (x,y) por rejection sampling          │
        ├─ máscaras de 8 bits ─┐                         │
        ▼                      ▼                         │
   pixel del frame  ──XOR──►  bytes físicos (R,G,B)      │
                                   │                     │
                                   ▼                     ▼
                          mix = BLAKE2b( físico ‖ urandom )   ← trng/mixer.py
                                   │
                                   ▼
                          CounterCSPRNG(seed)  ← trng/csprng.py
                                   │
                                   ▼
                          token de 256 bits
```

qiskit aporta la parte **física/cuántica**; `urandom` la parte del kernel; BLAKE2b
las funde en un seed. La física es **secundaria aditiva**: si fuera mala, urandom
sostiene la garantía.

---

## Mapa de archivos

| Archivo | Rol de qiskit |
|---|---|
| [`trng/physical.py`](trng/physical.py) | **Motor**: circuito Hadamard, simulador, cache, `get_quantum_random_int`. Usos: coords + máscaras. |
| [`api.py`](api.py) | `_grid_select` usa `get_quantum_random_int(10)` para la grilla de `/backstage`. |
| [`trng/sources.py`](trng/sources.py) | `PhysicalSource` cosecha los bytes que produce `capture_entropy`. |
| [`requirements.txt`](requirements.txt) | `qiskit`, `qiskit-aer`. |

Resumen de parámetros:
- **Coordenadas**: `width.bit_length()` / `height.bit_length()` qubits (≤14, por el límite de 16384 px).
- **Whitening**: 8 qubits × 3 canales por píxel.
- **Grilla backstage**: 10 qubits, 32 selecciones.

---

## Pasar a hardware cuántico real (opcional)

El circuito ya está listo; solo cambia **dónde corre**. En vez de `AerSimulator`,
se usa un backend real de IBM Quantum con el mismo circuito:

```python
# pip install qiskit-ibm-runtime
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2
service = QiskitRuntimeService()                 # requiere token de IBM Quantum
backend = service.least_busy(operational=True)   # una QPU real
# mismo qc.h(range(n)) + measure; se ejecuta con Sampler en el backend
```

Trade-off: una QPU real tiene **latencia de cola** (segundos a minutos por job) y
ruido de hardware, por eso para la demo el simulador es lo práctico. La estructura
del código (un solo `get_quantum_random_int`) hace que el swap sea local a
`physical.py`.
