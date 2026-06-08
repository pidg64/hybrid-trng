import os
import numpy as np
from nistrng import check_eligibility_all_battery, run_all_battery, pack_sequence, SP800_22R1A_BATTERY
from api import capture_hybrid_entropy

def generate_bit_sequence_hybrid(num_samples=1000):
    """
    Recolecta bits de la entropía cruda (valores RGB blanqueados)
    para armar una secuencia binaria que nistrng pueda evaluar.
    1000 muestras * 24 bits (8R + 8G + 8B) = 24.000 bits.
    """
    print(f"[*] Recolectando entropía Híbrida ({num_samples} iteraciones cuánticas)... Esto puede demorar.")
    bits = []
    for i in range(num_samples):
        # Obtenemos directamente de la función para probar la entropía base,
        # sin el sesgo del hash criptográfico SHA-256 posterior.
        _, _, r, g, b = capture_hybrid_entropy("frame.png")
        
        # Convertimos los canales r, g, b a formato binario estricto (8 bits)
        for val in (r, g, b):
            bin_str = format(val, '08b')
            for bit in bin_str:
                bits.append(int(bit))
                
        if (i + 1) % 100 == 0:
            print(f"    Progreso: {i + 1}/{num_samples} capturas...")

    print(f"[*] Secuencia Híbrida recolectada exitosamente: {len(bits)} bits.")
    return np.array(bits, dtype=int)

def generate_bit_sequence_urandom(num_bits=24000):
    """
    Recolecta bits del CSPRNG del sistema operativo (/dev/urandom)
    como baseline para comparar la calidad de la entropía.
    """
    print(f"[*] Recolectando entropía de /dev/urandom ({num_bits} bits)...")
    # Calculamos cuántos bytes necesitamos
    num_bytes = (num_bits + 7) // 8
    random_bytes = os.urandom(num_bytes)
    
    bits = []
    for b in random_bytes:
        bin_str = format(b, '08b')
        for bit in bin_str:
            bits.append(int(bit))
            
    # Cortamos los bits extras si la división no era exacta
    bits = bits[:num_bits]
    print(f"[*] Secuencia de /dev/urandom recolectada exitosamente: {len(bits)} bits.")
    return np.array(bits, dtype=int)

def evaluate_sequence(binary_sequence, title):
    print(f"\n+++ Evaluando: {title} +++")
    # 2. Verificar qué tests (batteries) de NIST son elegibles para este tamaño
    # 0.01 es el nivel de significancia típico (alpha).
    eligible_battery: dict = check_eligibility_all_battery(binary_sequence, SP800_22R1A_BATTERY)
    
    print(f"[*] Baterías (tests) aplicables para este tamaño de muestra: {len(eligible_battery)}")
    print("[*] Ejecutando análisis estadístico...")

    # 3. Correr los tests
    results = run_all_battery(binary_sequence, eligible_battery, False)

    # 4. Mostrar Resultados
    print(f"\n=== Resultados de Pruebas de Aleatoriedad ({title}) ===")
    passed_tests = 0
    total_tests = len(results)
    
    for result, elapsed_time in results:
        status = "PASSED ✓" if result.passed else "FAILED ✗"
        if result.passed:
            passed_tests += 1
        
        print(f" - {result.name.ljust(35)} | P-Value: {result.score:.5f} | {status}")

    print(f"\nResultado Final {title}: {passed_tests}/{total_tests} tests aprobados.")
    return passed_tests, total_tests

def main():
    print("=== Herramienta de Validación NIST SP 800-22 ===")
    print("Comparativa: Entropía Híbrida vs /dev/urandom\n")
    
    num_samples = 1000
    target_bits = num_samples * 24 # 24000 bits

    # 1. Híbrida
    seq_hybrid = generate_bit_sequence_hybrid(num_samples)
    pass_hybrid, total_hybrid = evaluate_sequence(seq_hybrid, "Entropía Híbrida Cruda")

    # 2. /dev/urandom
    seq_urandom = generate_bit_sequence_urandom(target_bits)
    pass_urandom, total_urandom = evaluate_sequence(seq_urandom, "/dev/urandom")

    # 3. Resumen Final
    print("\n=============================================")
    print("=== RESUMEN COMPARATIVO ===")
    print(f"Entropía Híbrida Cruda: {pass_hybrid}/{total_hybrid} aprobados.")
    print(f"/dev/urandom:           {pass_urandom}/{total_urandom} aprobados.")
    print("=============================================")

if __name__ == "__main__":
    main()