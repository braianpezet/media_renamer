# -*- coding: utf-8 -*-
import os
import re
import difflib
from .utils import sanitize_filename
from .config import ACRONYM_RULES

# Palabras que introducen un número de episodio, en español y en inglés. Se comparten
# con el modo «solo limpiar» (organizer.plan_clean_filenames) para que ambos reconozcan
# exactamente los mismos formatos. El orden importa: lo más largo va primero, y la «e»
# suelta queda al final como último recurso (para «T4 E16»).
EPISODE_WORDS = r'(?:cap[ií]tulos?|chapter|chap|episodios?|episodes?|parts?|partes?|cap|ch|ep|e)'

# Marcador completo: palabra de episodio + número, con segundo episodio opcional
#   Capítulo 5 · Capitulo 5 · Cap 5 · Ep 5 · Ep. 5 · Episodio 5
#   Episode 5 · Chapter 5 · Ch 5 · Episode 5 y 6 · Chapter 5-6
# El \b inicial evita falsos positivos por palabras que contienen la clave
# (p. ej. «PREP 5» o «Teach 5»): sin él, la «ep»/«ch» de adentro haría match.
EPISODE_MARKER_RE = re.compile(
    r'\b' + EPISODE_WORDS + r'[\.\-_ ]*(\d{1,3})'
    r'(?:\s*(?:y|and|,|-)\s*(\d{1,3})(?![0-9pP]))?',
    re.IGNORECASE,
)

# Cola «(id)» que la pestaña de YouTube agrega a los archivos: 11 caracteres
# alfanuméricos, guion o guion bajo. Sus dígitos no son un número de episodio, así que
# se quitan antes de interpretar el nombre. El largo exacto es a propósito: así no se
# toca un año como «(1984)», que el matcher sí necesita para buscar la serie.
_YOUTUBE_ID_TAIL_RE = re.compile(r'\s*\([A-Za-z0-9_-]{11}\)\s*$')


def parse_filename(filename: str):
    """
    Extrae (temporada, ep_inicio, ep_fin, titulo_local, ext, tag_acronimo).
    Soporta:
      - 01PRFDT.mkv, 100PRMM.mkv (número + acrónimo)
      - PRMM01.mkv, PRFDT02.mkv (acrónimo + número)
      - 5x125-126 Titulo (1).mkv
      - S01E02 o S01E02-E03
      - 4x69 Tortugas en Peking.mkv
      - SHA-01.mp4 o 01.mp4
      - Titulo del video (dQw4w9WgXcQ).strm
    """
    base, ext = os.path.splitext(filename)
    base = _YOUTUBE_ID_TAIL_RE.sub('', base)

    # Patrón 0: Número seguido de acrónimo conocido (ej. 01PRFDT, 100PRMM)
    m0 = re.match(r'^(\d{1,3})([a-zA-Z]{2,6})$', base)
    if m0:
        ep = int(m0.group(1))
        tag = m0.group(2).upper()
        return None, ep, None, "", ext, tag

    # Patrón 0b: Acrónimo seguido de número (ej. PRFDT01, PRMM100)
    m0b = re.match(r'^([a-zA-Z]{2,6})(\d{1,3})$', base)
    if m0b:
        tag = m0b.group(1).upper()
        ep = int(m0b.group(2))
        return None, ep, None, "", ext, tag

    # Patrón 1: S01E02 o S01E02-E03
    m1 = re.search(r'[Ss](\d{1,2})[\.\-_ ]*[Ee](\d{1,3})(?:[\.\-_ ]*[Ee](\d{1,3}))?', base)
    if m1:
        s = int(m1.group(1))
        e1 = int(m1.group(2))
        e2 = int(m1.group(3)) if m1.group(3) else None
        local_title = base[m1.end():].strip(' -_.')
        local_title = re.sub(r'\s*\(\d+\)$', '', local_title).strip()
        return s, e1, e2, local_title, ext, None

    # Patrón 2: 5x125-126 o 4x69
    m2 = re.search(r'(\d{1,2})[xX](\d{1,3})(?:-(\d{1,3}))?', base)
    if m2:
        s = int(m2.group(1))
        e1 = int(m2.group(2))
        e2 = int(m2.group(3)) if m2.group(3) else None
        local_title = base[m2.end():].strip(' -_.')
        local_title = re.sub(r'\s*\(\d+\)$', '', local_title).strip()
        return s, e1, e2, local_title, ext, None

    # Patrón 2b: "Temporada 4, Episodio 16" / "Season 2 Episode 5" / "T4 E16"
    # Es el formato habitual de los títulos de YouTube. Exige temporada Y episodio, así
    # que no pisa a los patrones anteriores ni se dispara con números sueltos.
    # Va antes del patrón 3 para no perder la temporada que sí está en el nombre.
    m2b = re.search(
        r'\b(?:temporada|season|temp|t)\s*[\.\-_ ]*(\d{1,2})'
        r'\s*(?:[,;:\-]|\u2013|\u2014)?\s*(?:y\s+|and\s+)?'
        r'(?:episodios?|episodes?|cap[ií]tulos?|ep|e|cap)\s*[\.\-_ ]*(\d{1,3})',
        base, re.IGNORECASE)
    if m2b:
        s = int(m2b.group(1))
        e1 = int(m2b.group(2))
        # Título local = texto posterior al marcador (misma convención que el resto).
        # En "… - Temporada 4, Episodio 16" queda vacío a propósito: así el fuzzy
        # matching no puede pisar el número de episodio que ya leímos del título.
        local_title = base[m2b.end():].strip(' -_.')
        # Quita el sufijo «(id)» de YouTube, sin tocar títulos como «(1a parte)».
        local_title = re.sub(r'\s*\([^)\s]{1,20}\)\s*$', '', local_title).strip()
        return s, e1, None, local_title, ext, None

    # Patrón 3: palabra de episodio + número, en español y en inglés, con segundo
    # episodio opcional ("Episodio 5", "Ep. 5", "Chapter 5", "Episode 5 y 6").
    m3 = EPISODE_MARKER_RE.search(base)
    if m3:
        e1 = int(m3.group(1))
        e2 = int(m3.group(2)) if m3.group(2) else None
        local_title = base[m3.end():].strip(' -_.')
        local_title = re.sub(r'\s*\(\d+\)$', '', local_title).strip()
        return None, e1, e2, local_title, ext, None

    # Patrón 4: Prefijo o número suelto (ej. SHA-01, 01 - Titulo)
    m4 = re.search(r'(?:^|[^\d])(\d{1,3})(?:[^\d]|$)', base)
    if m4:
        e1 = int(m4.group(1))
        local_title = base[m4.end():].strip(' -_.')
        local_title = re.sub(r'\s*\(\d+\)$', '', local_title).strip()
        return None, e1, None, local_title, ext, None

    return None, None, None, base.strip(), ext, None

