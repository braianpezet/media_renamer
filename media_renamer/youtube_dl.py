# -*- coding: utf-8 -*-
"""
Módulo para operaciones con yt-dlp: extracción de información de YouTube
y generación de archivos .strm para playlists y canales.
"""
import os
import json
import subprocess
import threading
from pathlib import Path
from typing import Optional, Callable, Any
from urllib.parse import urlparse, parse_qs

from .config import (
    DEFAULT_STRM_FORMAT,
    DEFAULT_STRM_TEMPLATE,
    DEFAULT_YTDLP_CLIENTS,
    DEFAULT_YTDLP_TIMEOUT,
    VIDEO_EXTENSIONS,
    find_yt_dlp,
)
from .utils import sanitize_filename


# Línea que guarda la URL original dentro del .strm. Jellyfin ignora las líneas que
# empiezan con '#' y usa la primera restante, así que el reproductor ve la URL directa
# mientras el plugin YtDlpStrm conserva la fuente para poder refrescar el archivo.
STRM_SOURCE_MARKER = "#YtDlpStrm:source="

# Destinos soportados al generar los .strm.
DEST_VLC = "vlc"                          # VLC / Kodi: la URL de YouTube como única línea útil
DEST_JELLYFIN = "jellyfin"                # Jellyfin + plugin: marcador de origen, el plugin resuelve
DEST_JELLYFIN_DIRECT = "jellyfin-direct"  # Jellyfin: resuelve la URL directa en el momento


def build_strm_content(
    source_url: str,
    direct_url: Optional[str] = None,
    destination: str = DEST_VLC
) -> str:
    """
    Arma el contenido de un archivo .strm.

    Args:
        source_url: URL de YouTube del video.
        direct_url: URL directa resuelta con yt-dlp (solo para DEST_JELLYFIN_DIRECT).
        destination: DEST_VLC, DEST_JELLYFIN o DEST_JELLYFIN_DIRECT.

    Returns:
        Contenido del archivo, terminado en salto de línea.
    """
    lines = ["#EXTM3U", "#EXTVLCOPT:network-caching=1000"]
    if destination != DEST_VLC:
        lines.append(f"{STRM_SOURCE_MARKER}{source_url}")
    lines.append(direct_url or source_url)
    return "\n".join(lines) + "\n"


def _summarize(lines: list[str], limit: int = 3, width: int = 300) -> str:
    """Resume las líneas de diagnóstico de yt-dlp para mostrarlas en el log."""
    useful = [line.strip() for line in lines if line.strip() and not line.startswith('[')]
    if not useful:
        useful = [line.strip() for line in lines if line.strip()]
    if not useful:
        return "(sin salida de error)"
    text = " | ".join(useful[-limit:])
    return text if len(text) <= width else text[:width] + "…"


def _safe_log(log_fn: Callable[[str], None], message: str) -> None:
    """Loguea sin que un fallo del logger afecte la operación en curso.

    En Windows es habitual que un ``print`` termine en una consola cp1252, que no
    puede representar caracteres como «≈». Sin esta protección, un error de log
    se propagaría al ``except`` del generador y contaría como archivo fallido
    aunque el .strm se haya escrito bien.
    """
    try:
        log_fn(message)
    except Exception:
        pass


