#!/usr/bin/env python3
import os
import sys
import subprocess
import json
import argparse
import re
import datetime
import pickle
import yt_dlp
from google import genai
from dotenv import load_dotenv
from static_ffmpeg import run

# Librerías oficiales de Google para la API de YouTube
try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except ImportError:
    print("[-] Error: Faltan las librerías de la API de YouTube.", file=sys.stderr)
    print("Por favor ejecuta: pip install google-api-python-client google-auth-oauthlib google-auth-httplib2", file=sys.stderr)
    sys.exit(1)

# Cargar variables de entorno (.env)
load_dotenv()

# Inicializar el cliente oficial de Gemini (Librería moderna)
try:
    client = genai.Client()
except Exception as e:
    print(f"Aviso al inicializar Gemini: {e}. Asegúrate de tener GEMINI_API_KEY en tu .env", file=sys.stderr)

# Inicializar FFmpeg en el entorno usando la función correcta
ffmpeg_path, ffprobe_path = run.get_or_fetch_platform_executables_else_raise()

# Variables globales para control de fallos y limpieza dinámica
TRACKED_OUTPUTS = []
TEMP_FILES = []
ERROR_LOG = []
SUCCESS_LOG = []
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

def log_error(step_name, detail):
    """Registra un error interno para el informe final."""
    ERROR_LOG.append(f"[-] ERROR EN PASO [{step_name}]: {detail}")

def ask_to_continue(step_name, error_detail):
    """
    Pausa el flujo, muestra la falla y pregunta al usuario si desea continuar.
    Si elige 'no', limpia absolutamente todo (incluidos temporales) y aborta.
    """
    log_error(step_name, error_detail)
    print(f"\n⚠️  ¡ATENCIÓN! Ocurrió un error en el paso: {step_name}")
    print(f"Detalle del error: {error_detail}")
    
    while True:
        choice = input("¿Deseas continuar con el resto de los pasos? (s/n): ").strip().lower()
        if choice in ['s', 'si', 'yes']:
            print("-> Continuando con el pipeline a pesar de la falla...\n")
            return True
        if choice in ['n', 'no']:
            print("\n❌ Cancelando pipeline. Iniciando rollback total de archivos generados y temporales...")
            
            # Limpiar salidas principales rastreadas
            for path in TRACKED_OUTPUTS:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                        print(f"   [Eliminado] {path}")
                    except OSError:
                        pass
            
            # Limpiar archivos temporales estrictamente bajo cancelación
            for temp_path in TEMP_FILES:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                        print(f"   [Temporal Eliminado] {temp_path}")
                    except OSError:
                        pass
                        
            print("Abandono completado de forma limpia. Saliendo.")
            sys.exit(1)

def refresh_windows_path():
    """Refresca las variables de entorno PATH en Windows leyendo directamente del registro."""
    if sys.platform == 'win32':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as key:
                machine_path = winreg.QueryValueEx(key, "Path")[0]
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                    user_path = winreg.QueryValueEx(key, "Path")[0]
            except FileNotFoundError:
                user_path = ""
            
            combined_path = machine_path + ";" + user_path
            os.environ["PATH"] = os.path.expandvars(combined_path)
        except Exception as e:
            print(f"Aviso: No se pudo refrescar el PATH desde el registro: {e}", file=sys.stderr)

def get_video_info(file_path):
    """Usa ffprobe para obtener información técnica del video."""
    cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', '-show_format', file_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error al analizar el video '{file_path}' con ffprobe.", file=sys.stderr)
        raise e
        
    video_stream = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), None)
    audio_stream = next((s for s in info.get('streams', []) if s.get('codec_type') == 'audio'), None)
    
    if not video_stream:
        raise ValueError(f"El archivo '{file_path}' no contiene pistas de video válidas.")
        
    width = int(video_stream.get('width', 1920))
    height = int(video_stream.get('height', 1080))
    
    fps = 30.0
    if 'r_frame_rate' in video_stream:
        try:
            num, den = map(int, video_stream['r_frame_rate'].split('/'))
            if den != 0:
                fps = num / den
        except Exception:
            pass
            
    duration = 0.0
    if 'format' in info and 'duration' in info['format']:
        try:
            duration = float(info['format']['duration'])
        except Exception:
            pass
            
    has_audio = audio_stream is not None
    return {'width': width, 'height': height, 'fps': fps, 'duration': duration, 'has_audio': has_audio}

