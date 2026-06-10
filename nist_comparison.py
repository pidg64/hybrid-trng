"""
Suite NIST SP 800-22 Rev 1a — comparativa de 3 fuentes de entropía + gráficos.

Script INDEPENDIENTE del código de producción. Compara:

    * solo físico   : foto + qiskit (con whitening cuántico)  -> tu código
    * solo urandom  : /dev/urandom puro (CONTROL / patrón de calibración)
    * combinado     : pipeline híbrido (físico + urandom mezclados con BLAKE2b)

METODOLOGÍA (importante, leer)
------------------------------
NIST SP 800-22 es una herramienta de FALSACIÓN, no de ranking: puede rechazar la
hipótesis de aleatoriedad, pero NO puede decir que una fuente que pasa sea "más
aleatoria" que otra. Por eso la pregunta científica correcta NO es "¿el físico es
mejor que urandom?" (indemostrable), sino:

    "¿El combinado es estadísticamente indistinguible de urandom?"
    (es decir: la mezcla no degrada — Leftover Hash Lemma en la práctica)

CONTROL DE VALIDEZ: la librería `nistrng` tiene tests con bugs de implementación
que hacen fallar incluso a urandom (DFT/CumSum por dtype unsigned; ApEn por una
condición invertida; etc.). Por eso usamos urandom como PATRÓN: un test solo se
considera CONFIABLE si urandom lo pasa. Los tests que urandom reprueba se marcan
como ARTEFACTO y se excluyen del veredicto. (Misma lógica que tu validar_nist.py.)

Dos modos de evaluación:
  * --sequences 1  (default): una secuencia; se reporta el p-value por test y se
    filtra por validez de urandom.
  * --sequences M (M>1): metodología NIST completa: por test se reporta la
    PROPORCIÓN de secuencias que pasan (dentro de la banda esperada) y la
    UNIFORMIDAD de los p-values (P-value of P-values).

Todos los artefactos (.bin, PNG, results.md) viven en --outdir (default nist_output/).
Para limpiar todo: borrar esa carpeta.

Uso:
    python nist_comparison.py --compare --bits 1000000
    python nist_comparison.py --compare --bits 1000000 --sequences 10
    python nist_comparison.py --file nist_output/hybrid.bin --title "Combinado"

Criterio base: un test PASA si p-value >= 0.01 (alpha = 0.01, estándar NIST).
"""

import argparse
import logging
import math
import os
import time
from datetime import datetime

import numpy as np
import scipy.special
from nistrng import SP800_22R1A_BATTERY, check_eligibility_all_battery

ALPHA = 0.01
# Umbral NIST para la uniformidad de los p-values (P-value of P-values).
UNIFORMITY_THRESHOLD = 0.0001


# --------------------------------------------------------------------------- #
#  Carga / generación de bits
# --------------------------------------------------------------------------- #
def load_bits(path: str) -> np.ndarray:
    """Lee un .bin (bytes empaquetados) y devuelve un array de bits.

    dtype int64 con SIGNO: crítico. Varios tests de nistrng hacen 0 -> -1; si el
    array fuera unsigned (uint8), ese -1 se envolvería a 255 y rompería DFT y
    Cumulative Sums dando p-value 0.0000 espurio. int64 lo evita y además no
    desborda en las sumas acumuladas.
    """
    with open(path, "rb") as f:
        raw = f.read()
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8)).astype(np.int64)
    assert bits.min() >= 0 and bits.max() <= 1, "Los bits deben ser 0/1"
    return bits


