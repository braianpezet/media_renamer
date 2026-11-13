# -*- coding: utf-8 -*-
"""Generación de archivos ``.strm`` a partir de carpetas públicas de Google Drive.

Google Drive no ofrece una URL de streaming estable que un ``.strm`` pueda llevar con
cabeceras de autenticación. Por eso esta integración trabaja con carpetas y archivos
compartidos como «Cualquier persona con el enlace».

La URL que se escribe dentro del ``.strm`` es la descarga directa de Google
(``drive.usercontent.google.com``), que admite peticiones ``Range``: Jellyfin, VLC y
Kodi pueden abrir el video y moverse dentro de él sin descargarlo entero primero.

Para listar una carpeta hay dos caminos:

- Sin API key (por defecto): se lee el listado que Google incrusta en la página de la
  carpeta pública. No requiere configuración y funciona para carpetas compartidas.
- Con ``GOOGLE_DRIVE_API_KEY``: se usa la API oficial de Drive v3. Es más robusto,
  pagina automáticamente y conviene para carpetas grandes.
"""
import codecs
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .config import USER_AGENT, VIDEO_EXTENSIONS
from .matcher import EPISODE_MARKER_RE, parse_filename
from .utils import clean_query_string, natural_sort_key, sanitize_filename


DRIVE_SOURCE_MARKER = "#GoogleDriveStrm:source="
DRIVE_API_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"

# Marcas de ripeo que se quitan del título propuesto para el nombre del .strm.
_TECHNICAL_RE = re.compile(
    r"\b(?:"
    r"1080p|720p|480p|2160p|4k|8k|"
    r"x264|x265|h\.?26[45]|hevc|avc|av1|10bit|8bit|12bit|"
    r"bdrip|bluray|blu-ray|brrip|bdremux|web[- .]?dl|webrip|hdtv|hdrip|dvdrip|remux|"
    r"aac|ac3|dts|flac|mp3|ddp?\d?(?:\.\d)?|truehd|atmos|web|"
    r"proper|repack|rerip|multi|dual|subs?|softsub|hardsub"
    r")\b",
    re.IGNORECASE,
)

_NUMBERING_RE = re.compile(
    r"(?:"
    r"[Ss]\d{1,2}[\.\-_ ]*[Ee]\d{1,3}(?:[\.\-_ ]*[Ee]\d{1,3})?"
    r"|\d{1,2}[xX]\d{1,3}(?:-\d{1,3})?"
    r"|\b(?:temporada|season|temp|t)\s*[\.\-_ ]*\d{1,2}\s*"
    r"(?:[,;:\-]|\u2013|\u2014)?\s*(?:episodios?|episodes?|cap[ií]tulos?|ep|e|cap)"
    r"[\.\-_ ]*\d{1,3}"
    r"|\b(?:cap[ií]tulos?|chapter|episodios?|episodes?|cap|ch|ep|e)[\.\-_ ]*\d{1,3}"
    r")",
    re.IGNORECASE,
)

_SPECIAL_FOLDERS = {
    "especiales", "especial", "specials", "special", "ova", "ovas", "extras", "extra",
}

_SEASON_FOLDER_RE = re.compile(
    r"^(?:s|season|temporada|temp|t)\s*[\.\-_ ]*(\d{1,2})$",
    re.IGNORECASE,
)

_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")


class GoogleDriveError(RuntimeError):
    """Error de acceso o de formato al leer Google Drive."""


@dataclass
class DriveVideo:
    """Un video encontrado en Drive y los datos que necesita el generador de .strm."""

    id: str
    name: str
    mime_type: str = ""
    size: int = 0
    path: list[str] = field(default_factory=list)
    season: int | None = None
    episode: int | None = None

    @property
    def source_url(self) -> str:
        return f"https://drive.google.com/file/d/{self.id}/view"

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mime_type": self.mime_type,
            "size": self.size,
            "path": list(self.path),
            "season": self.season,
            "episode": self.episode,
            "source_url": self.source_url,
            "url": self.source_url,
        }


@dataclass
class DriveScanResult:
    root_id: str
    root_name: str
    videos: list[DriveVideo]
    folders_read: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "root_id": self.root_id,
            "root_name": self.root_name,
            "videos": [video.as_dict() for video in self.videos],
            "folders_read": self.folders_read,
            "errors": list(self.errors),
        }


