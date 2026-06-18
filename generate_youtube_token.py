import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow

# Permisos necesarios para subir videos, poner miniaturas y leer listas de reproducción
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload', 
    'https://www.googleapis.com/auth/youtube'
]

def generate_token():
    token_path = 'youtube_token.pickle'
    secrets_path = 'client_secrets.json'

    if not os.path.exists(secrets_path):
        print(f"[-] Error: Falta el archivo '{secrets_path}'. Asegúrate de tenerlo en esta carpeta.")
        return

    print("Iniciando el proceso de autenticación con YouTube...")
    print("Se abrirá una ventana en tu navegador web. Por favor, inicia sesión con tu cuenta y acepta los permisos.")
    
    try:
        # Configuramos el flujo OAuth 2.0 usando el archivo client_secrets.json
        flow = InstalledAppFlow.from_client_secrets_file(secrets_path, SCOPES)
        
        # Forzamos la obtención de un Refresh Token usando 'offline' y 'consent'
        credentials = flow.run_local_server(
            port=0,
            access_type='offline',
            prompt='consent'
        )

        # Guardamos las credenciales para usos futuros
        with open(token_path, 'wb') as token_file:
            pickle.dump(credentials, token_file)
            
        print("\n[+] ¡Éxito! El token se ha generado correctamente.")
        print(f"[+] Archivo guardado como: {token_path}")
        print("Ahora puedes usar la aplicación principal normalmente.")
        
    except Exception as e:
        print(f"\n[-] Ocurrió un error al generar el token: {e}")

if __name__ == "__main__":
    generate_token()
