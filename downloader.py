import os
import sys
import shutil
import pathlib
import datetime
from typing import List, Dict, Any, Optional
import yt_dlp

# Directorio de descargas predeterminado: C:\Users\<Usuario>\Downloads\Musica_Descargada
DEFAULT_DOWNLOAD_DIR = pathlib.Path.home() / "Downloads" / "Musica_Descargada"
current_download_dir = DEFAULT_DOWNLOAD_DIR

# Diccionario para almacenar el progreso de las descargas en tiempo real
download_progress: Dict[str, Dict[str, Any]] = {}

def get_progress(task_id: str) -> Dict[str, Any]:
    """Retorna el estado de avance actual de una descarga."""
    return download_progress.get(task_id, {
        "status": "idle",
        "percent": 0.0,
        "speed": "-- MB/s",
        "eta": "--:--",
        "filename": None,
        "error": None
    })

def format_speed(bytes_per_sec: Optional[float | int]) -> str:
    """Convierte bytes/segundo a formato legible KB/s o MB/s."""
    if not bytes_per_sec or bytes_per_sec <= 0:
        return "-- MB/s"
    mb = bytes_per_sec / (1024 * 1024)
    if mb >= 1.0:
        return f"{mb:.1f} MB/s"
    kb = bytes_per_sec / 1024
    return f"{kb:.0f} KB/s"

def get_current_download_dir() -> pathlib.Path:
    """Retorna el directorio de descargas activo."""
    global current_download_dir
    current_download_dir.mkdir(parents=True, exist_ok=True)
    return current_download_dir

def set_current_download_dir(path_str: str) -> pathlib.Path:
    """Actualiza el directorio de descargas activo."""
    global current_download_dir
    new_path = pathlib.Path(path_str).resolve()
    new_path.mkdir(parents=True, exist_ok=True)
    current_download_dir = new_path
    return current_download_dir

def ensure_download_dir() -> pathlib.Path:
    """Asegura que la carpeta de destino exista y la retorna."""
    return get_current_download_dir()

def get_ffmpeg_path() -> str:
    """
    Resuelve la ruta absoluta del binario ffmpeg.exe.
    Prioridad:
    1. sys._MEIPASS (si está congelado en PyInstaller)
    2. Directorio actual o adyacente a la aplicación
    3. PATH del sistema operativo
    """
    # 1. Empaquetado con PyInstaller (Onefile descomprime en _MEIPASS)
    if getattr(sys, 'frozen', False):
        base_dir = pathlib.Path(getattr(sys, '_MEIPASS', sys.executable)).resolve()
        bundle_ffmpeg = base_dir / "ffmpeg.exe"
        if bundle_ffmpeg.exists():
            return str(bundle_ffmpeg)

    # 2. Junto al ejecutable o script
    local_dir = pathlib.Path(__file__).resolve().parent
    local_ffmpeg = local_dir / "ffmpeg.exe"
    if local_ffmpeg.exists():
        return str(local_ffmpeg)

    parent_ffmpeg = local_dir.parent / "ffmpeg.exe"
    if parent_ffmpeg.exists():
        return str(parent_ffmpeg)

    # 3. PATH del sistema
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    # Fallback predeterminado
    return "ffmpeg"

def format_duration(seconds: Optional[int | float]) -> str:
    """Convierte segundos en formato legible MM:SS o HH:MM:SS."""
    if seconds is None:
        return "--:--"
    try:
        total_seconds = int(seconds)
        if total_seconds < 0:
            return "--:--"
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"
    except Exception:
        return "--:--"

def get_best_thumbnail(entry: Dict[str, Any], video_id: str) -> str:
    """Extrae la mejor URL de miniatura disponible o devuelve la estándar de YouTube."""
    default_thumb = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    thumbnails = entry.get("thumbnails")
    if isinstance(thumbnails, list) and thumbnails:
        # Filtrar miniaturas válidas y seleccionar la de mayor resolución
        valid_thumbs = [t for t in thumbnails if isinstance(t, dict) and t.get("url")]
        if valid_thumbs:
            # Ordenar por ancho o resolución si está disponible
            valid_thumbs.sort(key=lambda x: (x.get("width") or 0) * (x.get("height") or 0), reverse=True)
            return valid_thumbs[0].get("url", default_thumb)
    
    thumb = entry.get("thumbnail")
    if isinstance(thumb, str) and thumb.startswith("http"):
        return thumb
        
    return default_thumb

