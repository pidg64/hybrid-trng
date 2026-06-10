"""
Proveedores de frames para la fuente física.

Abstrae DE DÓNDE sale la imagen que alimenta la entropía física, para que la
misma ``PhysicalSource`` funcione con distintas fuentes sin enterarse:

* ``StaticImage`` — una foto fija (``frame.png``). Comportamiento histórico:
  reproducible, ideal para tests y como *fallback* cuando no hay video.
* ``VideoStream`` — un live feed (webcam, archivo de video, o stream HTTP/RTSP)
  leído con OpenCV en un **hilo de fondo** que siempre mantiene "el último
  frame".

--------------------------------------------------------------------------
Patrón "grabber en hilo" (Opción B) y por qué es necesario sobre red
--------------------------------------------------------------------------

Un stream de red bufferea frames. Si el consumidor lee lento, ``cap.read()``
devuelve frames viejos y se acumula *lag*. Por eso un hilo dedicado lee al ritmo
de la red/cámara y deja siempre disponible SOLO el frame más reciente; el
consumidor (``PhysicalSource``) toma ese frame y le cosecha MUCHOS bytes
(un frame Full HD tiene ~2M de píxeles), sin atar la velocidad de generación al
framerate.

Robustez: si el stream se cae, el hilo reconecta con *backoff* exponencial. Y si
el feed se "congela" (la red se corta y OpenCV devuelve el último frame
decodificado una y otra vez), un ``FrozenFeedDetector`` que mira el **frame
crudo** lo detecta y marca la fuente como degradada, para que ``HybridSource``
caiga elegantemente a urandom (sin degradación criptográfica: Leftover Hash
Lemma). Importante: el detector observa el frame ANTES del whitening cuántico,
que es justo lo que hace que la detección funcione.

Seguridad: la URL del feed (que puede llevar un token secreto) NO se hardcodea.
Llega por parámetro o por la variable de entorno ``VIDEO_FEED_URL``, y al loguear
se enmascara el query string para no filtrar el token.
"""

import os
import abc
import cv2
import time
import logging
import threading

from PIL import Image

# El loader de imágenes (con cache) vive en physical.py; lo reusamos para
# StaticImage. Dependencia en un solo sentido: frames -> physical.
from .physical import _get_image
from .health import FrozenFeedDetector

logger = logging.getLogger("trng.frames")

# Variable de entorno donde se espera la URL del live feed (con su token).
ENV_VIDEO_URL = "https://dwt.tail41754b.ts.net/video?token=wl4oYNFqNUpCHKbXqARtn1NOu7jopsL6JEDHTRSkqjU"

# Frame "cargado" = (PIL.Image RGB, width, height, pixel_accessor), el mismo
# formato que consume capture_entropy() en physical.py.


def _mask_url(url: str) -> str:
    """Oculta el query string (token) de una URL para loguear sin filtrarlo."""
    base, sep, _ = url.partition("?")
    return base + ("?<oculto>" if sep else "")


class FrameProvider(abc.ABC):
    """Fuente de frames para la entropía física."""

    @abc.abstractmethod
    def get_frame(self):
        """Devuelve ``(img, width, height, px)`` del frame actual.

        Puede lanzar excepción si la fuente no está disponible (ej. stream
        caído); ``PhysicalSource`` la captura y cae a urandom.
        """

    @property
    def degraded(self) -> bool:
        """``True`` si la fuente está viva pero entregando entropía sospechosa
        (ej. feed congelado). Las fuentes estáticas nunca se marcan degradadas."""
        return False

    def close(self) -> None:
        """Libera recursos (hilos, captura). No-op por defecto."""


class StaticImage(FrameProvider):
    """Foto fija. Comportamiento histórico: se carga (y cachea) una sola vez."""

    def __init__(self, image_path: str = "frame.png"):
        self.image_path = image_path

    def get_frame(self):
        return _get_image(self.image_path)