def download_youtube_video(url, temp_output_path):
    """Descarga el video de YouTube en formato MP4 usando la biblioteca yt-dlp."""
    print(f"\n[1/5] Descargando video de YouTube: {url}")
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': temp_output_path,
        'merge_output_format': 'mp4',
        'quiet': False,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    
    if not os.path.exists(temp_output_path):
        base, _ = os.path.splitext(temp_output_path)
        for f in os.listdir(os.path.dirname(temp_output_path) or '.'):
            if f.startswith(os.path.basename(base)) and f.endswith('.mp4'):
                temp_output_path = os.path.join(os.path.dirname(temp_output_path), f)
                break
                
    if not os.path.exists(temp_output_path):
        raise FileNotFoundError("No se pudo localizar el video descargado en el disco.")
        
    print(f"-> Descarga completada con éxito. Archivo temporal: {temp_output_path}")
    return temp_output_path

def trim_video(input_path, output_path, start_seconds=0.0, end_seconds=0.0):
    """Recorta segundos al inicio y/o al final de un video usando FFmpeg."""
    if start_seconds == 0.0 and end_seconds == 0.0:
        return input_path

    info = get_video_info(input_path)
    total_duration = info['duration']
    trim_start = start_seconds
    trim_duration = total_duration - start_seconds - end_seconds

    if trim_duration <= 0:
        raise ValueError(f"El tiempo a recortar ({start_seconds}s inicio, {end_seconds}s fin) excede la duración total ({total_duration}s).")

    print(f"\n[Recorte] Aplicando recorte al video descargado:")
    cmd = [
        'ffmpeg', '-y', '-ss', str(trim_start), '-i', input_path, '-t', str(trim_duration),
        '-c:v', 'libx264', '-crf', '18', '-preset', 'fast', '-c:a', 'aac', '-b:a', '192k', output_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"-> Recorte finalizado con éxito. Nuevo archivo intermedio: {output_path}")
    return output_path

def clean_vtt_subtitles(vtt_content):
    """Limpia un string en formato WebVTT para Gemini."""
    lines = vtt_content.splitlines()
    cleaned_lines = []
    timestamp_regex = re.compile(r'(\d{2}:)?\d{2}:\d{2}\.\d{3}')
    html_regex = re.compile(r'<[^>]*>')

    for line in lines:
        line = line.strip()
        if not line or line.startswith('WEBVTT') or line.startswith('Kind:') or line.startswith('Language:') or timestamp_regex.search(line):
            continue
        line_clean = html_regex.sub('', line).strip()
        if line_clean and line_clean not in cleaned_lines:
            cleaned_lines.append(line_clean)
    return " ".join(cleaned_lines)