def ensure_bits_file(mode: str, path: str, total_bits: int, image: str = "frame.png") -> np.ndarray:
    """Devuelve los bits del modo (genera con ``trng`` si falta o si el .bin es chico)."""
    if os.path.exists(path) and len(load_bits(path)) >= total_bits:
        print(f"[*] Reusando archivo existente: {path}")
        return load_bits(path)[:total_bits]

    print(f"[*] Generando '{mode}' ({total_bits:,} bits) -> {path} ...")
    from trng.sources import HybridSource, PhysicalSource, UrandomSource  # import perezoso

    sources = {
        "physical": lambda: PhysicalSource(image_path=image),
        "urandom": lambda: UrandomSource(),
        "hybrid": lambda: HybridSource(physical=PhysicalSource(image_path=image)),
    }
    source = sources[mode]()
    t0 = time.time()
    data = source.get_bytes((total_bits + 7) // 8)
    with open(path, "wb") as f:
        f.write(data)
    print(f"[*] '{mode}' generado en {time.time() - t0:.1f}s | estado fuente: {source.health().value}")
    return load_bits(path)[:total_bits]


# --------------------------------------------------------------------------- #
#  Evaluación NIST (una o varias secuencias)
# --------------------------------------------------------------------------- #
def _run_battery(bits: np.ndarray) -> dict:
    """Corre la batería elegible sobre UNA secuencia. Devuelve {test: (p, passed)}."""
    eligible = check_eligibility_all_battery(bits, SP800_22R1A_BATTERY)
    out = {}
    for i, (name, test) in enumerate(eligible.items(), start=1):
        t0 = time.time()
        print(f"        · ({i}/{len(eligible)}) {name} ...", flush=True)
        result, _ = test.run(bits)
        out[name] = (float(result.score), bool(result.passed))
        logging.getLogger("trng").debug("%s: p=%.5f (%.1fs)", name, result.score, time.time() - t0)
    return out


def evaluate(all_bits: np.ndarray, n_seq: int, bits_per_seq: int, title: str) -> dict:
    """Evalúa ``n_seq`` secuencias de ``bits_per_seq`` bits. Devuelve, por test,
    las listas de p-values y de PASS/FAIL acumuladas sobre las secuencias."""
    print(f"\n+++ Evaluando: {title} ({n_seq} secuencia(s) × {bits_per_seq:,} bits) +++")
    per_test = {}
    for s in range(n_seq):
        if n_seq > 1:
            print(f"    Secuencia {s + 1}/{n_seq}:")
        seg = all_bits[s * bits_per_seq:(s + 1) * bits_per_seq]
        for name, (p, ok) in _run_battery(seg).items():
            entry = per_test.setdefault(name, {"p": [], "pass": []})
            entry["p"].append(p)
            entry["pass"].append(ok)
    return per_test


# --------------------------------------------------------------------------- #
#  Resumen estadístico por test (single-seq vs multi-seq)
# --------------------------------------------------------------------------- #
def _uniformity_pvalue(pvalues: list) -> float:
    """P-value of P-values (NIST): chi-cuadrado de los p-values en 10 bins."""
    m = len(pvalues)
    counts, _ = np.histogram(pvalues, bins=10, range=(0.0, 1.0))
    expected = m / 10.0
    chi_sq = np.sum((counts - expected) ** 2 / expected)
    return float(scipy.special.gammaincc(9 / 2.0, chi_sq / 2.0))


def test_verdict(stats: dict, n_seq: int):
    """Devuelve ``(ok, celda)`` para un test: ``ok`` resume si lo pasa según el
    modo (single = p>=alpha; multi = proporción dentro de banda + uniformidad)."""
    pvals, passes = stats["p"], stats["pass"]
    if n_seq == 1:
        ok = passes[0]
        return ok, f"{pvals[0]:.4f} {'PASS' if ok else 'FAIL'}"
    # Multi-secuencia: metodología NIST real.
    proportion = float(np.mean(passes))
    low = (1 - ALPHA) - 3 * math.sqrt(ALPHA * (1 - ALPHA) / n_seq)  # banda inferior aceptable
    uniformity = _uniformity_pvalue(pvals)
    ok = proportion >= low and uniformity >= UNIFORMITY_THRESHOLD
    return ok, f"{proportion * 100:.0f}% u={uniformity:.3f} {'OK' if ok else 'X'}"


def _union_of_tests(summaries: dict):
    tests = []
    for per_test in summaries.values():
        for name in per_test:
            if name not in tests:
                tests.append(name)
    return tests


def build_report(summaries: dict, n_seq: int):
    """Calcula, por test, el veredicto de cada modo y la validez (control urandom).

    Devuelve ``(tests, cells, valid)`` donde:
      * cells[mode][test] = (ok, celda_texto)
      * valid[test] = True si urandom pasa el test (=> test confiable)
    """
    tests = _union_of_tests(summaries)
    cells = {mode: {} for mode in summaries}
    has_control = "Solo urandom" in summaries
    valid = {}
    for test in tests:
        for mode, per_test in summaries.items():
            if test in per_test:
                cells[mode][test] = test_verdict(per_test[test], n_seq)
            else:
                cells[mode][test] = (None, "—")
        # Validez por control: un test es confiable solo si urandom lo pasa.
        # Sin control (modo --file), no podemos filtrar -> se asume válido.
        if has_control:
            valid[test] = cells["Solo urandom"].get(test, (None,))[0] is True
        else:
            valid[test] = True
    return tests, cells, valid


# --------------------------------------------------------------------------- #
#  Impresión: tabla + veredicto
# --------------------------------------------------------------------------- #
def print_report(summaries: dict, n_seq: int):
    tests, cells, valid = build_report(summaries, n_seq)
    modes = list(summaries.keys())

    print("\n=================== RESUMEN COMPARATIVO ===================")
    if n_seq > 1:
        print("(multi-secuencia: celda = % de secuencias que pasan | u=uniformidad)")
    print("Validez = ✔ si urandom (control) pasa el test; ✘ = artefacto de librería\n")

    header = "Test".ljust(34) + "Val  " + "".join(m.ljust(20) for m in modes)
    print(header)
    print("-" * len(header))
    for test in tests:
        val = "✔" if valid[test] else "✘"
        line = test.ljust(34) + f" {val}   "
        for mode in modes:
            line += cells[mode][test][1].ljust(20)
        print(line)
    print("-" * len(header))
    print_verdict(tests, cells, valid, modes)
    return tests, cells, valid


def print_verdict(tests, cells, valid, modes):
    valid_tests = [t for t in tests if valid[t]]
    n_valid = len(valid_tests)
    print(f"\nTests CONFIABLES (urandom los pasa): {n_valid}/{len(tests)}")
    excluded = [t for t in tests if not valid[t]]
    if excluded:
        print(f"Excluidos como ARTEFACTO de librería: {', '.join(excluded)}")

    print("\nSobre los tests confiables:")
    for mode in modes:
        passed = sum(1 for t in valid_tests if cells[mode][t][0] is True)
        print(f"  - {mode:14s}: {passed}/{n_valid}")

    # Veredicto científico: combinado vs urandom (no degradación).
    combo = "Combinado"
    if combo in modes and n_valid > 0:
        combo_pass = sum(1 for t in valid_tests if cells[combo][t][0] is True)
        print("\nVEREDICTO:")
        if combo_pass == n_valid:
            print(f"  ✓ El combinado pasa los {n_valid} tests confiables, igual que urandom.")
            print("    => Indistinguible de urandom: la mezcla NO degrada (era lo que queríamos).")
        else:
            print(f"  ⚠ El combinado pasa {combo_pass}/{n_valid} tests confiables.")
            print("    Revisar: con más secuencias/bits esto debería igualar a urandom.")
    print("  · Nota: estos tests NO pueden afirmar que 'físico es mejor' que un CSPRNG;")
    print("    solo dicen 'sin evidencia de no-aleatoriedad'. No interpretar más PASS como 'mejor'.")
    print("===========================================================")


# --------------------------------------------------------------------------- #
#  Gráfico
# --------------------------------------------------------------------------- #
def plot_results(summaries, n_seq, tests, cells, valid, out_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    modes = list(summaries.keys())
    colors = ["#1f77b4", "#2ca02c", "#d62728", "#9467bd"]

    def metric(mode, test):
        per_test = summaries[mode].get(test)
        if per_test is None:
            return 0.0
        if n_seq == 1:
            return per_test["p"][0]
        return float(np.mean(per_test["pass"]))  # proporción

    fig, ax = plt.subplots(figsize=(16, 7))
    x = np.arange(len(tests))
    width = 0.8 / max(len(modes), 1)
    for i, mode in enumerate(modes):
        vals = [metric(mode, t) for t in tests]
        ax.bar(x + i * width, vals, width, label=mode, color=colors[i % len(colors)])

    if n_seq == 1:
        ax.axhline(ALPHA, color="black", ls="--", lw=1, label=f"umbral α={ALPHA}")
        ax.set_ylabel("p-value")
    else:
        low = (1 - ALPHA) - 3 * math.sqrt(ALPHA * (1 - ALPHA) / n_seq)
        ax.axhline(low, color="black", ls="--", lw=1, label="banda mínima NIST")
        ax.set_ylabel("proporción de secuencias que pasan")

    # Sombrear los tests no confiables (urandom falla -> artefacto).
    for j, t in enumerate(tests):
        if not valid[t]:
            ax.axvspan(j - 0.1, j + 0.8 - width, color="grey", alpha=0.12)

    labels = [t + ("" if valid[t] else "\n(artefacto)") for t in tests]
    ax.set_xticks(x + width * (len(modes) - 1) / 2)
    ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=8)
    ax.set_title("NIST SP 800-22: físico vs urandom vs combinado "
                 f"({n_seq} secuencia(s)). Zonas grises = test no confiable.")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    print(f"\n[*] Gráfico guardado en: {out_path}")


# --------------------------------------------------------------------------- #
#  results.md
# --------------------------------------------------------------------------- #
def write_results_md(summaries, n_seq, bits_per_seq, tests, cells, valid, path, png_name):
    modes = list(summaries.keys())
    valid_tests = [t for t in tests if valid[t]]
    L = []
    L.append("# Resultados NIST SP 800-22 — comparativa de fuentes")
    L.append("")
    L.append(f"- **Fecha:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"- **Secuencias × bits:** {n_seq} × {bits_per_seq:,}")
    L.append(f"- **Umbral (alpha):** {ALPHA}")
    L.append(f"- **Gráfico:** `{png_name}`")
    L.append("")
    L.append("## Tabla (Val = ✔ si urandom pasa el test → confiable)")
    L.append("")
    L.append("| Test | Val | " + " | ".join(modes) + " |")
    L.append("|" + "---|" * (len(modes) + 2))
    for t in tests:
        val = "✔" if valid[t] else "✘ artefacto"
        cellstr = " | ".join(cells[m][t][1] for m in modes)
        L.append(f"| {t} | {val} | {cellstr} |")
    L.append("")
    L.append("## Veredicto (solo sobre tests confiables)")
    L.append("")
    L.append(f"- Tests confiables (urandom los pasa): **{len(valid_tests)}/{len(tests)}**")
    for m in modes:
        passed = sum(1 for t in valid_tests if cells[m][t][0] is True)
        L.append(f"- {m}: **{passed}/{len(valid_tests)}**")
    if "Combinado" in modes and valid_tests:
        cp = sum(1 for t in valid_tests if cells["Combinado"][t][0] is True)
        if cp == len(valid_tests):
            L.append("")
            L.append("> ✓ El combinado iguala a urandom en todos los tests confiables: "
                     "**la mezcla no degrada** (Leftover Hash Lemma).")
    L.append("")
    L.append("## Cómo interpretarlo (honesto)")
    L.append("")
    L.append("- NIST falsa, no rankea: **no** se puede concluir 'físico > urandom'. "
             "Más PASS ≠ mejor.")
    L.append("- Tests marcados ✘ (artefacto): urandom los reprueba → son bugs de `nistrng`, "
             "no propiedades de la entropía. Se excluyen.")
    L.append("- La afirmación válida es: **el combinado es indistinguible de urandom**.")
    L.append("")
    L.append("> Para limpiar todo: borrá esta carpeta.")
    L.append("")
    with open(path, "w") as f:
        f.write("\n".join(L))
    print(f"[*] Resumen guardado en: {path}")


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(description="Suite NIST SP 800-22 comparativa.")
    parser.add_argument("--file", help="Evaluar un único .bin.")
    parser.add_argument("--title", default="Secuencia", help="Título para --file.")
    parser.add_argument("--compare", action="store_true", help="Comparar física/urandom/combinado.")
    parser.add_argument("--bits", type=int, default=1_000_000, help="Bits POR SECUENCIA (default 1e6).")
    parser.add_argument("--sequences", type=int, default=1,
                        help="Cantidad de secuencias (>1 activa proporción + uniformidad NIST).")
    parser.add_argument("--image", default="frame.png", help="Imagen fuente física.")
    parser.add_argument("--no-plot", action="store_true", help="No generar PNG.")
    parser.add_argument("--outdir", default="nist_output", help="Carpeta de artefactos.")
    args = parser.parse_args()

    # Solo nuestros logs (trng.*); root en WARNING para callar qiskit.
    logging.basicConfig(level=logging.WARNING, format="    %(message)s")
    logging.getLogger("trng").setLevel(logging.INFO)

    if args.file:
        bits = load_bits(args.file)
        summaries = {args.title: evaluate(bits, 1, len(bits), args.title)}
        print_report(summaries, 1)
        return

    if not args.compare:
        parser.error("Indicá --file <archivo> o --compare.")

    os.makedirs(args.outdir, exist_ok=True)
    png_path = os.path.join(args.outdir, "nist_comparison_results.png")
    md_path = os.path.join(args.outdir, "results.md")
    total_bits = args.bits * args.sequences

    modes = [
        ("physical", "Solo físico", "physical.bin"),
        ("urandom", "Solo urandom", "urandom.bin"),
        ("hybrid", "Combinado", "hybrid.bin"),
    ]

    # ---- FASE 1: generar (o reusar) los bits de los 3 modos ----
    print("\n########## FASE 1: GENERACIÓN DE BITS ##########")
    bits_by_label = {}
    for mode, label, filename in modes:
        path = os.path.join(args.outdir, filename)
        bits_by_label[label] = ensure_bits_file(mode, path, total_bits, args.image)

    # ---- FASE 2: correr la batería NIST ----
    print("\n########## FASE 2: TESTS NIST SP 800-22 ##########")
    if args.bits >= 1_000_000:
        print("[!] Aviso: Serial y ApproximateEntropy son O(2^m·n) en Python puro;")
        print("    a 1e6 bits cada secuencia puede tardar varios minutos.")
    summaries = {}
    for _mode, label, _filename in modes:
        summaries[label] = evaluate(bits_by_label[label], args.sequences, args.bits, label)

    # ---- FASE 3: reporte ----
    tests, cells, valid = print_report(summaries, args.sequences)
    if not args.no_plot:
        plot_results(summaries, args.sequences, tests, cells, valid, png_path)
    write_results_md(summaries, args.sequences, args.bits, tests, cells, valid, md_path,
                     os.path.basename(png_path))
    print(f"\n[*] Todo en: {args.outdir}/  (borrala para limpiar)")


if __name__ == "__main__":
    main()
