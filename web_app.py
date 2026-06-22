#!/usr/bin/env python3
"""
web_app.py
FastAPI server for the Pronectis YouTube Automation Web UI.

Start with:
    uvicorn web_app:app --host 0.0.0.0 --port 8000
or:
    python web_app.py
"""
import os
import sys
import asyncio
import datetime
import time
import subprocess
from typing import List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
OUTPUTS_DIR = os.path.join(SCRIPT_DIR, "outputs")
TEMP_DIR    = os.path.join(SCRIPT_DIR, "temp_uploads")
STATIC_DIR  = os.path.join(SCRIPT_DIR, "static")

sys.path.insert(0, SCRIPT_DIR)
from pipeline_runner import PipelineRunner

LAST_ACTIVITY_TIMESTAMP = time.time()

# ─────────────────────────────────────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Pronectis YT Automation", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# EL PARCHE CORRECTO: Reescribe la ruta para HTTP y WebSockets sin romper nada
@app.middleware("http")
async def limpiar_ruta_app(request, call_next):
    # Ignorar pings del balanceador de cargas (Health Checks) para el auto-apagado
    user_agent = request.headers.get("user-agent", "")
    if "GoogleHC" not in user_agent:
        global LAST_ACTIVITY_TIMESTAMP
        LAST_ACTIVITY_TIMESTAMP = time.time()
    
    # Si por alguna razón entra acá algo de static, lo dejamos pasar
    if "/static" in request.scope["path"]:
        return await call_next(request)
        
    if request.scope["path"].startswith("/app"):
        request.scope["path"] = request.scope["path"].replace("/app", "", 1)
        if not request.scope["path"]:
            request.scope["path"] = "/"
            
    return await call_next(request)

# Serve static files (CSS, JS)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Singleton pipeline runner

runner = PipelineRunner()

# ─────────────────────────────────────────────────────────────────────────────
# WebSocket connection manager
# ─────────────────────────────────────────────────────────────────────────────

class WSManager:
    def __init__(self):
        self.connections: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, msg: dict):
        dead = []
        for ws in self.connections:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = WSManager()

# ─────────────────────────────────────────────────────────────────────────────
# Background task: drain runner queue → broadcast to all WebSocket clients
# ─────────────────────────────────────────────────────────────────────────────

async def _drain_queue():
    while True:
        await asyncio.sleep(0.05)
        while not runner.log_queue.empty():
            try:
                msg = runner.log_queue.get_nowait()
                await manager.broadcast(msg)
            except Exception:
                break


async def _inactivity_monitor():
    while True:
        await asyncio.sleep(60)
        # Si el pipeline se está ejecutando, consideramos que la app está activa
        if runner.status != "idle":
            global LAST_ACTIVITY_TIMESTAMP
            LAST_ACTIVITY_TIMESTAMP = time.time()
            continue
            
        idle_time = time.time() - LAST_ACTIVITY_TIMESTAMP
        if idle_time > 15 * 60:
            print(f"[{datetime.datetime.now()}] Inactividad detectada (15 min). Avisando al usuario...")
            await manager.broadcast({"type": "shutdown_warning", "timeout": 30})
            
            cancelled = False
            for _ in range(30):
                await asyncio.sleep(1)
                if time.time() - LAST_ACTIVITY_TIMESTAMP < 15 * 60:
                    cancelled = True
                    break
            
            if cancelled:
                print(f"[{datetime.datetime.now()}] Apagado cancelado por el usuario.")
                continue
                
            print(f"[{datetime.datetime.now()}] Tiempo agotado o apagado confirmado. Apagando máquina...")
            if os.name == 'nt':
                subprocess.run(["shutdown", "/s", "/t", "5"])
            else:
                # Intenta apagar linux usando la ruta absoluta que autorizamos en visudo
                os.system("sudo /sbin/shutdown -h now")
            break # Salimos del loop porque se está apagando

@app.on_event("startup")
async def _startup():
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    os.makedirs(STATIC_DIR, exist_ok=True)
    asyncio.create_task(_drain_queue())
    asyncio.create_task(_inactivity_monitor())


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────────────────────

from fastapi import UploadFile, File

class RunRequest(BaseModel):
    url: str = ""
    local_file: str = ""
    project_name: str = ""
    trim_start: float = 0.0
    trim_end: float = 0.0
    video_format: str = "normal"
    extra_context: str = ""
    auto_upload: bool = False


class PromptResponseModel(BaseModel):
    action: str  # "continue" | "cancel"


class UploadRequest(BaseModel):
    video_filename: str
    title: str
    description: str
    category_id: str = "28"
    tags: str = ""
    playlist_id: str = ""
    thumbnail_file: str = ""
    privacy_status: str = "private"

class RegenerateRequest(BaseModel):
    field: str
    current_value: str
    instructions: str
    context: str

