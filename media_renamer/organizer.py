# -*- coding: utf-8 -*-
import os
import re
import glob
import shutil
import json
from datetime import datetime

from .config import (
    VIDEO_EXTENSIONS,
    SUBTITLE_EXTENSIONS,
    BOOK_EXTENSIONS,
    COMIC_EXTENSIONS,
    HISTORY_FILE,
    ACRONYM_RULES,
)
from .utils import (
    clean_query_string,
    clean_movie_query,
    clean_book_query,
    parse_book_filename,
    parse_comic_filename,
    natural_sort_key,
    sanitize_filename,
)
from .book_client import BookClient
from .matcher import parse_filename, match_tv_episode, EPISODE_WORDS
from difflib import SequenceMatcher

def _calculate_confidence(
        clean_title: str,
        detected_year: str | None,
        movie_info: dict,
        candidates_count: int = 1,
        auto_selected: bool = False,
        user_selected: bool = False,
    ) -> tuple[str, float]:
        """
        Calcula nivel de confianza para un movimiento.
        Retorna (nivel, score) donde nivel ∈ {'high', 'medium', 'low'}
        """
        score = 0.5  # base
        
        # Year match from filename
        if detected_year and movie_info.get('year') == detected_year:
            score += 0.3
        elif detected_year and movie_info.get('year'):
            # Year mismatch
            score -= 0.2
        
        # Title similarity
        tmdb_title = movie_info.get('title', '').lower()
        if tmdb_title and clean_title:
            similarity = SequenceMatcher(None, clean_title.lower(), tmdb_title).ratio()
            score += similarity * 0.2  # up to 0.2
        
        # Multiple candidates penalty
        if candidates_count > 1:
            score -= 0.1 * min(candidates_count - 1, 3)
        
        # Auto-selected bonus (year matched)
        if auto_selected:
            score += 0.15
        
        # User selected bonus
        if user_selected:
            score += 0.1
        
        # Clamp
        score = max(0.0, min(1.0, score))
        
        if score >= 0.75:
            return 'high', score
        elif score >= 0.45:
            return 'medium', score
        else:
            return 'low', score


# Bloque de numeración que se quita del nombre para quedarse con el prefijo (la serie).
# Cubre los mismos formatos que parse_filename: S01E05 / S01E05-E06, 1x05, la temporada
# delante del episodio (Temporada 1 Episodio 5) y los números sueltos. El orden importa:
# la alternativa más específica va primero, porque gana la coincidencia más a la izquierda.
_NUMBERING_RE = re.compile(
    r'(?:'
    r'[Ss]\d{1,2}[\.\-_ ]*[Ee]\d{1,3}(?:[\.\-_ ]*[Ee]\d{1,3})?'          # S01E05 / S01E05-E06
    r'|\d{1,2}[xX]\d{1,3}(?:-\d{1,3})?'                                   # 1x05
    r'|\b(?:temporada|season|temp|t)\s*[\.\-_ ]*\d{1,2}\s*'
    r'(?:[,;:\-]|\u2013|\u2014)?\s*(?:y\s+|and\s+)?' + EPISODE_WORDS +
    r'[\.\-_ ]*\d{1,3}(?:\s*(?:y|and|,|-)\s*\d{1,3}(?![0-9pP]))?'          # Temporada 1 Episodio 5
    r'|\b' + EPISODE_WORDS + r'[\.\-_ ]*\d{1,3}'
    r'(?:\s*(?:y|and|,|-)\s*\d{1,3}(?![0-9pP]))?'                          # Episodio 5 / Episode 5 y 6
    r'|(?<!\d)\d{1,3}(?!\d)'                                              # número suelto
    r')',
    re.IGNORECASE,
)

# Cola técnica suelta (1080p, x264, WEB-DL…) que no debe quedar como título de episodio
# cuando el nombre no la trae entre corchetes.
_TECHNICAL_TAIL_RE = re.compile(
    r'\b(?:\d{3,4}p|4k|2160p|x264|x265|h\.?26[45]|hevc|av1|aac|ac3|web[ .-]?dl|bluray|'
    r'bdrip|brrip|hdtv|hdrip|dvdrip|10bit|remux|proper|repack)\b',
    re.IGNORECASE,
)


def _same_path(a: str, b: str) -> bool:
    """True si ambas rutas apuntan al mismo archivo.

    Evita emitir "movimientos" que en realidad son un no-op: en Windows mover un
    archivo sobre sí mismo falla, y en la vista previa aparece como una fila inútil.
    """
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _emit(log_fn, message: str):
    (log_fn or print)(message)


