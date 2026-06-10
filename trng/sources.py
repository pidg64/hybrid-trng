"""
Abstracción de fuentes de entropía.

Define la interfaz común ``EntropySource`` y sus tres implementaciones:

* ``UrandomSource``  — CSPRNG del kernel (fuente PRIMARIA).
* ``PhysicalSource`` — foto + qiskit (fuente SECUNDARIA aditiva, con health check).
* ``HybridSource``   — mezcla criptográfica de ambas (modelo LavaRand).

Gracias a esta abstracción, la suite NIST puede pedir bytes a cualquiera de las
tres con la misma llamada ``.get_bytes(n)`` y compararlas en igualdad de
condiciones.
"""

import abc
import logging
import secrets
from enum import Enum

from .csprng import CounterCSPRNG
from .health import FrozenFeedDetector
from .mixer import mix
from .physical import capture_hybrid_entropy

logger = logging.getLogger("trng.sources")

# Cada cuántos bits generados reportamos progreso (evita loguear todo el tiempo).
_LOG_EVERY_BITS = 25_000


class HealthStatus(Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


class EntropySource(abc.ABC):
    """Interfaz común a todas las fuentes de entropía."""

    name: str = "abstract"

    @abc.abstractmethod
    def get_bytes(self, n: int) -> bytes:
        """Devuelve exactamente ``n`` bytes de entropía."""

    def health(self) -> HealthStatus:
        """Estado de la fuente. Por defecto OK; las fuentes con sensores la sobreescriben."""
        return HealthStatus.OK


class UrandomSource(EntropySource):
    """Fuente PRIMARIA: CSPRNG del sistema operativo.

    Usa ``secrets.token_bytes`` (equivalente a ``os.urandom``), que ya incorpora
    entropía del kernel: RDRAND, timing de interrupciones, jitter, etc.
    """

    name = "urandom"

    def get_bytes(self, n: int) -> bytes:
        return secrets.token_bytes(n)


class PhysicalSource(EntropySource):
    """Fuente SECUNDARIA: foto + qiskit (con whitening cuántico).

    Cada captura aporta 3 bytes de entropía física (canales R, G, B ya
    blanqueados). Incluye un health check que detecta capturas idénticas
    consecutivas (feed congelado) y degradación elegante: si qiskit/PIL lanzan
    una excepción, la fuente se marca FAILED y devuelve los bytes que pudo reunir
    (cero si ninguno), dejando que ``HybridSource`` decida cómo seguir.
    """

    name = "physical"

    def __init__(self, image_path: str = "frame.png", max_identical: int = 3):
        self.image_path = image_path
        self._detector = FrozenFeedDetector(max_identical=max_identical)
        self._failed = False

    def get_bytes(self, n: int) -> bytes:
        out = bytearray()
        next_log = _LOG_EVERY_BITS
        while len(out) < n:
            try:
                _, _, r, g, b = capture_hybrid_entropy(self.image_path)
            except Exception as exc:  # qiskit / PIL / lo que sea
                self._failed = True
                logger.warning(
                    "Fuente física FAILED durante la captura (%s). "
                    "Se devuelven %d/%d bytes recolectados.",
                    exc, len(out), n,
                )
                break
            reading = bytes((r, g, b))
            self._detector.observe(reading)
            out.extend(reading)
            if len(out) * 8 >= next_log:
                logger.info("[physical] %d bits generados", min(len(out) * 8, n * 8))
                next_log += _LOG_EVERY_BITS
        return bytes(out[:n])

    def health(self) -> HealthStatus:
        if self._failed:
            return HealthStatus.FAILED
        if self._detector.degraded:
            return HealthStatus.DEGRADED
        return HealthStatus.OK


class HybridSource(EntropySource):
    """Fuente COMBINADA (modelo LavaRand / defense in depth).

    Pipeline::

        seed   = HASH(bytes_fisicos || bytes_urandom)     # mezcla / extractor
        stream = CounterCSPRNG(seed).get_bytes(...)        # expansión DRBG

    * urandom es la fuente primaria; la física es aditiva.
    * **Reseed periódico**: cada ``reseed_every`` bytes de salida se vuelve a
      mezclar entropía fresca de ambas fuentes y se deriva un seed nuevo.
    * **Degradación elegante**: si la física falla o está degradada, se sigue
      sembrando con urandom solo. Por el Leftover Hash Lemma el seed NO se
      debilita respecto a urandom puro.
    """

    name = "hybrid"

    def __init__(
        self,
        physical: PhysicalSource | None = None,
        urandom: UrandomSource | None = None,
        algo: str = "blake2b",
        seed_bytes: int = 32,
        reseed_every: int = 1024,
    ):
        self.physical = physical if physical is not None else PhysicalSource()
        self.urandom = urandom if urandom is not None else UrandomSource()
        self.algo = algo
        self.seed_bytes = seed_bytes
        self.reseed_every = reseed_every

    def _derive_seed(self) -> bytes:
        """Mezcla entropía fresca de física + urandom en un seed."""
        urandom_bytes = self.urandom.get_bytes(self.seed_bytes)

        physical_bytes = b""
        try:
            physical_bytes = self.physical.get_bytes(self.seed_bytes)
        except Exception as exc:  # red de seguridad extra
            logger.warning("Física inaccesible al derivar seed (%s); sigo con urandom.", exc)

        if not physical_bytes or self.physical.health() != HealthStatus.OK:
            logger.warning(
                "Física no OK (status=%s); el seed se deriva con urandom como "
                "fuente dominante. Sin degradación criptográfica (Leftover Hash Lemma).",
                self.physical.health().value,
            )

        # Aunque physical_bytes sea b"", la mezcla sigue siendo segura: urandom
        # aporta toda la entropía necesaria.
        return mix(physical_bytes, urandom_bytes, algo=self.algo)

    def get_bytes(self, n: int) -> bytes:
        out = bytearray()
        next_log = _LOG_EVERY_BITS
        while len(out) < n:
            seed = self._derive_seed()
            csprng = CounterCSPRNG(seed, algo=self.algo)
            chunk = csprng.get_bytes(min(self.reseed_every, n - len(out)))
            out.extend(chunk)
            if len(out) * 8 >= next_log:
                logger.info("[hybrid] %d bits generados", min(len(out) * 8, n * 8))
                next_log += _LOG_EVERY_BITS
        return bytes(out[:n])

    def health(self) -> HealthStatus:
        # La salud del híbrido sigue a la física para observabilidad, pero el
        # híbrido nunca FALLA mientras urandom esté disponible.
        phys = self.physical.health()
        return HealthStatus.OK if phys == HealthStatus.OK else HealthStatus.DEGRADED