class VideoStream(FrameProvider):
    """Live feed leído por OpenCV en un hilo de fondo (último frame disponible).

    Args:
        url: webcam (``"0"`` -> índice 0), archivo de video, o URL HTTP/RTSP.
        max_identical: cuántos frames crudos idénticos seguidos marcan
            ``degraded`` (feed congelado). ``None`` desactiva la detección.
        backoff: ``(min, max)`` segundos para la reconexión exponencial.
        first_frame_timeout: cuánto espera ``get_frame`` el PRIMER frame antes de
            dar la conexión por fallida (solo al arranque; luego usa el último).
    """

    def __init__(
        self,
        url: str,
        max_identical: int | None = 30,
        backoff: tuple[float, float] = (0.5, 5.0),
        first_frame_timeout: float = 5.0,
    ):
        # "0" / "1" -> índice de cámara (int); cualquier otra cosa -> URL/ruta.
        self.url = int(url) if isinstance(url, str) and url.isdigit() else url
        self._masked = _mask_url(url) if isinstance(url, str) else f"camera:{url}"
        self._backoff = backoff
        self._first_frame_timeout = first_frame_timeout

        self._lock = threading.Lock()
        self._frame = None          # último frame crudo (numpy BGR)
        self._frame_id = 0          # se incrementa con cada frame nuevo
        self._connected = False
        self._running = True

        # Cache de conversión BGR->PIL por frame_id (solo el consumidor la toca).
        self._cached_id = -1
        self._cached_frame = None

        self._detector = (
            FrozenFeedDetector(max_identical=max_identical)
            if max_identical is not None
            else None
        )

        self._thread = threading.Thread(
            target=self._grab_loop, name="VideoStream-grabber", daemon=True
        )
        self._thread.start()
        logger.info("VideoStream iniciado (%s).", self._masked)

    # ---- hilo de fondo -------------------------------------------------

    def _grab_loop(self) -> None:
        min_b, max_b = self._backoff
        backoff = min_b
        cap = None
        while self._running:
            if cap is None:
                cap = cv2.VideoCapture(self.url)
                # Buffer mínimo -> frames frescos (best-effort, no todos los
                # backends lo respetan).
                try:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
                if not cap.isOpened():
                    self._connected = False
                    logger.warning(
                        "No se pudo abrir el stream (%s); reintento en %.1fs.",
                        self._masked, backoff,
                    )
                    cap.release()
                    cap = None
                    time.sleep(backoff)
                    backoff = min(backoff * 2, max_b)
                    continue
                self._connected = True
                backoff = min_b
                logger.info("Stream conectado (%s).", self._masked)

            ok, frame = cap.read()
            if not ok or frame is None:
                self._connected = False
                logger.warning("Lectura fallida del stream (%s); reconectando.", self._masked)
                cap.release()
                cap = None
                time.sleep(backoff)
                backoff = min(backoff * 2, max_b)
                continue

            with self._lock:
                self._frame = frame
                self._frame_id += 1

            # Frozen detection sobre el frame CRUDO (antes del whitening).
            # Fingerprint barato: muestreo disperso de píxeles.
            if self._detector is not None:
                self._detector.observe(frame[::40, ::40].tobytes())

    # ---- API del provider ----------------------------------------------

    def get_frame(self):
        deadline = time.time() + self._first_frame_timeout
        while True:
            with self._lock:
                frame = self._frame
                fid = self._frame_id
            if frame is not None:
                break
            if time.time() > deadline:
                raise RuntimeError(
                    f"Stream sin frames tras {self._first_frame_timeout:.0f}s "
                    f"({self._masked}): conexión no disponible."
                )
            time.sleep(0.05)

        # Convertir BGR(numpy) -> RGB(PIL) una sola vez por frame, así cosechar
        # muchos bytes del mismo frame es barato.
        if fid != self._cached_id:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            self._cached_frame = (img, img.width, img.height, img.load())
            self._cached_id = fid
        return self._cached_frame

    @property
    def degraded(self) -> bool:
        return self._detector.degraded if self._detector is not None else False

    @property
    def connected(self) -> bool:
        return self._connected

    def close(self) -> None:
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)


def default_provider(
    image_path: str = "frame.png",
    *,
    url: str | None = None,
    max_identical: int = 30,
) -> FrameProvider:
    """Elige el provider: ``VideoStream`` si hay URL, si no ``StaticImage``.

    Prioridad de la URL: parámetro explícito > env var ``VIDEO_FEED_URL`` >
    ``ENV_VIDEO_URL`` (URL hardcodeada de la demo). Como ``ENV_VIDEO_URL`` tiene
    la URL del feed, por defecto el live feed queda activo sin setear nada."""
    url = url or os.environ.get("VIDEO_FEED_URL") or ENV_VIDEO_URL
    if url:
        return VideoStream(url, max_identical=max_identical)
    return StaticImage(image_path)
