#!/usr/bin/env python3
"""
Visor de live feed desde la fuente física de entropía.

Consume frames del módulo ``trng.frames`` (VideoStream o StaticImage) y los muestra
en tiempo real en una ventana interactiva. Permite inspeccionar visualmente la calidad
de la entropía física antes del blanqueamiento cuántico.

Uso:
    python livestream.py                    # Usa frame.png (StaticImage)
    python livestream.py --video 0          # Usa webcam (cámara 0)
    python livestream.py --video rtsp://... # Usa stream RTSP/HTTP

Controles:
    - 'q' para salir
    - 'p' para pausar/reanudar (StaticImage)
    - 'f' para alternar a pantalla completa
"""

import argparse
import os
import sys
import time

try:
    import cv2
except ImportError:
    print("Error: OpenCV no está instalado. Instalá con: pip install opencv-python")
    sys.exit(1)

from trng.frames import StaticImage, VideoStream, ENV_VIDEO_URL


def main():
    parser = argparse.ArgumentParser(
        description="Visor de live feed para la fuente de entropía física."
    )
    parser.add_argument(
        "--video",
        type=str,
        default=None,
        help="URL del stream de video (webcam, HTTP/RTSP). "
             "Si omitís, usa frame.png (StaticImage).",
    )
    parser.add_argument(
        "--image",
        type=str,
        default="frame.png",
        help="Ruta a la imagen estática (si no usás --video). Default: frame.png",
    )
    parser.add_argument(
        "--fps-limit",
        type=float,
        default=30.0,
        help="Límite de frames por segundo para no saturar CPU (default: 30).",
    )
    parser.add_argument(
        "--fullscreen",
        action="store_true",
        help="Arrancar en pantalla completa.",
    )

    args = parser.parse_args()

    # Crear el provider (VideoStream o StaticImage)
    print("[*] Inicializando provider de frames...")
    
    # Prioridad: --video > env var VIDEO_FEED_URL > ENV_VIDEO_URL (URL hardcodeada) > StaticImage
    url = args.video or os.environ.get("VIDEO_FEED_URL") or ENV_VIDEO_URL
    if url:
        provider = VideoStream(url)
    else:
        provider = StaticImage(args.image)

    window_name = "Live Feed - Entropía Física"
    paused = False
    current_frame = None
    fullscreen = args.fullscreen

    # Crear ventana
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    if fullscreen:
        cv2.setWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    print("[+] Provider listo. Mostrando live feed...")
    print("    Controles: 'q' = salir, 'p' = pausar (StaticImage), 'f' = fullscreen")

    frame_time = 1.0 / args.fps_limit
    last_frame_time = time.time()

    try:
        while True:
            # Obtener frame
            if not paused:
                try:
                    img, width, height, px = provider.get_frame()
                    # Convertir PIL a numpy (BGR para OpenCV)
                    import numpy as np

                    current_frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
                except Exception as e:
                    print(f"[-] Error al obtener frame: {e}")
                    if isinstance(provider, StaticImage):
                        break
                    # Para VideoStream, intenta reconectar
                    time.sleep(0.5)
                    continue

            # Mostrar frame
            if current_frame is not None:
                # Agregar overlay con info
                info_text = f"FPS: {args.fps_limit:.1f}"
                if hasattr(provider, "degraded") and provider.degraded:
                    info_text += " | ⚠ DEGRADED"
                if hasattr(provider, "connected"):
                    info_text += f" | {'✓ CONNECTED' if provider.connected else '✗ DISCONNECTED'}"

                frame_with_info = current_frame.copy()
                cv2.putText(
                    frame_with_info,
                    info_text,
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 0),
                    2,
                )

                cv2.imshow(window_name, frame_with_info)

            # Control de velocidad
            elapsed = time.time() - last_frame_time
            wait_ms = max(1, int((frame_time - elapsed) * 1000))
            key = cv2.waitKey(wait_ms) & 0xFF

            if key == ord("q"):
                print("[*] Saliendo...")
                break
            elif key == ord("p"):
                paused = not paused
                state = "PAUSADO" if paused else "REANUDADO"
                print(f"[*] {state}")
            elif key == ord("f"):
                fullscreen = not fullscreen
                prop = cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL
                cv2.setWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN, prop)

            last_frame_time = time.time()

    except KeyboardInterrupt:
        print("\n[*] Interrupción del usuario.")
    finally:
        provider.close()
        cv2.destroyAllWindows()
        print("[+] Cerrado.")


if __name__ == "__main__":
    main()
