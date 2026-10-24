import os
import re
import datetime
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Depends
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, desc, func
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# 1. Configuración de Base de Datos (PostgreSQL en Render o SQLite local)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./telemetry.db")

# Normalizar esquemas para SQLAlchemy 2.0 (usar psycopg2 / psycopg de forma explícita)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# URLs donde estarán alojados los archivos (ej. GitHub Releases)
ZIP_DOWNLOAD_URL = os.getenv(
    "ZIP_DOWNLOAD_URL",
    "https://github.com/cealfarias/MusicaDownloader/releases/latest/download/MusicaDownloader-v1.2-Windows.zip"
)
EXE_DOWNLOAD_URL = os.getenv(
    "EXE_DOWNLOAD_URL",
    "https://github.com/cealfarias/MusicaDownloader/releases/latest/download/MusicaDownloader.exe"
)

# 2. Modelos de Base de Datos
class DownloadLog(Base):
    __tablename__ = "download_logs"

    id = Column(Integer, primary_key=True, index=True)
    ip_address = Column(String(64), index=True)
    user_agent = Column(String(512))
    os_name = Column(String(64), index=True)
    country = Column(String(64), default="Desconocido")
    file_format = Column(String(16), default="zip")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

class Feedback(Base):
    __tablename__ = "user_feedbacks"

    id = Column(Integer, primary_key=True, index=True)
    rating = Column(Integer, default=5)
    category = Column(String(32), default="opinion")  # opinion, suggestion, bug
    message = Column(Text)
    email = Column(String(128), nullable=True)
    app_version = Column(String(32), default="v1.2.0")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