class YTDLPExtractor:
    """Wrapper para yt-dlp para extraer información de playlists y canales de YouTube."""

    def __init__(
        self,
        log_fn: Optional[Callable[[str], None]] = None,
        yt_dlp_path: Optional[str] = None,
        format_selector: Optional[str] = None,
        clients: Optional[str] = None,
        timeout: Optional[int] = None
    ):
        self.log_fn = log_fn or (lambda msg: None)
        self.yt_dlp_path = yt_dlp_path or find_yt_dlp()
        self.format_selector = format_selector or DEFAULT_STRM_FORMAT
        self.clients = DEFAULT_YTDLP_CLIENTS if clients is None else clients
        self.timeout = timeout or DEFAULT_YTDLP_TIMEOUT
        self._cancelled = False

    def cancel(self):
        """Cancela la operación en curso."""
        self._cancelled = True

    def _emit(self, message: str):
        _safe_log(self.log_fn, message)

    def _client_list(self) -> list:
        """Clientes alternativos de YouTube, normalizados a lista."""
        clients = getattr(self, 'clients', DEFAULT_YTDLP_CLIENTS)
        if not clients:
            return []
        if isinstance(clients, str):
            return [c.strip() for c in clients.split(',') if c.strip()]
        return list(clients)

    def _build_yt_dlp_cmd(self, url: str, extra_args: list = None) -> list:
        """Construye el comando base de yt-dlp."""
        cmd = [
            self.yt_dlp_path or "yt-dlp",
            "--no-warnings",
            "--flat-playlist",
            "--print-json",
            "--no-download",
        ]
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(url)
        return cmd

    def extract_playlist_info(self, url: str) -> dict | None:
        """
        Extrae información de una playlist o canal de YouTube.
        Retorna dict con 'entries' (lista de videos) y metadatos.
        """
        self._cancelled = False

        try:
            cmd = self._build_yt_dlp_cmd(url)
            self._emit(f"[i] Extrayendo información de: {url}")

            # stderr se une a stdout a propósito: si se abre como pipe y nadie lo lee,
            # el búfer se llena y yt-dlp se queda bloqueado (la ventana parecía colgada).
            # Las líneas que no son JSON se guardan como diagnóstico del error real.
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding='utf-8',
                errors='replace'
            )

            entries = []
            playlist_info = {}
            diagnostics = []

            for line in process.stdout:
                if self._cancelled:
                    process.terminate()
                    return None

                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    diagnostics.append(line)
                    continue

                if not isinstance(data, dict):
                    continue

                # Primer objeto suele ser info de la playlist (solo en modo no-flat).
                if data.get('_type') == 'playlist':
                    playlist_info = {
                        'title': data.get('title') or '',
                        'id': data.get('id', ''),
                        'description': data.get('description') or '',
                        'uploader': data.get('uploader') or '',
                        'webpage_url': data.get('webpage_url', ''),
                    }
                    continue

                if 'id' not in data:
                    continue

                # Es un video individual. Los videos borrados o privados de la lista
                # llegan sin título (así los marca yt-dlp), así que se registran como
                # no disponibles para no ofrecer un .strm que nunca va a reproducirse.
                title = data.get('title') or ''
                entries.append({
                    'id': data.get('id', ''),
                    'title': title,
                    'url': data.get('url', f"https://www.youtube.com/watch?v={data.get('id', '')}"),
                    'duration': data.get('duration'),
                    'uploader': data.get('uploader') or '',
                    'view_count': data.get('view_count'),
                    'upload_date': data.get('upload_date'),
                    'available': bool(title),
                })

                # Con --flat-playlist yt-dlp no emite un objeto de playlist: los datos
                # de la serie vienen dentro de cada video, así que se toman del primero.
                if not playlist_info and data.get('playlist_title'):
                    playlist_info = {
                        'title': data.get('playlist_title', ''),
                        'id': data.get('playlist_id', ''),
                        'description': '',
                        'uploader': data.get('playlist_uploader', ''),
                        'webpage_url': url,
                    }

            return_code = process.wait()

            if self._cancelled:
                return None

            if return_code != 0 and not entries:
                self._emit(
                    f"[!] yt-dlp salió con código {return_code}: {_summarize(diagnostics)}"
                )
                return None

            if not entries:
                self._emit(f"[!] No se encontraron videos. {_summarize(diagnostics)}")

            return {
                'playlist': playlist_info,
                'entries': entries,
                'total': len(entries)
            }

        except FileNotFoundError:
            self._emit(
                f"[!] No se encontró yt-dlp en '{self.yt_dlp_path}'. "
                f"Instalalo con 'pip install yt-dlp' o indicá la ruta correcta."
            )
            return None
        except Exception as e:
            self._emit(f"[!] Error extrayendo playlist: {e}")
            return None

    def resolve_direct_url(self, url: str) -> Optional[str]:
        """
        Resuelve la URL directa del stream de un video de YouTube.

        Se prueba primero el cliente por defecto y luego los alternativos, porque muchos
        videos solo ofrecen streams separados (DASH) y solo un formato progresivo
        (audio y video en un mismo stream) sirve para un .strm de una sola URL.

        Returns:
            La URL directa, o None si no se pudo resolver.
        """
        for client in [None] + self._client_list():
            args = [self.yt_dlp_path or "yt-dlp", "--no-warnings", "--no-playlist", "--get-url"]
            if client:
                args.extend(["--extractor-args", f"youtube:player_client={client}"])
            if self.format_selector:
                args.extend(["--format", self.format_selector])
            args.append(url)

            try:
                proc = subprocess.run(
                    args,
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=self.timeout,
                )
            except FileNotFoundError:
                self._emit(
                    f"[!] No se encontró yt-dlp en '{self.yt_dlp_path}'. "
                    f"Instalalo con 'pip install yt-dlp' o indicá la ruta correcta."
                )
                return None
            except subprocess.TimeoutExpired:
                self._emit(f"[!] Timeout de {self.timeout}s al resolver {url}")
                return None

            if proc.returncode != 0:
                continue

            for line in proc.stdout.splitlines():
                line = line.strip()
                if line.startswith('http'):
                    return line

        return None

    def generate_strm_files(
        self,
        entries: list[dict],
        output_dir: str,
        naming_template: str = DEFAULT_STRM_TEMPLATE,
        log_fn: Optional[Callable[[str], None]] = None,
        overwrite: bool = False,
        destination: str = DEST_VLC,
        season: int = 1,
        episode_start: Optional[int] = None
    ) -> tuple[int, int]:
        """
        Genera archivos .strm para una lista de entradas.

        Args:
            entries: Lista de diccionarios con info de videos (id, title, url)
            output_dir: Directorio donde crear los .strm
            naming_template: Plantilla para nombres de archivo
            log_fn: Función de logging opcional
            overwrite: Si es True, sobrescribe archivos existentes en vez de duplicarlos
            destination: DEST_VLC, DEST_JELLYFIN o DEST_JELLYFIN_DIRECT
            season: Número de temporada, disponible como {season} en la plantilla
            episode_start: Primer episodio de la lista; habilita {ep} y {episode}
                (si es None se numera desde 1)

        Returns:
            Tupla (creados, errores)
        """
        raw_log = log_fn or self._emit

        def log(message: str) -> None:
            """Loguea sin que un fallo del logger aborte la generación."""
            _safe_log(raw_log, message)

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        total = len(entries)
        creados = 0
        errores = 0

        if destination == DEST_JELLYFIN_DIRECT and total:
            log(f"[i] Resolviendo {total} URL(s) con yt-dlp (~3 s por video, puede tardar)...")

        for index, entry in enumerate(entries):
            if self._cancelled:
                log("[!] Generación cancelada.")
                break

            try:
                video_id = entry.get('id', '')
                # Un video borrado o privado no tiene título: no se genera un .strm
                # que nunca va a reproducirse.
                if not entry.get('available', True):
                    log(f"[!] «{video_id}» no está disponible en YouTube (borrado o privado); se omite.")
                    errores += 1
                    continue
                # «or» y no «get(default)»: la clave existe con valor None y el default
                # no se aplicaría (rompía el nombre del archivo).
                title = entry.get('title') or f'video_{video_id}'
                url = entry.get('url') or f"https://www.youtube.com/watch?v={video_id}"

                episode = episode_start + index if episode_start is not None else index + 1

                # Generar nombre de archivo
                safe_title = sanitize_filename(title)
                try:
                    filename = naming_template.format(
                        title=safe_title,
                        id=video_id,
                        url=url,
                        season=season,
                        ep=episode,
                        episode=episode,
                        index=index + 1,
                    )
                except (KeyError, IndexError, ValueError) as e:
                    log(f"[!] Plantilla inválida ({e}); se usa el nombre por defecto.")
                    filename = f"{safe_title} ({video_id}).strm"

                # Asegurar extensión .strm
                if not filename.endswith('.strm'):
                    filename += '.strm'

                # Sanitizar nombre final
                filename = sanitize_filename(filename)
                if not filename.lower().endswith('.strm'):
                    filename += '.strm'

                filepath = output_path / filename

                if not overwrite:
                    # Evitar sobrescribir
                    counter = 1
                    original_stem = filepath.stem
                    while filepath.exists():
                        filepath = output_path / f"{original_stem} ({counter}).strm"
                        counter += 1

                direct_url = None
                if destination == DEST_JELLYFIN_DIRECT:
                    log(f"[i] Resolviendo {index + 1}/{total}: {title[:60]}")
                    direct_url = self.resolve_direct_url(url)

                # Escribir archivo .strm
                filepath.write_text(
                    build_strm_content(url, direct_url, destination),
                    encoding='utf-8'
                )
                creados += 1

                if destination == DEST_JELLYFIN_DIRECT and not direct_url:
                    errores += 1
                    log(f"[!] Sin URL directa (el plugin la resolverá luego): {filepath.name}")
                else:
                    log(f"[OK] Creado: {filepath.name}")

            except Exception as e:
                log(f"[!] Error con {entry.get('title') or entry.get('id') or 'video'}: {e}")
                errores += 1

        return creados, errores