class ApplyLogoRequest(BaseModel):
    thumbnail_file: str

class RegenThumbnailRequest(BaseModel):
    prompt: str
    output_filename: str


class SettingsSave(BaseModel):
    key: str
    content: str


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/status")
async def api_status():
    return {
        "status": runner.status,
        "step": runner.current_step,
        "outputs": runner.last_outputs,
    }

@app.post("/api/upload-video")
async def api_upload_video(file: UploadFile = File(...)):
    """Recibe un archivo de video local y lo guarda en la carpeta temporal, devolviendo el nombre."""
    import uuid
    # Create temp dir if not exists
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    # Generate a unique filename keeping original extension if possible
    ext = os.path.splitext(file.filename)[1] if file.filename else ".mp4"
    if ext.lower() not in [".mp4", ".mov", ".avi", ".mkv", ".m4v"]:
        ext = ".mp4"
        
    unique_name = f"local_upload_{uuid.uuid4().hex[:8]}{ext}"
    out_path = os.path.join(TEMP_DIR, unique_name)
    
    # Write by chunks to avoid RAM overload with big videos
    with open(out_path, "wb") as buffer:
        while True:
            chunk = await file.read(1024 * 1024 * 5) # 5MB chunks
            if not chunk:
                break
            buffer.write(chunk)
            
    return {"ok": True, "filename": out_path}

# ── Pipeline ──────────────────────────────────────────────────────────────────

@app.get("/api/logs")
def get_logs():
    import automate
    return {"logs": automate.ERROR_LOG + automate.SUCCESS_LOG}

@app.post("/api/cancel-pipeline")
async def cancel_pipeline():
    if runner.status in ("running", "waiting_confirmation"):
        runner.abort("Cancelado por usuario.")
        return {"status": "ok", "message": "Pipeline cancelado y limpiando temporales."}
    return {"status": "ignored", "message": "Pipeline no está en ejecución."}

@app.post("/api/run")
async def run_pipeline(req: RunRequest):
    if runner.status in ("running", "waiting_confirmation"):
        raise HTTPException(409, "Ya hay un pipeline en ejecución. Esperá a que termine.")

    if not req.url.strip() and not req.local_file.strip():
        raise HTTPException(400, "Debe proveer una URL o un archivo local.")
        
    if req.url.strip():
        url = req.url.strip()
        if "youtube.com" not in url and "youtu.be" not in url and "drive.google.com" not in url:
            raise HTTPException(400, "La URL debe ser de YouTube o Google Drive.")
        if "drive.google.com" in url:
            if "/file/d/" not in url and "id=" not in url:
                raise HTTPException(400, "La URL de Drive debe ser un enlace directo a un archivo (no a una carpeta).")
            if len(req.extra_context.strip()) < 50:
                raise HTTPException(400, "El contexto debe tener al menos 50 caracteres para videos de Drive.")
            
    if req.local_file.strip() and len(req.extra_context.strip()) < 50:
        raise HTTPException(400, "El contexto debe tener al menos 50 caracteres para videos locales.")

    # Verify Intro/Outro exist before starting
    intro = os.path.join(SCRIPT_DIR, "Intro Pronectis.mp4")
    outro = os.path.join(SCRIPT_DIR, "Outro Pronectis.mp4")
    if not os.path.exists(intro) or not os.path.exists(outro):
        raise HTTPException(
            500,
            "No se encuentran los archivos 'Intro Pronectis.mp4' y/o 'Outro Pronectis.mp4' "
            "en el directorio del servidor.",
        )

    ok = runner.start(
        url=req.url,
        local_file=req.local_file,
        project_name=req.project_name,
        trim_start=req.trim_start,
        trim_end=req.trim_end,
        video_format=req.video_format,
        extra_context=req.extra_context,
        auto_upload=req.auto_upload,
        outputs_dir=OUTPUTS_DIR,
    )
    if not ok:
        raise HTTPException(409, "No se pudo iniciar el pipeline.")

    return {"ok": True, "message": "Pipeline iniciado correctamente."}


@app.post("/api/prompt-response")
async def prompt_response(req: PromptResponseModel):
    if runner.status != "waiting_confirmation":
        raise HTTPException(409, "No hay ningún prompt de error activo en este momento.")
    if req.action not in ("continue", "cancel"):
        raise HTTPException(400, "El campo 'action' debe ser 'continue' o 'cancel'.")
    runner.respond_to_prompt(req.action)
    return {"ok": True}


# ── History ───────────────────────────────────────────────────────────────────

