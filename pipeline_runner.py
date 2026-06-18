#!/usr/bin/env python3
"""
pipeline_runner.py
Web-compatible adapter for the Pronectis YouTube automation pipeline.

Wraps automate.py so the entire pipeline runs in a background thread,
emitting typed WebSocket events instead of writing to stdout/stdin.
"""
import os
import sys
import io
import re
import queue
import threading
import datetime
import subprocess
from typing import Optional, List

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# Importing automate triggers static_ffmpeg init and Gemini client setup.
import automate


# ─────────────────────────────────────────────────────────────────────────────
# Stdout → Queue bridge
# ─────────────────────────────────────────────────────────────────────────────

class StreamToQueue:
    """
    Drop-in sys.stdout replacement.
    Each newline-terminated string from print() becomes a WebSocket 'log' event.
    """
    def __init__(self, q: queue.Queue):
        self._q = q
        self._buf = ""

    def write(self, text: str):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._q.put({"type": "log", "msg": line.strip()})

    def flush(self):
        if self._buf.strip():
            self._q.put({"type": "log", "msg": self._buf.strip()})
            self._buf = ""

    def fileno(self):
        raise io.UnsupportedOperation("fileno")


# ─────────────────────────────────────────────────────────────────────────────
# Custom exception
# ─────────────────────────────────────────────────────────────────────────────

class PipelineAbortedError(Exception):
    """Raised when the user cancels the pipeline from the web UI."""


# ─────────────────────────────────────────────────────────────────────────────
# PipelineRunner
# ─────────────────────────────────────────────────────────────────────────────