def is_youtube_url(url: str) -> bool:
    """Verifica si una URL es de YouTube."""
    parsed = urlparse(url)
    return parsed.netloc in ('www.youtube.com', 'youtube.com', 'youtu.be', 'music.youtube.com')


def extract_playlist_id(url: str) -> Optional[str]:
    """Extrae el ID de playlist de una URL de YouTube."""
    parsed = urlparse(url)
    if parsed.netloc in ('www.youtube.com', 'youtube.com', 'youtu.be'):
        query = parse_qs(parsed.query)
        if 'list' in query:
            return query['list'][0]
    return None


def extract_channel_id(url: str) -> Optional[str]:
    """Extrae el ID de canal de una URL de YouTube."""
    parsed = urlparse(url)
    path = parsed.path
    
    if '/channel/' in path:
        return path.split('/channel/')[-1].split('/')[0]
    elif '/c/' in path:
        return path.split('/c/')[-1].split('/')[0]
    elif '/@' in path:
        return path.split('/@')[-1].split('/')[0]
    elif '/user/' in path:
        return path.split('/user/')[-1].split('/')[0]
    return None


def detect_youtube_url_type(url: str) -> str:
    """
    Detecta el tipo de URL de YouTube.
    Retorna: 'video', 'playlist', 'channel', 'unknown'
    """
    if not is_youtube_url(url):
        return 'unknown'
    
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    path = parsed.path
    
    # Un enlace de video con &list=... es, para esta app, una playlist: yt-dlp
    # extrae la lista completa (no se usa --no-playlist) aunque el enlace
    # apunte a un video concreto.
    if 'list' in query:
        return 'playlist'
    elif '/watch' in path and 'v' in query:
        return 'video'
    elif any(x in url for x in ['/channel/', '/c/', '/@', '/user/']):
        return 'channel'
    elif 'youtu.be/' in url:
        return 'video'
    
    return 'unknown'