def fuzzy_match_episode(local_title: str, episodes_in_season: dict) -> tuple[int | None, float]:
    """Compara por similitud difusa el título local con los títulos oficiales de la temporada."""
    if not local_title or not episodes_in_season:
        return None, 0.0

    best_match = None
    best_score = 0.0
    clean_local = local_title.lower()

    for ep_num, title in episodes_in_season.items():
        clean_target = title.lower()
        score = difflib.SequenceMatcher(None, clean_local, clean_target).ratio()
        if score > best_score:
            best_score = score
            best_match = ep_num

    return best_match, best_score

def match_tv_episode(s, e1, e2, local_title, tmdb_show_info, tag=None, file_index=1):
    """
    Calcula la temporada, episodio(s) y título canónico utilizando metadatos de TMDb,
    reglas de acrónimos, offsets acumulados automáticos y fuzzy matching.
    """
    seasons = tmdb_show_info.get('seasons', {})
    season_counts = tmdb_show_info.get('season_counts', {})

    dest_season = s
    dest_e1 = e1
    dest_e2 = e2

    # Regla A: Acrónimo o subserie conocida (ej. PRMM, PRFDT)
    if tag and tag in ACRONYM_RULES:
        rule = ACRONYM_RULES[tag]
        if 'target_season' in rule:
            dest_season = rule['target_season']
            dest_e1 = e1
        elif 'season_ranges' in rule:
            for s_cand, (start, end) in rule['season_ranges'].items():
                if start <= e1 <= end:
                    dest_season = s_cand
                    dest_e1 = e1 - start + 1
                    break

    # Regla B: Resolver números globales acumulados (ej. 4x69 -> Temporada 4, Episodio 1)
    if dest_season and dest_e1 and dest_season in season_counts:
        max_in_season = season_counts[dest_season]
        if dest_e1 > max_in_season:
            cumulative_offset = sum(season_counts.get(prev_s, 0) for prev_s in range(1, dest_season)) + 1
            calculated_e1 = dest_e1 - cumulative_offset + 1
            if 1 <= calculated_e1 <= max_in_season:
                dest_e1 = calculated_e1
                if dest_e2:
                    dest_e2 = dest_e2 - cumulative_offset + 1

    # Regla C: Si no había temporada explícita y no hubo tag
    if dest_season is None and e1 is not None:
        total_episodes_all = sum(season_counts.values())
        if 1 <= e1 <= total_episodes_all:
            cum = 0
            for s_num in sorted(season_counts.keys()):
                cnt = season_counts[s_num]
                if cum < e1 <= cum + cnt:
                    dest_season = s_num
                    dest_e1 = e1 - cum
                    break
                cum += cnt
        else:
            dest_season = 1
            dest_e1 = e1

    if dest_season is None:
        dest_season = 1
    if dest_e1 is None:
        dest_e1 = file_index

    episodes_in_season = seasons.get(dest_season, {})

    # Regla D: Fuzzy matching por título si hay texto local.
    # No se aplica cuando el «título local» es en realidad el nombre de la serie
    # (habitual en archivos generados como «S08E08 - Boku no Hero Academia.strm»):
    # una similitud alta con el nombre de la serie no debe pisar el episodio explícito.
    show_name = (tmdb_show_info.get('name') or '').strip().lower()
    show_similarity = (
        difflib.SequenceMatcher(None, local_title.lower(), show_name).ratio()
        if local_title and show_name else 0.0
    )
    if local_title and show_similarity < 0.7:
        matched_ep, score = fuzzy_match_episode(local_title, episodes_in_season)
        if matched_ep and score > 0.75:
            dest_e1 = matched_ep

    # Obtener título canónico final de TMDb
    dest_title = None
    if dest_season in seasons and dest_e1 in episodes_in_season:
        dest_title = episodes_in_season[dest_e1]
    elif local_title:
        dest_title = sanitize_filename(local_title)
    else:
        dest_title = f"Episodio {dest_e1}"

    # Formatear prefijo (ej. S01E01 o S05E21-E22)
    if dest_e2 and dest_e2 != dest_e1:
        prefix = f"S{dest_season:02d}E{dest_e1:02d}-E{dest_e2:02d}"
    else:
        prefix = f"S{dest_season:02d}E{dest_e1:02d}"

    return dest_season, prefix, dest_title