class MediaOrganizer:
    def __init__(self, tv_client, movie_client=None, book_client=None):
        self.tv = tv_client
        self.movies = movie_client or tv_client
        self.books = book_client or BookClient()

    def _collect_files(self, path_or_pattern: str | list[str], extensions: set[str]) -> list[dict]:
        """Busca archivos con las extensiones indicadas en rutas o carpetas."""
        paths = []
        if isinstance(path_or_pattern, str):
            expanded = glob.glob(path_or_pattern)
            paths = expanded if expanded else [path_or_pattern]
        else:
            paths = path_or_pattern

        collected = []
        for p in paths:
            if not os.path.exists(p):
                continue
            if os.path.isfile(p):
                folder = os.path.dirname(p)
                f = os.path.basename(p)
                ext = os.path.splitext(f)[1].lower()
                if ext in extensions:
                    collected.append({'folder': folder, 'file': f})
            elif os.path.isdir(p):
                for f in os.listdir(p):
                    full = os.path.join(p, f)
                    if os.path.isfile(full):
                        ext = os.path.splitext(f)[1].lower()
                        if ext in extensions:
                            collected.append({'folder': p, 'file': f})

        collected.sort(key=lambda x: natural_sort_key(x['file']))
        return collected

    def find_files_in_paths(self, path_or_pattern: str | list[str]) -> list[dict]:
        return self._collect_files(path_or_pattern, VIDEO_EXTENSIONS)

    def find_book_files_in_paths(self, path_or_pattern: str | list[str]) -> list[dict]:
        return self._collect_files(path_or_pattern, BOOK_EXTENSIONS | COMIC_EXTENSIONS)

    def find_book_folders(self, root_path: str) -> list[str]:
        """Carpetas que contienen libros o cómics, incluidas las subcarpetas."""
        if os.path.isfile(root_path):
            return [os.path.dirname(root_path)]
        if not os.path.isdir(root_path):
            return []

        extensions = BOOK_EXTENSIONS | COMIC_EXTENSIONS
        folders = []
        for folder, _, files in os.walk(root_path):
            if any(os.path.splitext(name)[1].lower() in extensions for name in files):
                folders.append(folder)
        return sorted(folders, key=natural_sort_key)

    def find_media_folders(self, root_path: str) -> list[str]:
        """Devuelve las carpetas que contienen videos, incluyendo subcarpetas."""
        if os.path.isfile(root_path):
            return [os.path.dirname(root_path)]
        if not os.path.isdir(root_path):
            return []

        folders = []
        for folder, _, files in os.walk(root_path):
            if any(os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS for name in files):
                folders.append(folder)
        return sorted(folders, key=natural_sort_key)

    def find_companion_subtitles(self, folder: str, video_filename: str) -> list[str]:
        base, _ = os.path.splitext(video_filename)
        subs = []
        for f in os.listdir(folder):
            if f == video_filename:
                continue
            f_base, f_ext = os.path.splitext(f)
            if f_ext.lower() in SUBTITLE_EXTENSIONS:
                if f_base.startswith(base):
                    subs.append(f)
        return subs

    def plan_clean_filenames(self, paths: str | list[str], show_title: str = None, log_fn=None):
        """Crea un plan local, sin consultar proveedores de metadatos.

        Conserva los archivos en su carpeta actual y elimina etiquetas técnicas
        habituales. La numeración la resuelve ``parse_filename`` —el mismo que usa el
        organizador con metadatos—, así que reconoce ``S01E02``, ``4x05``, ``Capítulo 5``,
        ``Episodio 5``, ``Episode 5``, ``Chapter 5`` y números sueltos. Si el nombre trae
        temporada se normaliza como ``S01E02``; si no, como ``Capítulo 02``.
        """
        video_items = self.find_files_in_paths(paths)
        if not video_items:
            _emit(log_fn, "[!] No se encontraron archivos de video.")
            return None

        # Nombre de reserva, por si el prefijo de algún archivo queda vacío.
        first_base = os.path.splitext(video_items[0]['file'])[0]
        fallback, _ = clean_query_string(_NUMBERING_RE.sub(" ", first_base, count=1))
        clean_title, _ = clean_query_string(show_title or fallback)
        clean_title = sanitize_filename(clean_title or "Videos")

        moves = []
        used_names = set()
        technical_suffix = re.compile(
            r'\s*[\(\[\{][^\)\]\}]*\b(?:\d{3,4}p|fps|h\.?26[45]|hevc|av1|aac|bit(?:/s)?|web[ .-]?dl)\b[^\)\]\}]*[\)\]\}]\s*$',
            re.IGNORECASE,
        )

        for item in video_items:
            folder, filename = item['folder'], item['file']
            base, ext = os.path.splitext(filename)
            season, e1, e2, local_title, _, tag = parse_filename(filename)
            if tag:
                # Convención de acrónimos (01PRFDT, PRMM100): el organizador la usa para
                # deducir la serie, así que el limpiador local no debe destruirla.
                _emit(log_fn, f"[i] «{filename}» usa un acrónimo conocido; se deja como está.")
                continue

            if e1 is not None:
                # El título del episodio es el texto posterior al número: se quita del
                # prefijo ANTES de limpiar (si no, clean_query_string borra "(1a parte)"
                # y el texto queda pegado al nombre de la serie). Se vuelve a agregar abajo.
                episode_title = _TECHNICAL_TAIL_RE.sub(" ", local_title).strip(" -_.")

                if show_title:
                    # El nombre escrito por el usuario manda sobre lo que diga el archivo.
                    prefix = clean_title
                else:
                    # El prefijo sale de CADA archivo. Antes se calculaba una sola vez con
                    # el primero y el nombre de ese episodio se contagiaba a todos los demás.
                    raw_prefix = _NUMBERING_RE.sub(" ", base, count=1)
                    if local_title:
                        raw_prefix = re.sub(re.escape(local_title) + r'\s*$', ' ', raw_prefix,
                                            flags=re.IGNORECASE)
                    prefix, _ = clean_query_string(raw_prefix)
                    prefix = sanitize_filename(prefix).strip(" ,;") or clean_title

                if season is not None:
                    code = f"S{season:02d}E{e1:02d}" + (f"-E{e2:02d}" if e2 else "")
                    new_base = f"{prefix} {code}"
                else:
                    code = f"Capítulo {e1:02d}" + (f"-{e2:02d}" if e2 else "")
                    new_base = f"{prefix} - {code}"
                if episode_title and not episode_title.startswith("(") \
                        and episode_title.lower() not in prefix.lower():
                    new_base = f"{new_base} - {episode_title}"
                new_base = sanitize_filename(new_base)
            else:
                # Para archivos sin número no inventamos una numeración: solo se
                # quitan las etiquetas de descarga y se conserva el texto restante.
                local_name = technical_suffix.sub("", base)
                local_name = re.sub(r'\s*[-_.]+\s*(?:telefe|canal\s*\d+)\s*$', "", local_name,
                                    flags=re.IGNORECASE)
                new_base = sanitize_filename(local_name) or clean_title

            destination = os.path.join(folder, f"{new_base}{ext}")
            source = os.path.join(folder, filename)
            if _same_path(source, destination):
                continue
            # Nunca dos archivos distintos al mismo destino: shutil.move sobrescribe el
            # destino sin avisar y el archivo pisado se pierde. Mismo criterio que
            # plan_bulk_movies: se agrega el sufijo «(2)», «(3)»…
            if os.path.normcase(destination) in used_names or os.path.exists(destination):
                counter = 2
                while True:
                    candidate = os.path.join(folder, f"{new_base} ({counter}){ext}")
                    if os.path.normcase(candidate) not in used_names and not os.path.exists(candidate):
                        break
                    counter += 1
                _emit(log_fn, f"[i] «{new_base}{ext}» ya estaba tomado; se usará "
                              f"«{os.path.basename(candidate)}».")
                destination = candidate
            used_names.add(os.path.normcase(destination))
            moves.append({
                'src': source,
                'dest': destination,
                'rel_dest': os.path.basename(destination),
            })

        first_folder = video_items[0]['folder']
        return {
            'type': 'clean',
            'title': f"{clean_title} (limpieza local)",
            'source_folders': list({item['folder'] for item in video_items}),
            'dest_main_folder': first_folder,
            'moves': moves,
        }

    def plan_tv_show(self, paths: str | list[str], show_query: str = None, lang: str = "es-MX", target_base_dir: str = None, log_fn=None, pick_result_fn=None, forced_season: int | None = None):
        video_items = self.find_files_in_paths(paths)
        if not video_items:
            _emit(log_fn, "[!] No se encontraron archivos de video.")
            return None

        # Autodetección de nombre de serie a partir de acrónimos en los archivos
        detected_query = None
        for item in video_items[:10]:
            _, _, _, _, _, tag = parse_filename(item['file'])
            if tag and tag in ACRONYM_RULES:
                detected_query = ACRONYM_RULES[tag].get('parent_query')
                break

        final_query = show_query or detected_query
        if not final_query:
            first_folder = video_items[0]['folder']
            final_query = os.path.basename(os.path.abspath(first_folder))

        show_info = self.tv.search_tv(final_query, lang=lang, pick_result_fn=pick_result_fn)
        if not show_info and detected_query and detected_query != final_query:
            # Reintentar con el acrónimo detectado si falló el nombre de la carpeta
            show_info = self.tv.search_tv(detected_query, lang=lang, pick_result_fn=pick_result_fn)

        if not show_info:
            provider = getattr(self.tv, "provider_name", "el proveedor configurado")
            _emit(log_fn, f"[!] No se pudo encontrar la serie '{final_query}' en {provider}.")
            return None

        tv_id = show_info['id']
        if forced_season is not None:
            forced_season = int(forced_season)
            if forced_season < 1 or forced_season not in show_info.get('season_counts', {}):
                _emit(log_fn, f"[!] La temporada {forced_season} no está disponible para esta serie.")
                return None
        show_name = sanitize_filename(show_info['name'])
        year_str = f" ({show_info['year']})" if show_info.get('year') else ""
        canonical_folder_name = f"{show_name}{year_str}"

        if target_base_dir is None:
            first_folder = video_items[0]['folder']
            target_base_dir = os.path.dirname(os.path.abspath(first_folder))

        dest_main_folder = os.path.join(target_base_dir, canonical_folder_name)
        source_folders = list({item['folder'] for item in video_items})

        # Cache local de temporadas requeridas (lazy loading)
        loaded_seasons = {}

        moves = []
        for idx, item in enumerate(video_items, 1):
            folder = item['folder']
            vf = item['file']
            s, e1, e2, local_title, ext, tag = parse_filename(vf)

            # Predecir qué temporada se necesitará para cargar sus episodios
            temp_season = forced_season if forced_season is not None else s
            if forced_season is None and tag and tag in ACRONYM_RULES:
                rule = ACRONYM_RULES[tag]
                if 'target_season' in rule:
                    temp_season = rule['target_season']
                elif 'season_ranges' in rule:
                    for s_cand, (start, end) in rule['season_ranges'].items():
                        if start <= e1 <= end:
                            temp_season = s_cand
                            break
            if temp_season is None:
                temp_season = 1

            if temp_season not in loaded_seasons:
                loaded_seasons[temp_season] = self.tv.get_season_episodes(tv_id, temp_season, lang=lang)
                show_info['seasons'][temp_season] = loaded_seasons[temp_season]

            if forced_season is not None:
                dest_season = forced_season
                dest_e1 = e1 if e1 is not None else idx
                dest_e2 = e2
                if dest_e2 and dest_e2 != dest_e1:
                    ep_prefix = f"S{dest_season:02d}E{dest_e1:02d}-E{dest_e2:02d}"
                else:
                    ep_prefix = f"S{dest_season:02d}E{dest_e1:02d}"
                dest_title = loaded_seasons[dest_season].get(dest_e1)
                if not dest_title:
                    dest_title = sanitize_filename(local_title) if local_title else f"Episodio {dest_e1}"
            else:
                dest_season, ep_prefix, dest_title = match_tv_episode(
                    s, e1, e2, local_title, show_info, tag=tag, file_index=idx
                )

            # Si la temporada calculada final aún no estaba cargada
            if dest_season not in loaded_seasons:
                loaded_seasons[dest_season] = self.tv.get_season_episodes(tv_id, dest_season, lang=lang)
                show_info['seasons'][dest_season] = loaded_seasons[dest_season]
                # Re-obtener título oficial
                ep_map = loaded_seasons[dest_season]
                dest_e1 = int(re.search(r'E(\d+)', ep_prefix).group(1))
                if dest_e1 in ep_map:
                    dest_title = ep_map[dest_e1]

            season_folder = f"Season {dest_season:02d}"
            new_video_name = f"{ep_prefix} - {dest_title}{ext}"

            src_video_path = os.path.join(folder, vf)
            dest_video_path = os.path.join(dest_main_folder, season_folder, new_video_name)

            # Si el archivo ya está en su lugar final no hay nada que mover.
            if not _same_path(src_video_path, dest_video_path):
                moves.append({
                    'src': src_video_path,
                    'dest': dest_video_path,
                    'rel_dest': os.path.join(season_folder, new_video_name)
                })

            # Subtítulos asociados
            subs = self.find_companion_subtitles(folder, vf)
            for sub in subs:
                sub_ext = os.path.splitext(sub)[1]
                new_sub_name = f"{ep_prefix} - {dest_title}{sub_ext}"
                src_sub_path = os.path.join(folder, sub)
                dest_sub_path = os.path.join(dest_main_folder, season_folder, new_sub_name)
                if _same_path(src_sub_path, dest_sub_path):
                    continue
                moves.append({
                    'src': src_sub_path,
                    'dest': dest_sub_path,
                    'rel_dest': os.path.join(season_folder, new_sub_name)
                })

        return {
            'type': 'tv',
            'title': canonical_folder_name,
            'source_folders': source_folders,
            'dest_main_folder': dest_main_folder,
            'moves': moves
        }

    def plan_movie(self, path: str, movie_query: str = None, lang: str = "es-MX", target_base_dir: str = None, log_fn=None, pick_result_fn=None):
        video_items = self.find_files_in_paths(path)
        if not video_items:
            _emit(log_fn, "[!] No se encontraron archivos de video.")
            return None

        if not movie_query:
            first_folder = video_items[0]['folder']
            movie_query = os.path.basename(os.path.abspath(first_folder))

        movie_info = self.movies.search_movie(movie_query, lang=lang, pick_result_fn=pick_result_fn)
        if not movie_info:
            _emit(log_fn, f"[!] No se encontró la película '{movie_query}' en TMDb.")
            return None

        # Confidence for single movie mode
        clean_title, detected_year = clean_movie_query(movie_query)
        auto_selected = detected_year is not None and movie_info.get('year') == detected_year
        user_selected = pick_result_fn is not None and not auto_selected
        confidence_level, confidence_score = _calculate_confidence(
            clean_title, detected_year, movie_info,
            candidates_count=1,
            auto_selected=auto_selected,
            user_selected=user_selected,
        )

        title = sanitize_filename(movie_info['title'])
        year_str = f" ({movie_info['year']})" if movie_info.get('year') else ""
        canonical_name = f"{title}{year_str}"
        candidates = movie_info.get('candidates', [])

        main_item = max(video_items, key=lambda it: os.path.getsize(os.path.join(it['folder'], it['file'])))
        folder = main_item['folder']
        vf = main_item['file']
        ext = os.path.splitext(vf)[1]

        if target_base_dir is None:
            target_base_dir = os.path.dirname(os.path.abspath(folder))

        dest_folder = os.path.join(target_base_dir, canonical_name)
        new_file_name = f"{canonical_name}{ext}"

        moves = [{
            'src': os.path.join(folder, vf),
            'dest': os.path.join(dest_folder, new_file_name),
            'rel_dest': new_file_name,
            'confidence': confidence_level,
            'confidence_score': confidence_score,
        }]
        if candidates:
            moves[0]['candidates'] = candidates

        subs = self.find_companion_subtitles(folder, vf)
        for sub in subs:
            sub_ext = os.path.splitext(sub)[1]
            new_sub_name = f"{canonical_name}{sub_ext}"
            sub_move = {
                'src': os.path.join(folder, sub),
                'dest': os.path.join(dest_folder, new_sub_name),
                'rel_dest': new_sub_name,
                'confidence': confidence_level,
                'confidence_score': confidence_score,
            }
            if candidates:
                sub_move['candidates'] = candidates
            moves.append(sub_move)

        return {
            'type': 'movie',
            'title': canonical_name,
            'source_folders': [folder],
            'dest_main_folder': dest_folder,
            'moves': moves
        }

    def plan_bulk_movies(
        self,
        paths: str | list[str],
        target_base_dir: str | None = None,
        lang: str = "es-MX",
        log_fn=None,
        pick_result_fn=None,
    ) -> dict | None:
        """
        Plan renaming for multiple movie files in a single directory.
        No per-movie subfolders — all files go directly to target_base_dir.
        """
        video_items = self.find_files_in_paths(paths)
        if not video_items:
            _emit(log_fn, "[!] No se encontraron archivos de video.")
            return None

        # Determine target directory
        if target_base_dir is None:
            # Default to the folder containing the first video file (not its parent)
            target_base_dir = os.path.abspath(video_items[0]['folder'])
        else:
            target_base_dir = os.path.abspath(target_base_dir)

        moves = []
        used_canonical_names = set()

        for item in video_items:
            folder, filename = item['folder'], item['file']
            base, ext = os.path.splitext(filename)

            # Clean filename for search
            clean_title, detected_year = clean_movie_query(base)
            if not clean_title:
                _emit(log_fn, f"[!] No se pudo extraer título de '{filename}', omitiendo.")
                continue

            # Search TMDb with auto-select by year
            movie_info = self.movies.search_movie(
                clean_title, lang=lang,
                pick_result_fn=pick_result_fn,
                auto_select_year=detected_year
            )
            if not movie_info:
                _emit(log_fn, f"[!] Sin coincidencia para '{clean_title}' (de '{filename}'), omitiendo.")
                continue

            # Determine confidence factors
            candidates = movie_info.get('candidates', [])
            candidates_count = len(candidates) if candidates else 1
            auto_selected = detected_year is not None and movie_info.get('year') == detected_year
            user_selected = pick_result_fn is not None and not auto_selected

            # Calculate confidence
            confidence_level, confidence_score = _calculate_confidence(
                clean_title, detected_year, movie_info,
                candidates_count=candidates_count,
                auto_selected=auto_selected,
                user_selected=user_selected,
            )

            # Build canonical name
            title = sanitize_filename(movie_info['title'])
            year_str = f" ({movie_info['year']})" if movie_info.get('year') else ""
            canonical = f"{title}{year_str}{ext}"

            src = os.path.join(folder, filename)
            dest = os.path.join(target_base_dir, canonical)

            # Handle collisions: check against already-planned moves in this batch
            if canonical in used_canonical_names:
                base_dest, ext_dest = os.path.splitext(canonical)
                counter = 1
                while f"{base_dest} ({counter}){ext_dest}" in used_canonical_names:
                    counter += 1
                canonical = f"{base_dest} ({counter}){ext_dest}"
                dest = os.path.join(target_base_dir, canonical)
            elif os.path.exists(dest) and not os.path.samefile(src, dest):
                # Also check for existing files on disk
                base_dest, ext_dest = os.path.splitext(canonical)
                counter = 1
                while os.path.exists(os.path.join(target_base_dir, f"{base_dest} ({counter}){ext_dest}")):
                    counter += 1
                canonical = f"{base_dest} ({counter}){ext_dest}"
                dest = os.path.join(target_base_dir, canonical)

            used_canonical_names.add(canonical)

            move = {
                'src': src,
                'dest': dest,
                'rel_dest': canonical,
                'confidence': confidence_level,
                'confidence_score': confidence_score,
            }
            if candidates:
                move['candidates'] = candidates
            moves.append(move)

            # Subtítulos asociados
            subs = self.find_companion_subtitles(folder, filename)
            for sub in subs:
                # Preserve full extension including language codes (e.g., .spa.srt, .eng.srt)
                # The subtitle base starts with the video base; everything after is the "extension"
                video_base, _ = os.path.splitext(filename)
                if sub.startswith(video_base):
                    sub_full_ext = sub[len(video_base):]
                else:
                    # Fallback: use the part after the last known subtitle extension
                    sub_full_ext = ""
                    for known_ext in SUBTITLE_EXTENSIONS:
                        if sub.lower().endswith(known_ext.lower()):
                            idx = sub.lower().rfind(known_ext.lower())
                            sub_full_ext = sub[idx:]
                            break
                    if not sub_full_ext:
                        sub_base, sub_full_ext = os.path.splitext(sub)
                
                sub_canonical = f"{title}{year_str}{sub_full_ext}"
                # Handle subtitle collisions too
                if sub_canonical in used_canonical_names:
                    base_sub = sub_canonical[:-len(sub_full_ext)] if sub_full_ext and sub_canonical.endswith(sub_full_ext) else sub_canonical
                    ext_sub = sub_full_ext
                    counter = 1
                    while f"{base_sub} ({counter}){ext_sub}" in used_canonical_names:
                        counter += 1
                    sub_canonical = f"{base_sub} ({counter}){ext_sub}"
                used_canonical_names.add(sub_canonical)
                sub_move = {
                    'src': os.path.join(folder, sub),
                    'dest': os.path.join(target_base_dir, sub_canonical),
                    'rel_dest': sub_canonical,
                    'confidence': confidence_level,
                    'confidence_score': confidence_score,
                }
                if candidates:
                    sub_move['candidates'] = candidates
                moves.append(sub_move)

        if not moves:
            _emit(log_fn, "[!] No se pudo procesar ningún archivo.")
            return None

        return {
            'type': 'bulk_movie',
            'title': f"Renombrado masivo de películas ({len(moves)} archivos)",
            'source_folders': list({item['folder'] for item in video_items}),
            'dest_main_folder': target_base_dir,
            'moves': moves,
        }

    # ==================== Libros y cómics ====================

    @staticmethod
    def _fold_folder_name(name: str) -> str:
        """Normaliza un nombre de carpeta para comparar Books/Libros/Comics."""
        return (name or '').strip().lower().replace('ó', 'o').replace('í', 'i')

    def _book_root(self, base_dir: str, kind: str) -> str:
        """Devuelve la carpeta Books/Comics sin duplicarla si ya es la raíz."""
        base_dir = os.path.abspath(base_dir)
        current = self._fold_folder_name(os.path.basename(base_dir))
        if kind == 'comics':
            if current in {'comics', 'comic', 'cómics', 'tebeos', 'historietas'}:
                return base_dir
            return os.path.join(base_dir, 'Comics')
        if current in {'books', 'book', 'libros', 'libro', 'ebooks', 'ebook', 'biblioteca'}:
            return base_dir
        return os.path.join(base_dir, 'Books')

    def _book_destination(
        self,
        base_dir: str,
        *,
        title: str,
        author: str | None,
        year: str | int | None,
        ext: str,
        is_comic: bool = False,
        series: str | None = None,
        issue: int | None = None,
    ) -> tuple[str, str]:
        """Construye la carpeta y el nombre de archivo con la estructura de Jellyfin.

        Libros: ``Books/Autor/Título (Año)/Título.ext``.
        Cómics: ``Comics/Serie (Año)/Serie #001 (Año).ext``.
        """
        year = str(year).strip() if year not in (None, '', '?') else None
        title = sanitize_filename(title) or 'Libro'
        ext = ext or '.epub'
        if not ext.startswith('.'):
            ext = '.' + ext

        if is_comic:
            series = sanitize_filename(series or title) or 'Comic'
            folder_name = f"{series} ({year})" if year else series
            folder = os.path.join(self._book_root(base_dir, 'comics'), folder_name)
            if issue is not None:
                file_name = f"{series} #{issue:03d}"
            else:
                file_name = series
            if year:
                file_name += f" ({year})"
            file_name += ext
            return folder, file_name

        author = sanitize_filename(author) if author else ''
        folder_name = f"{title} ({year})" if year else title
        root = self._book_root(base_dir, 'books')
        folder = os.path.join(root, author, folder_name) if author else os.path.join(root, folder_name)
        # El nombre del archivo coincide con el título de la carpeta; Jellyfin usa
        # principalmente la carpeta para identificar el libro.
        return folder, f"{title}{ext}"

    @staticmethod
    def _unique_destination(dest: str, used: set[str]) -> str:
        """Evita pisar archivos ya planificados o existentes en el disco."""
        def key(path: str) -> str:
            return os.path.normcase(os.path.abspath(path))

        base, ext = os.path.splitext(dest)
        candidate = dest
        counter = 1
        while key(candidate) in used or os.path.exists(candidate):
            candidate = f"{base} ({counter}){ext}"
            counter += 1
        used.add(key(candidate))
        return candidate

    def plan_books(
        self,
        paths: str | list[str],
        query: str | None = None,
        lang: str = "es-MX",
        target_base_dir: str | None = None,
        log_fn=None,
        pick_result_fn=None,
        use_metadata: bool = True,
    ) -> dict | None:
        """Planifica libros y cómics con la estructura recomendada por Jellyfin.

        - EPUB/PDF/MOBI/AZW3: ``Books/Autor/Título (Año)/Título.ext``.
        - CBZ/CBR: ``Comics/Serie (Año)/Serie #001 (Año).ext``.
        - Si no hay metadatos, conserva el nombre limpio y marca confianza baja.
        """
        book_items = self.find_book_files_in_paths(paths)
        if not book_items:
            _emit(log_fn, "[!] No se encontraron libros ni cómics.")
            return None

        if target_base_dir:
            base_dir = os.path.abspath(target_base_dir)
        elif isinstance(paths, str) and os.path.isdir(paths):
            base_dir = os.path.abspath(paths)
        elif isinstance(paths, (list, tuple)) and len(paths) == 1 and os.path.isdir(paths[0]):
            base_dir = os.path.abspath(paths[0])
        else:
            base_dir = os.path.abspath(book_items[0]['folder'])

        moves = []
        used_dests: set[str] = set()
        single_item = len(book_items) == 1

        for item in book_items:
            folder, filename = item['folder'], item['file']
            base_name, ext = os.path.splitext(filename)
            clean_title, detected_year = clean_book_query(base_name)
            is_comic = ext.lower() in COMIC_EXTENSIONS
            info = None
            candidates = []

            if is_comic:
                series, issue, comic_year = parse_comic_filename(base_name)
                title = series or clean_title or base_name
                author = None
                year = comic_year or detected_year
                confidence = 'high' if issue is not None else 'medium'
                confidence_score = 0.9 if issue is not None else 0.65
            else:
                search_query = query if (query and single_item) else (clean_title or base_name)
                if use_metadata:
                    info = self.books.search_book(
                        search_query,
                        lang=lang,
                        auto_select_year=detected_year,
                        pick_result_fn=pick_result_fn if single_item else None,
                    )
                if info:
                    title = sanitize_filename(info.get('title') or clean_title or base_name)
                    author = info.get('author') or None
                    year = info.get('year') or detected_year
                    candidates = info.get('candidates') or []
                    if detected_year and year and str(year) != str(detected_year):
                        confidence, confidence_score = 'medium', 0.7
                    else:
                        confidence, confidence_score = 'high', 0.95
                    _emit(
                        log_fn,
                        f"[i] {filename} → {title}"
                        + (f" — {author}" if author else "")
                        + (f" ({year})" if year else ""),
                    )
                else:
                    title = sanitize_filename(clean_title or base_name)
                    author = None
                    year = detected_year
                    confidence, confidence_score = 'low', 0.35
                    if use_metadata:
                        _emit(log_fn, f"[!] Sin coincidencia para '{clean_title or base_name}'; se usa el nombre local.")
                    # Si el nombre trae "(Autor)" entre paréntesis, aprovecharlo para
                    # no dejar el autor dentro del título.
                    fallback_title, fallback_author, fallback_year = parse_book_filename(base_name)
                    if fallback_title:
                        title = sanitize_filename(fallback_title)
                    author = fallback_author or author
                    year = year or fallback_year
                series = None
                issue = None

            if not title:
                _emit(log_fn, f"[!] No se pudo extraer un título de '{filename}', omitiendo.")
                continue

            dest_folder, dest_name = self._book_destination(
                base_dir,
                title=title,
                author=author,
                year=year,
                ext=ext,
                is_comic=is_comic,
                series=series,
                issue=issue,
            )
            src = os.path.join(folder, filename)
            dest = os.path.join(dest_folder, dest_name)
            if _same_path(src, dest):
                _emit(log_fn, f"[i] '{filename}' ya está en la ubicación correcta.")
                continue

            dest = self._unique_destination(dest, used_dests)
            rel_dest = os.path.relpath(dest, base_dir)
            move = {
                'src': src,
                'dest': dest,
                'rel_dest': rel_dest,
                'confidence': confidence,
                'confidence_score': confidence_score,
                'book': {
                    'base_dir': base_dir,
                    'title': title,
                    'author': author,
                    'year': year,
                    'is_comic': is_comic,
                    'series': series,
                    'issue': issue,
                    'ext': ext,
                },
            }
            if candidates:
                move['candidates'] = candidates
            moves.append(move)

        if not moves:
            _emit(log_fn, "[!] No se pudo procesar ningún libro.")
            return None

        hay_comics = any(move.get('book', {}).get('is_comic') for move in moves)
        return {
            'type': 'book',
            'title': f"Libros y cómics ({len(moves)} archivos)" if hay_comics else f"Libros ({len(moves)} archivos)",
            'source_folders': sorted({item['folder'] for item in book_items}),
            'dest_main_folder': base_dir,
            'moves': moves,
        }

    def rebuild_book_move(self, move: dict, chosen: dict) -> dict:
        """Recalcula el destino de un libro al elegir otro candidato en la GUI."""
        book = dict(move.get('book') or {})
        if not book:
            return move

        title = sanitize_filename(chosen.get('name') or book.get('title') or '')
        if not title:
            return move
        author = chosen.get('author') if chosen.get('author') is not None else book.get('author')
        year = chosen.get('year') if chosen.get('year') not in (None, '', '?') else book.get('year')
        ext = book.get('ext') or os.path.splitext(move['src'])[1]
        is_comic = bool(book.get('is_comic'))

        folder, filename = self._book_destination(
            book['base_dir'],
            title=title,
            author=author,
            year=year,
            ext=ext,
            is_comic=is_comic,
            series=book.get('series') or title,
            issue=book.get('issue'),
        )
        dest = os.path.join(folder, filename)
        move['dest'] = dest
        try:
            move['rel_dest'] = os.path.relpath(dest, book['base_dir'])
        except ValueError:
            move['rel_dest'] = filename
        move['confidence'] = 'high'
        move['confidence_score'] = 0.95
        move['title'] = title
        move['year'] = year
        book.update({
            'title': title,
            'author': author,
            'year': year,
        })
        move['book'] = book
        return move

    def execute_plan(self, plan: dict, dry_run: bool = False, auto_yes: bool = False, log_fn=None, confirm_fn=None) -> bool:
        _emit(log_fn, "")
        _emit(log_fn, "=" * 65)
        _emit(log_fn, f"ORGANIZANDO: {plan['title']}")
        _emit(log_fn, f"Destino: {plan['dest_main_folder']}")
        _emit(log_fn, "=" * 65)
        if len(plan['moves']) > 15:
            for m in plan['moves'][:6]:
                _emit(log_fn, f"  {os.path.basename(m['src'])}")
                _emit(log_fn, f"    -> {m['rel_dest']}")
            _emit(log_fn, f"  ... y {len(plan['moves']) - 10} archivos más ...")
            for m in plan['moves'][-4:]:
                _emit(log_fn, f"  {os.path.basename(m['src'])}")
                _emit(log_fn, f"    -> {m['rel_dest']}")
        else:
            for m in plan['moves']:
                _emit(log_fn, f"  {os.path.basename(m['src'])}")
                _emit(log_fn, f"    -> {m['rel_dest']}")
        _emit(log_fn, "-" * 65)
        _emit(log_fn, f"Total: {len(plan['moves'])} archivo(s) a mover")

        if dry_run:
            _emit(log_fn, "[MODO SIMULACIÓN - DRY RUN] No se ha modificado ningún archivo.")
            return True

        if not auto_yes:
            if confirm_fn is None:
                answer = input("\n¿Confirmar y aplicar estos cambios? (s/N): ").strip().lower()
                confirmed = answer in ['s', 'si', 'y', 'yes']
            else:
                confirmed = bool(confirm_fn())
            if not confirmed:
                _emit(log_fn, "Operación cancelada.")
                return False

        return self.apply_moves(plan, log_fn=log_fn)

    def apply_moves(self, plan: dict, log_fn=None) -> bool:
        history_entry = {
            'timestamp': datetime.now().isoformat(),
            'title': plan['title'],
            'dest_folder': plan['dest_main_folder'],
            'moves': [{'src': m['src'], 'dest': m['dest']} for m in plan['moves']],
            'source_folders': plan['source_folders']
        }
        self._record_history(history_entry, log_fn=log_fn)

        os.makedirs(plan['dest_main_folder'], exist_ok=True)
        for m in plan['moves']:
            os.makedirs(os.path.dirname(m['dest']), exist_ok=True)
            shutil.move(m['src'], m['dest'])

        for s_folder in plan['source_folders']:
            if os.path.exists(s_folder) and os.path.abspath(s_folder) != os.path.abspath(plan['dest_main_folder']):
                try:
                    if not os.listdir(s_folder):
                        os.rmdir(s_folder)
                        _emit(log_fn, f"[OK] Carpeta vacía eliminada: {os.path.basename(s_folder)}")
                except Exception:
                    pass

        _emit(log_fn, "[OK] Organización completada exitosamente.")
        return True

    def _record_history(self, entry: dict, log_fn=None):
        history = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            except Exception:
                history = []
        history.append(entry)
        try:
            with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _emit(log_fn, f"[!] No se pudo registrar historial: {e}")

    def undo_last_operation(self, log_fn=None) -> bool:
        if not os.path.exists(HISTORY_FILE):
            _emit(log_fn, "[!] No hay historial de operaciones registrado para deshacer.")
            return False

        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except Exception as e:
            _emit(log_fn, f"[!] Error leyendo historial: {e}")
            return False

        if not history:
            _emit(log_fn, "[!] El historial está vacío.")
            return False

        last_op = history.pop()
        _emit(log_fn, f"[DESHACER] Revertiendo operación: {last_op['title']} ({last_op['timestamp']})")

        reverted_count = 0
        for m in reversed(last_op['moves']):
            current_dest = m['dest']
            original_src = m['src']
            if os.path.exists(current_dest):
                os.makedirs(os.path.dirname(original_src), exist_ok=True)
                shutil.move(current_dest, original_src)
                reverted_count += 1

        dest_main = last_op.get('dest_folder')
        if dest_main and os.path.exists(dest_main):
            for root, dirs, files in os.walk(dest_main, topdown=False):
                if not os.listdir(root):
                    os.rmdir(root)

        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

        _emit(log_fn, f"[OK] Se restauraron {reverted_count} archivos a su ubicación original.")
        return True
