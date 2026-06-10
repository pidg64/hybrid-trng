"""
trng — Generador de números aleatorios híbrido con defense in depth.

Expone una abstracción de fuentes de entropía (``EntropySource``) con tres
implementaciones intercambiables:

* ``UrandomSource``  — CSPRNG del sistema operativo (fuente primaria).
* ``PhysicalSource`` — entropía física: foto + qiskit (fuente secundaria aditiva).
* ``HybridSource``   — mezcla criptográfica de ambas (modelo LavaRand).

El diseño permite correr la suite NIST SP 800-22 sobre cualquiera de las tres
fuentes de forma aislada para compararlas.
"""

from .frames import FrameProvider, StaticImage, VideoStream, default_provider
from .sources import (
    EntropySource,
    HealthStatus,
    HybridSource,
    PhysicalSource,
    UrandomSource,
)

__all__ = [
    "EntropySource",
    "HealthStatus",
    "UrandomSource",
    "PhysicalSource",
    "HybridSource",
    "FrameProvider",
    "StaticImage",
    "VideoStream",
    "default_provider",
]