def get_video_context(url):
    """Extrae tanto los metadatos como la transcripción real del video con yt-dlp."""
    print(f"\n[2/5] Extrayendo contexto y transcripción real con yt-dlp...")
    meta_opts = {'quiet': True, 'no_warnings': True}
    with yt_dlp.YoutubeDL(meta_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get('title')
        original_desc = info.get('description', '')

    temp_sub_prefix = "temp_transcript_extraction"
    sub_opts = {
        'writesubtitles': True, 'writeautomaticsub': True,
        'subtitleslangs': ['es-419', 'es', 'es-AR', 'en'], 'skip_download': True,
        'outtmpl': temp_sub_prefix, 'quiet': True, 'no_warnings': True,
    }
    transcript_text = ""
    with yt_dlp.YoutubeDL(sub_opts) as ydl:
        try: ydl.download([url])
        except Exception: pass

    vtt_file = None
    for f in os.listdir('.'):
        if f.startswith(temp_sub_prefix) and f.endswith('.vtt'):
            vtt_file = f
            break

    if vtt_file and os.path.exists(vtt_file):
        try:
            with open(vtt_file, 'r', encoding='utf-8') as file:
                raw_vtt = file.read()
            transcript_text = clean_vtt_subtitles(raw_vtt)
            print("-> Transcripción real obtenida y procesada con éxito a través de yt-dlp.")
        except Exception as e:
            print(f"-> Aviso: Error leyendo el archivo de transcripción: {e}")
        finally:
            try: os.remove(vtt_file)
            except OSError: pass
    
    if transcript_text:
        return f"Título Original: {title}\n\nTranscripción real del video:\n{transcript_text}"
    return f"Título Original: {title}\n\nDescripción Original:\n{original_desc}"

def generate_marketing_assets(video_context):
    """Llama a Gemini para redactar la descripción y proponer un único título (JSON)."""
    print(f"-> Conectando con Gemini para estructurar la descripción y el título...")
    if "GEMINI_API_KEY" not in os.environ:
        raise ValueError("No se encontró la variable GEMINI_API_KEY en el archivo .env")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, "landing_pages.txt"), 'r', encoding='utf-8') as f: landings = f.read()
    with open(os.path.join(script_dir, "descripcion_ejemplo.txt"), 'r', encoding='utf-8') as f: ejemplo = f.read()
    with open(os.path.join(script_dir, "titulos_ejemplo.txt"), 'r', encoding='utf-8') as f: titulos_referencia = f.read()
    
    prompt = f"""
    Eres un experto en SEO para YouTube y redactor de contenidos corporativos de la empresa Pronectis.
    Genera dos recursos clave basados en este contexto:
    1. Una descripción definitiva unificada bajo las reglas corporativas.
    2. Un (1) único título definitivo optimizado.

    CONTEXTO DEL VIDEO: {video_context}
    LANDINGS: {landings}
    EJEMPLOS TÍTULOS: {titulos_referencia}
    ESTRUCTURA EJEMPLO: {ejemplo}

    Devuelve OBLIGATORIAMENTE un JSON con las llaves "descripcion" y "titulo" (MAX 100 CARACTERES PARA EL TITULO), sin textos adicionales:
    {{
        "descripcion": "Texto final...",
        "titulo": "Título final..."
    }}
    """
    response = client.models.generate_content(
        model='gemini-2.5-flash', contents=prompt, config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text)

