#!/usr/bin/env python3
import os
import sys
import subprocess
import json
import argparse
import re
import yt_dlp
from google import genai
from dotenv import load_dotenv
from static_ffmpeg import run

# Cargar variables de entorno (.env)
load_dotenv()

# Inicializar el cliente oficial de Gemini (Librería moderna)
try:
    client = genai.Client()
except Exception as e:
    print(f"Aviso al inicializar Gemini: {e}. Asegúrate de tener GEMINI_API_KEY en tu .env", file=sys.stderr)

# Inicializar FFmpeg en el entorno usando la función correcta
ffmpeg_path, ffprobe_path = run.get_or_fetch_platform_executables_else_raise()

def refresh_windows_path():
    """
    Refresca las variables de entorno PATH en Windows leyendo directamente del registro.
    """
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
    """
    Usa ffprobe para obtener información técnica del video.
    """
    cmd = [
        'ffprobe',
        '-v', 'quiet',
        '-print_format', 'json',
        '-show_streams',
        '-show_format',
        file_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
    except subprocess.CalledProcessError:
        print(f"Error al analizar el video '{file_path}' con ffprobe.", file=sys.stderr)
        sys.exit(1)
        
    video_stream = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), None)
    audio_stream = next((s for s in info.get('streams', []) if s.get('codec_type') == 'audio'), None)
    
    if not video_stream:
        print(f"Error: El archivo '{file_path}' no contiene pistas de video válidas.", file=sys.stderr)
        sys.exit(1)
        
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
    
    return {
        'width': width,
        'height': height,
        'fps': fps,
        'duration': duration,
        'has_audio': has_audio
    }

def download_youtube_video(url, temp_output_path):
    """
    Descarga el video de YouTube en formato MP4 usando la biblioteca yt-dlp.
    """
    print(f"\n[1/5] Descargando video de YouTube: {url}")
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': temp_output_path,
        'merge_output_format': 'mp4',
        'quiet': False,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        print(f"Error al descargar desde YouTube: {e}", file=sys.stderr)
        sys.exit(1)
    
    if not os.path.exists(temp_output_path):
        base, _ = os.path.splitext(temp_output_path)
        for f in os.listdir(os.path.dirname(temp_output_path) or '.'):
            if f.startswith(os.path.basename(base)) and f.endswith('.mp4'):
                temp_output_path = os.path.join(os.path.dirname(temp_output_path), f)
                break
                
    if not os.path.exists(temp_output_path):
        print("Error: No se pudo localizar el video descargado.", file=sys.stderr)
        sys.exit(1)
        
    print(f"-> Descarga completada con éxito. Archivo temporal: {temp_output_path}")
    return temp_output_path

def trim_video(input_path, output_path, start_seconds=0.0, end_seconds=0.0):
    """
    Recorta segundos al inicio y/o al final de un video usando FFmpeg.
    """
    if start_seconds == 0.0 and end_seconds == 0.0:
        return input_path

    info = get_video_info(input_path)
    total_duration = info['duration']
    
    trim_start = start_seconds
    trim_duration = total_duration - start_seconds - end_seconds

    if trim_duration <= 0:
        print(f"Error: El tiempo a recortar ({start_seconds}s inicio, {end_seconds}s fin) excede la duración total del video ({total_duration}s).", file=sys.stderr)
        sys.exit(1)

    print(f"\n[Recorte] Aplicando recorte al video descargado:")
    if start_seconds > 0:
        print(f"   -> Removiendo {start_seconds} segundos al inicio.")
    if end_seconds > 0:
        print(f"   -> Removiendo {end_seconds} segundos al final.")

    cmd = [
        'ffmpeg', '-y',
        '-ss', str(trim_start),
        '-i', input_path,
        '-t', str(trim_duration),
        '-c:v', 'libx264', '-crf', '18', '-preset', 'fast',
        '-c:a', 'aac', '-b:a', '192k',
        output_path
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"-> Recorte finalizado con éxito. Nuevo archivo intermedio: {output_path}")
        return output_path
    except subprocess.CalledProcessError:
        print("Error al intentar recortar el video con FFmpeg.", file=sys.stderr)
        sys.exit(1)

