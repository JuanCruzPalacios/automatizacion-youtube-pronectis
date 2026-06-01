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
from typing import List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
OUTPUTS_DIR = os.path.join(SCRIPT_DIR, "outputs")
STATIC_DIR  = os.path.join(SCRIPT_DIR, "static")

sys.path.insert(0, SCRIPT_DIR)
from pipeline_runner import PipelineRunner

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


@app.on_event("startup")
async def _startup():
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    os.makedirs(STATIC_DIR, exist_ok=True)
    asyncio.create_task(_drain_queue())


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    url: str
    out_filename: str = "final_output.mp4"
    trim_start: float = 0.0
    trim_end: float = 0.0
    auto_upload: bool = False


class PromptResponseModel(BaseModel):
    action: str  # "continue" | "cancel"


class UploadRequest(BaseModel):
    video_filename: str
    title: str
    description: str
    privacy_status: str = "private"


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


# ── Pipeline ──────────────────────────────────────────────────────────────────

@app.post("/api/run")
async def run_pipeline(req: RunRequest):
    if runner.status in ("running", "waiting_confirmation"):
        raise HTTPException(409, "Ya hay un pipeline en ejecución. Esperá a que termine.")

    if not req.url.strip():
        raise HTTPException(400, "La URL no puede estar vacía.")
    if "youtube.com" not in req.url and "youtu.be" not in req.url:
        raise HTTPException(400, "La URL debe ser de YouTube (youtube.com o youtu.be).")
    if not req.out_filename.strip():
        raise HTTPException(400, "El nombre del archivo de salida no puede estar vacío.")

    out_fn = req.out_filename.strip()
    if not out_fn.lower().endswith(".mp4"):
        out_fn += ".mp4"

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
        out_filename=out_fn,
        trim_start=req.trim_start,
        trim_end=req.trim_end,
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


# ── Outputs ───────────────────────────────────────────────────────────────────

@app.get("/api/outputs")
async def list_outputs():
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    result = []
    for name in os.listdir(OUTPUTS_DIR):
        if name.startswith("."):
            continue
        full = os.path.join(OUTPUTS_DIR, name)
        if os.path.isfile(full) and not name.startswith("temp_"):
            st = os.stat(full)
            result.append({
                "name": name,
                "size": st.st_size,
                "modified": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(),
                "ext": os.path.splitext(name)[1].lower(),
            })
    return sorted(result, key=lambda x: x["modified"], reverse=True)


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


# ── YouTube manual upload ─────────────────────────────────────────────────────

@app.post("/api/upload-youtube")
async def upload_youtube(req: UploadRequest):
    if runner.status in ("running", "waiting_confirmation"):
        raise HTTPException(409, "El pipeline está en ejecución. Esperá a que termine.")

    if req.privacy_status not in ("private", "unlisted", "public"):
        raise HTTPException(400, "privacy_status debe ser 'private', 'unlisted' o 'public'.")

    video_path = os.path.join(OUTPUTS_DIR, req.video_filename)
    if not os.path.isfile(video_path):
        raise HTTPException(404, f"Video no encontrado: {req.video_filename}")

    if not req.title.strip():
        raise HTTPException(400, "El título no puede estar vacío.")
    if not req.description.strip():
        raise HTTPException(400, "La descripción no puede estar vacía.")

    async def do_upload():
        runner.status = "running"
        try:
            vid_id = runner._step_upload(
                video_path, req.title.strip(), req.description.strip(), req.privacy_status
            )
            if vid_id:
                await manager.broadcast({
                    "type": "youtube_done",
                    "video_id": vid_id,
                    "url": f"https://youtu.be/{vid_id}",
                    "privacy": req.privacy_status,
                })
            else:
                await manager.broadcast({
                    "type": "youtube_error",
                    "error": "La API de YouTube no retornó un ID de video. "
                             "Revisá el token y los permisos.",
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


# ── WebSocket ─────────────────────────────────────────────────────────────────

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
