#!/usr/bin/env python3
import os
import sys
import subprocess
import json
import argparse
import yt_dlp

def refresh_windows_path():
    """
    Refresca las variables de entorno PATH en Windows leyendo directamente
    del registro del sistema. Esto permite detectar FFmpeg o Python recién instalados
    sin necesidad de reiniciar la terminal o el proceso actual.
    """
    if sys.platform == 'win32':
        import winreg
        try:
            # Leer el PATH de la máquina
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as key:
                machine_path = winreg.QueryValueEx(key, "Path")[0]
            # Leer el PATH del usuario
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                    user_path = winreg.QueryValueEx(key, "Path")[0]
            except FileNotFoundError:
                user_path = ""
            
            # Combinar y expandir variables
            combined_path = machine_path + ";" + user_path
            os.environ["PATH"] = os.path.expandvars(combined_path)
        except Exception as e:
            print(f"Aviso: No se pudo refrescar el PATH desde el registro: {e}", file=sys.stderr)

def get_video_info(file_path):
    """
    Usa ffprobe para obtener información técnica del video:
    resolución, fps, duración y si posee canal de audio.
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
    except subprocess.CalledProcessError as e:
        print(f"Error al analizar el video '{file_path}' con ffprobe.", file=sys.stderr)
        print(e.stderr, file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: 'ffprobe' no está instalado o no se encuentra en el PATH.", file=sys.stderr)
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
    if duration == 0.0 and 'duration' in video_stream:
        try:
            duration = float(video_stream['duration'])
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
    print(f"\n[1/3] Descargando video de YouTube: {url}")
    ydl_opts = {
        # Preferimos mp4 (H264 + AAC) para facilitar la concatenación directa
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
    
    # Comprobar si el archivo fue creado (a veces yt-dlp puede agregar la extensión si no coincide exactamente)
    if not os.path.exists(temp_output_path):
        # Buscar variantes
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

def concatenate_videos(clips_paths, output_path):
    """
    Concatena una lista de clips de video en un único archivo de salida usando FFmpeg.
    Normaliza todos los clips para que coincidan con la máxima resolución y FPS
    encontrados entre todos los clips para evitar la pérdida de calidad.
    Genera audio silencioso en caso de que algún clip no posea pista de audio.
    """
    print(f"\n[2/3] Procesando y concatenando {len(clips_paths)} videos con FFmpeg...")
    
    # Lista para almacenar los metadatos de los clips
    clips_meta = []
    for path in clips_paths:
        info = get_video_info(path)
        clips_meta.append((path, info))
        
    # Buscamos el clip con la resolución más alta (por área de pixeles) para evitar degradar la intro/outro
    target_clip = max(clips_meta, key=lambda c: c[1]['width'] * c[1]['height'])
    target_w = target_clip[1]['width']
    target_h = target_clip[1]['height']
    
    # Seleccionamos los FPS máximos detectados para no perder fluidez
    target_fps = max(c[1]['fps'] for c in clips_meta)
    
    print(f"-> Normalizando todos los videos hacia arriba:")
    print(f"   Resolución objetivo: {target_w}x{target_h}")
    print(f"   Tasa de cuadros (FPS) objetivo: {target_fps:.2f}")

    # Estructuramos el comando de FFmpeg
    ffmpeg_cmd = ['ffmpeg', '-y']
    
    # Añadimos los archivos de entrada al comando
    for path, _ in clips_meta:
        ffmpeg_cmd.extend(['-i', path])
        
    num_inputs = len(clips_meta)
    extra_inputs = []
    
    video_filters = []
    audio_filters = []
    
    # Generar filtros para normalizar video y audio de cada clip
    for i, (path, info) in enumerate(clips_meta):
        # Filtro de video: escalado con preservación de aspecto (pad/letterbox/pillarbox), FPS unificado y formato de color yuv420p
        v_filter = (
            f"[{i}:v]scale=w={target_w}:h={target_h}:force_original_aspect_ratio=decrease,"
            f"pad=w={target_w}:h={target_h}:x=({target_w}-iw)/2:y=({target_h}-ih)/2:color=black,"
            f"fps=fps={target_fps},format=yuv420p[v{i}]"
        )
        video_filters.append(v_filter)
        
        # Filtro de audio
        if info['has_audio']:
            a_filter = f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]"
            audio_filters.append(a_filter)
        else:
            # Si el clip no tiene audio (ej: intros mudas), generamos una pista de silencio con su misma duración
            silent_idx = num_inputs + len(extra_inputs)
            duration = info['duration'] if info['duration'] > 0 else 5.0  # Duración por defecto si falla
            extra_inputs.append((duration, silent_idx))
            
            a_filter = f"[{silent_idx}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]"
            audio_filters.append(a_filter)
            
    # Agregamos los generadores de silencio (lavfi anullsrc) como entradas adicionales de FFmpeg
    for duration, _ in extra_inputs:
        ffmpeg_cmd.extend(['-f', 'lavfi', '-t', str(duration), '-i', 'anullsrc=r=48000:cl=stereo'])
        
    # Construcción final del string filter_complex
    filter_complex = ";".join(video_filters + audio_filters)
    
    # Añadimos la directiva final de concatenación
    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(num_inputs))
    filter_complex += f";{concat_inputs}concat=n={num_inputs}:v=1:a=1[v_out][a_out]"
    
    ffmpeg_cmd.extend(['-filter_complex', filter_complex])
    ffmpeg_cmd.extend(['-map', '[v_out]', '-map', '[a_out]'])
    
    # Codecs estándar compatibles con la mayoría de reproductores y navegadores
    ffmpeg_cmd.extend([
        '-c:v', 'libx264',
        '-preset', 'medium',
        '-crf', '23',
        '-c:a', 'aac',
        '-b:a', '192k',
        output_path
    ])
    
    # Ejecutamos el comando ffmpeg
    try:
        subprocess.run(ffmpeg_cmd, check=True)
        print(f"-> Fusión completada con éxito. Archivo final guardado en: {output_path}")
    except subprocess.CalledProcessError as e:
        print("Error durante la concatenación con FFmpeg.", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: 'ffmpeg' no está instalado o no se encuentra en el PATH.", file=sys.stderr)
        sys.exit(1)

def main():
    refresh_windows_path()
    
    parser = argparse.ArgumentParser(
        description="Automatización para descargar videos de YouTube e incluirles una intro y outro fijas.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('-u', '--url', required=True, help="URL del video de YouTube a descargar.")
    parser.add_argument('--out', default='final_output.mp4', help="Nombre del archivo de video final generado.")
    
    args = parser.parse_args()
    
    # Rutas fijas (hardcodeadas) relativas al script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    intro_path = os.path.join(script_dir, "Intro Pronectis.mp4")
    outro_path = os.path.join(script_dir, "Outro Pronectis.mp4")
    
    # Validaciones previas de archivos de intro/outro
    if not os.path.exists(intro_path):
        print(f"Error: No se encontró el archivo de introducción requerido: {intro_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(outro_path):
        print(f"Error: No se encontró el archivo de cierre requerido: {outro_path}", file=sys.stderr)
        sys.exit(1)
        
    temp_yt_video = "temp_downloaded_yt_video.mp4"
    
    # 1. Descargar video de YouTube
    downloaded_path = download_youtube_video(args.url, temp_yt_video)
    
    # 2. Concatenación (siempre une intro + descargado + outro normalizando hacia arriba)
    clips_to_merge = [intro_path, downloaded_path, outro_path]
        
    concatenate_videos(clips_to_merge, args.out)
    
    # Limpieza de archivo temporal de descarga
    if os.path.exists(downloaded_path):
        try:
            os.remove(downloaded_path)
            print("[3/3] Archivos temporales eliminados.")
        except OSError as e:
            print(f"Aviso: No se pudo eliminar el archivo temporal '{downloaded_path}': {e}", file=sys.stderr)

if __name__ == '__main__':
    main()
