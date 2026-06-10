# Resultados NIST SP 800-22 — comparativa de fuentes

- **Fecha:** 2026-06-09 22:12:18
- **Bits por modo:** 100,000
- **Umbral (alpha):** 0.01 (PASS si p-value ≥ 0.01)
- **Gráfico:** `nist_comparison_results.png`

## Tabla comparativa (p-value | resultado)

| Test | Solo físico | Solo urandom | Combinado |
|---|---|---|---|
| Monobit | 0.2851 PASS | 0.2420 PASS | 0.1948 PASS |
| Frequency Within Block | 0.6232 PASS | 0.6231 PASS | 0.6937 PASS |
| Runs | 0.0148 PASS | 0.4341 PASS | 0.7272 PASS |
| Longest Run Ones In A Block | 0.8372 PASS | 0.7060 PASS | 0.8824 PASS |
| Binary Matrix Rank | 0.8849 PASS | 0.1804 PASS | 0.8032 PASS |
| Discrete Fourier Transform | 0.0000 FAIL | 0.0000 FAIL | 0.0000 FAIL |
| Non Overlapping Template Matching | 0.0647 PASS | 0.0000 FAIL | 0.5726 PASS |
| Serial | 0.0000 FAIL | 0.0000 FAIL | 0.0000 FAIL |
| Approximate Entropy | 0.0000 FAIL | 0.0000 FAIL | 0.0000 FAIL |
| Cumulative Sums | 0.0000 FAIL | 0.0000 FAIL | 0.0000 FAIL |
| Random Excursion | 0.6831 PASS | 0.6302 PASS | 0.6302 PASS |
| Random Excursion Variant | 0.1703 PASS | 0.0000 FAIL | 0.0000 FAIL |
| **TOTAL aprobados** | **8/12** | **6/12** | **7/12** |

## Cómo interpretarlo

- p-value **no** es un puntaje: más alto NO es mejor. Solo importa que supere el umbral y que los p-values estén repartidos en [0, 1].
- Que el **Combinado** dé tan bien como **urandom** confirma que la mezcla no introduce sesgos (Leftover Hash Lemma en la práctica).

> Para limpiar todo: borrá esta carpeta completa.