class AppEvent(Base):
    __tablename__ = "app_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(64), index=True)  # search, download_success, download_error
    extra_info = Column(String(256), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    print(f"[!] Advertencia al inicializar tablas: {e}")

# 3. Inicialización de FastAPI
app = FastAPI(
    title="MusicaDownloader Telemetry API",
    description="Servicio de seguimiento de descargas y métricas en Render",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def parse_os(user_agent: str) -> str:
    """Extrae el sistema operativo a partir del User-Agent."""
    ua = user_agent.lower()
    if "windows" in ua:
        return "Windows"
    elif "android" in ua:
        return "Android"
    elif "iphone" in ua or "ipad" in ua:
        return "iOS"
    elif "mac os" in ua or "macintosh" in ua:
        return "macOS"
    elif "linux" in ua:
        return "Linux"
    return "Otro"

def get_real_ip(request: Request) -> str:
    """Obtiene la IP real del cliente detrás de proxies o CDNs."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"

# 4. Endpoints del Sistema

@app.get("/")
def home():
    return {
        "service": "MusicaDownloader Telemetry API",
        "status": "online",
        "endpoints": {
            "download_and_track": "/download",
            "stats_json": "/api/stats",
            "dashboard": "/stats"
        }
    }

@app.get("/download")
def download_tracker(request: Request, format: str = "zip", db: Session = Depends(get_db)):
    """
    Registra la descarga en la base de datos y redirige al usuario al archivo solicitado (.ZIP o .EXE).
    El formato .ZIP es el predeterminado para evitar bloqueos y falsas alarmas de navegadores.
    """
    ip = get_real_ip(request)
    ua_string = request.headers.get("user-agent", "Desconocido")
    os_detected = parse_os(ua_string)
    country = request.headers.get("cf-ipcountry") or request.headers.get("x-country-code") or "Desconocido"
    fmt = "exe" if format.lower() == "exe" else "zip"

    # Registrar evento con formato
    log_entry = DownloadLog(
        ip_address=ip,
        user_agent=ua_string[:500],
        os_name=os_detected,
        country=country,
        file_format=fmt
    )
    db.add(log_entry)
    db.commit()

    target_url = EXE_DOWNLOAD_URL if fmt == "exe" else ZIP_DOWNLOAD_URL
    return RedirectResponse(url=target_url, status_code=302)

class FeedbackPayload(BaseModel):
    rating: int = 5
    category: str = "opinion"  # opinion, suggestion, bug
    message: str
    email: Optional[str] = None
    app_version: str = "v1.2.0"

class EventPayload(BaseModel):
    event_type: str
    extra_info: Optional[str] = None

@app.post("/api/feedback")
def submit_feedback(payload: FeedbackPayload, db: Session = Depends(get_db)):
    """Recibe sugerencias, reportes de error o calificaciones de los usuarios."""
    clean_message = payload.message.strip()
    if not clean_message:
        return JSONResponse(status_code=400, content={"success": False, "error": "El mensaje no puede estar vacío"})

    entry = Feedback(
        rating=max(1, min(5, payload.rating)),
        category=payload.category,
        message=clean_message[:2000],
        email=payload.email[:128] if payload.email else None,
        app_version=payload.app_version
    )
    db.add(entry)
    db.commit()
    return {"success": True, "message": "¡Gracias por tus comentarios! Nos ayudan a mejorar la app."}

@app.post("/api/telemetry/event")
def log_app_event(payload: EventPayload, db: Session = Depends(get_db)):
    """Registra eventos anónimos de uso: búsquedas y descargas exitosas."""
    entry = AppEvent(
        event_type=payload.event_type[:64],
        extra_info=payload.extra_info[:256] if payload.extra_info else None
    )
    db.add(entry)
    db.commit()
    return {"success": True}

# ==========================================
# MOTOR DE BÚSQUEDA Y MULTIMEDIA PARA LA WEB
# ==========================================

def get_backend_ffmpeg() -> str:
    """Detecta la ruta del binario ffmpeg en el entorno de Render."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        return shutil.which("ffmpeg") or "ffmpeg"

def backend_search_youtube(query: str, max_results: int = 15):
    """Búsqueda en YouTube mediante yt-dlp."""
    import yt_dlp
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'skip_download': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        entries = info.get('entries', [])
        results = []
        for item in entries:
            v_id = item.get("id")
            if not v_id:
                continue
            dur = item.get("duration")
            m, s = divmod(int(dur or 0), 60)
            results.append({
                "id": v_id,
                "url": f"https://www.youtube.com/watch?v={v_id}",
                "title": item.get("title") or "Sin título",
                "channel": item.get("uploader") or item.get("channel") or "Desconocido",
                "duration": f"{m:02d}:{s:02d}" if dur else "--:--",
                "thumbnail": f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg"
            })
        return results

def backend_get_stream_url(video_id: str):
    """Extrae la URL de flujo directo de googlevideo."""
    import yt_dlp
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        return info.get("url"), info.get("title", "Audio")

@app.get("/api/search")
async def web_search(q: str):
    """Endpoint de búsqueda para la web y la PWA móvil."""
    import asyncio
    clean_q = q.strip()
    if not clean_q:
        return JSONResponse(status_code=400, content={"success": False, "error": "Búsqueda vacía"})
    try:
        results = await asyncio.to_thread(backend_search_youtube, clean_q)
        return {"success": True, "results": results}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/audio-proxy/{video_id}")
async def web_audio_proxy(video_id: str):
    """Transmite el flujo de audio directamente al navegador sin bloqueos de CORS."""
    import httpx
    import asyncio
    try:
        stream_url, _ = await asyncio.to_thread(backend_get_stream_url, video_id)
        if not stream_url:
            return JSONResponse(status_code=404, content={"error": "Audio no disponible"})
        client = httpx.AsyncClient(follow_redirects=True, timeout=30.0)
        req = client.build_request("GET", stream_url)
        resp = await client.send(req, stream=True)
        return StreamingResponse(
            resp.aiter_bytes(),
            media_type=resp.headers.get("content-type", "audio/webm"),
            background=client.aclose
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/ringtone-download")
async def web_ringtone_download(
    id: str,
    start: float = 0.0,
    duration: float = 30.0,
    format: str = "mp3"
):
    """Recorta 30 segundos de audio con FFmpeg, aplica Fade In/Out y entrega el archivo al celular."""
    import asyncio, subprocess, tempfile
    try:
        stream_url, title = await asyncio.to_thread(backend_get_stream_url, id)
        if not stream_url:
            return JSONResponse(status_code=404, content={"error": "Audio no disponible"})
        
        clean_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip() or "Tono"
        fmt = "m4r" if format.lower() == "m4r" else "mp3"
        ffmpeg_exe = get_backend_ffmpeg()
        
        temp_dir = tempfile.mkdtemp()
        out_path = os.path.join(temp_dir, f"{clean_title}_tono.{fmt}")
        
        fade_out_start = max(0.0, duration - 2.0)
        audio_filter = f"afade=t=in:ss=0:d=1,afade=t=out:st={fade_out_start}:d=2"
        
        cmd = [
            ffmpeg_exe, "-y",
            "-ss", str(start),
            "-t", str(duration),
            "-i", stream_url,
            "-af", audio_filter,
        ]
        if fmt == "m4r":
            cmd += ["-c:a", "aac", "-b:a", "192k", "-f", "ipod", out_path]
        else:
            cmd += ["-c:a", "libmp3lame", "-b:a", "320k", out_path]
            
        def run_proc():
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            
        await asyncio.to_thread(run_proc)
        
        return FileResponse(
            path=out_path,
            filename=f"{clean_title}_tono.{fmt}",
            media_type="audio/mp4" if fmt == "m4r" else "audio/mpeg"
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/mp3-download")
async def web_mp3_download(id: str):
    """Convierte y descarga la canción completa en MP3 320kbps directamente al celular."""
    import asyncio, subprocess, tempfile
    try:
        stream_url, title = await asyncio.to_thread(backend_get_stream_url, id)
        if not stream_url:
            return JSONResponse(status_code=404, content={"error": "Audio no disponible"})
            
        clean_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip() or "Cancion"
        ffmpeg_exe = get_backend_ffmpeg()
        temp_dir = tempfile.mkdtemp()
        out_path = os.path.join(temp_dir, f"{clean_title}.mp3")
        
        cmd = [
            ffmpeg_exe, "-y",
            "-i", stream_url,
            "-c:a", "libmp3lame", "-b:a", "320k",
            out_path
        ]
        
        def run_proc():
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            
        await asyncio.to_thread(run_proc)
        
        return FileResponse(
            path=out_path,
            filename=f"{clean_title}.mp3",
            media_type="audio/mpeg"
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Retorna métricas cuantitativas para la landing page o dashboard."""
    total_downloads = db.query(DownloadLog).count()
    unique_ips = db.query(DownloadLog.ip_address).distinct().count()

    # Descargas en las últimas 24 horas
    yesterday = datetime.datetime.utcnow() - datetime.timedelta(days=1)
    downloads_24h = db.query(DownloadLog).filter(DownloadLog.created_at >= yesterday).count()

    # Desglose por Sistema Operativo
    os_breakdown = dict(
        db.query(DownloadLog.os_name, func.count(DownloadLog.id))
        .group_by(DownloadLog.os_name)
        .all()
    )

    # Métricas de Feedback
    total_feedbacks = db.query(Feedback).count()
    avg_rating_res = db.query(func.avg(Feedback.rating)).scalar()
    avg_rating = round(float(avg_rating_res), 1) if avg_rating_res else 5.0
    recent_feedbacks = db.query(Feedback).order_by(desc(Feedback.created_at)).limit(10).all()

    # Métricas de Eventos de la App
    total_searches = db.query(AppEvent).filter(AppEvent.event_type == "search").count()
    total_app_downloads = db.query(AppEvent).filter(AppEvent.event_type == "download_success").count()

    # Últimas 15 descargas (con IP anonimizada)
    recent = db.query(DownloadLog).order_by(desc(DownloadLog.created_at)).limit(15).all()
    recent_list = []
    for r in recent:
        parts = r.ip_address.split(".")
        masked_ip = f"{parts[0]}.{parts[1]}.*.*" if len(parts) == 4 else r.ip_address[:6] + "..."
        recent_list.append({
            "ip": masked_ip,
            "os": r.os_name,
            "country": r.country,
            "date": r.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        })

    return {
        "total_downloads": total_downloads,
        "unique_users": unique_ips,
        "downloads_last_24h": downloads_24h,
        "os_breakdown": os_breakdown,
        "recent_downloads": recent_list,
        "total_feedbacks": total_feedbacks,
        "avg_rating": avg_rating,
        "total_searches": total_searches,
        "total_app_downloads": total_app_downloads,
        "recent_feedbacks": [
            {
                "id": f.id,
                "rating": f.rating,
                "category": f.category,
                "message": f.message,
                "email": f.email or "Anónimo",
                "version": f.app_version,
                "date": f.created_at.strftime("%Y-%m-%d %H:%M UTC")
            } for f in recent_feedbacks
        ]
    }

@app.get("/stats", response_class=HTMLResponse)
def stats_dashboard(db: Session = Depends(get_db)):
    """Panel visual para que el administrador consulte las estadísticas en vivo."""
    stats = get_stats(db)

    rows_html = "".join([
        f"""<tr class="border-b border-slate-800 text-xs">
            <td class="py-2.5 px-3 font-mono text-slate-300">{r['ip']}</td>
            <td class="py-2.5 px-3 font-medium text-emerald-400">{r['os']}</td>
            <td class="py-2.5 px-3 text-slate-400">{r['country']}</td>
            <td class="py-2.5 px-3 text-slate-500 font-mono">{r['date']}</td>
        </tr>""" for r in stats["recent_downloads"]
    ]) or "<tr><td colspan='4' class='py-4 text-center text-slate-500 text-xs'>Aún no hay descargas registradas</td></tr>"

    feedbacks_html = "".join([
        f"""<div class="p-3.5 rounded-xl bg-slate-950/80 border border-slate-800/80 mb-3">
          <div class="flex items-center justify-between mb-1.5">
            <div class="flex items-center gap-2">
              <span class="text-amber-400 font-bold text-xs">{'★' * fb['rating']}{'☆' * (5 - fb['rating'])}</span>
              <span class="text-[11px] px-2 py-0.5 rounded-full bg-slate-800 text-slate-300 capitalize">{fb['category']}</span>
            </div>
            <span class="text-[11px] text-slate-500 font-mono">{fb['date']}</span>
          </div>
          <p class="text-xs text-slate-200 mb-1">{fb['message']}</p>
          <div class="flex items-center justify-between text-[11px] text-slate-500">
            <span>Contacto: <span class="text-slate-400 font-mono">{fb['email']}</span></span>
            <span>App: {fb['version']}</span>
          </div>
        </div>""" for fb in stats["recent_feedbacks"]
    ]) or "<p class='text-xs text-slate-500 text-center py-4'>Aún no se han recibido comentarios de usuarios.</p>"

    return f"""<!DOCTYPE html>
<html lang="es" class="dark">
<head>
  <meta charset="UTF-8">
  <title>Métricas y Feedback - MusicaDownloader</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen p-6 font-sans">
  <div class="max-w-5xl mx-auto">
    <div class="flex items-center justify-between mb-8 pb-4 border-b border-slate-800">
      <div>
        <h1 class="text-2xl font-black text-white flex items-center gap-2">
          MusicaDownloader Analytics & Feedback
          <span class="text-xs px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Render Live</span>
        </h1>
        <p class="text-xs text-slate-400">Telemetría de la aplicación, descargas y opiniones en vivo</p>
      </div>
      <a href="/" class="text-xs text-slate-400 hover:text-white underline">API Status</a>
    </div>

    <!-- TARJETAS DE MÉTRICAS GLOBALES -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
      <div class="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-lg">
        <span class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Total Descargas Web</span>
        <p class="text-2xl sm:text-3xl font-black text-white mt-1">{stats['total_downloads']}</p>
        <span class="text-[11px] text-emerald-400 font-medium">{stats['unique_users']} usuarios únicos</span>
      </div>
      <div class="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-lg">
        <span class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Últimas 24 Horas</span>
        <p class="text-2xl sm:text-3xl font-black text-amber-400 mt-1">{stats['downloads_last_24h']}</p>
        <span class="text-[11px] text-slate-500">Nuevas instalaciones</span>
      </div>
      <div class="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-lg">
        <span class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Búsquedas en App</span>
        <p class="text-2xl sm:text-3xl font-black text-teal-400 mt-1">{stats['total_searches']}</p>
        <span class="text-[11px] text-slate-500">{stats['total_app_downloads']} MP3s convertidos</span>
      </div>
      <div class="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-lg">
        <span class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Calificación Promedio</span>
        <p class="text-2xl sm:text-3xl font-black text-amber-300 mt-1">{stats['avg_rating']} ★</p>
        <span class="text-[11px] text-slate-500">{stats['total_feedbacks']} comentarios</span>
      </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- FEEDBACK DE LOS USUARIOS -->
      <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg flex flex-col justify-between">
        <div>
          <h3 class="text-sm font-bold text-white mb-3 flex items-center gap-2">
            💬 Opiniones y Sugerencias de Usuarios
            <span class="text-[11px] px-2 py-0.5 rounded-full bg-slate-800 text-emerald-400">{stats['total_feedbacks']} recibidos</span>
          </h3>
          <div class="max-h-[380px] overflow-y-auto pr-1">
            {feedbacks_html}
          </div>
        </div>
      </div>

      <!-- TABLA DE DESCARGAS RECIENTES -->
      <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg">
        <h3 class="text-sm font-bold text-white mb-3">Historial Reciente de Descargas Web</h3>
        <div class="overflow-x-auto max-h-[380px] overflow-y-auto">
          <table class="w-full text-left">
            <thead>
              <tr class="border-b border-slate-800 text-[11px] text-slate-400 uppercase tracking-wider">
                <th class="py-2 px-3">IP</th>
                <th class="py-2 px-3">SO</th>
                <th class="py-2 px-3">País</th>
                <th class="py-2 px-3">Fecha</th>
              </tr>
            </thead>
            <tbody>
              {rows_html}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</body>
</html>"""