@app.get("/api/history")
async def api_history():
    """Return the last 5 projects sorted by creation time (newest first)."""
    import json as _json
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    projects = []
    for entry in os.scandir(OUTPUTS_DIR):
        if not entry.is_dir():
            continue
        meta_path = os.path.join(entry.path, "metadata.json")
        if not os.path.exists(meta_path):
            continue
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = _json.load(f)
            meta["_mtime"] = entry.stat().st_mtime
            projects.append(meta)
        except Exception:
            pass
    projects.sort(key=lambda p: p.get("_mtime", 0), reverse=True)
    for p in projects:
        p.pop("_mtime", None)
    return {"projects": projects[:5]}


@app.get("/api/outputs/{filename:path}")
async def get_output(filename: str):
    safe = os.path.realpath(os.path.join(OUTPUTS_DIR, filename))
    if not safe.startswith(os.path.realpath(OUTPUTS_DIR)):
        raise HTTPException(403, "Acceso denegado.")
    if not os.path.isfile(safe):
        raise HTTPException(404, f"Archivo no encontrado: {filename}")
    return FileResponse(safe, filename=os.path.basename(filename))


# ── Log ───────────────────────────────────────────────────────────────────────

@app.get("/api/log")
async def get_log():
    log_path = os.path.join(SCRIPT_DIR, "pipeline_execution.log")
    if not os.path.exists(log_path):
        return {"content": "Sin registros de ejecución aún."}
    with open(log_path, "r", encoding="utf-8") as f:
        return {"content": f.read()}


@app.get("/api/playlists")
async def api_playlists():
    import automate
    playlists = automate.get_youtube_playlists()
    return {"playlists": playlists}

@app.post("/api/regenerate")
async def api_regenerate(req: RegenerateRequest):
    import automate
    try:
        new_text = automate.regenerate_asset(req.field, req.current_value, req.instructions, req.context)
        return {"ok": True, "new_value": new_text}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/upload-thumbnail")
async def api_upload_thumbnail(file: UploadFile = File(...)):
    # Determine target project dir (use latest project if available)
    project_dir = OUTPUTS_DIR
    last = runner.last_outputs or {}
    proj_name = last.get("project_name")
    if proj_name:
        candidate = os.path.join(OUTPUTS_DIR, proj_name)
        if os.path.isdir(candidate):
            project_dir = candidate
    out_path = os.path.join(project_dir, "thumbnail.jpg")
    with open(out_path, "wb") as buffer:
        buffer.write(await file.read())
    rel = f"{proj_name}/thumbnail.jpg" if proj_name else "thumbnail.jpg"
    return {"ok": True, "thumbnail_file": rel}

@app.post("/api/regenerate-thumbnail")
async def api_regenerate_thumbnail(req: RegenThumbnailRequest):
    import automate
    try:
        # Save inside current project dir
        last = runner.last_outputs or {}
        proj_name = last.get("project_name")
        if proj_name:
            candidate = os.path.join(OUTPUTS_DIR, proj_name)
            os.makedirs(candidate, exist_ok=True)
            out_path = os.path.join(candidate, "thumbnail.jpg")
        else:
            out_path = os.path.join(OUTPUTS_DIR, req.output_filename)
        thumb_path = automate.generate_thumbnail_ai(req.prompt, out_path)
        if thumb_path:
            # Si generamos en el directorio del proyecto, la ruta es esa.
            # Sino, la ruta relativa es exactamente req.output_filename
            rel = f"{proj_name}/thumbnail.jpg" if proj_name else req.output_filename
            return {"ok": True, "thumbnail_file": rel}
        else:
            raise HTTPException(500, "La IA no devolvió ninguna imagen.")
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/apply-logo")
async def api_apply_logo(req: ApplyLogoRequest):
    import automate
    logo_path = os.path.join(SCRIPT_DIR, "Logo Pronectis Color.png")
    thumb_path = os.path.join(OUTPUTS_DIR, req.thumbnail_file)
    if not os.path.exists(thumb_path) or not os.path.exists(logo_path):
        raise HTTPException(400, "Falta la miniatura o el logo ('Logo Pronectis Color.png').")
    # Save logo'd thumbnail in same dir as original
    thumb_dir  = os.path.dirname(thumb_path)
    out_path   = os.path.join(thumb_dir, "thumbnail_logo.jpg")
    automate.apply_logo_to_thumbnail(thumb_path, logo_path, out_path)
    # Build relative path
    rel = os.path.relpath(out_path, OUTPUTS_DIR).replace("\\", "/")
    return {"ok": True, "new_thumbnail": rel}

# ── YouTube manual upload ─────────────────────────────────────────────────────