def search_youtube(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Busca videos en YouTube utilizando la API interna de yt-dlp sin descargar contenido.
    Retorna los primeros max_results resultados formateados.
    """
    clean_query = query.strip()
    if not clean_query:
        return []

    # Configuración de búsqueda rápida
    ydl_opts = {
        'extract_flat': True,
        'quiet': True,
        'no_warnings': True,
        'default_search': f'ytsearch{max_results}',
        'skip_download': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'mweb']
            }
        }
    }

    results = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(f"ytsearch{max_results}:{clean_query}", download=False)
            entries = info.get("entries", []) if info else []
            for item in entries:
                if not item:
                    continue
                v_id = item.get("id", "")
                if not v_id:
                    continue
                    
                v_url = item.get("url")
                if not v_url or not v_url.startswith("http"):
                    v_url = f"https://www.youtube.com/watch?v={v_id}"

                channel = item.get("uploader") or item.get("channel") or "Desconocido"
                title = item.get("title") or "Sin título"
                duration = format_duration(item.get("duration"))
                thumbnail = get_best_thumbnail(item, v_id)

                results.append({
                    "id": v_id,
                    "url": v_url,
                    "title": title,
                    "duration": duration,
                    "channel": channel,
                    "thumbnail": thumbnail,
                })
        except Exception as e:
            raise RuntimeError(f"Error al realizar la búsqueda: {str(e)}")

    return results

def get_audio_stream_url(video_id: str) -> Optional[str]:
    """
    Extrae la URL directa del flujo de audio desde YouTube (googlevideo)
    para permitir reproducción nativa HTML5 sin anuncios y evadir bloqueos de inserción.
    """
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'mweb']
            }
        }
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(video_url, download=False)
            return info.get("url")
        except Exception:
            return None

def download_audio_as_mp3(
    video_url: str, 
    custom_dir: Optional[str] = None, 
    task_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Descarga el audio de una URL/ID de YouTube y lo convierte a formato MP3
    con la más alta calidad en la carpeta de descargas del usuario,
    reportando el porcentaje de avance en tiempo real si se provee task_id.
    """
    if custom_dir:
        dest_dir = set_current_download_dir(custom_dir)
    else:
        dest_dir = get_current_download_dir()
    ffmpeg_exe = get_ffmpeg_path()

    # Inicializar estado del progreso
    if task_id:
        download_progress[task_id] = {
            "status": "downloading",
            "percent": 0.0,
            "speed": "-- MB/s",
            "eta": "--:--",
            "filename": None,
            "error": None
        }

    def ytdl_progress_hook(d):
        if not task_id:
            return
        status = d.get('status')
        if status == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes') or 0
            percent = 0.0
            if total > 0:
                percent = round((downloaded / total) * 100, 1)
            else:
                # Intentar parsear de _percent_str
                p_str = d.get('_percent_str', '').replace('%', '').strip()
                try:
                    percent = float(p_str)
                except ValueError:
                    percent = 50.0

            speed = format_speed(d.get('speed'))
            eta_seconds = d.get('eta')
            eta = format_duration(eta_seconds) if eta_seconds is not None else "--:--"

            download_progress[task_id].update({
                "status": "downloading",
                "percent": min(percent, 99.0),
                "speed": speed,
                "eta": eta
            })
        elif status == 'finished':
            download_progress[task_id].update({
                "status": "converting",
                "percent": 100.0,
                "speed": "-- MB/s",
                "eta": "00:00"
            })

    def ytdl_postprocessor_hook(d):
        if not task_id:
            return
        status = d.get('status')
        if status == 'started':
            download_progress[task_id]["status"] = "converting"

    # Plantilla de salida limpia
    outtmpl = str(dest_dir / "%(title)s.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': outtmpl,
        'ffmpeg_location': ffmpeg_exe,
        'progress_hooks': [ytdl_progress_hook],
        'postprocessor_hooks': [ytdl_postprocessor_hook],
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '0',  # Mejor calidad VBR/CBR
        }],
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'mweb']
            }
        }
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(video_url, download=True)
            title = info.get("title", "Desconocido")
            
            # Nombre esperado del archivo .mp3
            filename = f"{title}.mp3"
            target_path = dest_dir / filename

            # Si el título tiene caracteres sanitizados por yt-dlp, buscamos el archivo recién creado
            if not target_path.exists():
                mp3_files = list(dest_dir.glob("*.mp3"))
                if mp3_files:
                    mp3_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                    target_path = mp3_files[0]
                    filename = target_path.name

            result = {
                "success": True,
                "title": title,
                "filename": filename,
                "path": str(target_path),
                "folder": str(dest_dir),
            }

            if task_id:
                download_progress[task_id].update({
                    "status": "finished",
                    "percent": 100.0,
                    "filename": filename
                })

            return result
        except Exception as e:
            if task_id:
                download_progress[task_id].update({
                    "status": "error",
                    "error": str(e)
                })
            return {
                "success": False,
                "error": str(e),
                "folder": str(dest_dir),
            }