def parse_drive_target(value: str) -> tuple[str, str]:
    """Devuelve ``('folder' | 'file', id)`` desde una URL o un ID de Drive.

    Acepta enlaces de carpeta (``/drive/folders/ID``), de archivo (``/file/d/ID``),
    enlaces ``open?id=ID`` y el ID pelado.
    """
    text = (value or "").strip().strip('"\'')
    if not text:
        raise GoogleDriveError("Falta la URL o el ID de Google Drive.")

    if _DRIVE_ID_RE.match(text) and "://" not in text and "/" not in text:
        return "folder", text

    parsed = urllib.parse.urlparse(text)
    path = parsed.path or ""
    query = urllib.parse.parse_qs(parsed.query)

    match = re.search(r"/folders/([A-Za-z0-9_-]+)", path)
    if match:
        return "folder", match.group(1)

    match = re.search(r"/file/d/([A-Za-z0-9_-]+)", path)
    if match:
        return "file", match.group(1)

    match = re.search(r"/d/([A-Za-z0-9_-]+)", path)
    if match:
        return "file", match.group(1)

    for key in ("id", "folderId", "fileId"):
        if query.get(key):
            kind = "file" if key == "fileId" else "folder"
            return kind, query[key][0]

    # Enlaces cortos de Drive o IDs dentro de texto libre.
    match = re.search(r"([A-Za-z0-9_-]{20,})", text)
    if match:
        return ("file" if "/file/" in path else "folder"), match.group(1)

    raise GoogleDriveError(
        "No se pudo reconocer el enlace de Google Drive. "
        "Usá un enlace de carpeta o de archivo compartido."
    )


def build_stream_url(file_id: str) -> str:
    """URL de descarga directa que se escribe en el .strm.

    ``drive.usercontent.google.com`` responde con ``Range`` y evita la página de
    confirmación de virus de ``drive.google.com/uc`` para archivos grandes.
    """
    file_id = urllib.parse.quote(file_id, safe="")
    return (
        "https://drive.usercontent.google.com/download"
        f"?id={file_id}&export=download&confirm=t"
    )


def build_strm_content(file_id: str, source_url: str | None = None) -> str:
    """Contenido de un .strm con la URL directa de Drive.

    La línea ``#GoogleDriveStrm:source=`` documenta el archivo original y es ignorada
    por los reproductores (las líneas que empiezan con ``#`` no se reproducen).
    """
    source = source_url or f"https://drive.google.com/file/d/{file_id}/view"
    lines = [
        "#EXTM3U",
        "#EXTVLCOPT:network-caching=1000",
        f"{DRIVE_SOURCE_MARKER}{source}",
        build_stream_url(file_id),
    ]
    return "\n".join(lines) + "\n"


def _safe_log(log_fn, message: str) -> None:
    """Loguea sin que un fallo del logger (consola cp1252) aborte la operación."""
    if log_fn is None:
        return
    try:
        log_fn(message)
    except Exception:
        pass


def _unescape_js(text: str) -> str:
    """Resuelve los escapes de la cadena JavaScript de ``_DRIVE_ivd``."""

    def replace(match: re.Match) -> str:
        token = match.group(0)
        if token.startswith(("\\x", "\\u")):
            try:
                return chr(int(token[2:], 16))
            except ValueError:
                return token
        return {
            "\\/": "/",
            "\\'": "'",
            '\\"': '"',
            "\\\\": "\\",
            "\\n": "\n",
            "\\r": "\r",
            "\\t": "\t",
        }.get(token, token[1:])

    return re.sub(r"\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}|\\.", replace, text)


