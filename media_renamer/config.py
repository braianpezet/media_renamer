# -*- coding: utf-8 -*-
"""Configuration module for Media Renamer.

Loads settings from environment variables (.env file) and provides
validated configuration constants.
"""
import os
from pathlib import Path
from dotenv import load_dotenv


# Load .env file at module import time
# This must happen before any other module reads these constants
# Look for .env in package directory first, then parent (project root)
_env_path = Path(__file__).parent / ".env"
if not _env_path.exists():
    _env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_env_path, override=False)


def _require_env(key: str) -> str:
    """Get required environment variable or raise RuntimeError."""
    value = os.environ.get(key, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {key}. "
            f"Copy .env.example to .env and configure your credentials."
        )
    return value


def _get_env(key: str, default: str = "") -> str:
    """Get optional environment variable with default."""
    return os.environ.get(key, default).strip()


# --- TMDb Credentials (Required) ---
TMDB_API_KEY = _require_env("TMDB_API_KEY")
TMDB_BEARER = _require_env("TMDB_BEARER")

# --- TheTVDB Credentials (Optional) ---
THETVDB_API_KEY = _get_env("THETVDB_API_KEY")
THETVDB_PIN = _get_env("THETVDB_PIN")

# --- Google Books (Optional) ---
# Sin clave la API pública funciona con límites bajos; una clave propia evita
# los HTTP 429 cuando se organizan muchos libros de una vez.
GOOGLE_BOOKS_API_KEY = _get_env("GOOGLE_BOOKS_API_KEY")

# --- Google Drive (Optional) ---
# Las carpetas públicas se pueden listar sin clave leyendo el HTML de Drive. Con una
# API key de Google Cloud (Drive API habilitada) se usa la API oficial, que pagina
# automáticamente y es más estable para carpetas grandes. No da acceso a archivos
# privados: la carpeta debe estar compartida como «Cualquier persona con el enlace».
GOOGLE_DRIVE_API_KEY = _get_env("GOOGLE_DRIVE_API_KEY")

# --- Paths ---
PACKAGE_DIR = Path(__file__).parent
CACHE_FILE = PACKAGE_DIR / ".tmdb_cache.json"
THETVDB_CACHE_FILE = PACKAGE_DIR / ".thetvdb_cache.json"
BOOK_CACHE_FILE = PACKAGE_DIR / ".books_cache.json"
HISTORY_FILE = PACKAGE_DIR / "rename_history.json"

# --- Recognized Extensions ---
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.strm'}
SUBTITLE_EXTENSIONS = {'.srt', '.ass', '.vtt', '.sub', '.ssa'}

# Libros electrónicos y cómics. Se separan porque Jellyfin usa una estructura
# distinta para cada tipo (Books/Author/Title y Comics/Series/Issue).
BOOK_EXTENSIONS = {'.epub', '.mobi', '.azw', '.azw3', '.pdf', '.fb2', '.djvu', '.lit'}
COMIC_EXTENSIONS = {'.cbz', '.cbr'}

# --- Defaults ---
DEFAULT_LANGUAGE = _get_env("DEFAULT_LANGUAGE", "es-MX")
USER_AGENT = 'MediaRenamerBot/3.1 (Windows NT 10.0; Win64; x64)'

# --- yt-dlp / generación de .strm ---
DEFAULT_STRM_TEMPLATE = _get_env("STRM_TEMPLATE", "{title} ({id}).strm")

# YouTube entrega video y audio por separado (DASH). Sin forzar un formato muxeado,
# `--get-url` devolvería dos URLs y el .strm apuntaría a un video sin sonido.
DEFAULT_STRM_FORMAT = "best[acodec!=none][vcodec!=none]/best"

# Si el cliente por defecto no expone ningún formato progresivo (solo DASH), se
# reintenta con estos clientes, que sí lo hacen.
DEFAULT_YTDLP_CLIENTS = "android,mweb"
DEFAULT_YTDLP_TIMEOUT = 90

# Rutas candidatas a yt-dlp cuando no está en el PATH. La primera es la copia que
# usa el plugin YtDlpStrm de Jellyfin en este equipo.
YTDLP_CANDIDATES = (
    r"C:\ProgramData\Jellyfin\yt-dlp\yt-dlp.exe",
    str(Path.home() / "AppData" / "Local" / "Programs" / "yt-dlp" / "yt-dlp.exe"),
)


def find_yt_dlp() -> str:
    """Ubica el ejecutable de yt-dlp.

    Orden de preferencia: variable de entorno YTDLP_PATH, rutas candidatas conocidas
    (incluida la copia del plugin de Jellyfin) y, como último recurso, el nombre
    suelto ``yt-dlp`` para que lo busque el PATH.
    """
    configured = _get_env("YTDLP_PATH")
    if configured:
        return configured
    for candidate in YTDLP_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return "yt-dlp"

# --- Acronym Rules ---
ACRONYM_RULES_FILE = PACKAGE_DIR / "acronym_rules.json"


def _load_acronym_rules() -> dict:
    import json
    try:
        with open(ACRONYM_RULES_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[!] No se pudo cargar acronym_rules.json: {e}")
        return {}

    rules = {}
    for key, value in raw.items():
        if key.startswith('_'):
            continue  # Ignore comment/metadata keys
        if not isinstance(value, dict):
            continue
        entry = {k: v for k, v in value.items() if not k.startswith('_')}
        # Convert season_ranges keys from str to int (JSON only allows str keys)
        if 'season_ranges' in entry:
            entry['season_ranges'] = {
                int(k): tuple(v) for k, v in entry['season_ranges'].items()
            }
        rules[key] = entry
    return rules


ACRONYM_RULES = _load_acronym_rules()