@app.post("/api/upload-youtube")
async def upload_youtube(req: UploadRequest):
    if runner.status in ("running", "waiting_confirmation"):
        raise HTTPException(409, "El pipeline está en ejecución. Esperá a que termine.")

    video_path = os.path.join(OUTPUTS_DIR, req.video_filename)
    if not os.path.isfile(video_path):
        raise HTTPException(404, f"Video no encontrado: {req.video_filename}")

    if not req.title.strip():
        raise HTTPException(400, "El título no puede estar vacío.")

    async def do_upload():
        runner.status = "running"
        import automate, json as _json
        try:
            tags_list = [t.strip() for t in req.tags.split(",") if t.strip()]
            vid_id = automate.upload_video_to_youtube(
                video_path, req.title.strip(), req.description.strip(), 
                category_id=req.category_id, tags=tags_list, privacy_status="private"
            )
            if vid_id:
                # Upload thumbnail if exists
                if req.thumbnail_file:
                    thumb_path = os.path.join(OUTPUTS_DIR, req.thumbnail_file)
                    if os.path.exists(thumb_path):
                        automate.set_youtube_thumbnail(vid_id, thumb_path)
                        
                # Add to playlist
                if req.playlist_id:
                    automate.add_video_to_playlist(vid_id, req.playlist_id)

                # ── Update metadata.json with YouTube URL ──────────────────────
                last = runner.last_outputs or {}
                proj_name = last.get("project_name")
                if proj_name:
                    meta_path = os.path.join(OUTPUTS_DIR, proj_name, "metadata.json")
                    if os.path.exists(meta_path):
                        try:
                            meta = _json.loads(open(meta_path, encoding="utf-8").read())
                            meta["youtube_id"]  = vid_id
                            meta["youtube_url"] = f"https://youtu.be/{vid_id}"
                            meta["status"]      = "done"
                            with open(meta_path, "w", encoding="utf-8") as f:
                                _json.dump(meta, f, ensure_ascii=False, indent=2)
                        except Exception:
                            pass
                    
                await manager.broadcast({
                    "type": "youtube_done",
                    "video_id": vid_id,
                    "url": f"https://youtu.be/{vid_id}",
                    "privacy": "private",
                })
            else:
                await manager.broadcast({
                    "type": "youtube_error",
                    "error": "La API de YouTube no retornó un ID de video.",
                })
        except Exception as exc:
            await manager.broadcast({"type": "youtube_error", "error": str(exc)})
        finally:
            runner.status = "idle"

    asyncio.create_task(do_upload())
    return {"ok": True, "message": "Subida a YouTube iniciada."}


# ── Settings ──────────────────────────────────────────────────────────────────

SETTINGS_FILES = {
    "landing_pages":       "landing_pages.txt",
    "descripcion_ejemplo": "descripcion_ejemplo.txt",
    "titulos_ejemplo":     "titulos_ejemplo.txt",
}


@app.get("/api/settings")
async def get_settings():
    data = {}
    for key, fname in SETTINGS_FILES.items():
        path = os.path.join(SCRIPT_DIR, fname)
        data[key] = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    return data


@app.post("/api/settings")
async def save_settings(req: SettingsSave):
    if req.key not in SETTINGS_FILES:
        raise HTTPException(400, f"Clave de configuración inválida: '{req.key}'.")
    path = os.path.join(SCRIPT_DIR, SETTINGS_FILES[req.key])
    with open(path, "w", encoding="utf-8") as f:
        f.write(req.content)
    return {"ok": True, "message": f"'{req.key}' guardado correctamente."}


@app.post("/api/shutdown")
async def shutdown_vm():
    """Apaga la máquina virtual completamente."""
    import platform, subprocess
    try:
        if platform.system() == "Windows":
            subprocess.Popen(["shutdown", "/s", "/t", "1"])
        else:
            subprocess.Popen(["sudo", "/sbin/shutdown", "-h", "now"])
        return {"ok": True, "message": "Apagando la VM..."}
    except Exception as e:
        raise HTTPException(500, f"Error al apagar: {str(e)}")


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/app/ws")
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    # Immediately send the current state to the newly connected client
    await ws.send_json({
        "type": "status",
        "status": runner.status,
        "step": runner.current_step,
        "outputs": runner.last_outputs,
    })
    try:
        while True:
            data = await ws.receive_json()
            global LAST_ACTIVITY_TIMESTAMP
            
            if data.get("type") == "cancel_shutdown":
                LAST_ACTIVITY_TIMESTAMP = time.time()
                continue
            elif data.get("type") == "confirm_shutdown":
                import subprocess, os
                if os.name == 'nt':
                    subprocess.run(["shutdown", "/s", "/t", "5"])
                else:
                    subprocess.run(["sudo", "/sbin/shutdown", "-h", "now"])
                continue
                
            LAST_ACTIVITY_TIMESTAMP = time.time()
            
            # Client can respond to error prompts directly via WebSocket
            if data.get("type") == "prompt_response":
                action = data.get("action", "cancel")
                runner.respond_to_prompt(action)
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web_app:app", host="0.0.0.0", port=8000, reload=False)