def decode_drive_listing(html: str) -> list[dict]:
    """Extrae los hijos de una carpeta desde el HTML público de Drive.

    Google incrusta el listado en ``window['_DRIVE_ivd']`` como una cadena con el
    JSON codificado. Se devuelve una lista de diccionarios con ``id``, ``name``,
    ``mime_type`` y ``size``.
    """
    match = re.search(r"window\['_DRIVE_ivd'\]\s*=\s*'((?:[^'\\]|\\.)*)'", html)
    if not match:
        return []

    raw = _unescape_js(match.group(1))
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Algunos payloads antiguos venían con escapes tipo \x5b.
        try:
            fallback = codecs.decode(match.group(1), "unicode_escape")
            payload = json.loads(fallback)
        except Exception as exc:  # noqa: BLE001 - se informa como error de formato
            raise GoogleDriveError(f"No se pudo interpretar el listado de Drive: {exc}") from exc

    if not isinstance(payload, list) or not payload:
        return []

    raw_items = payload[0] if isinstance(payload[0], list) else payload
    items = []
    for raw_item in raw_items:
        if not isinstance(raw_item, list) or len(raw_item) < 4:
            continue
        file_id, name, mime_type = raw_item[0], raw_item[2], raw_item[3]
        if not file_id or not name or not mime_type:
            continue
        size = raw_item[13] if len(raw_item) > 13 else 0
        items.append({
            "id": file_id,
            "name": name,
            "mime_type": mime_type,
            "size": int(size) if isinstance(size, (int, float)) else 0,
        })
    return items