def concatenate_videos(clips_paths, output_path):
    """Concatena la lista de clips multimedia usando FFmpeg."""
    print(f"\n[4/5] Procesando y concatenando {len(clips_paths)} videos con FFmpeg...")
    clips_meta = []
    for path in clips_paths:
        info = get_video_info(path)
        clips_meta.append((path, info))
        
    target_clip = max(clips_meta, key=lambda c: c[1]['width'] * c[1]['height'])
    target_w = target_clip[1]['width']
    target_h = target_clip[1]['height']
    target_fps = max(c[1]['fps'] for c in clips_meta)
    
    print(f"-> Normalizando propiedades multimedia:")
    print(f"   Resolución objetivo: {target_w}x{target_h} | FPS: {target_fps:.2f}")

    ffmpeg_cmd = ['ffmpeg', '-y']
    for path, _ in clips_meta:
        ffmpeg_cmd.extend(['-i', path])
        
    num_inputs = len(clips_meta)
    extra_inputs = []
    video_filters = []
    audio_filters = []
    
    for i, (path, info) in enumerate(clips_meta):
        v_filter = (
            f"[{i}:v]scale=w={target_w}:h={target_h}:force_original_aspect_ratio=decrease,"
            f"pad=w={target_w}:h={target_h}:x=({target_w}-iw)/2:y=({target_h}-ih)/2:color=black,"
            f"fps=fps={target_fps},format=yuv420p[v{i}]"
        )
        video_filters.append(v_filter)
        
        if info['has_audio']:
            a_filter = f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]"
            audio_filters.append(a_filter)
        else:
            silent_idx = num_inputs + len(extra_inputs)
            duration = info['duration'] if info['duration'] > 0 else 5.0
            extra_inputs.append((duration, silent_idx))
            a_filter = f"[{silent_idx}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]"
            audio_filters.append(a_filter)
            
    for duration, _ in extra_inputs:
        ffmpeg_cmd.extend(['-f', 'lavfi', '-t', str(duration), '-i', 'anullsrc=r=48000:cl=stereo'])
        
    filter_complex = ";".join(video_filters + audio_filters)
    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(num_inputs))
    filter_complex += f";{concat_inputs}concat=n={num_inputs}:v=1:a=1[v_out][a_out]"
    
    # IMPORTANTE: Corregido con el guion correspondiente ('-filter_complex')
    ffmpeg_cmd.extend(['-filter_complex', filter_complex])
    ffmpeg_cmd.extend(['-map', '[v_out]', '-map', '[a_out]'])
    ffmpeg_cmd.extend(['-c:v', 'libx264', '-preset', 'medium', '-crf', '23', '-c:a', 'aac', '-b:a', '192k', output_path])
    
    subprocess.run(ffmpeg_cmd, check=True)
    print(f"-> Fusión multimedia completada con éxito: {output_path}")

# =========================================================================
# NUEVAS FUNCIONES COMPONENTES DE LA API DE YOUTUBE DATA v3
# =========================================================================
def get_youtube_service():
    """Autentica y gestiona la sesión de YouTube guardando un token local."""
    credentials = None
    token_path = 'youtube_token.pickle'
    secrets_path = 'client_secrets.json'

    if os.path.exists(token_path):
        with open(token_path, 'rb') as token:
            credentials = pickle.load(token)

    # 🚨 LOGICA CORREGIDA: Si el token existe pero expiró, lo refrescamos usando el Refresh Token
    if credentials and credentials.expired and credentials.refresh_token:
        from google.auth.transport.requests import Request
        print("-> [Seguridad] El token de acceso expiró. Renovándolo automáticamente...")
        credentials.refresh(Request())
        # Pisamos el archivo con el token actualizado para que dure otra hora más de fondo
        with open(token_path, 'wb') as token:
            pickle.dump(credentials, token)

    # Si de verdad no hay credenciales válidas (o no se pudieron refrescar)
    if not credentials or not credentials.valid:
        if not os.path.exists(secrets_path):
            raise FileNotFoundError(f"Falta el archivo indispensable '{secrets_path}' en el directorio.")
        
        print("\n[OAuth] Abriendo el navegador para validar los accesos con tu cuenta Workspace...")
        flow = InstalledAppFlow.from_client_secrets_file(secrets_path, SCOPES)
        
        # 🚨 CAMBIO CRUCIAL: Forzamos a Google a que nos entregue el Refresh Token definitivo
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            prompt='consent'
        )
        
        # Ejecutamos el servidor local usando los parámetros offline pre-configurados
        credentials = flow.run_local_server(
            port=0,
            access_type='offline',
            prompt='consent'
        )
        
        with open(token_path, 'wb') as token:
            pickle.dump(credentials, token)
            print("-> Token permanente guardado exitosamente para usos automatizados.")

    return build('youtube', 'v3', credentials=credentials)