def create_ringtone(
    video_url: str,
    start_time: float = 0.0,
    duration: float = 30.0,
    format_type: str = "mp3",
    fade_in: float = 1.0,
    fade_out: float = 2.0,
    custom_dir: Optional[str] = None,
    task_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Recorta un segmento de audio (por defecto 30 segundos) de una canción de YouTube,
    aplica fade-in / fade-out suave y lo exporta en la carpeta Tonos_Celular
    en formato .MP3 (Android) o .M4R (iPhone).
    """
    import subprocess

    if custom_dir:
        base_dest = set_current_download_dir(custom_dir)
    else:
        base_dest = get_current_download_dir()

    # Subcarpeta organizada para tonos de llamada
    dest_dir = base_dest / "Tonos_Celular"
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    ffmpeg_exe = get_ffmpeg_path()
    start_time = max(0.0, float(start_time))
    duration = max(5.0, min(60.0, float(duration)))
    fmt = "m4r" if format_type.lower() == "m4r" else "mp3"

    if task_id:
        download_progress[task_id] = {
            "status": "downloading",
            "percent": 25.0,
            "speed": "Conectando audio",
            "eta": "00:03",
            "filename": None,
            "error": None
        }

    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios', 'mweb']
                }
            }
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            title = info.get("title", "Tono")
            audio_source = info.get("url")

        if not audio_source:
            raise ValueError("No se pudo obtener el flujo de audio para recortar")

        clean_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()
        if not clean_title:
            clean_title = "Tono_Celular"
        
        start_min = int(start_time // 60)
        start_sec = int(start_time % 60)
        start_str = f"{start_min:02d}-{start_sec:02d}"
        filename = f"{clean_title}_tono_{start_str}.{fmt}"
        target_path = dest_dir / filename

        if task_id:
            download_progress[task_id].update({
                "status": "converting",
                "percent": 70.0,
                "speed": "Recortando con FFmpeg",
                "eta": "00:01"
            })

        fade_out_start = max(0.0, duration - fade_out)
        audio_filter = f"afade=t=in:ss=0:d={fade_in},afade=t=out:st={fade_out_start}:d={fade_out}"

        cmd = [
            ffmpeg_exe, "-y",
            "-ss", str(start_time),
            "-t", str(duration),
            "-i", audio_source,
            "-af", audio_filter,
        ]

        if fmt == "m4r":
            cmd += ["-c:a", "aac", "-b:a", "192k", "-f", "ipod", str(target_path)]
        else:
            cmd += ["-c:a", "libmp3lame", "-b:a", "320k", str(target_path)]

        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, startupinfo=startupinfo)
        if proc.returncode != 0:
            err_msg = proc.stderr.decode("utf-8", errors="ignore")
            raise RuntimeError(f"Error en FFmpeg al recortar: {err_msg[:200]}")

        if task_id:
            download_progress[task_id].update({
                "status": "finished",
                "percent": 100.0,
                "filename": filename
            })

        return {
            "success": True,
            "title": title,
            "filename": filename,
            "path": str(target_path),
            "folder": str(dest_dir),
            "duration": duration,
            "format": fmt
        }
    except Exception as e:
        if task_id:
            download_progress[task_id].update({
                "status": "error",
                "error": str(e)
            })
        return {
            "success": False,
            "error": str(e),
            "folder": str(dest_dir)
        }

