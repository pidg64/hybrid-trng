"""
CLI: vuelca N bits de entropía a un archivo binario, eligiendo la fuente.

Esto desacopla la GENERACIÓN (este módulo, parte del código de producción) de la
EVALUACIÓN (``nist_suite.py``, independiente). El archivo de salida son bytes
crudos empaquetados (8 bits por byte), listos para que la suite NIST los lea.

Ejemplos::

    python -m trng.cli --mode urandom  --bits 1000000 --out urandom.bin
    python -m trng.cli --mode physical --bits 1000000 --out physical.bin
    python -m trng.cli --mode hybrid   --bits 1000000 --out hybrid.bin
"""

import argparse
import logging
import sys
import time

from .sources import HybridSource, PhysicalSource, UrandomSource

_MODES = {
    "urandom": lambda args: UrandomSource(),
    "physical": lambda args: PhysicalSource(
        image_path=args.image, video_url=args.video_url
    ),
    "hybrid": lambda args: HybridSource(
        physical=PhysicalSource(image_path=args.image, video_url=args.video_url),
        algo=args.algo,
    ),
}


def build_source(args):
    return _MODES[args.mode](args)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Volcado de entropía a archivo binario.")
    parser.add_argument("--mode", required=True, choices=list(_MODES),
                        help="Fuente de entropía a usar.")
    parser.add_argument("--bits", type=int, default=1_000_000,
                        help="Cantidad de bits a generar (default 1.000.000).")
    parser.add_argument("--out", required=True, help="Archivo binario de salida.")
    parser.add_argument("--image", default="frame.png", help="Imagen fuente física (fallback).")
    parser.add_argument("--video-url", default=None,
                        help="URL/índice del live feed para la fuente física "
                             "(ej. rtsp://..., http://..., o '0' para webcam). "
                             "Si se omite, usa la env VIDEO_FEED_URL o la imagen estática.")
    parser.add_argument("--algo", default="blake2b", choices=["blake2b", "sha256"],
                        help="Hash de mezcla para el modo hybrid.")
    args = parser.parse_args(argv)

    # Solo nuestros logs (trng.*) en INFO; root en WARNING para silenciar qiskit.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("trng").setLevel(logging.INFO)

    n_bytes = (args.bits + 7) // 8
    source = build_source(args)

    print(f"[*] Modo: {args.mode} | bits: {args.bits} ({n_bytes} bytes) -> {args.out}")
    if args.mode in ("physical", "hybrid"):
        print("[*] Aviso: el modo físico corre qiskit por captura; 1M bits puede tardar.")

    t0 = time.time()
    try:
        data = source.get_bytes(n_bytes)
    finally:
        elapsed = time.time() - t0
        status = source.health().value
        source.close()  # detiene el hilo del stream si lo hubiera

    with open(args.out, "wb") as f:
        f.write(data)

    print(f"[*] Listo: {len(data)} bytes escritos en {elapsed:.1f}s. "
          f"Estado fuente: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