def upload_video_to_youtube(video_file, title, description, privacy_status='private'):
    """Sube el entregable final a YouTube de forma resumable por Chunks."""
    print(f"\n[YouTube] Iniciando la transferencia multimedia para: {video_file}")
    try:
        youtube = get_youtube_service()
    except Exception as e:
        print(f"[-] Fallo crítico al invocar las credenciales de YouTube: {e}")
        return False

    body = {
        'snippet': {
            'title': title,
            'description': description,
            'tags': ['Pronectis', 'Google Workspace', 'Automatización', 'SEO'],
            'categoryId': '28' # Ciencia y Tecnología
        },
        'status': {
            'privacyStatus': privacy_status,
            'selfDeclaredMadeForKids': False
        }
    }

    media = MediaFileUpload(video_file, chunksize=1024*1024, resumable=True, mimetype='video/mp4')
    request = youtube.videos().insert(part=','.join(body.keys()), body=body, media_body=media)

    response = None
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"   -> Progreso de subida corporativa: {int(status.progress() * 100)}%...")
        except Exception as e:
            print(f"[-] Error intermedio de red en la API de YouTube: {e}")
            return False

    if "id" in response:
        print(f"-> ¡Video subido a YouTube exitosamente! ID asignado: {response['id']}")
        return response["id"]
    return False

