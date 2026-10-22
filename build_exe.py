"""
Script automatizado para compilar la aplicación a un único ejecutable portable (.exe)
con PyInstaller, empaquetando templates y ffmpeg.exe.
"""

import os
import sys
import shutil
import pathlib
import subprocess

def build():
    workspace = pathlib.Path(__file__).resolve().parent
    templates_dir = workspace / "templates"
    main_script = workspace / "main.py"

    print("==================================================")
    print(" INICIANDO COMPILACIÓN DE MUSICA DOWNLOADER (EXE)")
    print("==================================================")

    # 0. Cerrar instancias previas si están abiertas para evitar 'Acceso denegado'
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/IM", "MusicaDownloader.exe"], capture_output=True)
        except Exception:
            pass

    # 1. Localizar ffmpeg.exe
    ffmpeg_source = shutil.which("ffmpeg")
    local_ffmpeg = workspace / "ffmpeg.exe"

    if not ffmpeg_source and not local_ffmpeg.exists():
        print("[!] ERROR: No se encontró 'ffmpeg.exe' en el PATH del sistema ni en la raíz.")
        print("Por favor, asegúrate de que ffmpeg esté instalado o coloca 'ffmpeg.exe' en esta carpeta.")
        sys.exit(1)

    ffmpeg_target = local_ffmpeg if local_ffmpeg.exists() else pathlib.Path(ffmpeg_source)
    print(f"[*] Binario ffmpeg detectado en: {ffmpeg_target}")

    # 2. Configurar argumentos de PyInstaller
    # Separador en Windows es ';' para --add-data y --add-binary
    data_arg = f"{templates_dir};templates"
    binary_arg = f"{ffmpeg_target};."

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name", "MusicaDownloader",
        "--add-data", data_arg,
        "--add-binary", binary_arg,
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols",
        "--hidden-import", "uvicorn.protocols.http",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.http.h11_impl",
        "--hidden-import", "uvicorn.protocols.websockets",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.filedialog",
        "--hidden-import", "yt_dlp",
        "--hidden-import", "yt_dlp.extractor",
        "--hidden-import", "yt_dlp.postprocessor",
        "--hidden-import", "httpx",
        "--copy-metadata", "yt-dlp",
        "--copy-metadata", "fastapi",
        str(main_script)
    ]

    print("[*] Ejecutando comando de empaquetado...")
    print(" ".join(cmd))
    print("--------------------------------------------------")

    result = subprocess.run(cmd, cwd=str(workspace))
    if result.returncode != 0:
        print("[X] Falló la compilación con PyInstaller.")
        sys.exit(result.returncode)

    dist_exe = workspace / "dist" / "MusicaDownloader.exe"
    dist_zip = workspace / "dist" / "MusicaDownloader-v1.2-Windows.zip"

    if dist_exe.exists():
        size_mb = dist_exe.stat().st_size / (1024 * 1024)
        print("--------------------------------------------------")
        print("[OK] ¡COMPILACIÓN EXITOSA!")
        print(f"[OK] Archivo ejecutable generado: {dist_exe} ({size_mb:.2f} MB)")

        # Empaquetar en archivo ZIP para evitar bloqueos de navegadores
        import zipfile
        print("[*] Comprimiendo a archivo ZIP para distribución web...")
        with zipfile.ZipFile(dist_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.write(dist_exe, arcname="MusicaDownloader.exe")
        zip_size_mb = dist_zip.stat().st_size / (1024 * 1024)
        print(f"[OK] Archivo ZIP generado:        {dist_zip} ({zip_size_mb:.2f} MB)")
        print("==================================================")
    else:
        print("[!] Advertencia: La compilación finalizó pero no se localizó el archivo en dist/.")

if __name__ == "__main__":
    build()
