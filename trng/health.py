"""
Health check de la fuente de entropía física.

Detecta el caso clásico de fallo de una fuente física: un feed "congelado" o una
escena estática manipulada que devuelve siempre la misma lectura cruda. Si N
capturas consecutivas son idénticas (delta = 0 entre lecturas), la fuente se
considera DEGRADADA.

Importante: este chequeo es de **observabilidad**, no de seguridad. Aunque la
física quede DEGRADED (constante), la mezcla con urandom (ver ``mixer.py``,
Leftover Hash Lemma) garantiza que el seed combinado no se debilita. El warning
sirve para que un operador sepa que hay que revisar la cámara.
"""

import logging

logger = logging.getLogger("trng.health")


class FrozenFeedDetector:
    """Marca la fuente como degradada tras N lecturas crudas idénticas seguidas."""

    def __init__(self, max_identical: int = 3):
        if max_identical < 2:
            raise ValueError("max_identical debe ser >= 2")
        self.max_identical = max_identical
        self._last: bytes | None = None
        self._identical_streak = 0
        self._degraded = False

    @property
    def degraded(self) -> bool:
        return self._degraded

    def observe(self, raw_reading: bytes) -> None:
        """Registra una lectura cruda de la fuente física y actualiza el estado."""
        if self._last is not None and raw_reading == self._last:
            self._identical_streak += 1
            if self._identical_streak >= self.max_identical - 1 and not self._degraded:
                self._degraded = True
                logger.warning(
                    "Fuente física DEGRADADA: %d capturas consecutivas idénticas "
                    "(posible feed congelado o escena estática manipulada). "
                    "El sistema continúa con urandom sin degradación criptográfica.",
                    self.max_identical,
                )
        else:
            # Lectura distinta -> la fuente está viva, reseteamos.
            self._identical_streak = 0
            if self._degraded:
                logger.info("Fuente física recuperada: la lectura volvió a variar.")
                self._degraded = False
        self._last = raw_reading
