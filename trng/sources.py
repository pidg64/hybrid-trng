"""
Abstracción de fuentes de entropía.

Define la interfaz común ``EntropySource`` y sus tres implementaciones:

* ``UrandomSource``  — CSPRNG del kernel (fuente PRIMARIA).
* ``PhysicalSource`` — foto o live feed + qiskit (fuente SECUNDARIA aditiva).
* ``HybridSource``   — mezcla criptográfica de ambas (MAELSTROM; modelo tipo Cloudflare LavaRand).

Gracias a esta abstracción, la suite NIST puede pedir bytes a cualquiera de las
tres con la misma llamada ``.get_bytes(n)`` y compararlas en igualdad de
condiciones.
"""

import abc
import logging
import secrets
from enum import Enum

from .csprng import CounterCSPRNG
from .frames import default_provider
from .mixer import mix
from .physical import capture_entropy

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

    def close(self) -> None:
        """Libera recursos (hilos, captura). No-op por defecto."""


class UrandomSource(EntropySource):
    """Fuente PRIMARIA: CSPRNG del sistema operativo.

    Usa ``secrets.token_bytes`` (equivalente a ``os.urandom``), que ya incorpora
    entropía del kernel: RDRAND, timing de interrupciones, jitter, etc.
    """

    name = "urandom"

    def get_bytes(self, n: int) -> bytes:
        return secrets.token_bytes(n)


class PhysicalSource(EntropySource):
    """Fuente SECUNDARIA: frame + qiskit (con whitening cuántico).

    El frame lo entrega un ``FrameProvider`` (ver ``frames.py``): una foto fija
    (``StaticImage``) o un live feed por OpenCV (``VideoStream``). Por defecto se
    elige según la URL del feed (parámetro ``video_url`` o env ``VIDEO_FEED_URL``);
    si no hay, cae a la foto ``image_path``.

    Eficiencia ("1 frame -> muchos bytes"): por cada frame que pide al provider,
    cosecha hasta ``bytes_per_frame`` bytes muestreando muchos píxeles, en vez de
    pedir un frame nuevo por captura. Así la velocidad no queda atada al framerate
    del stream.

    Degradación elegante:
      * Si el provider no puede entregar frame (stream caído, qiskit/PIL fallan),
        la fuente se marca FAILED y devuelve los bytes que pudo reunir (cero si
        ninguno), dejando que ``HybridSource`` siga con urandom.
      * Si el live feed se congela, el provider lo reporta como ``degraded`` (lo
        detecta sobre el frame CRUDO) y la fuente devuelve DEGRADED.
    """

    name = "physical"

    def __init__(
        self,
        image_path: str = "frame.png",
        max_identical: int = 30,
        frame_provider=None,
        video_url: str | None = None,
        bytes_per_frame: int = 4096,
    ):
        self.image_path = image_path
        self._provider = frame_provider or default_provider(
            image_path, url=video_url, max_identical=max_identical
        )
        self._bytes_per_frame = bytes_per_frame
        self._failed = False

    def get_bytes(self, n: int) -> bytes:
        out = bytearray()
        next_log = _LOG_EVERY_BITS
        while len(out) < n:
            try:
                _img, width, height, px = self._provider.get_frame()
            except Exception as exc:  # stream caído / qiskit / PIL / lo que sea
                self._failed = True
                logger.warning(
                    "Fuente física FAILED al obtener frame (%s). "
                    "Se devuelven %d/%d bytes recolectados.",
                    exc, len(out), n,
                )
                break
            # 1 frame -> muchos bytes: cosechamos del MISMO frame hasta el budget.
            harvested = 0
            while len(out) < n and harvested < self._bytes_per_frame:
                _, _, r, g, b = capture_entropy(px, width, height)
                out.extend((r, g, b))
                harvested += 3
                if len(out) * 8 >= next_log:
                    logger.info("[physical] %d bits generados", min(len(out) * 8, n * 8))
                    next_log += _LOG_EVERY_BITS
        return bytes(out[:n])

    def health(self) -> HealthStatus:
        if self._failed:
            return HealthStatus.FAILED
        if getattr(self._provider, "degraded", False):
            return HealthStatus.DEGRADED
        return HealthStatus.OK

    def close(self) -> None:
        """Libera el provider (detiene el hilo del stream, si aplica)."""
        self._provider.close()


class HybridSource(EntropySource):
    """Fuente COMBINADA de MAELSTROM (modelo tipo Cloudflare LavaRand / defense in depth).

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

    def close(self) -> None:
        """Libera la fuente física (detiene el hilo del stream, si aplica)."""
        self.physical.close()
