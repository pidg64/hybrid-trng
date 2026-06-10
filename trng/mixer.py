"""
Mezcla criptográfica de fuentes de entropía (extractor de aleatoriedad).

--------------------------------------------------------------------------
¿Por qué mezclar con una función hash preserva la entropía del input más fuerte?
--------------------------------------------------------------------------

Una función hash criptográfica (BLAKE2b / SHA-256) se comporta como un
*extractor de aleatoriedad* casi-universal. El resultado teórico que nos
respalda es el **Leftover Hash Lemma**:

    Si H es una familia de funciones hash (casi) universal y X es una variable
    con min-entropía H_inf(X) >= k, entonces (H, H(X)) es estadísticamente
    indistinguible de (H, U) —uniforme— salvo un epsilon despreciable, siempre
    que la salida sea suficientemente más corta que k.

Consecuencia práctica para ``seed = HASH(fisico || urandom)``:

* Basta con que **UNA** de las entradas concatenadas tenga suficiente
  min-entropía para que el seed sea indistinguible de uniforme.
* Un atacante que controle por completo la fuente física (cámara congelada,
  escena manipulada, datos estáticos) NO puede degradar el seed mientras
  ``urandom`` aporte su entropía del kernel. En el peor caso la física suma 0
  bits de entropía, pero **nunca resta**: agregar fuentes solo puede mejorar.
* Por eso la física es una fuente **secundaria aditiva**: en el mejor caso
  refuerza, en el peor caso es inocua. La salida es tan fuerte como la fuente
  más fuerte.

La mezcla es **determinista y auditable**: mismos inputs -> mismo seed, sin
estado oculto. Concatenamos con prefijo de longitud para que la frontera entre
fuentes sea inequívoca (evita ambigüedad de concatenación, p. ej. que
``"ab"||"c"`` colisione con ``"a"||"bc"``).
"""

import hashlib
import struct

# Algoritmos soportados. BLAKE2b por performance (default); SHA-256 como fallback.
_ALGORITHMS = {
    "blake2b": hashlib.blake2b,
    "sha256": hashlib.sha256,
}


def mix(*chunks: bytes, algo: str = "blake2b") -> bytes:
    """Combina N fuentes de entropía en un único seed determinista.

    Args:
        *chunks: bloques de bytes de cada fuente (ej. físico, urandom).
        algo: ``"blake2b"`` (default) o ``"sha256"``.

    Returns:
        El digest (seed) como ``bytes``.
    """
    if algo not in _ALGORITHMS:
        raise ValueError(f"Algoritmo no soportado: {algo!r}. Usá: {list(_ALGORITHMS)}")

    h = _ALGORITHMS[algo]()
    for chunk in chunks:
        # Prefijo de longitud (8 bytes, big-endian) -> concatenación inyectiva.
        h.update(struct.pack(">Q", len(chunk)))
        h.update(chunk)
    return h.digest()
