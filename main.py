import os
import sys
import time
import socket
import logging
import asyncio
import threading
import webbrowser
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

# Manejo seguro de sys.stdout y sys.stderr en modo --noconsole (PyInstaller)
class NullWriter:
    def write(self, s): pass
    def flush(self): pass
    def isatty(self): return False

if sys.stdout is None:
    sys.stdout = NullWriter()
if sys.stderr is None:
    sys.stderr = NullWriter()

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import StreamingResponse
from pydantic import BaseModel
import uvicorn
import httpx

from downloader import (
    search_youtube,
    download_audio_as_mp3,
    ensure_download_dir,
    get_current_download_dir,
    set_current_download_dir,
    get_ffmpeg_path,
    get_progress,
    get_audio_stream_url,
)

# Configuración básica de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("MusicaDownloader")

# Inicialización de FastAPI
app = FastAPI(
    title="Musica Downloader API",
    description="Servidor local para búsqueda y descarga de audio MP3",
    version="1.1.0"
)

# Control de Heartbeat y Ciclo de Vida
DEFAULT_PORT = 8765
HEARTBEAT_TIMEOUT_SECONDS = 12.0  # Umbral de desconexión sin recibir heartbeat (permite F5/recargas)
last_heartbeat_time = time.time()
has_received_first_heartbeat = False
server_start_time = time.time()
is_shutting_down = False

class DownloadRequest(BaseModel):
    url: str
    id: Optional[str] = None
    custom_dir: Optional[str] = None
    task_id: Optional[str] = None

class SetFolderRequest(BaseModel):
    folder: str

def get_base_dir() -> Path:
    """Obtiene la carpeta base del proyecto considerando PyInstaller (_MEIPASS)."""
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS', sys.executable)).resolve()
    return Path(__file__).resolve().parent

def load_index_html() -> str:
    """Carga el contenido del archivo templates/index.html."""
    base_dir = get_base_dir()
    template_path = base_dir / "templates" / "index.html"
    
    if template_path.exists():
        try:
            return template_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Error al leer template desde {template_path}: {e}")

    current_dir_template = Path("templates") / "index.html"
    if current_dir_template.exists():
        return current_dir_template.read_text(encoding="utf-8")

    return """<!DOCTYPE html><html><body style="font-family:sans-serif;background:#090d16;color:white;text-align:center;padding:50px;">
    <h2>Musica Downloader</h2><p>No se pudo localizar el archivo index.html</p></body></html>"""

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Sirve la interfaz web de usuario."""
    content = load_index_html()
    return HTMLResponse(content=content, status_code=200)

@app.post("/api/heartbeat")
async def heartbeat():
    """Recibe señales periódicas del frontend para mantener el servidor vivo."""
    global last_heartbeat_time, has_received_first_heartbeat
    last_heartbeat_time = time.time()
    if not has_received_first_heartbeat:
        has_received_first_heartbeat = True
        logger.info("[+] Primer latido recibido desde el navegador. Sesión activa.")
    return {"status": "alive", "timestamp": last_heartbeat_time}

@app.post("/api/shutdown")
async def shutdown():
    """Endpoint de apagado del servidor."""
    def delayed_exit():
        time.sleep(1.0)
        logger.info("Cerrando proceso del servidor...")
        os._exit(0)

    threading.Thread(target=delayed_exit, daemon=True).start()
    return {"status": "shutting_down"}

@app.get("/api/download-folder")
async def get_download_folder_endpoint():
    """Retorna la carpeta actual de descargas."""
    current_dir = get_current_download_dir()
    return {"folder": str(current_dir)}

def _open_folder_dialog(initial: str) -> Optional[str]:
    """Abre el diálogo nativo de selección de carpeta de Windows en un hilo seguro."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        selected = filedialog.askdirectory(initialdir=initial, title="Selecciona la carpeta para guardar la música")
        root.destroy()
        return selected if selected else None
    except Exception as e:
        logger.error(f"Error en diálogo de selección: {e}")
        return None

@app.post("/api/choose-folder")
async def choose_folder_endpoint():
    """Abre el explorador nativo para escoger una nueva carpeta de guardado."""
    current_folder = str(get_current_download_dir())
    selected = await asyncio.to_thread(_open_folder_dialog, current_folder)
    if selected:
        new_dir = set_current_download_dir(selected)
        return {"success": True, "folder": str(new_dir)}
    return {"success": False, "folder": current_folder, "message": "No se seleccionó ninguna carpeta"}

@app.post("/api/set-download-folder")
async def set_download_folder_endpoint(payload: SetFolderRequest):
    """Establece manualmente una ruta para guardar las descargas."""
    try:
        new_dir = set_current_download_dir(payload.folder)
        return {"success": True, "folder": str(new_dir)}
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})

@app.get("/api/search")
async def search_endpoint(q: str = Query(..., min_length=1, description="Término o URL a buscar")):
    """
    Busca videos en YouTube y retorna hasta 5 resultados con información de audio.
    """
    try:
        results = await asyncio.to_thread(search_youtube, q, 5)
        return {
            "success": True,
            "query": q,
            "count": len(results),
            "results": results
        }
    except Exception as e:
        logger.error(f"Error en búsqueda: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e), "results": []}
        )

