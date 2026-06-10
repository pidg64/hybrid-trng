"""
CSPRNG en modo contador (hash-based DRBG).

A partir de un ``seed`` de longitud fija, expande un stream de bytes ilimitado:

    bloque_i = HASH(seed || counter_i)
    stream   = bloque_0 || bloque_1 || bloque_2 || ...

Propiedades:

* **Determinista y auditable:** mismo seed -> mismo stream, sin estado oculto
  más allá del contador explícito. Dado el seed se puede reproducir bit a bit.
* **Forward secrecy básica:** recuperar un bloque no revela el seed (preimagen
  de la hash) ni permite retroceder a bloques anteriores.
* Es el componente de "expansión" del modelo LavaRand: la entropía cara
  (física + urandom) se mezcla en un seed y luego se estira barato con la hash.
"""

import hashlib
import struct

_ALGORITHMS = {
    "blake2b": hashlib.blake2b,
    "sha256": hashlib.sha256,
}


class CounterCSPRNG:
    """DRBG en modo contador alimentado por un seed."""

    def __init__(self, seed: bytes, algo: str = "blake2b"):
        if algo not in _ALGORITHMS:
            raise ValueError(f"Algoritmo no soportado: {algo!r}")
        self._seed = seed
        self._algo = algo
        self._counter = 0

    def get_bytes(self, n: int) -> bytes:
        """Devuelve ``n`` bytes del stream, avanzando el contador interno."""
        out = bytearray()
        hashfn = _ALGORITHMS[self._algo]
        while len(out) < n:
            block = hashfn(self._seed + struct.pack(">Q", self._counter)).digest()
            out.extend(block)
            self._counter += 1
        return bytes(out[:n])
