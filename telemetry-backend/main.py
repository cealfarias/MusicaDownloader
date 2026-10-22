import os
import re
import datetime
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Depends
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, DateTime, desc, func
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# 1. Configuración de Base de Datos (PostgreSQL en Render o SQLite local)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./telemetry.db")

# Render proporciona URLs tipo postgres:// que SQLAlchemy requiere como postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

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

# 2. Modelo de Base de Datos
class DownloadLog(Base):
    __tablename__ = "download_logs"

    id = Column(Integer, primary_key=True, index=True)
    ip_address = Column(String(64), index=True)
    user_agent = Column(String(512))
    os_name = Column(String(64), index=True)
    country = Column(String(64), default="Desconocido")
    file_format = Column(String(16), default="zip")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

Base.metadata.create_all(bind=engine)

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

    # Últimas 15 descargas (con IP anonimizada)
    recent = db.query(DownloadLog).order_by(desc(DownloadLog.created_at)).limit(15).all()
    recent_list = []
    for r in recent:
        # Anonimizar IP para privacidad (ej. 192.168.1.xxx)
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
        "recent_downloads": recent_list
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

    return f"""<!DOCTYPE html>
<html lang="es" class="dark">
<head>
  <meta charset="UTF-8">
  <title>Métricas de Descargas - MusicaDownloader</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen p-6 font-sans">
  <div class="max-w-4xl mx-auto">
    <div class="flex items-center justify-between mb-8 pb-4 border-b border-slate-800">
      <div>
        <h1 class="text-2xl font-black text-white flex items-center gap-2">
          MusicaDownloader Analytics
          <span class="text-xs px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Render Live</span>
        </h1>
        <p class="text-xs text-slate-400">Panel de control de descargas y telemetría</p>
      </div>
      <a href="/" class="text-xs text-slate-400 hover:text-white underline">API Status</a>
    </div>

    <!-- TARJETAS DE MÉTRICAS -->
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
      <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg">
        <span class="text-xs font-semibold text-slate-400 uppercase tracking-wider">Total Descargas</span>
        <p class="text-3xl font-black text-white mt-1">{stats['total_downloads']}</p>
        <span class="text-[11px] text-emerald-400 font-medium">Contador global</span>
      </div>
      <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg">
        <span class="text-xs font-semibold text-slate-400 uppercase tracking-wider">Usuarios Únicos (IPs)</span>
        <p class="text-3xl font-black text-teal-400 mt-1">{stats['unique_users']}</p>
        <span class="text-[11px] text-slate-500">Direcciones IP distintas</span>
      </div>
      <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg">
        <span class="text-xs font-semibold text-slate-400 uppercase tracking-wider">Últimas 24 Horas</span>
        <p class="text-3xl font-black text-amber-400 mt-1">{stats['downloads_last_24h']}</p>
        <span class="text-[11px] text-slate-500">Descargas recientes</span>
      </div>
    </div>

    <!-- TABLA DE DESCARGAS RECIENTES -->
    <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg">
      <h3 class="text-sm font-bold text-white mb-3">Historial Reciente de Descargas</h3>
      <div class="overflow-x-auto">
        <table class="w-full text-left">
          <thead>
            <tr class="border-b border-slate-800 text-[11px] text-slate-400 uppercase tracking-wider">
              <th class="py-2 px-3">IP (Anonimizada)</th>
              <th class="py-2 px-3">Sistema Operativo</th>
              <th class="py-2 px-3">País</th>
              <th class="py-2 px-3">Fecha y Hora</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
      </div>
    </div>
  </div>
</body>
</html>"""