class PipelineRunner:
    """
    Singleton that manages one pipeline execution at a time.
    The pipeline runs in a daemon thread; all I/O goes to self.log_queue,
    which the FastAPI server drains and broadcasts over WebSockets.
    """

    def __init__(self):
        self.log_queue: queue.Queue = queue.Queue()
        self._prompt_event = threading.Event()
        self._prompt_response: Optional[str] = None
        # idle | running | waiting_confirmation | done | error
        self.status: str = "idle"
        self.current_step: int = 0
        self.last_outputs: dict = {}
        self._thread: Optional[threading.Thread] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def emit(self, msg_type: str, **kwargs):
        """Push a typed message into the log queue."""
        self.log_queue.put({"type": msg_type, **kwargs})

    def respond_to_prompt(self, action: str):
        """
        Called from the async WebSocket handler when the user clicks
        'Continuar' or 'Cancelar' in the error modal.
        """
        self._prompt_response = action
        self._prompt_event.set()

    def abort(self, reason: str = "Cancelado"):
        """Abort the pipeline mid-run if possible."""
        self.emit("log", msg=f"⚠️ Petición de cancelación recibida: {reason}")
        if self.status == "waiting_confirmation":
            self.respond_to_prompt("cancel")
        else:
            self.status = "aborting"
            self.emit("pipeline_aborted", reason=reason)
            # Raise exception in main thread if possible, or we let the next step check status.


    def start(self, *, url: str, project_name: str, trim_start: float,
              trim_end: float, video_format: str, extra_context: str, auto_upload: bool, outputs_dir: str) -> bool:
        """Launch the pipeline in a background daemon thread."""
        if self.status in ("running", "waiting_confirmation"):
            return False
        self.status = "running"
        self.current_step = 0
        self.last_outputs = {}
        self._thread = threading.Thread(
            target=self._thread_main,
            args=(url, project_name, trim_start, trim_end, video_format, extra_context, auto_upload, outputs_dir),
            daemon=True,
        )
        self._thread.start()
        return True

    # ── ask_to_continue replacement ───────────────────────────────────────────

    def _ask_to_continue_web(self, step_name: str, error_detail: str) -> bool:
        """
        Web replacement for automate.ask_to_continue().
        Emits an 'error_prompt' event, then blocks the pipeline thread
        until the user responds via WebSocket.
        """
        automate.log_error(step_name, error_detail)
        self.emit("error_prompt", step=step_name, detail=error_detail)
        self.status = "waiting_confirmation"

        self._prompt_event.clear()
        responded = self._prompt_event.wait(timeout=600)  # 10 min timeout
        self.status = "running"

        if not responded or self._prompt_response != "continue":
            self.emit("log", msg="❌ Pipeline cancelado. Iniciando rollback de archivos generados...")
            for p in automate.TRACKED_OUTPUTS:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                        self.emit("log", msg=f"   [Eliminado] {os.path.basename(p)}")
                    except OSError:
                        pass
            for p in automate.TEMP_FILES:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                        self.emit("log", msg=f"   [Temporal eliminado] {os.path.basename(p)}")
                    except OSError:
                        pass
            raise PipelineAbortedError("Pipeline cancelado por el usuario.")

        self.emit("log", msg="→ Continuando pipeline a pesar del error...")
        return True

    # ── FFmpeg runner with progress ───────────────────────────────────────────

    def _run_ffmpeg(self, cmd: List[str], total_duration: float = 0.0):
        """Run an FFmpeg command, parsing stderr to emit progress events."""
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
        )
        time_re = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")
        for line in proc.stderr:
            line = line.rstrip()
            if total_duration > 0 and "time=" in line:
                m = time_re.search(line)
                if m:
                    elapsed = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
                    pct = min(99, int(elapsed / total_duration * 100))
                    self.emit("ffmpeg_progress", pct=pct)
            # Surface critical FFmpeg messages
            low = line.lower()
            if any(kw in low for kw in ("error", "invalid", "no such file", "failed")):
                if "error" in low or "no such file" in low:
                    self.emit("log", msg=f"[FFmpeg] {line}")
        proc.wait()
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd)
        self.emit("ffmpeg_progress", pct=100)

    # ── yt-dlp progress hooks ─────────────────────────────────────────────────

    def _ydl_hooks(self):
        runner = self

        def hook(d):
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                downloaded = d.get("downloaded_bytes", 0)
                speed = d.get("speed") or 0
                eta = d.get("eta") or 0
                pct = int(downloaded / total * 100) if total else 0
                speed_str = f"{speed / 1024 / 1024:.1f} MB/s" if speed else "—"
                eta_str = f"{int(eta)}s" if eta else "—"
                runner.emit("download_progress", pct=pct, speed=speed_str, eta=eta_str)
            elif d["status"] == "finished":
                runner.emit("download_progress", pct=100, speed="", eta="")
                runner.emit("log", msg="→ Streams descargados, fusionando en MP4...")

        return [hook]

    # ── Pipeline step implementations ─────────────────────────────────────────

    def _step_download(self, url: str, temp_path: str) -> str:
        import yt_dlp
        self.emit("log", msg=f"[1/5] Descargando video de YouTube: {url}")
        
        opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": temp_path,
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": self._ydl_hooks(),
        }

        # ── CONFIGURACIÓN DE COOKIES PARA EVITAR EL BLOQUEO DE BOT ──
        # Render suele montar los Secret Files en la raíz del proyecto o en /etc/secrets/
        posibles_rutas_cookies = [
            "cookies.txt", 
            "/etc/secrets/cookies.txt",
            os.path.join(SCRIPT_DIR, "cookies.txt")
        ]
        
        cookie_detectada = None
        for ruta in posibles_rutas_cookies:
            if os.path.exists(ruta):
                cookie_detectada = ruta
                break
                
        if cookie_detectada:
            self.emit("log", msg=f"→ [Seguridad] Usando archivo de cookies detectado en: {cookie_detectada}")
            opts["cookiefile"] = cookie_detectada
        else:
            self.emit("log", msg="⚠️ Aviso: No se detectó ningún archivo 'cookies.txt'. Se intentará descargar sin cookies.")
        # ───────────────────────────────────────────────────────────

        with yt_dlp.YoutubeDL(opts) as ydl:
            ret_code = ydl.download([url])
            if ret_code != 0:
                raise Exception("yt-dlp reportó un error durante la descarga del video.")

        # yt-dlp sometimes appends extra chars; find the actual file
        if not os.path.exists(temp_path):
            base = os.path.splitext(temp_path)[0]
            parent = os.path.dirname(temp_path) or "."
            for f in os.listdir(parent):
                if f.startswith(os.path.basename(base)) and f.endswith(".mp4"):
                    temp_path = os.path.join(parent, f)
                    break
                    
        if not os.path.exists(temp_path):
            raise FileNotFoundError("No se pudo localizar el video descargado en disco.")
            
        if os.path.getsize(temp_path) < 1024:
            raise Exception("El archivo descargado está corrupto o vacío (menos de 1KB).")

        self.emit("log", msg="→ Descarga completada exitosamente.")
        return temp_path

    def _step_trim(self, input_path: str, output_path: str,
                   start: float, end: float) -> str:
        if start == 0.0 and end == 0.0:
            return input_path
        info = automate.get_video_info(input_path)
        total = info["duration"]
        trim_dur = total - start - end
        if trim_dur <= 0:
            raise ValueError(
                f"Recorte ({start}s inicio + {end}s fin) excede la duración total ({total:.1f}s)."
            )
        self.emit("log", msg=f"[Recorte] -{start}s inicio / -{end}s fin → nueva duración: {trim_dur:.1f}s")
        cmd = [
            "ffmpeg", "-y", "-ss", str(start), "-i", input_path,
            "-t", str(trim_dur), "-c:v", "libx264", "-crf", "18",
            "-preset", "fast", "-c:a", "aac", "-b:a", "192k", output_path,
        ]
        self._run_ffmpeg(cmd, total_duration=trim_dur)
        self.emit("log", msg="→ Recorte finalizado correctamente.")
        return output_path

    def _step_ai_assets(self, url: str, out_desc: str, out_title: str, extra_context: str):
        self.emit("log", msg="[2/5] Extrayendo contexto y transcripción del video con yt-dlp...")
        context = automate.get_video_context(url)
        self.emit("log", msg="→ Contexto obtenido. Conectando con Gemini AI para generar activos...")
        assets = automate.generate_marketing_assets(context, extra_context=extra_context)

        desc = assets.get("descripcion", "").strip()
        title = assets.get("titulo", "").strip()
        tags = assets.get("tags", "")

        if desc:
            with open(out_desc, "w", encoding="utf-8") as f:
                f.write(desc)
            automate.SUCCESS_LOG.append(f"[✓] Descripción IA guardada: {os.path.basename(out_desc)}")
            self.emit("log", msg="→ Descripción generada por Gemini y guardada.")
        else:
            automate.log_error("Descripción IA", "Campo 'descripcion' vino vacío.")

        if title:
            with open(out_title, "w", encoding="utf-8") as f:
                f.write(title)
            automate.SUCCESS_LOG.append(f"[✓] Título IA guardado: {os.path.basename(out_title)}")
            self.emit("log", msg=f'→ Título generado: "{title}"')
        else:
            automate.log_error("Título IA", "Campo 'titulo' vino vacío.")

        return title, desc, tags

    def _step_thumbnail(self, prompt_text: str, output_path: str) -> Optional[str]:
        self.emit("log", msg="[AI] Generando miniatura con Google Imagen 3...")
        thumb_path = automate.generate_thumbnail_ai(prompt_text, output_path)
        if thumb_path:
            automate.SUCCESS_LOG.append(f"[✓] Miniatura generada: {os.path.basename(thumb_path)}")
            self.emit("log", msg="→ Miniatura IA generada con éxito.")
        return thumb_path



    def _step_concat(self, clips: List[str], out_path: str):
        self.emit("log", msg=f"[3/5] Procesando y concatenando {len(clips)} videos con FFmpeg...")
        clips_meta = [(p, automate.get_video_info(p)) for p in clips]
        total_dur = sum(m["duration"] for _, m in clips_meta)
        target = max(clips_meta, key=lambda c: c[1]["width"] * c[1]["height"])
        w, h = target[1]["width"], target[1]["height"]
        fps = max(m["fps"] for _, m in clips_meta)
        self.emit("log", msg=f"→ Normalizando a {w}×{h} | {fps:.2f} FPS | {total_dur:.0f}s total")

        cmd = ["ffmpeg", "-y"]
        for p, _ in clips_meta:
            cmd += ["-i", p]

        n = len(clips_meta)
        extra, vf, af = [], [], []
        for i, (_, info) in enumerate(clips_meta):
            vf.append(
                f"[{i}:v]scale=w={w}:h={h}:force_original_aspect_ratio=decrease,"
                f"pad=w={w}:h={h}:x=({w}-iw)/2:y=({h}-ih)/2:color=black,"
                f"fps=fps={fps},format=yuv420p[v{i}]"
            )
            if info["has_audio"]:
                af.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
            else:
                si = n + len(extra)
                dur = info["duration"] if info["duration"] > 0 else 5.0
                extra.append((dur, si))
                af.append(f"[{si}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")

        for dur, _ in extra:
            cmd += ["-f", "lavfi", "-t", str(dur), "-i", "anullsrc=r=48000:cl=stereo"]

        fc = ";".join(vf + af)
        ci = "".join(f"[v{i}][a{i}]" for i in range(n))
        fc += f";{ci}concat=n={n}:v=1:a=1[v_out][a_out]"
        cmd += ["-filter_complex", fc, "-map", "[v_out]", "-map", "[a_out]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k", out_path]

        self._run_ffmpeg(cmd, total_duration=total_dur)
        automate.SUCCESS_LOG.append(f"[✓] Fusión exitosa: {os.path.basename(out_path)}")
        self.emit("log", msg="→ Fusión multimedia completada exitosamente.")

    def _step_short_format(self, in_file: str, out_file: str, mode: str):
        """Formatea un video 16:9 a 9:16 (Short) usando crop o pad con blur."""
        self.emit("log", msg=f"[FFmpeg] Formateando para Short (modo: {mode})...")
        
        info = automate.get_video_info(in_file)
        if not info:
            raise ValueError(f"No se pudo leer info de {in_file}")
            
        dur = info["duration"]
        
        # Base config
        cmd = ["ffmpeg", "-y", "-i", in_file]
        
        if mode == "crop":
            # Crop center 9:16
            cmd += ["-vf", "crop=ih*(9/16):ih"]
        elif mode == "pad":
            # Blur background pad
            cmd += [
                "-filter_complex",
                "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,boxblur=20:20,crop=1080:1920[bg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2"
            ]
            
        cmd += [
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            out_file
        ]
        
        self._run_ffmpeg(cmd, total_duration=dur)
        automate.SUCCESS_LOG.append(f"[✓] Formato Short ({mode}) aplicado.")
        self.emit("log", msg="→ Formato Short completado.")


    def _step_upload(self, video_file: str, title: str, description: str,
                     privacy: str = "private") -> Optional[str]:
        self.emit("log", msg=f"[YouTube] Iniciando subida: {os.path.basename(video_file)}")
        
        import pickle
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        
        # Definimos la ruta exacta donde vive tu archivo de tokens en el proyecto
        ruta_token = os.path.join(SCRIPT_DIR, "youtube_token.pickle") 
        
        try:
            # 1. Intentamos leer las credenciales directo del archivo físico .pickle
            if os.path.exists(ruta_token):
                with open(ruta_token, "rb") as token_file:
                    creds = pickle.load(token_file)
                
                # 🚨 RENOVAR AUTOMÁTICAMENTE SI EXPIRO:
                # Si el token de acceso de 1 hora venció, el Refresh Token lo extiende otra hora más
                if creds and creds.expired and creds.refresh_token:
                    self.emit("log", msg="→ [Seguridad] El token de acceso expiró. Renovándolo automáticamente...")
                    creds.refresh(Request())
                    
                    # Volvemos a escribir el archivo pickle modificado con la nueva fecha de vida
                    with open(ruta_token, "wb") as token_file:
                        pickle.dump(creds, token_file)
                    self.emit("log", msg="→ [Seguridad] Token renovado y guardado con éxito en disco.")
                
                # Construimos el cliente oficial usando las credenciales frescas
                youtube = build('youtube', 'v3', credentials=creds)
                
            else:
                # Si no existiera el archivo en la carpeta por alguna razón, usamos el fallback de automate
                self.emit("log", msg="⚠️ Advertencia: No se encontró 'youtube_token.pickle'. Usando fallback de automate.py")
                youtube = automate.get_youtube_service()
                
        except Exception as e:
            self.emit("log", msg=f"❌ Error autenticando con YouTube: {e}")
            if "invalid_grant" in str(e).lower():
                self.emit("token_expired", msg=(
                    "El token fue revocado o es totalmente inválido. "
                    "Requiere una nueva autenticación manual desde la consola."
                ))
            return None

        # 2. Proceso de subida multimedia original
        from googleapiclient.http import MediaFileUpload
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": ["Pronectis", "Google Workspace", "Automatización", "SEO"],
                "categoryId": "28",
            },
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        }
        media = MediaFileUpload(
            video_file, chunksize=1024 * 1024, resumable=True, mimetype="video/mp4"
        )
        request = youtube.videos().insert(
            part=",".join(body.keys()), body=body, media_body=media
        )

        response = None
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    self.emit("upload_progress", pct=pct)
            except Exception as e:
                self.emit("log", msg=f"❌ Error durante la subida a YouTube: {e}")
                return None

        if "id" in response:
            vid_id = response["id"]
            automate.SUCCESS_LOG.append(
                f"[✓] YouTube publicado ({privacy}): https://youtu.be/{vid_id}"
            )
            self.emit("log", msg=f"✅ Video subido exitosamente. ID: {vid_id}")
            return vid_id
        return None

        from googleapiclient.http import MediaFileUpload
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": ["Pronectis", "Google Workspace", "Automatización", "SEO"],
                "categoryId": "28",
            },
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        }
        media = MediaFileUpload(
            video_file, chunksize=1024 * 1024, resumable=True, mimetype="video/mp4"
        )
        request = youtube.videos().insert(
            part=",".join(body.keys()), body=body, media_body=media
        )

        response = None
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    self.emit("upload_progress", pct=pct)
            except Exception as e:
                self.emit("log", msg=f"❌ Error durante la subida a YouTube: {e}")
                return None

        if "id" in response:
            vid_id = response["id"]
            automate.SUCCESS_LOG.append(
                f"[✓] YouTube publicado ({privacy}): https://youtu.be/{vid_id}"
            )
            self.emit("log", msg=f"✅ Video subido exitosamente. ID: {vid_id}")
            return vid_id
        return None

    # ── Thread entry point ────────────────────────────────────────────────────

    def _prune_old_projects(self, outputs_dir: str, keep: int = 5):
        """Elimina las carpetas de proyecto más antiguas, dejando solo las últimas `keep`."""
        try:
            entries = [
                e for e in os.scandir(outputs_dir)
                if e.is_dir() and os.path.exists(os.path.join(e.path, "metadata.json"))
            ]
            entries.sort(key=lambda e: e.stat().st_mtime)
            to_delete = entries[:-keep] if len(entries) > keep else []
            for entry in to_delete:
                import shutil
                shutil.rmtree(entry.path, ignore_errors=True)
                self.emit("log", msg=f"[Limpieza] Proyecto antiguo eliminado: {entry.name}")
        except Exception as e:
            self.emit("log", msg=f"⚠️ No se pudo podar proyectos antiguos: {e}")

    def _thread_main(self, url: str, project_name: str, trim_start: float,
                     trim_end: float, video_format: str, extra_context: str, auto_upload: bool, outputs_dir: str):
        # Redirect stdout so all print() calls go to the queue
        old_stdout = sys.stdout
        sys.stdout = StreamToQueue(self.log_queue)
        # Patch ask_to_continue to use web events
        old_ask = automate.ask_to_continue
        automate.ask_to_continue = self._ask_to_continue_web
        # Change to script dir so yt-dlp subtitle temp files resolve correctly
        old_cwd = os.getcwd()
        os.chdir(SCRIPT_DIR)

        try:
            # Reset module-level globals
            automate.TRACKED_OUTPUTS.clear()
            automate.TEMP_FILES.clear()
            automate.ERROR_LOG.clear()
            automate.SUCCESS_LOG.clear()

            os.makedirs(outputs_dir, exist_ok=True)

            # ── Create per-project directory ──────────────────────────────────
            import datetime as _dt
            import re
            ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            if not project_name:
                project_name = f"proyecto_{ts}"
            else:
                project_name = re.sub(r'[^A-Za-z0-9_-]', '_', project_name) + f"_{ts}"
            project_dir  = os.path.join(outputs_dir, project_name)
            os.makedirs(project_dir, exist_ok=True)
            self.emit("log", msg=f"📁 Proyecto creado: {project_name}")

            # ── Save initial metadata immediately so the project appears in history even on failure ──
            import json as _json
            meta_path = os.path.join(project_dir, "metadata.json")
            _initial_meta = {
                "project_name": project_name,
                "project_dir":  project_name,
                "source_url":   url,
                "created_at":   ts,
                "status":       "in_progress",
                "video":        None,
                "titulo":       None,
                "descripcion":  None,
                "tags":         None,
                "thumbnail":    None,
                "youtube_id":   None,
                "youtube_url":  None,
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                _json.dump(_initial_meta, f, ensure_ascii=False, indent=2)

            # Prune old projects (keep only last 5)
            self._prune_old_projects(outputs_dir, keep=5)

            out_filename = "video.mp4"
            out_path    = os.path.join(project_dir, out_filename)
            out_desc    = os.path.join(project_dir, "descripcion.txt")
            out_title_f = os.path.join(project_dir, "titulo.txt")
            out_tags_f  = os.path.join(project_dir, "tags.txt")
            temp_yt     = os.path.join(project_dir, "temp_downloaded.mp4")
            temp_trim   = os.path.join(project_dir, "temp_trimmed.mp4")

            automate.TEMP_FILES.extend([temp_yt, temp_trim])
            # Note: thumbnail is NOT added to TEMP_FILES to avoid accidental deletion

            intro = os.path.join(SCRIPT_DIR, "Intro Pronectis.mp4")
            outro = os.path.join(SCRIPT_DIR, "Outro Pronectis.mp4")
            if not os.path.exists(intro) or not os.path.exists(outro):
                raise FileNotFoundError(
                    "No se encuentran los archivos 'Intro Pronectis.mp4' y/o 'Outro Pronectis.mp4' "
                    "en el directorio del proyecto."
                )

            # ── STEP 1: Download ──────────────────────────────────────────────
            self.current_step = 1
            self.emit("step", step=1, name="Descarga")
            downloaded = None
            try:
                downloaded = self._step_download(url, temp_yt)
                automate.SUCCESS_LOG.append("[✓] Descarga completada.")
            except PipelineAbortedError:
                raise
            except Exception as e:
                self._ask_to_continue_web("Descarga de Video de YouTube", str(e))

            # ── STEP 1b: Trim ─────────────────────────────────────────────────
            to_merge = downloaded
            if downloaded and (trim_start > 0 or trim_end > 0):
                try:
                    to_merge = self._step_trim(downloaded, temp_trim, trim_start, trim_end)
                    automate.SUCCESS_LOG.append("[✓] Recorte aplicado.")
                except PipelineAbortedError:
                    raise
                except Exception as e:
                    self._ask_to_continue_web("Recorte de Video (FFmpeg)", str(e))
                    to_merge = None

            # ── STEP 2: AI content ────────────────────────────────────────────
            self.current_step = 2
            self.emit("step", step=2, name="Gemini IA")
            titulo_ia, descripcion_ia, tags_ia = "", "", ""
            try:
                titulo_ia, descripcion_ia, tags_ia = self._step_ai_assets(url, out_desc, out_title_f, extra_context)
                if "short" in video_format and "#shorts" not in tags_ia.lower():
                    tags_ia += ", Shorts"
            except PipelineAbortedError:
                raise
            except Exception as e:
                self._ask_to_continue_web(
                    "Generación de Activos de Marketing (Gemini IA)", str(e)
                )

            # ── STEP 2.5: Thumbnail ───────────────────────────────────────────
            thumb_path = os.path.join(project_dir, "thumbnail.jpg")  # always in project dir
            try:
                if video_format == "normal":
                    prompt_img = (
                        f"Minimalista, tecnología corporativa, informativo. "
                        f"Tema: {titulo_ia}. "
                        "Estilo: Pronectis, empresa de software y tecnología, paleta azul oscuro y blanco, "
                        "limpio, profesional, sin texto sobreimpreso, moderno, alta calidad."
                    )
                    gen_thumb = self._step_thumbnail(prompt_img, thumb_path)
                    if not gen_thumb:
                        thumb_path = None
                else:
                    thumb_path = None
            except Exception as e:
                self.emit("log", msg=f"⚠️ Error miniatura: {e}")
                thumb_path = None

            # ── STEP 3: Concatenate or Format ──────────────────────────────────
            self.current_step = 3
            self.emit("step", step=3, name="Fusión FFmpeg")
            if to_merge:
                try:
                    if video_format == "normal":
                        self._step_concat([intro, to_merge, outro], out_path)
                    elif video_format == "short_direct":
                        import shutil
                        shutil.copy2(to_merge, out_path)
                        automate.SUCCESS_LOG.append(f"[✓] Short copiado directamente: {os.path.basename(out_path)}")
                        self.emit("log", msg="→ Short conservado en su formato original.")
                    elif video_format == "short_crop":
                        self._step_short_format(to_merge, out_path, "crop")
                    elif video_format == "short_pad":
                        self._step_short_format(to_merge, out_path, "pad")

                except PipelineAbortedError:
                    raise
                except Exception as e:
                    self._ask_to_continue_web("Procesamiento de Video (FFmpeg)", str(e))

            # ── STEP 4: Upload ────────────────────────────────────────────────
            self.current_step = 4
            self.emit("step", step=4, name="YouTube")
            video_id = None
            if auto_upload and os.path.exists(out_path) and titulo_ia and descripcion_ia:
                self.emit("log", msg="[4/5] Subida automática a YouTube habilitada...")
                try:
                    video_id = self._step_upload(
                        out_path, titulo_ia, descripcion_ia, "private"
                    )
                    if video_id:
                        # Upload thumbnail if available
                        if thumb_path and os.path.exists(thumb_path):
                            try:
                                automate.set_youtube_thumbnail(video_id, thumb_path)
                                self.emit("log", msg="[✓] Miniatura asignada exitosamente al video.")
                            except Exception as th_err:
                                self.emit("log", msg=f"⚠️ No se pudo asignar la miniatura automáticamente: {th_err}")
                    else:
                        automate.log_error(
                            "Subida YouTube", "La API no retornó ID de video."
                        )
                except PipelineAbortedError:
                    raise
                except Exception as e:
                    self._ask_to_continue_web("Subida Automática YouTube", str(e))
            elif not auto_upload:
                self.emit(
                    "log",
                    msg="→ Subida automática deshabilitada. Esperando revisión y aprobación manual.",
                )
            else:
                automate.log_error(
                    "Subida YouTube",
                    "Falta el video final o los textos de IA para subir.",
                )

            # ── STEP 5: Cleanup ───────────────────────────────────────────────
            self.current_step = 5
            self.emit("step", step=5, name="Limpieza")
            self.emit("log", msg="[5/5] Eliminando archivos temporales...")
            for tmp in automate.TEMP_FILES:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                        self.emit("log", msg=f"   [Eliminado] {os.path.basename(tmp)}")
                    except OSError:
                        pass

            # Write execution log
            ts_log = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_path = os.path.join(SCRIPT_DIR, "pipeline_execution.log")
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"\n{'=' * 50}\n EJECUCIÓN - {ts_log}\n{'=' * 50}\n")
                lf.write(f"URL: {url}\nProyecto: {project_name}\n\n")
                lf.write("[+] COMPLETADOS:\n")
                for s in (automate.SUCCESS_LOG or ["  Ninguno."]):
                    lf.write(f"  {s}\n")
                lf.write("\n[-] ERRORES:\n")
                for e in (automate.ERROR_LOG or ["  [✓] Sin errores."]):
                    lf.write(f"  {e}\n")
                lf.write("-" * 50 + "\n")
            self.emit("log", msg="→ Log de auditoría guardado en pipeline_execution.log")

            # Save tags to file
            if tags_ia:
                with open(out_tags_f, "w", encoding="utf-8") as f:
                    f.write(tags_ia)

            # Build output summary
            thumb_relative = f"{project_name}/thumbnail.jpg" if thumb_path and os.path.exists(thumb_path) else None
            self.last_outputs = {
                "project_name": project_name,
                "project_dir":  project_name,
                "source_url":   url,
                "created_at":   ts,
                "status":       "done",
                "video":        f"{project_name}/video.mp4" if os.path.exists(out_path) else None,
                "titulo":       titulo_ia,
                "descripcion":  descripcion_ia,
                "tags":         tags_ia,
                "thumbnail":    thumb_relative,
                "titulo_file":  f"{project_name}/titulo.txt" if os.path.exists(out_title_f) else None,
                "desc_file":    f"{project_name}/descripcion.txt" if os.path.exists(out_desc) else None,
                "video_format": video_format,
                "youtube_id":   video_id,
                "youtube_url":  f"https://youtu.be/{video_id}" if video_id else None,
            }

            # Update metadata.json with final state
            with open(meta_path, "w", encoding="utf-8") as f:
                _json.dump(self.last_outputs, f, ensure_ascii=False, indent=2)
            self.emit(
                "pipeline_done",
                outputs=self.last_outputs,
                errors=automate.ERROR_LOG[:],
            )
            self.status = "done"

        except PipelineAbortedError as ex:
            self.emit("pipeline_aborted", reason=str(ex))
            self.status = "idle"
            # Delete the project directory completely if aborted
            try:
                if 'project_dir' in dir() or 'project_dir' in locals():
                    if os.path.exists(project_dir):
                        import shutil
                        shutil.rmtree(project_dir, ignore_errors=True)
                        self.emit("log", msg=f"🗑️ Proyecto eliminado tras cancelación.")
            except Exception:
                pass
        except Exception as ex:
            self.emit("pipeline_error", error=str(ex))
            self.status = "error"
            # Try to mark metadata as error if project dir was already created
            try:
                if os.path.exists(meta_path):
                    _existing = _json.loads(open(meta_path, encoding="utf-8").read())
                    _existing["status"] = "error"
                    _existing["error_detail"] = str(ex)
                    with open(meta_path, "w", encoding="utf-8") as f:
                        _json.dump(_existing, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        finally:
            sys.stdout = old_stdout
            automate.ask_to_continue = old_ask
            os.chdir(old_cwd)