# =========================================================================
# CUERPO PRINCIPAL DEL PIPELINE
# =========================================================================
def main():
    refresh_windows_path()
    
    parser = argparse.ArgumentParser(description="Automatización Pronectis total.")
    parser.add_argument('-u', '--url', required=True, help="URL de YouTube a procesar.")
    parser.add_argument('--out', default='final_output.mp4', help="Nombre del video final.")
    parser.add_argument('-ts', '--trim-start', type=float, default=0.0, help="Recorte inicio.")
    parser.add_argument('-te', '--trim-end', type=float, default=0.0, help="Recorte fin.")
    args = parser.parse_args()
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    intro_path = os.path.join(script_dir, "Intro Pronectis.mp4")
    outro_path = os.path.join(script_dir, "Outro Pronectis.mp4")
    
    if not os.path.exists(intro_path) or not os.path.exists(outro_path):
        print("Error: No se encuentran las plantillas multimedia Intro/Outro.", file=sys.stderr)
        sys.exit(1)
        
    temp_yt_video = "temp_downloaded_yt_video.mp4"
    temp_trimmed_video = "temp_trimmed_yt_video.mp4"
    
    TEMP_FILES.extend([temp_yt_video, temp_trimmed_video])
    TRACKED_OUTPUTS.append(args.out)
    base_output_name = os.path.splitext(args.out)[0]
    output_desc_txt = base_output_name + "_descripcion.txt"
    output_title_txt = base_output_name + "_titulo.txt"
    TRACKED_OUTPUTS.extend([output_desc_txt, output_title_txt])

    # 1. Descargar video original
    try:
        downloaded_path = download_youtube_video(args.url, temp_yt_video)
        SUCCESS_LOG.append(f"[✓] Descarga completada: {temp_yt_video}")
    except Exception as e:
        ask_to_continue("Descarga de Video de YouTube", str(e))
        downloaded_path = None

    # Recorte
    video_a_fusionar = downloaded_path
    if downloaded_path and (args.trim_start > 0.0 or args.trim_end > 0.0):
        try:
            video_a_fusionar = trim_video(downloaded_path, temp_trimmed_video, args.trim_start, args.trim_end)
            SUCCESS_LOG.append(f"[✓] Recorte aplicado exitosamente: {temp_trimmed_video}")
        except Exception as e:
            ask_to_continue("Recorte de Video (FFmpeg)", str(e))
            video_a_fusionar = None

    # 2. IA - Generación de activos
    titulo_ia = ""
    descripcion_ia = ""
    try:
        contexto = get_video_context(args.url)
        if contexto:
            ia_assets = generate_marketing_assets(contexto)
            if ia_assets:
                if "descripcion" in ia_assets and ia_assets["descripcion"]:
                    descripcion_ia = ia_assets["descripcion"]
                    with open(output_desc_txt, "w", encoding="utf-8") as f: f.write(descripcion_ia)
                    SUCCESS_LOG.append(f"[✓] Contenido IA - Descripción creada con éxito en: {output_desc_txt}")
                else:
                    log_error("Escritura de Descripción IA", f"La llave 'descripcion' vino vacía. No se creó: {output_desc_txt}")
                    
                if "titulo" in ia_assets and ia_assets["titulo"]:
                    titulo_ia = ia_assets["titulo"].strip()
                    with open(output_title_txt, "w", encoding="utf-8") as f: f.write(titulo_ia)
                    SUCCESS_LOG.append(f"[✓] Contenido IA - Título creado con éxito en: {output_title_txt}")
                else:
                    log_error("Escritura de Título IA", f"La llave 'titulo' vino vacía. No se creó: {output_title_txt}")
            else:
                raise ValueError("Estructura JSON corrupta de la IA.")
    except Exception as e:
        log_error("Generación de Contenido IA", f"Fallo en bloque de IA. No se crearon: {output_desc_txt} ni {output_title_txt}")
        ask_to_continue("Generación de Activos de Marketing (Gemini IA)", str(e))

    # 3. Concatenación y Subida Automática
    if video_a_fusionar:
        try:
            clips_to_merge = [intro_path, video_a_fusionar, outro_path]
            concatenate_videos(clips_to_merge, args.out)
            SUCCESS_LOG.append(f"[✓] Fusión de video final exitosa: {args.out}")
            
            # BLOQUE INTEGRADO: EJECUTAR SUBIDA A YOUTUBE
            if os.path.exists(args.out) and titulo_ia and descripcion_ia:
                video_id = upload_video_to_youtube(
                    video_file=args.out,
                    title=titulo_ia,
                    description=descripcion_ia,
                    privacy_status='private' # Se sube privado por control de calidad
                )
                if video_id:
                    SUCCESS_LOG.append(f"[✓] YouTube API - Publicado con éxito de forma Privada. URL: https://youtu.be/{video_id}")
                else:
                    log_error("Subida Automática YouTube", "La API rechazó el paquete multimedia o no retornó ID.")
            else:
                log_error("Subida Automática YouTube", "No se intentó subir a YouTube porque faltaba el video final o los textos de la IA.")
        except Exception as e:
            ask_to_continue("Fusión multimedia / YouTube API", str(e))
    else:
        log_error("Fusión de Video Final", f"Se omitió la mezcla multimedia y subida. {args.out} NO fue creado.")

    # 5. Limpieza regular
    print("\n[5/5] Limpiando residuos temporales...")
    for temp_file in TEMP_FILES:
        if os.path.exists(temp_file):
            try: os.remove(temp_file)
            except OSError: pass

    # Escritura en Historial Log
    log_report_path = "pipeline_execution.log"
    timestamp_now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_report_path, "a", encoding="utf-8") as log_file:
        log_file.write(f"\n==================================================\n EJECUCIÓN DEL PIPELINE - {timestamp_now}\n==================================================\n")
        log_file.write(f"URL Procesada: {args.url}\nArchivo esperado: {args.out}\n\n[+] PROCESOS COMPLETADOS CON ÉXITO:\n")
        for success in SUCCESS_LOG: log_file.write(f" {success}\n") if SUCCESS_LOG else log_file.write(" Ninguno.\n")
        log_file.write("\n[-] ANOMALÍAS / ARCHIVOS NO CREADOS:\n")
        for error in ERROR_LOG: log_file.write(f" {error}\n") if ERROR_LOG else log_file.write(" [✓] Todo finalizó con 0 errores.\n")
        log_file.write("--------------------------------------------------\n")

    print(f"-> Historial de auditoría guardado en: {log_report_path}")

if __name__ == '__main__':
    main()