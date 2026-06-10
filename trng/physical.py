"""
Captura de entropía física: foto (caos macroscópico) + qiskit (caos cuántico).

Este módulo contiene el motor de entropía física tal cual lo teníamos en
``api.py``, ahora desacoplado para poder tratarlo como una fuente más dentro de
la abstracción ``EntropySource``. La lógica de entropía es idéntica a la
original:

1. qiskit decide coordenadas (x, y) con Rejection Sampling (sin Modulo Bias).
2. Se lee el pixel de ``frame.png`` (caos del mundo físico).
3. Se blanquea con una máscara cuántica independiente vía XOR (anti Histogram Bias).

OPTIMIZACIÓN DE PERFORMANCE (clave):
  * El circuito transpilado se CACHEA por cantidad de qubits. ``transpile()``
    corre todo el pass manager y es caro; antes se hacía en CADA medición, ahora
    una sola vez por tamaño. (Esta era la causa principal de la lentitud y del
    spam de logs "Pass: ...").
  * La imagen se CACHEA: ``frame.png`` es estático, así que se abre y decodifica
    una sola vez en lugar de en cada captura.
Ninguna de estas optimizaciones cambia la entropía generada: mismas mediciones
cuánticas, mismo whitening.
"""

import logging

from PIL import Image

from qiskit_aer import AerSimulator
from qiskit import QuantumCircuit, transpile

logger = logging.getLogger("trng.physical")

# Simulador global para evitar instanciación constante por captura.
_simulator = AerSimulator()

# Cache de circuitos ya transpilados, indexados por cantidad de qubits.
_compiled_circuits: dict[int, object] = {}

# Cache de la imagen física: (img, width, height, pixel_accessor) por ruta.
_image_cache: dict[str, tuple] = {}


def _get_compiled_circuit(num_bits: int):
    """Devuelve el circuito de ``num_bits`` qubits transpilado, transpilando solo
    la primera vez que se pide ese tamaño."""
    compiled = _compiled_circuits.get(num_bits)
    if compiled is None:
        qc = QuantumCircuit(num_bits, num_bits)
        qc.h(range(num_bits))
        qc.measure(range(num_bits), range(num_bits))
        compiled = transpile(qc, _simulator)
        _compiled_circuits[num_bits] = compiled
        logger.debug("Circuito de %d qubits transpilado y cacheado.", num_bits)
    return compiled


def get_quantum_random_int(num_bits: int = 12) -> int:
    """Mide ``num_bits`` qubits en superposición perfecta (Hadamard) y devuelve
    el entero clásico resultante del colapso."""
    compiled = _get_compiled_circuit(num_bits)
    job = _simulator.run(compiled, shots=1, memory=True)
    binary_string = job.result().get_memory()[0]
    return int(binary_string, 2)


def _get_image(image_path: str):
    """Devuelve ``(img, width, height, px)`` desde cache, cargando la imagen una
    sola vez. ``px`` es el accesor rápido de píxeles (``img.load()``)."""
    cached = _image_cache.get(image_path)
    if cached is None:
        try:
            img = Image.open(image_path).convert("RGB")
        except FileNotFoundError:
            img = Image.new("RGB", (800, 600), color="black")
        # Limitamos dimensiones para no exceder 14 qubits (width/height < 16384).
        if img.width >= 16384 or img.height >= 16384:
            img.thumbnail((16383, 16383))
        cached = (img, img.size[0], img.size[1], img.load())
        _image_cache[image_path] = cached
        logger.debug("Imagen '%s' cargada y cacheada (%dx%d).", image_path, *img.size)
    return cached


def capture_hybrid_entropy(image_path: str = "frame.png"):
    """Devuelve ``(x, y, r, g, b)``: coordenadas cuánticas + RGB blanqueado.

    Los canales ``r, g, b`` ya vienen con whitening cuántico (XOR contra una
    máscara qiskit independiente), por lo que son ruido blanco apto para usar
    como bytes de entropía física.
    """
    _img, width, height, px = _get_image(image_path)

    # 1. Coordenadas con Rejection Sampling (elimina Modulo Bias).
    bits_x = width.bit_length()
    while True:
        x = get_quantum_random_int(num_bits=bits_x)
        if x < width:
            break
    bits_y = height.bit_length()
    while True:
        y = get_quantum_random_int(num_bits=bits_y)
        if y < height:
            break

    pixel_color = px[x, y]

    # 2. Máscara de blanqueamiento (3 mediciones de 8 qubits independientes).
    mask_r = get_quantum_random_int(num_bits=8)
    mask_g = get_quantum_random_int(num_bits=8)
    mask_b = get_quantum_random_int(num_bits=8)

    # 3. Blanqueamiento XOR (anti Histogram Bias -> ruido blanco).
    r = pixel_color[0] ^ mask_r
    g = pixel_color[1] ^ mask_g
    b = pixel_color[2] ^ mask_b

    return x, y, r, g, b