def clean_vtt_subtitles(vtt_content):
    """
    Limpia un string en formato WebVTT, removiendo timestamps, tags XML y duplicados
    para devolver un texto limpio y continuo ideal para Gemini.
    """
    lines = vtt_content.splitlines()
    cleaned_lines = []
    
    # Expresión regular para quitar timestamps tipo 00:01:20.000 --> 00:01:23.000
    timestamp_regex = re.compile(r'(\d{2}:)?\d{2}:\d{2}\.\d{3}')
    # Expresión para remover tags HTML/XML como <c> o </c> que mete YouTube
    html_regex = re.compile(r'<[^>]*>')

    for line in lines:
        line = line.strip()
        # Ignorar cabeceras VTT y líneas de tiempo
        if not line or line.startswith('WEBVTT') or line.startswith('Kind:') or line.startswith('Language:') or timestamp_regex.search(line):
            continue
        
        # Limpiar tags internos
        line_clean = html_regex.sub('', line).strip()
        
        if line_clean and line_clean not in cleaned_lines:
            cleaned_lines.append(line_clean)
            
    return " ".join(cleaned_lines)

def get_video_context(url):
    """
    Usa yt-dlp para extraer tanto los metadatos como la transcripción real del video,
    evitando APIs obsoletas.
    """
    print(f"\n[2/5] Extrayendo contexto y transcripción real con yt-dlp...")
    
    # 1. Obtener Metadatos Base
    meta_opts = {'quiet': True, 'no_warnings': True}
    with yt_dlp.YoutubeDL(meta_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            title = info.get('title')
            original_desc = info.get('description', '')
        except Exception as e:
            print(f"Error al extraer metadatos iniciales: {e}", file=sys.stderr)
            return None

    # 2. Descargar la transcripción real usando yt-dlp (Soporta manuales y auto-generados)
    temp_sub_prefix = "temp_transcript_extraction"
    sub_opts = {
        'writesubtitles': True,         # Descargar subtítulos reales creados por humanos
        'writeautomaticsub': True,      # Fallback a la transcripción automática real si no hay manuales
        'subtitleslangs': ['es.*', 'en.*'], # Priorizar español (cualquier variante regional) o inglés
        'skip_download': True,          # No queremos bajar el video de nuevo acá, solo el texto
        'outtmpl': temp_sub_prefix,
        'quiet': True,
        'no_warnings': True,
    }
    
    transcript_text = ""
    with yt_dlp.YoutubeDL(sub_opts) as ydl:
        try:
            ydl.download([url])
        except Exception:
            pass

    # Buscar el archivo de subtítulos generado en el directorio actual (.vtt)
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
            # Limpiar archivo de subtítulos temporal inmediatamente
            try:
                os.remove(vtt_file)
            except OSError:
                pass
    
    if transcript_text:
        return f"Título: {title}\n\nTranscripción real del video:\n{transcript_text}"
    else:
        print("-> Aviso: No se pudo localizar una pista de transcripción en el reproductor. Usando título y descripción.")
        return f"Título: {title}\n\nDescripción Original:\n{original_desc}"

def generate_description_with_gemini(video_context):
    """
    Llama a Gemini usando el SDK moderno (google-genai) para redactar la descripción.
    """
    print(f"-> Conectando con Gemini para estructurar la nueva descripción...")
    
    if "GEMINI_API_KEY" not in os.environ:
        print("Error crítico: No se encontró la variable GEMINI_API_KEY en el archivo .env", file=sys.stderr)
        return None

    script_dir = os.path.dirname(os.path.abspath(__file__))
    landing_path = os.path.join(script_dir, "landing_pages.txt.txt")
    ejemplo_path = os.path.join(script_dir, "descripcion_ejemplo.txt")
    
    try:
        with open(landing_path, 'r', encoding='utf-8') as f:
            landings = f.read()
        with open(ejemplo_path, 'r', encoding='utf-8') as f:
            ejemplo = f.read()
    except FileNotFoundError as e:
        print(f"Error crítico: Faltan archivos de referencia necesarios para la IA: {e}", file=sys.stderr)
        return None
    
    prompt = f"""
    Eres un experto en SEO para YouTube y redactor de contenidos corporativos de la empresa Pronectis.
    Tu tarea consiste en redactar la descripción definitiva para un nuevo video basándote en su contexto actual (título, transcripción o resumen).

    CONTEXTO DEL VIDEO A PROCESAR:
    {video_context}

    LISTA DE LANDINGS DISPONIBLES:
    {landings}

    REGLAS ESTRICTAS DE REDACCIÓN:
    1. Analiza con precisión el tema del video actual y elige obligatoriamente una URL de la 'LISTA DE LANDINGS DISPONIBLES' que tenga directa relación con lo tratado (ej: si habla de correos o firmas usa 'gsignature', si habla de seguridad usa 'fortinet' o 'eset', si habla de IA usa 'gemini-ia', etc.).
    2. Debes reescribir por completo ÚNICAMENTE la primera sección de la descripción (los primeros 2 o 3 párrafos del texto). Esta debe resumir de forma ganchera el video analizado e incluir con naturalidad el enlace seleccionado bajo el formato de llamada a la acción (ej: "Para acceder a más información acerca de..., visitá nuestra página: [enlace]").
    3. Toda la segunda sección del bloque (empezando exactamente desde "Si querés que ayudemos a tu organización con nuestros especialistas contactanos desde este link:") debe mantenerse TEXTUAL E IDÉNTICA al archivo de ejemplo provisto. No modifiques redes, textos informativos ni enlaces fijos del bloque institucional.

    ESTRUCTURA DE REFERENCIA A SEGUIR:
    {ejemplo}

    Genera el bloque de texto final unificado directamente. No agregues saludos, explicaciones ni formatos de bloque de código adicionales.
    """

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        return response.text
    except Exception as e:
        print(f"Error al generar la descripción con Gemini: {e}", file=sys.stderr)
        return None

def concatenate_videos(clips_paths, output_path):
    """
    Concatena la lista de clips multimedia usando FFmpeg.
    """
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
    
    ffmpeg_cmd.extend(['-filter_complex', filter_complex])
    ffmpeg_cmd.extend(['-map', '[v_out]', '-map', '[a_out]'])
    
    ffmpeg_cmd.extend([
        '-c:v', 'libx264',
        '-preset', 'medium',
        '-crf', '23',
        '-c:a', 'aac',
        '-b:a', '192k',
        output_path
    ])
    
    try:
        subprocess.run(ffmpeg_cmd, check=True)
        print(f"-> Fusión multimedia completada con éxito: {output_path}")
    except subprocess.CalledProcessError:
        print("Error durante la concatenación con FFmpeg.", file=sys.stderr)
        sys.exit(1)

def main():
    refresh_windows_path()
    
    parser = argparse.ArgumentParser(
        description="Automatización Pronectis: Video + IA Descripción + Recorte Opcional.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('-u', '--url', required=True, help="URL del video de YouTube a procesar.")
    parser.add_argument('--out', default='final_output.mp4', help="Nombre del archivo de video final generado.")
    
    parser.add_argument('-ts', '--trim-start', type=float, default=0.0, help="Segundos a recortar al INICIO del video original.")
    parser.add_argument('-te', '--trim-end', type=float, default=0.0, help="Segundos a recortar al FINAL del video original.")
    
    args = parser.parse_args()
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    intro_path = os.path.join(script_dir, "Intro Pronectis.mp4")
    outro_path = os.path.join(script_dir, "Outro Pronectis.mp4")
    
    if not os.path.exists(intro_path):
        print(f"Error: No se encontró el archivo de introducción: {intro_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(outro_path):
        print(f"Error: No se encontró el archivo de cierre: {outro_path}", file=sys.stderr)
        sys.exit(1)
        
    temp_yt_video = "temp_downloaded_yt_video.mp4"
    temp_trimmed_video = "temp_trimmed_yt_video.mp4"
    
    # 1. Descargar video
    downloaded_path = download_youtube_video(args.url, temp_yt_video)
    
    # Aplicar recorte si aplica
    video_a_fusionar = downloaded_path
    if args.trim_start > 0.0 or args.trim_end > 0.0:
        video_a_fusionar = trim_video(downloaded_path, temp_trimmed_video, args.trim_start, args.trim_end)
    
    # 2. IA: Procesar descripción con la extracción nativa de yt-dlp
    contexto = get_video_context(args.url)
    if contexto:
        descripcion_final = generate_description_with_gemini(contexto)
        if descripcion_final:
            output_txt = os.path.splitext(args.out)[0] + "_descripcion.txt"
            with open(output_txt, "w", encoding="utf-8") as f:
                f.write(descripcion_final)
            print(f"-> ¡Descripción generada con éxito por la IA! Guardada en: {output_txt}")
    
    # 3. Combinar videos
    clips_to_merge = [intro_path, video_a_fusionar, outro_path]
    concatenate_videos(clips_to_merge, args.out)
    
    # 5. Limpieza
    print("\n[5/5] Limpiando residuos temporales...")
    for temp_file in [temp_yt_video, temp_trimmed_video]:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass
    print("-> Pipeline finalizado exitosamente.")

if __name__ == '__main__':
    main()