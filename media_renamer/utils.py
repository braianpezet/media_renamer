# -*- coding: utf-8 -*-
"""Utility functions for filename parsing, cleaning, and sorting."""
import re


def sanitize_filename(name: str) -> str:
    """Elimina caracteres inválidos para Windows/Linux/macOS."""
    if not name:
        return ""
    cleaned = re.sub(r'[<>:"/\\|?*]', '', name)
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip('. ')


def clean_query_string(raw: str) -> tuple[str, str | None]:
    """
    Limpia etiquetas comunes de ripeo, códecs, resoluciones y fuentes.
    Retorna (título_limpio, año_detectado).
    """
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', raw)
    detected_year = year_match.group(1) if year_match else None

    # Quitar paréntesis y corchetes
    cleaned = re.sub(r'[\(\[\{].*?[\)\]\}]', '', raw)

    tags = [
        r'\b1080p\b', r'\b720p\b', r'\b4k\b', r'\b2160p\b', r'\bbluray\b', r'\bweb-dl\b',
        r'\blat\b', r'\blatino\b', r'\bespañol\b', r'\bespanol\b', r'\bcastellano\b',
        r'\bx264\b', r'\bx265\b', r'\bhevc\b', r'\bhdrip\b', r'\bdvdrip\b',
        r'\btelefe\b', r'\bcapitulos\b', r'\bblizzboygames\b', r'\bhdtv\b'
    ]
    for tag in tags:
        cleaned = re.sub(tag, '', cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r'[¡!¿?_.\-]+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned, detected_year


# Aggressive movie filename cleaning patterns
_MOVIE_TAG_PATTERNS = [
    # Resolution / quality
    r'\b(?:1080p|720p|4k|2160p|480p|8k)\b',
    # Source
    r'\b(?:WEBRip|BluRay|BRRip|BDRip|HDTV|HDRIP|DVDRip|DVDRIP|HDDVD|HD-DVD)\b',
    # Codec
    r'\b(?:x264|x265|h264|h265|HEVC|AVC|XviD|DivX)\b',
    # DTS-HD variants (must come before simple DTS)
    r'\b(?:DTS[- ]?HD\.?MA\.?\d+(?:\.\d+)?|DTS[- ]?X|DTS[- ]?HD)\b',
    # DTS-HD.MA.5.1 style (with dots)
    r'\bDTS[- ]?HD\.MA\.\d+(?:\.\d+)?\b',
    # Audio
    r'\b(?:AAC|AC3|DTS|DD5\.1|DD\+?|Atmos|TrueHD|FLAC|MP3)\b',
    # Release tags
    r'\b(?:REMUX|REPACK|PROPER|REAL|INTERNAL|LIMITED|UNRATED|EXTENDED|THEATRICAL|DC|CRITERION)\b',
    # Common release groups (non-exhaustive)
    r'\b(?:YIFY|RARBG|ETRG|EVO|GECKOS|NTb|NTG|DEFLATE|FLUX|CACTUS|TiNY|SPARKS|AMIABLE|Joy|BHD|ROVERS|CHD|EPSiLON|FRAMESiLO|HiFi|MAiN|PLAYNOW|RAR|REFiNED|SPARKS|STRiFE|TEPES|TOMMY|ViSiON|WATCHABLE|ZQ)\b',
    # HDR / color
    r'\b(?:HDR|HDR10|Dolby\.?Vision|DV|HLG)\b',
    # Dolby Vision / Dolby variants
    r'\b(?:Dolby\.?Vision|Dolby\.?Atmos|Dolby)\b',
    # 3D
    r'\b(?:3D|HSbs|SBS|OU|TAB)\b',
    # Additional patterns
    r'\b(?:WEB[- ]?DL|WEBDL)\b',
    r'\b(?:10bit|8bit|12bit)\b',
    r'\b(?:DDP\d+(?:\.\d+)?|DD\d+(?:\.\d+)?)\b',
    r'\b(?:Blu[- ]?Ray|BRRip|BDRemux)\b',
    r'\b(?:HC|Hardsub|Softsub)\b',
    r'\b(?:PROPER|REPACK|RERIP)\b',
]


def clean_movie_query(raw: str) -> tuple[str, str | None]:
    """
    Aggressive cleaning for movie filenames.
    Strips resolution, source, codec, audio, release group, and bracket tags.
    Returns (clean_title, detected_year).
    """
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', raw)
    detected_year = year_match.group(1) if year_match else None

    cleaned = raw
    for pattern in _MOVIE_TAG_PATTERNS:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)

    # Remove bracketed content - be aggressive but preserve title-like content
    # First pass: remove brackets containing known tag patterns
    cleaned = re.sub(
        r'[\(\[\{](?:19\d{2}|20\d{2}|1080p|720p|4k|2160p|BluRay|WEBRip|WEBDL|WEB-DL|x264|x265|HEVC|AAC|DTS|Atmos|REMUX|REPACK|PROPER|YIFY|RARBG|HDR|Dolby|Vision|3D|EXTENDED|UNRATED|LIMITED|INTERNAL|HDTV|HDRIP|DVDRip|DTS-HD|MA|TrueHD|DD\d+|DDP\d+|HDR10|HLG|IMAX|FRAMESiLO|NTb|SPARKS|AMIABLE|PLAYNOW).*?[\)\]\}]',
        '', cleaned, flags=re.IGNORECASE
    )
    # Second pass: remove brackets that are mostly separators/dots
    cleaned = re.sub(r'[\(\[\{][.\-_ ]+[\)\]\}]', '', cleaned)
    # Third pass: remove empty brackets
    cleaned = re.sub(r'[\(\[\{]\s*[\)\]\}]', '', cleaned)
    # Fourth pass: if entire string was wrapped in brackets, remove them
    cleaned = re.sub(r'^[\s\[\{\(]+|[\s\]\}\)]+$', '', cleaned)

    # Remove year from cleaned string if it wasn't in brackets
    if detected_year:
        cleaned = re.sub(r'\b' + re.escape(detected_year) + r'\b', '', cleaned)

    # Normalize separators
    cleaned = re.sub(r'[._\-]+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    return cleaned, detected_year


# Extensiones válidas para el organizador de libros. Se incluyen las mismas que
# reconoce Jellyfin (Bookshelf/Books) más algunos formatos habituales de ebook.
_BOOK_EXT_RE = re.compile(
    r'\.(?:epub|mobi|azw3?|pdf|fb2|djvu|lit|cbz|cbr)\s*$',
    re.IGNORECASE,
)

# Etiquetas que suelen agregar las tiendas, los escáneres y los sitios de descarga.
# No se eliminan palabras que puedan formar parte del título (p. ej. "digital" en
# "Vida digital"); sólo las que aparecen entre corchetes o como tag suelto.
_BOOK_TAG_RE = re.compile(
    r'\b(?:retail|unabridged|abridged|illustrated|annotated|edition|edici[oó]n|'
    r'spanish|espa[nñ]ol|castellano|english|ingl[eé]s|ebook|e-book|epub|mobi|'
    r'azw3?|pdf|fb2|djvu|lit|cbz|cbr|calibre|libgen|z-?lib(?:rary)?|annas?|'
    r'archive\.org|scan|scanned|escaneado|digital_?esc|digital|ocr|drm|'
    r'v\d+(?:\.\d+)?)\b',
    re.IGNORECASE,
)

# Grupos entre corchetes que casi siempre son etiquetas técnicas: [digital],
# [escaneado], [pdf], [libgen], etc. Los paréntesis se conservan porque suelen
# contener el autor o el año.
_BOOK_BRACKET_TAG_RE = re.compile(
    r'[\[\{][^\]\}]{1,40}?[\]\}]',
    re.IGNORECASE,
)

_BOOK_VENDOR_TAIL_RE = re.compile(
    r'\s*[-–—]\s*(?:libgen|z-?lib(?:rary)?|annas?|archive\.org)\b.*$',
    re.IGNORECASE,
)

_COMIC_ISSUE_RE = re.compile(
    r'(?:#\s*|(?:\bissue|\bnum(?:ber)?|\bn[°º]\.?|\bchapter|\bch\.?)\s*)(\d{1,4})',
    re.IGNORECASE,
)


def _detect_book_year(raw: str) -> str | None:
    """Detecta un año de edición, no un año que forme parte del título.

    Se acepta el año entre paréntesis/corchetes o al final del nombre. Así
    ``2001: Una odisea (1968)`` usa 1968, y ``2001: Una odisea`` no se queda con
    2001 como año.
    """
    for match in reversed(list(re.finditer(r'\b(1[5-9]\d{2}|20\d{2})\b', raw))):
        before = raw[:match.start()].rstrip()
        after = raw[match.end():].strip()
        if before.endswith(('(', '[')) and after.startswith((')', ']')):
            return match.group(1)
        if not after.strip(' ._-–—,;:'):
            return match.group(1)
    return None


def clean_book_query(raw: str) -> tuple[str, str | None]:
    """Limpia un nombre de archivo de libro y detecta el año.

    A diferencia de ``clean_movie_query``, conserva el autor que suele venir entre
    paréntesis (``Título (Autor).epub``) y no borra un año que forme parte del
    título (``1984``). Devuelve ``(título_o_consulta, año)``.
    """
    base = _BOOK_EXT_RE.sub('', raw)
    detected_year = _detect_book_year(base)

    cleaned = base
    # Quitar etiquetas entre corchetes, pero sólo si el contenido parece técnico.
    for match in list(_BOOK_BRACKET_TAG_RE.finditer(cleaned)):
        content = match.group(0).strip('[]{} ')
        if _BOOK_TAG_RE.search(content) or re.fullmatch(r'\d{1,3}p?', content):
            cleaned = cleaned.replace(match.group(0), ' ', 1)

    cleaned = _BOOK_TAG_RE.sub(' ', cleaned)
    cleaned = _BOOK_VENDOR_TAIL_RE.sub(' ', cleaned)
    cleaned = re.sub(r'[\(\[\{]\s*[\)\]\}]', ' ', cleaned)
    cleaned = re.sub(r'[_.]+', ' ', cleaned)
    cleaned = re.sub(r'[¡!¿?]+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' -–—,;:')

    # Si el nombre es solamente un año (por ejemplo, la novela "1984"), no lo
    # tratamos como año de edición.
    if detected_year and cleaned == detected_year:
        detected_year = None

    return cleaned, detected_year


def parse_comic_filename(raw: str) -> tuple[str, int | None, str | None]:
    """Extrae ``(serie, número, año)`` de un CBZ/CBR.

    Reconoce ``Serie #012 (2020).cbz``, ``Serie issue 12`` y variantes habituales.
    Si no hay número, devuelve ``None`` y el nombre limpio como serie.
    """
    base = _BOOK_EXT_RE.sub('', raw)
    year = _detect_book_year(base)

    issue_match = _COMIC_ISSUE_RE.search(base)
    issue = None
    series_source = base
    if issue_match:
        try:
            issue = int(issue_match.group(1))
        except (TypeError, ValueError):
            issue = None
        series_source = base[:issue_match.start()] + ' ' + base[issue_match.end():]

    series, _ = clean_book_query(series_source)
    if year:
        series = re.sub(r'\b' + re.escape(year) + r'\b', ' ', series)
    series = re.sub(r'[\(\[\{]\s*[\)\]\}]', ' ', series)
    series = sanitize_filename(series).strip(' -–—,;:')
    if not series:
        series, _ = clean_book_query(base)
        if year:
            series = re.sub(r'\b' + re.escape(year) + r'\b', ' ', series)
        series = re.sub(r'[\(\[\{]\s*[\)\]\}]', ' ', series)
        series = sanitize_filename(series).strip(' -–—,;:')

    return series, issue, year


def parse_book_filename(raw: str) -> tuple[str, str | None, str | None]:
    """Separa título, autor y año de nombres como ``Título (Autor) (Año).epub``.

    Se usa como respaldo cuando Open Library/Google Books no devuelven nada. No
    intenta adivinar autores en nombres sin paréntesis: en ese caso devuelve el
    título limpio y ``None`` como autor.
    """
    base = _BOOK_EXT_RE.sub('', raw)
    detected_year = _detect_book_year(base)

    author = None
    title_source = base
    # Recorrer los paréntesis de derecha a izquierda: el último suele ser el año
    # y el anterior el autor.
    for match in reversed(list(re.finditer(r'[\(\[\{]([^\)\]\}]{2,80})[\)\]\}]', base))):
        content = match.group(1).strip()
        if re.fullmatch(r'(?:1[5-9]\d{2}|20\d{2})', content):
            continue
        if _BOOK_TAG_RE.fullmatch(content):
            continue
        if not re.search(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]', content):
            continue
        # Evitar tomar "Temporada 1", "Vol. 2", etc. como autor.
        if re.search(r'\b(?:temporada|season|vol(?:umen)?|tomo|edici[oó]n|edition)\b', content, re.I):
            continue
        words = [word for word in re.split(r'\s+', content) if word]
        if len(words) > 6:
            continue
        author = re.sub(r'(?<=\w)-(?=\w)', ' ', content)
        author = author.replace('_', ' ')
        author = sanitize_filename(author)
        title_source = base[:match.start()] + ' ' + base[match.end():]
        break

    title, year = clean_book_query(title_source)
    # Si el año venía entre paréntesis, no debe quedar dentro del título: la
    # carpeta ya lo agrega como "Título (Año)".
    if detected_year and re.search(r'[\(\[]\s*' + re.escape(detected_year) + r'\s*[\)\]]', base):
        title = re.sub(r'\s*[\(\[]\s*' + re.escape(detected_year) + r'\s*[\)\]]\s*', ' ', title)
        title = re.sub(r'\s+', ' ', title).strip(' -–—,;:')
        year = detected_year
    elif title.strip() == str(detected_year):
        # "1984.epub" es el título, no el año de edición.
        year = None
    return title, author, year


def natural_sort_key(text: str):
    """Clave de orden natural para números dentro de cadenas (ej. ep2 antes de ep10)."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', text)]