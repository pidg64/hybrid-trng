# Live feed de video como fuente de entropía física

Este documento explica **cómo** el sistema toma entropía de un live feed de video
(webcam, archivo, o stream HTTP/RTSP) y cómo configurarlo.

---

## ⚠️ Importante: la URL NO está en el código

El sistema **no tiene ninguna URL hardcodeada**. La URL del feed (que puede llevar
un **token secreto** en el query string) se lee en tiempo de ejecución desde:

1. el flag de CLI `--video-url`, **o**
2. la variable de entorno `VIDEO_FEED_URL`.

Esto es a propósito: si la URL/token viviera en el código, **se filtraría en el
historial de git** al commitear. Por eso el código solo conoce "una URL que le
pasan", nunca una en particular. Además, al loguear se **enmascara** el token
(ver `_mask_url` en [`trng/frames.py`](trng/frames.py)) para que no aparezca en
los logs.

> Consecuencia práctica: si no seteás `VIDEO_FEED_URL` ni pasás `--video-url`, el
> sistema **no usa ningún video** — cae a la imagen estática `frame.png`.

---

## Cómo activarlo

**Opción A — variable de entorno (la usa la API automáticamente):**
```bash
export VIDEO_FEED_URL="https://tu-host/video?token=TU_TOKEN"
python api.py            # el endpoint de tokens ya usa el live feed
```

**Opción B — flag de CLI (para volcar bytes a un archivo):**
```bash
python -m trng.cli --mode hybrid --video-url "$VIDEO_FEED_URL" --bits 100000 --out hybrid.bin
```

**Webcam local:** `--video-url 0` (índice de cámara).
**Sin nada:** usa `frame.png` (comportamiento histórico, reproducible).

---

## Arquitectura

El sistema abstrae **de dónde** sale el frame con un `FrameProvider`
([`trng/frames.py`](trng/frames.py)):

```
FrameProvider (abstracto)
├── StaticImage   → una foto fija (frame.png). Fallback / tests.
└── VideoStream   → live feed por OpenCV, leído en un HILO de fondo.
```

`PhysicalSource` ([`trng/sources.py`](trng/sources.py)) no sabe ni le importa cuál
de los dos está usando: solo pide `provider.get_frame()` y le extrae entropía. El
provider se elige en `default_provider()` según haya o no URL.

---

## El "grabber en hilo" (cómo lee el video)

Un stream de red **bufferea** frames. Si el consumidor lee lento, `cap.read()`
devuelve frames viejos y se acumula *lag*. Solución: un **hilo de fondo dedicado**.

```
┌─────────────────────────── VideoStream ───────────────────────────┐
│                                                                    │
│   [HILO de fondo]  cv2.VideoCapture(url)                           │
│        │  loop:                                                    │
│        │    ok, frame = cap.read()      ← al ritmo de la red       │
│        │    guarda SOLO el último frame (bajo lock)                │
│        │    chequea "frozen" sobre el frame CRUDO                  │
│        │    si falla la lectura → reconecta con backoff            │
│        ▼                                                           │
│   "último frame disponible"  ←───────────┐                        │
│                                          │ get_frame()             │
└──────────────────────────────────────────┼────────────────────────┘
                                           │
                          [PhysicalSource] toma ese frame y le
                          cosecha MUCHOS bytes (ver abajo)
```

- El hilo es **daemon**: no bloquea ni cuelga el proceso principal.
- `get_frame()` devuelve el frame más reciente convertido a RGB. La conversión
  BGR→RGB se cachea por frame, así sacar muchos bytes del mismo frame es barato.
- Al arranque, `get_frame()` espera hasta `first_frame_timeout` (5 s) el **primer**
  frame; si no llega, considera la conexión caída.

---

## Eficiencia: "1 frame → muchos bytes"

El cuello de botella **no** es agarrar el frame (barato), sino qiskit: cada
captura de entropía corre ~5 simulaciones cuánticas para dar 3 bytes. Y un frame
Full HD tiene ~2 millones de píxeles = muchísima entropía cruda.

Por eso `PhysicalSource` **no pide un frame por byte**. Pide un frame y le cosecha
hasta `bytes_per_frame` (default **4096**) muestreando muchos píxeles distintos
(con coordenadas cuánticas + whitening), y recién después refresca el frame. Así
la velocidad de generación **no queda atada al framerate** del stream.

---

## De frame a entropía (el pipeline completo)

```
VideoStream.get_frame()  →  (img, width, height, px)
        │
        ▼
capture_entropy(px, w, h)   [trng/physical.py]
   1. coordenadas (x,y) por medición cuántica (Rejection Sampling, anti modulo-bias)
   2. lee el píxel crudo (x,y) del frame
   3. lo blanquea: r,g,b = pixel XOR máscara_cuántica   (ruido blanco)
        │
        ▼  (se repite hasta bytes_per_frame por frame)
bytes físicos
        │
        ▼
mix(físico, urandom)  [trng/mixer.py]   → seed (BLAKE2b)
        │
        ▼
CounterCSPRNG(seed)   [trng/csprng.py]  → stream final de bytes
```

La física es una fuente **secundaria aditiva**: refuerza, nunca debilita (Leftover
Hash Lemma). `urandom` es la fuente primaria.

---

## Robustez (qué pasa si el feed falla)

| Situación | Qué hace el sistema | Estado (`health()`) |
|---|---|---|
| Stream no abre / se corta | El hilo **reconecta** con backoff exponencial (0.5 s → 5 s). Si no hay frame, `PhysicalSource` se marca FAILED y devuelve lo que pudo. | `FAILED` |
| Feed **congelado** (mismo frame repetido) | Un `FrozenFeedDetector` sobre el **frame crudo** lo detecta tras `max_identical` (default 30) frames idénticos. | `DEGRADED` |
| Física FAILED o DEGRADED | `HybridSource` **sigue generando** sembrando con urandom. Por el Leftover Hash Lemma el seed **no se debilita**. | híbrido: `DEGRADED`, nunca falla |

> Clave: el frozen-detector mira el frame **antes** del whitening cuántico. Si
> mirara los bytes ya blanqueados, la máscara cuántica (distinta cada vez) taparía
> los frames repetidos y nunca detectaría el congelamiento.

---

## Perillas configurables

En `PhysicalSource(...)` / `VideoStream(...)`:

| Parámetro | Default | Qué controla |
|---|---|---|
| `bytes_per_frame` | 4096 | cuántos bytes se cosechan de cada frame antes de refrescar |
| `max_identical` | 30 | frames crudos idénticos seguidos para marcar "congelado" |
| `backoff` | (0.5, 5.0) | segundos mín/máx de reconexión exponencial |
| `first_frame_timeout` | 5.0 | cuánto espera el primer frame antes de dar la conexión por caída |

---

## Smoke test

```bash
python -c "from trng.frames import VideoStream; import time; \
v=VideoStream('$VIDEO_FEED_URL'); time.sleep(3); \
print('frame:', v.get_frame()[1:3], 'conectado=', v.connected); v.close()"
```

Si imprime las dimensiones del frame y `conectado= True`, el live feed funciona.
Si el formato no lo abre OpenCV, caería a `FAILED` y el híbrido seguiría con
urandom — el sistema nunca se rompe.
