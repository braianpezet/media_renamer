# -*- coding: utf-8 -*-
import os
import json
import urllib.parse
import urllib.request
import re

from .config import TMDB_API_KEY, TMDB_BEARER, CACHE_FILE, USER_AGENT, DEFAULT_LANGUAGE
from .utils import clean_query_string, sanitize_filename

class TMDBClient:
    provider_name = "TMDb"
    def __init__(self):
        self.cache = self._load_cache()

    @staticmethod
    def _normalise_tv_info(info: dict) -> dict:
        """Restaura claves numéricas que JSON guarda como texto."""
        value = dict(info)
        for key in ('season_counts', 'season_names', 'seasons'):
            if isinstance(value.get(key), dict):
                normalised = {}
                for season, item in value[key].items():
                    try:
                        season = int(season)
                    except (TypeError, ValueError):
                        pass
                    normalised[season] = item
                value[key] = normalised
        return value

    def _load_cache(self) -> dict:
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_cache(self):
        try:
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[!] No se pudo guardar la caché de TMDb: {e}")

    def _fetch(self, url: str) -> dict:
        headers = {
            'User-Agent': USER_AGENT,
            'Authorization': f'Bearer {TMDB_BEARER}',
            'Accept': 'application/json'
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def _build_tv_info(self, show: dict, year_fallback: str | None, lang: str) -> dict | None:
        """Construye el dict de info de una serie a partir de un resultado de búsqueda."""
        tv_id = show['id']
        name = show.get('name') or show.get('original_name')
        first_air = show.get('first_air_date')
        show_year = first_air[:4] if first_air else year_fallback

        detail_url = f"https://api.themoviedb.org/3/tv/{tv_id}?language={lang}&api_key={TMDB_API_KEY}"
        detail = self._fetch(detail_url)
        seasons_info = detail.get('seasons', [])

        season_counts = {}
        for s in seasons_info:
            s_num = s.get('season_number')
            if s_num != 0:
                season_counts[s_num] = s.get('episode_count', 0)

        season_names = {
            s.get('season_number'): s.get('name', '').strip()
            for s in seasons_info
            if s.get('season_number') not in (None, 0) and s.get('name', '').strip()
        }

        return {
            'id': tv_id,
            'name': name,
            'year': show_year,
            'seasons': {},
            'season_counts': season_counts,
            'season_names': season_names
        }

    def search_tv(self, query: str, lang: str = DEFAULT_LANGUAGE,
                  pick_result_fn=None) -> dict | None:
        """
        Busca una serie en TMDb.
        Si hay múltiples resultados y `pick_result_fn` no es None, se llama con la lista
        de candidatos (cada uno con 'id', 'name', 'year') y debe retornar el elegido.
        Si `pick_result_fn` es None, se usa el primer resultado automáticamente.
        """
        clean_q, year = clean_query_string(query)
        cache_key = f"search_tv:v2:{clean_q.lower()}:{lang}"
        if cache_key in self.cache:
            return self._normalise_tv_info(self.cache[cache_key])

        enc = urllib.parse.quote(clean_q)
        url = f"https://api.themoviedb.org/3/search/tv?query={enc}&language={lang}&api_key={TMDB_API_KEY}"
        try:
            data = self._fetch(url)
            results = data.get('results', [])
            if not results and lang != "en-US":
                url_en = f"https://api.themoviedb.org/3/search/tv?query={enc}&language=en-US&api_key={TMDB_API_KEY}"
                data = self._fetch(url_en)
                results = data.get('results', [])

            if not results:
                return None

            # Construir lista de candidatos resumidos para la selección
            # Increase limit to 15 to show more results (e.g., Dragon Ball, Dragon Ball Z, DBZ Kai, movies, etc.)
            candidates = [
                {
                    'id': r['id'],
                    'name': r.get('name') or r.get('original_name', '?'),
                    'year': (r.get('first_air_date') or '')[:4] or year or '?',
                    '_raw': r
                }
                for r in results[:15]  # Máximo 15 candidatos (era 8)
            ]

            if pick_result_fn and len(candidates) > 1:
                chosen = pick_result_fn(candidates)
                if chosen is None:
                    return None
                raw_show = chosen['_raw']
            else:
                raw_show = results[0]

            res = self._build_tv_info(raw_show, year, lang)
            if res:
                self.cache[cache_key] = res
                self._save_cache()
            return res
        except Exception as e:
            print(f"[!] Error buscando serie en TMDb '{clean_q}': {e}")
            return None

    def get_season_episodes(self, tv_id: int, season_num: int, lang: str = DEFAULT_LANGUAGE) -> dict:
        """Carga bajo demanda los episodios de una única temporada."""
        cache_key = f"season_eps:{tv_id}:{season_num}:{lang}"
        if cache_key in self.cache:
            # JSON guarda las claves como str; convertir a int para que los lookups funcionen
            return {int(k): v for k, v in self.cache[cache_key].items()}

        s_url = f"https://api.themoviedb.org/3/tv/{tv_id}/season/{season_num}?language={lang}&api_key={TMDB_API_KEY}"
        s_url_en = f"https://api.themoviedb.org/3/tv/{tv_id}/season/{season_num}?language=en-US&api_key={TMDB_API_KEY}"
        try:
            s_data = self._fetch(s_url)
            s_data_en = self._fetch(s_url_en)
            en_ep_map = {ep['episode_number']: ep.get('name') for ep in s_data_en.get('episodes', [])}

            ep_dict = {}
            for ep in s_data.get('episodes', []):
                ep_num = ep.get('episode_number')
                ep_name = ep.get('name', '').strip()
                if not ep_name or re.match(r'^(?:episodio|episode)\s*\d+$', ep_name, re.IGNORECASE):
                    ep_name = en_ep_map.get(ep_num) or f"Episodio {ep_num}"
                ep_dict[ep_num] = sanitize_filename(ep_name)  # claves int

            self.cache[cache_key] = ep_dict  # JSON lo serializa con claves str (ok para guardado)
            self._save_cache()
            return ep_dict  # retorna con claves int
        except Exception as ex:
            print(f"[!] Error cargando temporada {season_num}: {ex}")
            return {}

    def search_movie(self, query: str, lang: str = DEFAULT_LANGUAGE,
                     pick_result_fn=None, auto_select_year: str | None = None) -> dict | None:
        """
        Busca una película en TMDb.
        Si hay múltiples resultados y `pick_result_fn` no es None, se llama con la lista
        de candidatos y debe retornar el elegido.
        
        Si `auto_select_year` se proporciona, intenta coincidir automáticamente por año.
        
        Retorna dict con 'title', 'year' y opcionalmente 'candidates' (lista de alternativas).
        """
        clean_q, year = clean_query_string(query)
        cache_key = f"movie:{clean_q.lower()}:{lang}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        enc = urllib.parse.quote(clean_q)
        url = f"https://api.themoviedb.org/3/search/movie?query={enc}&language={lang}&api_key={TMDB_API_KEY}"
        try:
            data = self._fetch(url)
            results = data.get('results', [])
            if not results and lang != "en-US":
                url_en = f"https://api.themoviedb.org/3/search/movie?query={enc}&language=en-US&api_key={TMDB_API_KEY}"
                data = self._fetch(url_en)
                results = data.get('results', [])

            if not results:
                return None

            candidates = [
                {
                    'id': r['id'],
                    'name': r.get('title') or r.get('original_title', '?'),
                    'year': (r.get('release_date') or '')[:4] or year or '?',
                    '_raw': r
                }
                for r in results[:15]
            ]

            # Smart auto-select: if we have a year from filename, try to match
            if auto_select_year and len(candidates) > 1:
                year_matches = [c for c in candidates if c['year'] == auto_select_year]
                if len(year_matches) == 1:
                    movie = year_matches[0]['_raw']
                elif len(year_matches) > 1:
                    from difflib import SequenceMatcher
                    best_match = max(year_matches, 
                        key=lambda c: SequenceMatcher(None, clean_q.lower(), c['name'].lower()).ratio())
                    if SequenceMatcher(None, clean_q.lower(), best_match['name'].lower()).ratio() > 0.85:
                        movie = best_match['_raw']
                    else:
                        movie = results[0]
                else:
                    movie = results[0]
            elif pick_result_fn and len(candidates) > 1:
                chosen = pick_result_fn(candidates)
                if chosen is None:
                    return None
                movie = chosen['_raw']
            else:
                movie = results[0]

            title = movie.get('title') or movie.get('original_title')
            rel_date = movie.get('release_date')
            movie_year = rel_date[:4] if rel_date else year
            res = {
                'title': sanitize_filename(title),
                'year': movie_year
            }
            # Include candidates if there were multiple matches (for re-selection)
            if len(candidates) > 1:
                res['candidates'] = candidates
            self.cache[cache_key] = res
            self._save_cache()
            return res
        except Exception as e:
            print(f"[!] Error buscando pelicula en TMDb '{clean_q}': {e}")
            return None