@app.get("/api/audio-proxy/{video_id}")
async def audio_proxy_endpoint(video_id: str):
    """
    Proxy de streaming local para reproducción de audio directo en el navegador,
    eliminando restricciones de CORS y bloqueos de origen cruzado de googlevideo.
    """
    try:
        stream_url = await asyncio.to_thread(get_audio_stream_url, video_id)
        if not stream_url:
            return JSONResponse(status_code=404, content={"error": "Audio no disponible"})

        client = httpx.AsyncClient(timeout=30.0)
        req = client.build_request("GET", stream_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
        response = await client.send(req, stream=True)
        return StreamingResponse(
            response.aiter_bytes(),
            media_type=response.headers.get("content-type", "audio/webm"),
            background=client.aclose
        )
    except Exception as e:
        logger.error(f"Error en audio proxy: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/stream-url/{video_id}")
async def stream_url_endpoint(video_id: str):
    """Retorna la URL directa del audio para clientes externos."""
    try:
        url = await asyncio.to_thread(get_audio_stream_url, video_id)
        if url:
            return {"success": True, "stream_url": url}
        return JSONResponse(status_code=404, content={"success": False, "error": "No se pudo extraer el audio"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/progress/{task_id}")
async def progress_endpoint(task_id: str):
    """Consulta el avance en tiempo real de una descarga activa."""
    return get_progress(task_id)

@app.post("/api/download")
async def download_endpoint(payload: DownloadRequest):
    """
    Descarga el audio del video seleccionado y lo convierte a MP3 con ffmpeg.
    """
    if not payload.url:
        raise HTTPException(status_code=400, detail="URL no proporcionada")

    try:
        result = await asyncio.to_thread(
            download_audio_as_mp3, 
            payload.url, 
            payload.custom_dir, 
            payload.task_id
        )
        return result
    except Exception as e:
        logger.error(f"Error en descarga: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )

@app.post("/api/open-folder")
async def open_download_folder():
    """Abre la carpeta de descargas activa en el Explorador de archivos de Windows."""
    try:
        dest_dir = get_current_download_dir()
        if sys.platform == "win32":
            os.startfile(str(dest_dir))
        else:
            subprocess.run(["xdg-open", str(dest_dir)])
        return {"success": True, "path": str(dest_dir)}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/status")
async def get_status():
    """Consulta el estado interno de la aplicación y la ruta de ffmpeg detectada."""
    return {
        "ffmpeg_path": get_ffmpeg_path(),
        "download_directory": str(get_current_download_dir()),
        "uptime": round(time.time() - server_start_time, 2)
    }

# ==========================================
# GESTIÓN DEL CICLO DE VIDA (WATCHDOG)
# ==========================================
def heartbeat_watchdog():
    """
    Monitorea en segundo plano la recepción de heartbeats del navegador.
    Solo empieza a vigilar el timeout UNA VEZ que el navegador se haya conectado
    por primera vez, evitando apagados prematuros.
    """
    global is_shutting_down
    logger.info("[*] Watchdog de Heartbeat iniciado.")

    while not is_shutting_down:
        time.sleep(1.0)
        
        if not has_received_first_heartbeat:
            continue

        current_time = time.time()
        idle_duration = current_time - last_heartbeat_time
        
        if idle_duration > HEARTBEAT_TIMEOUT_SECONDS:
            logger.info(
                f"[-] No se detectó actividad del cliente web por {idle_duration:.1f}s. "
                "Apagando servidor ordenadamente..."
            )
            is_shutting_down = True
            time.sleep(0.3)
            os._exit(0)

def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Verifica si el puerto local está libre para enlace."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) != 0

def find_available_port(start_port: int = 8765, max_attempts: int = 20) -> int:
    """Encuentra un puerto libre comenzando por start_port."""
    for p in range(start_port, start_port + max_attempts):
        if is_port_available(p):
            return p
    return start_port

def start_server_daemon(host: str, port: int):
    """Ejecuta el servidor Uvicorn en modo silencioso y seguro para PyInstaller."""
    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="error",
        log_config=None,  # Crucial: desactiva formateadores estándar que fallan en --noconsole
        access_log=False
    )
    server = uvicorn.Server(config)
    server.run()

def run_app():
    """Función de arranque principal de la aplicación."""
    global server_start_time, last_heartbeat_time
    server_start_time = time.time()
    last_heartbeat_time = time.time()

    # 1. Asegurar carpeta de descargas
    download_folder = ensure_download_dir()

    # 2. Determinar puerto
    port = find_available_port(DEFAULT_PORT)
    host = "127.0.0.1"
    url = f"http://{host}:{port}"

    try:
        print("==================================================")
        print("   MUSICA DOWNLOADER - SERVIDOR LOCAL ACTIVO     ")
        print("==================================================")
        print(f"[*] Interfaz Web:        {url}")
        print(f"[*] Carpeta de Música:   {download_folder}")
        print("[*] Estado:              Esperando conexión del navegador...")
        print("==================================================")
    except Exception:
        pass

    # 3. Iniciar Uvicorn en un hilo demonio
    server_thread = threading.Thread(
        target=start_server_daemon,
        args=(host, port),
        daemon=True
    )
    server_thread.start()

    # 4. Iniciar Watchdog de Heartbeat en otro hilo demonio
    watchdog_thread = threading.Thread(
        target=heartbeat_watchdog,
        daemon=True
    )
    watchdog_thread.start()

    # 5. Esperar confirmación de socket antes de abrir navegador
    for _ in range(30):
        if not is_port_available(port, host):
            break
        time.sleep(0.1)

    # 6. Abrir automáticamente el navegador predeterminado
    try:
        webbrowser.open(url)
    except Exception as e:
        logger.warning(f"No se pudo abrir el navegador automáticamente: {e}")

    # 7. Mantener el hilo principal esperando hasta que el watchdog o shutdown terminen el proceso
    try:
        while not is_shutting_down:
            time.sleep(0.5)
    except (KeyboardInterrupt, SystemExit):
        try:
            print("\n[*] Apagando servidor Musica Downloader...")
        except Exception:
            pass
        os._exit(0)

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    run_app()