def _folder_name_from_html(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return re.sub(r"\s*-\s*Google Drive\s*$", "", title, flags=re.IGNORECASE).strip()


def detect_season_from_path(path: list[str]) -> int | None:
    """Deduce la temporada a partir de las carpetas del recorrido.

    ``S1``, ``Season 01`` y ``Temporada 2`` devuelven su número. Las carpetas de
    especiales (``Especiales``, ``Specials``, ``OVA``…) devuelven ``0``, que Jellyfin
    usa para ``Season 00``.
    """
    for part in reversed(path):
        normalized = (part or "").strip().lower()
        if normalized in _SPECIAL_FOLDERS:
            return 0
        match = _SEASON_FOLDER_RE.match(normalized)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def _has_explicit_episode(name: str) -> bool:
    """True si el nombre trae ``S01E02``, ``1x05`` o un marcador «Episodio 5»."""
    base, _ = os.path.splitext(name)
    if re.search(r"[Ss]\d{1,2}[\.\-_ ]*[Ee]\d{1,3}|\d{1,2}[xX]\d{1,3}", base):
        return True
    return bool(EPISODE_MARKER_RE.search(base))


def detect_episode_number(name: str, fallback: int | None) -> int | None:
    """Afina el número de episodio cuando el nombre no usa un marcador explícito.

    ``parse_filename`` toma el primer número suelto, lo que en anime rompe con títulos
    como «Boku no Hero Academia 4th Season OVAs … - 01». Si no hay ``SxxExx``, ``1x05``
    ni un marcador «Episodio 5», se prefiere el número que cierra el nombre.
    """
    base, _ = os.path.splitext(name)
    if re.search(r"[Ss]\d{1,2}[\.\-_ ]*[Ee]\d{1,3}|\d{1,2}[xX]\d{1,3}", base):
        return fallback
    if EPISODE_MARKER_RE.search(base):
        return fallback

    stripped = re.sub(r"[\[\(\{][^\]\)\}]*[\]\)\}]", " ", base)
    stripped = _TECHNICAL_RE.sub(" ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip(" ._-")
    match = re.search(r"(?:^|[\s._-])(\d{1,3})(?:\s*[vV]\d+)?\s*$", stripped)
    if not match:
        return fallback
    value = int(match.group(1))
    return value if 1 <= value <= 999 else fallback


def clean_drive_title(name: str, episode: int | None = None) -> str:
    """Propone un título legible a partir del nombre del archivo de Drive.

    Quita grupos entre corchetes (grupos de ripeo como ``[BeatZ-Anime]``), etiquetas
    técnicas y, cuando se conoce el episodio, el número que lo representa.
    """
    base, _ = os.path.splitext(name)
    cleaned = re.sub(r"\[[^\]]*\]", " ", base)
    cleaned = _TECHNICAL_RE.sub(" ", cleaned)
    cleaned = _NUMBERING_RE.sub(" ", cleaned)

    if episode is not None:
        # Quitar la última aparición del número de episodio, para no tocar números
        # que formen parte del título (p. ej. «Boku no Hero Academia 2nd Season»).
        matches = [
            match for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", cleaned)
            if int(match.group(1)) == episode
        ]
        if matches:
            target = matches[-1]
            cleaned = cleaned[:target.start()] + " " + cleaned[target.end():]

    cleaned = re.sub(r"[._]+", " ", cleaned)
    cleaned = re.sub(r"[\(\[\{]\s*[\)\]\}]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_.·")
    cleaned = sanitize_filename(cleaned)
    if cleaned:
        return cleaned
    fallback, _ = clean_query_string(base)
    return sanitize_filename(fallback) or sanitize_filename(base) or "Video"


class GoogleDriveClient:
    """Lista carpetas públicas de Google Drive y genera sus ``.strm``."""

    def __init__(
        self,
        api_key: str | None = None,
        log_fn=None,
        timeout: int = 40,
    ):
        self.api_key = (api_key or "").strip()
        self.log_fn = log_fn
        self.timeout = timeout

    # ------------------------- transporte HTTP -------------------------

    def _log(self, message: str) -> None:
        _safe_log(self.log_fn, message)

    def _request(self, url: str, *, as_json: bool = False, extra_headers: dict | None = None):
        headers = {"User-Agent": USER_AGENT}
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                encoding = response.headers.get_content_charset() or "utf-8"
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise GoogleDriveError(
                    "Google Drive negó el acceso. Verificá que la carpeta esté compartida "
                    "como «Cualquier persona con el enlace»."
                ) from error
            if error.code == 404:
                raise GoogleDriveError(
                    "No se encontró la carpeta o el archivo en Google Drive."
                ) from error
            raise GoogleDriveError(f"Google Drive respondió HTTP {error.code}.") from error
        except urllib.error.URLError as error:
            raise GoogleDriveError(
                f"No se pudo conectar con Google Drive: {error.reason}"
            ) from error

        if as_json:
            try:
                return json.loads(body.decode("utf-8", errors="replace"))
            except json.JSONDecodeError as exc:
                raise GoogleDriveError("Google Drive devolvió una respuesta inesperada.") from exc
        return body.decode(encoding, errors="replace")

    def _api_get(self, params: dict) -> dict:
        if not self.api_key:
            raise GoogleDriveError("Falta la API key de Google Drive.")
        query = urllib.parse.urlencode({**params, "key": self.api_key})
        return self._request(f"{DRIVE_API_URL}?{query}", as_json=True)

    # ------------------------- listado -------------------------

    def _list_folder_html(self, folder_id: str) -> tuple[list[dict], str]:
        url = f"https://drive.google.com/drive/folders/{urllib.parse.quote(folder_id)}?usp=sharing"
        html = self._request(url)
        items = decode_drive_listing(html)
        if not items and "_DRIVE_ivd" not in html:
            raise GoogleDriveError(
                "No se pudo leer la carpeta. Compartila como «Cualquier persona con el "
                "enlace» o configurá GOOGLE_DRIVE_API_KEY."
            )
        return items, _folder_name_from_html(html)

    def _list_folder_api(self, folder_id: str) -> tuple[list[dict], str]:
        items = []
        page_token = None
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime)",
                "pageSize": "1000",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._api_get(params)
            for item in data.get("files", []):
                try:
                    size = int(item.get("size") or 0)
                except (TypeError, ValueError):
                    size = 0
                items.append({
                    "id": item.get("id", ""),
                    "name": item.get("name", ""),
                    "mime_type": item.get("mimeType", ""),
                    "size": size,
                })
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return items, self._file_name_api(folder_id)

    def _list_folder(self, folder_id: str) -> tuple[list[dict], str]:
        if self.api_key:
            return self._list_folder_api(folder_id)
        return self._list_folder_html(folder_id)

    def _file_name_api(self, file_id: str) -> str:
        """Nombre visible de una carpeta o archivo; vacío si no se puede leer."""
        if not self.api_key:
            return ""
        try:
            query = urllib.parse.urlencode({"fields": "name", "key": self.api_key})
            data = self._request(
                f"{DRIVE_API_URL}/{urllib.parse.quote(file_id)}?{query}", as_json=True
            )
            return data.get("name") or ""
        except Exception:
            return ""

    def get_file(self, file_id: str) -> dict | None:
        """Devuelve los datos de un archivo concreto (enlace ``/file/d/…``)."""
        if self.api_key:
            query = urllib.parse.urlencode({
                "fields": "id,name,mimeType,size",
                "key": self.api_key,
            })
            data = self._request(
                f"{DRIVE_API_URL}/{urllib.parse.quote(file_id)}?{query}", as_json=True
            )
            try:
                size = int(data.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            return {
                "id": data.get("id", file_id),
                "name": data.get("name", ""),
                "mime_type": data.get("mimeType", ""),
                "size": size,
            }

        html = self._request(f"https://drive.google.com/file/d/{urllib.parse.quote(file_id)}/view")
        name = _folder_name_from_html(html)
        if not name:
            return None
        return {"id": file_id, "name": name, "mime_type": "", "size": 0}

    # ------------------------- recorrido -------------------------

    @staticmethod
    def is_video(item: dict) -> bool:
        mime_type = (item.get("mime_type") or "").lower()
        if mime_type.startswith("video/"):
            return True
        extension = os.path.splitext(item.get("name") or "")[1].lower()
        return extension in VIDEO_EXTENSIONS

    def scan(
        self,
        url_or_id: str,
        recursive: bool = True,
    ) -> DriveScanResult:
        """Recorre una carpeta (o lee un archivo) y devuelve los videos encontrados."""
        kind, root_id = parse_drive_target(url_or_id)
        if kind == "file":
            item = self.get_file(root_id)
            if not item:
                raise GoogleDriveError("No se pudo leer el archivo de Google Drive.")
            video = self._make_video(item, [])
            self._assign_episodes([video])
            return DriveScanResult(
                root_id=root_id,
                root_name=os.path.splitext(item["name"])[0],
                videos=[video],
                folders_read=1,
            )

        root_items, root_name = self._list_folder(root_id)
        videos: list[DriveVideo] = []
        errors: list[str] = []
        folders_read = 1
        visited: set[str] = {root_id}

        def walk(items: list[dict], path: list[str]) -> None:
            nonlocal folders_read
            for item in items:
                if item.get("mime_type") == DRIVE_FOLDER_MIME:
                    if not recursive:
                        continue
                    child_id = item.get("id") or ""
                    if not child_id or child_id in visited:
                        continue
                    visited.add(child_id)
                    child_path = [*path, item.get("name") or ""]
                    self._log(f"[i] Google Drive: leyendo «{'/'.join(child_path)}»…")
                    try:
                        child_items, _ = self._list_folder(child_id)
                    except GoogleDriveError as exc:
                        errors.append(f"{'/'.join(child_path)}: {exc}")
                        self._log(f"[!] {errors[-1]}")
                        continue
                    folders_read += 1
                    walk(child_items, child_path)
                    continue

                if self.is_video(item):
                    videos.append(self._make_video(item, path))

        walk(root_items, [])
        self._assign_episodes(videos)
        videos.sort(
            key=lambda video: (
                video.season if video.season is not None else 999,
                video.episode or 0,
                natural_sort_key(video.name),
            )
        )

        return DriveScanResult(
            root_id=root_id,
            root_name=root_name,
            videos=videos,
            folders_read=folders_read,
            errors=errors,
        )

    @staticmethod
    def _make_video(item: dict, path: list[str]) -> DriveVideo:
        file_season, episode, _episode_end, _local_title, _ext, _tag = parse_filename(
            item.get("name") or ""
        )
        episode = detect_episode_number(item.get("name") or "", episode)
        folder_season = detect_season_from_path(path)
        return DriveVideo(
            id=item.get("id") or "",
            name=item.get("name") or "",
            mime_type=item.get("mime_type") or "",
            size=item.get("size") or 0,
            path=list(path),
            season=folder_season if folder_season is not None else file_season,
            episode=episode,
        )

    @staticmethod
    def _assign_episodes(videos: list[DriveVideo]) -> None:
        """Completa y desambigua la numeración de episodios.

        - Temporadas normales: los archivos sin número se numeran evitando los ya usados.
        - Especiales (temporada 0): los números sueltos («Memories - 01», «OVA - 01»)
          suelen repetirse entre distintos OVAs. Se reasignan en orden natural para que
          cada ``.strm`` tenga un ``S00Exx`` único; los que ya traen ``SxxExx`` se respetan.
        """
        groups: dict[tuple, list[DriveVideo]] = {}
        for video in videos:
            if video.season is not None:
                groups.setdefault(tuple(video.path), []).append(video)

        for group in groups.values():
            group.sort(key=lambda video: natural_sort_key(video.name))
            season = group[0].season
            explicit = {
                video.episode for video in group
                if video.episode is not None and _has_explicit_episode(video.name)
            }

            if season == 0:
                next_episode = 1
                for video in group:
                    if video.episode is not None and _has_explicit_episode(video.name):
                        continue
                    while next_episode in explicit:
                        next_episode += 1
                    video.episode = next_episode
                    explicit.add(next_episode)
                    next_episode += 1
                continue

            next_episode = 1
            for video in group:
                if video.episode is not None:
                    continue
                while next_episode in explicit:
                    next_episode += 1
                video.episode = next_episode
                explicit.add(next_episode)
                next_episode += 1


def _default_name(video: DriveVideo) -> str:
    """Nombre de archivo sugerido: ``S01E01 - Título.strm`` cuando hay temporada."""
    if video.season is not None and video.episode is not None:
        title = clean_drive_title(video.name, video.episode)
        return f"S{video.season:02d}E{video.episode:02d} - {title}.strm"
    return f"{clean_drive_title(video.name)}.strm"


def write_strm_files(
    videos: list[DriveVideo | dict],
    output_dir: str,
    template: str | None = None,
    overwrite: bool = False,
    log_fn=None,
) -> tuple[list[str], int]:
    """Escribe los ``.strm`` de los videos seleccionados.

    Args:
        videos: Lista de ``DriveVideo`` o de diccionarios con las mismas claves.
        output_dir: Carpeta de salida.
        template: Plantilla opcional. Variables: ``{title}``, ``{code}``, ``{season}``,
            ``{ep}``, ``{episode}``, ``{id}``, ``{name}``, ``{original}``, ``{index}``.
        overwrite: Si es ``True`` reemplaza archivos existentes; si no, agrega ``(1)``.
        log_fn: Función de log opcional.

    Returns:
        ``(rutas_creadas, errores)``.
    """
    output_path = os.path.abspath(output_dir)
    os.makedirs(output_path, exist_ok=True)

    def log(message: str) -> None:
        _safe_log(log_fn, message)

    created: list[str] = []
    errors = 0
    used_names: set[str] = set()

    for index, raw_video in enumerate(videos, start=1):
        try:
            video = raw_video if isinstance(raw_video, DriveVideo) else DriveVideo(**{
                key: raw_video.get(key)
                for key in ("id", "name", "mime_type", "size", "path", "season", "episode")
                if key in raw_video
            })
            if not video.id:
                raise ValueError("el video no tiene ID de Drive")

            code = (
                f"S{video.season:02d}E{video.episode:02d}"
                if video.season is not None and video.episode is not None
                else ""
            )
            title = clean_drive_title(video.name, video.episode if code else None)
            original = os.path.splitext(os.path.basename(video.name))[0]
            values = {
                "title": title,
                "code": code,
                "season": video.season if video.season is not None else "",
                "ep": video.episode if video.episode is not None else "",
                "episode": video.episode if video.episode is not None else "",
                "id": video.id,
                "name": video.name,
                "original": original,
                "index": index,
            }

            if template:
                try:
                    filename = template.format(**values)
                except (KeyError, IndexError, ValueError) as exc:
                    log(f"[!] Plantilla inválida ({exc}); se usa el nombre por defecto.")
                    filename = _default_name(video)
            else:
                filename = _default_name(video)

            filename = sanitize_filename(filename)
            if not filename:
                filename = f"{sanitize_filename(original) or 'Video'}.strm"
            if not filename.lower().endswith(".strm"):
                filename += ".strm"

            base_name, extension = os.path.splitext(filename)
            candidate = filename
            counter = 2
            while os.path.normcase(candidate) in used_names or (
                not overwrite and os.path.exists(os.path.join(output_path, candidate))
            ):
                candidate = f"{base_name} ({counter}){extension}"
                counter += 1
            filename = candidate
            used_names.add(os.path.normcase(filename))

            filepath = os.path.join(output_path, filename)
            with open(filepath, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(build_strm_content(video.id, video.source_url))

            created.append(filepath)
            log(f"[OK] Creado: {filename}")
        except Exception as exc:  # noqa: BLE001 - se reporta por archivo
            errors += 1
            name = getattr(raw_video, "name", None) or (
                raw_video.get("name") if isinstance(raw_video, dict) else None
            ) or "video"
            log(f"[!] Error con {name}: {exc}")

    return created, errors
