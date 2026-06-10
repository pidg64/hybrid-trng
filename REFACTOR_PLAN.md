# Plan de Refactor: TRNG Híbrido (entropía física + /dev/urandom)

> Documento de diseño **previo a la implementación**. Lee esto, respondé las
> preguntas del final y recién ahí escribo el código.

## 0. Decisiones tomadas (2026-06-09)

1. **Modo "solo físico"** = salida actual **con whitening cuántico** (pixel XOR qiskit).
2. **Volumen** = 1.000.000 de bits para los tres modos (configurable por CLI; default 1M).
3. **Modo combinado** = sembrar una vez + expandir en **modo contador (DRBG)** con reseed periódico.
4. **Integración** = `api.py` pasa a usar `HybridSource`; se mantiene **`nistrng`**.
5. **Hash de mezcla** = BLAKE2b (default), SHA-256 como fallback configurable.

## 1. Objetivo

Pasar del modelo actual —donde la entropía física (foto + qiskit) es la **fuente
primaria y única**— al modelo tipo **Cloudflare LavaRand**, donde la entropía
física es una **fuente secundaria aditiva** que *complementa* al CSPRNG del
sistema operativo, nunca lo reemplaza.

La propiedad criptográfica que nos da garantías:

> Al mezclar N fuentes independientes con una función hash (extractor de
> aleatoriedad), la salida es **al menos tan fuerte como la fuente más fuerte**.
> Agregar fuentes nunca degrada; solo puede mejorar.

Fundamento teórico: **Leftover Hash Lemma**. Una función hash criptográfica se
comporta como un *extractor* casi-universal: si *cualquiera* de los inputs tiene
suficiente min-entropía (aunque los demás estén totalmente controlados por un
atacante o sean constantes), la salida es estadísticamente indistinguible de
uniforme. Por eso `BLAKE2b(fisico || urandom)` es seguro incluso si la cámara
está congelada: mientras `urandom` aporte su entropía, el seed final es fuerte.

## 2. Arquitectura propuesta (abstracción de fuentes)

El requisito central que pediste: poder correr los tests NIST sobre **(a) solo
urandom, (b) solo física, (c) combinada**, con el mismo código. Para eso defino
una interfaz común y tres implementaciones.

```
                  ┌─────────────────────────────┐
                  │   EntropySource (interfaz)   │
                  │   .get_bytes(n) -> bytes     │
                  │   .health() -> HealthStatus  │
                  └─────────────────────────────┘
                     ▲            ▲            ▲
        ┌────────────┘            │            └────────────┐
┌───────────────────┐  ┌───────────────────┐  ┌───────────────────────┐
│ PhysicalSource    │  │ UrandomSource     │  │ HybridSource          │
│ foto + qiskit     │  │ os.urandom /      │  │ mezcla con BLAKE2b     │
│ (tu código actual)│  │ secrets.token_b.  │  │ de las dos anteriores │
└───────────────────┘  └───────────────────┘  └───────────────────────┘
```

Archivos nuevos propuestos (módulo `trng/`, desacoplado de `api.py`):

| Archivo | Responsabilidad |
|---|---|
| `trng/sources.py` | `EntropySource` (ABC) + `PhysicalSource`, `UrandomSource`, `HybridSource` |
| `trng/mixer.py` | función de mezcla `mix(*chunks) -> seed` (BLAKE2b, determinista) |
| `trng/csprng.py` | DRBG en modo contador: `seed -> stream de bits ilimitado` |
| `trng/health.py` | health check de la fuente física (detección de frames idénticos) |
| `trng/cli.py` | vuelca N bits a un archivo binario, eligiendo el modo |
| `nist_suite.py` | suite NIST SP 800-22, lee archivos de bits, compara 3 modos |

`api.py` pasaría a consumir `HybridSource` en vez de `capture_hybrid_entropy`
directo (cambio mínimo, retrocompatible).

## 3. Pipeline del modo combinado (HybridSource)

```
  bytes_fisicos  = PhysicalSource.get_bytes(k)   # foto + qiskit (secundaria)
  bytes_urandom  = UrandomSource.get_bytes(k)    # kernel CSPRNG (primaria)

  seed = BLAKE2b(bytes_fisicos || bytes_urandom) # mezcla / extractor

  stream = CSPRNG(seed)                           # expansión a N bits
```

