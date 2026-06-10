# Resultados NIST SP 800-22 — comparativa de fuentes

- **Fecha:** 2026-06-09 23:08:22
- **Secuencias × bits:** 1 × 10,000,000
- **Umbral (alpha):** 0.01
- **Gráfico:** `nist_comparison_results.png`

## Tabla (Val = ✔ si urandom pasa el test → confiable)

| Test | Val | Solo físico | Solo urandom | Combinado |
|---|---|---|---|---|
| monobit | ✔ | 0.1344 PASS | 0.3329 PASS | 0.1113 PASS |
| frequency_within_block | ✔ | 0.1397 PASS | 0.4935 PASS | 0.4501 PASS |
| runs | ✔ | 0.3615 PASS | 0.0556 PASS | 0.5586 PASS |
| longest_run_ones_in_a_block | ✔ | 0.6589 PASS | 0.3687 PASS | 0.6177 PASS |
| binary_matrix_rank | ✔ | 0.3971 PASS | 0.3925 PASS | 0.1910 PASS |
| dft | ✘ artefacto | 0.0000 FAIL | 0.0000 FAIL | 0.0000 FAIL |
| non_overlapping_template_matching | ✔ | 1.0000 PASS | 0.3557 PASS | 0.0172 PASS |
| overlapping_template_matching | ✘ artefacto | 0.0000 FAIL | 0.0000 FAIL | 0.0000 FAIL |
| maurers_universal | ✘ artefacto | 0.0005 FAIL | 0.0005 FAIL | 0.0005 FAIL |
| linear_complexity | ✘ artefacto | 0.0000 FAIL | 0.0000 FAIL | 0.0000 FAIL |
| serial | ✘ artefacto | 0.0000 FAIL | 0.0000 FAIL | 0.0000 FAIL |
| approximate_entropy | ✘ artefacto | 0.0000 FAIL | 0.0000 FAIL | 0.0000 FAIL |
| cumulative sums | ✘ artefacto | 0.0000 FAIL | 0.0000 FAIL | 0.0000 FAIL |
| random_excursion | ✘ artefacto | 0.6302 FAIL | 0.6831 FAIL | 0.6831 FAIL |
| random_excursion_variant | ✔ | 0.0000 FAIL | 0.1703 PASS | 0.1703 PASS |

## Veredicto (solo sobre tests confiables)

- Tests confiables (urandom los pasa): **7/15**
- Solo físico: **6/7**
- Solo urandom: **7/7**
- Combinado: **7/7**

> ✓ El combinado iguala a urandom en todos los tests confiables: **la mezcla no degrada** (Leftover Hash Lemma).

## Cómo interpretarlo (honesto)

- NIST falsa, no rankea: **no** se puede concluir 'físico > urandom'. Más PASS ≠ mejor.
- Tests marcados ✘ (artefacto): urandom los reprueba → son bugs de `nistrng`, no propiedades de la entropía. Se excluyen.
- La afirmación válida es: **el combinado es indistinguible de urandom**.

> Para limpiar todo: borrá esta carpeta.