### CSPRNG (modo contador, determinista y auditable)

```
  bloque_i = BLAKE2b(seed || counter_i)
  stream   = bloque_0 || bloque_1 || ...
```

Sin estado oculto: mismos inputs → mismo stream. 100% reproducible/auditable.

**Reseed periódico (parámetro `reseed_every`):** cada R bytes de salida se vuelve
a mezclar física+urandom fresca y se deriva un seed nuevo. Esto define cuánta
entropía física consumimos para producir 1.000.000 de bits (ver pregunta de
performance abajo). Modelo idéntico al de un DRBG con reseeding de LavaRand.

## 4. Degradación elegante + health check

- Si `PhysicalSource` falla (no hay `frame.png`, excepción de qiskit, etc.) o
  está marcada como **degradada**, `HybridSource` sigue funcionando con
  `urandom` solo. Nunca lanza fallo catastrófico. Se loguea un `WARNING`.
- **Health check de la física:** si `N` frames/capturas consecutivas son
  idénticas (delta = 0 entre lecturas crudas), se loguea warning y la fuente se
  marca `DEGRADED`. Esto detecta cámara congelada o escena estática manipulada.
- Como la mezcla es un extractor, aunque la física esté `DEGRADED` (constante),
  el seed combinado **no se debilita** respecto a urandom solo (Leftover Hash
  Lemma). El health check es para *observabilidad*, no para seguridad.

## 5. Suite NIST SP 800-22 (`nist_suite.py`)

- Script **independiente** del código de producción.
- Input: archivo binario de bits crudos (los genera `trng/cli.py`).
- Volumen: ≥ 1.000.000 de bits por modo para significancia estadística.
- Output por test: nombre, p-value, PASS/FAIL (threshold p ≥ 0.01).
- 3 modos lado a lado: **solo físico** / **solo urandom** / **combinado**.
- Base propuesta: `nistrng` (ya está instalada y funcionando en `test_nist.py`).

## 6. Notas de plataforma

Estás en **macOS (darwin)**. `RNDADDENTROPY` / re-sembrado del pool del kernel
vía ioctl es **solo Linux**. En mac usamos el CSPRNG en espacio de usuario
(sección 3), que es portable y igual de auditable. `os.urandom()` /
`secrets.token_bytes()` funcionan perfecto en mac.

---

## Preguntas antes de implementar

Te las dejo acá ordenadas; las más importantes te las pregunto también por el
panel interactivo.

1. **Definición de "solo físico":** tu fuente física actual ya es híbrida
   (pixel de la foto **XOR** máscara cuántica de qiskit). El whitening XOR con
   bits cuánticos hace que "solo físico" pase NIST casi trivialmente, porque en
   la práctica ya estás midiendo un RNG cuántico. ¿Querés que "solo físico" sea
   (a) la salida actual con whitening cuántico, o (b) los bytes **crudos** del
   pixel **sin** whitening, para que la comparación NIST sea más honesta sobre
   la calidad real de la fuente física?

2. **Performance / volumen de la física:** generar 1.000.000 de bits desde la
   física es lento (cada captura corre varios circuitos qiskit; ~24 bits por
   captura ⇒ ~42.000 simulaciones). Opciones: (a) generar el 1M completo aunque
   tarde, (b) usar menos bits para el modo físico (ej. 100k) y solo 1M para
   urandom/combinado, (c) optimizar batcheando mediciones cuánticas (más shots
   por circuito). ¿Cuál preferís?

3. **Librería NIST:** ¿mantengo `nistrng` (ya funciona en tu repo) o querés que
   migre a `stevenang/randomness_testsuite`?

4. **Hash de mezcla:** confirmo **BLAKE2b** como default (con SHA-256 como
   fallback configurable), ¿ok?

5. **Construcción del modo combinado para NIST:** ¿el stream combinado se genera
   (a) re-mezclando física+urandom frescas cada bloque (más "puro" pero consume
   mucha física), o (b) sembrando una vez y expandiendo en modo contador (rápido,
   modelo DRBG real de LavaRand)? Mi recomendación es (b) con reseed periódico.

6. **Integración con `api.py`:** ¿reemplazo `capture_hybrid_entropy` por la nueva
   `HybridSource` (recomendado, deja el endpoint usando el pipeline nuevo), o
   prefieres que deje `api.py` intacto y el módulo `trng/` viva 100% aparte?
