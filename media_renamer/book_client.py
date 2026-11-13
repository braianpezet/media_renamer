# -*- coding: utf-8 -*-
"""Búsqueda de metadatos de libros y cómics.

No requiere claves: usa la API pública de Open Library y, como respaldo, Google
Books. El resultado se normaliza para que el organizador no dependa del formato de
cada proveedor.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

from .config import BOOK_CACHE_FILE, DEFAULT_LANGUAGE, GOOGLE_BOOKS_API_KEY, USER_AGENT
from .utils import clean_book_query, parse_book_filename


# Open Library usa códigos MARC de tres letras; Google Books usa ISO 639-1.
_OPENLIBRARY_LANGS = {
    'es': 'spa',
    'en': 'eng',
    'pt': 'por',
    'fr': 'fre',
    'de': 'ger',
    'it': 'ita',
    'ca': 'cat',
    'gl': 'glg',
    'eu': 'baq',
}


class BookClient:
    provider_name = "Open Library + Google Books"

    def __init__(self, cache_file: str | None = None):
        self.cache_file = str(cache_file or BOOK_CACHE_FILE)
        self.cache = self._load_cache()
        self._google_quota_blocked = False

    @staticmethod
    def _local_title(title: str, lang: str) -> str:
        """Si el proveedor devuelve un título bilingüe, elige la parte local.

        Open Library suele registrar ediciones como
        ``Dolor Que Fortalece / Pain That Strengthens``. Para una biblioteca en
        español queremos la primera parte, sin el título inglés.
        """
        parts = [part.strip() for part in re.split(r'\s*/\s*', title or '') if part.strip()]
        if len(parts) < 2:
            return (title or '').strip()
        # Evitar romper títulos como "AC/DC": sólo separamos cuando ambas partes
        # parecen títulos (dos o más palabras).
        if not all(len(part.split()) >= 2 for part in parts):
            return (title or '').strip()
        if (lang or '').lower().startswith('es'):
            spanish = [
                part for part in parts
                if re.search(r'[áéíóúüñ¿¡]', part, re.IGNORECASE)
                or re.search(r'\b(?:de|del|la|el|los|las|que|y|en|para|con|sin|una|un)\b', part, re.IGNORECASE)
            ]
            return spanish[0] if spanish else parts[0]
        return parts[-1]

    # ------------------------------------------------------------------
    # Caché
    # ------------------------------------------------------------------
    def _load_cache(self) -> dict:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_cache(self):
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"[!] No se pudo guardar la caché de libros: {exc}")

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    @staticmethod
    def _fetch_json(url: str) -> dict:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': USER_AGENT,
                'Accept': 'application/json',
            },
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode('utf-8'))

    # ------------------------------------------------------------------
    # Proveedores
    # ------------------------------------------------------------------
    def _search_open_library(self, query: str, lang: str) -> list[dict]:
        ol_lang = _OPENLIBRARY_LANGS.get((lang or '').split('-')[0].lower())
        base_params = {
            'q': query,
            'limit': '15',
            'fields': 'key,title,author_name,first_publish_year,language,isbn',
        }

        def fetch(with_lang: bool):
            params = dict(base_params)
            if with_lang and ol_lang:
                params['language'] = ol_lang
            url = 'https://openlibrary.org/search.json?' + urllib.parse.urlencode(params)
            data = self._fetch_json(url)
            return data.get('docs', []) or []

        try:
            docs = fetch(bool(ol_lang))
            if not docs and ol_lang:
                docs = fetch(False)
        except Exception as exc:
            print(f"[!] Error buscando en Open Library '{query}': {exc}")
            return []

        candidates = []
        for doc in docs:
            title = (doc.get('title') or '').strip()
            if not title:
                continue
            authors = [str(a).strip() for a in doc.get('author_name') or [] if str(a).strip()]
            author = ', '.join(authors[:3])
            if len(authors) > 3:
                author += ' et al.'
            first_year = doc.get('first_publish_year')
            year = str(first_year) if first_year else None
            languages = [str(item).lower() for item in doc.get('language') or []]
            candidates.append({
                'id': doc.get('key') or title,
                'name': self._local_title(title, lang),
                'author': author or None,
                'year': year,
                'source': 'Open Library',
                'languages': languages,
            })
        return candidates

    def _search_google_books(self, query: str, lang: str) -> list[dict]:
        if self._google_quota_blocked:
            return []
        gb_lang = (lang or '').split('-')[0].lower() or None

        def fetch(with_lang: bool):
            params = {
                'q': query,
                'maxResults': '15',
                'printType': 'books',
            }
            if with_lang and gb_lang:
                params['langRestrict'] = gb_lang
            if GOOGLE_BOOKS_API_KEY:
                params['key'] = GOOGLE_BOOKS_API_KEY
            url = 'https://www.googleapis.com/books/v1/volumes?' + urllib.parse.urlencode(params)
            data = self._fetch_json(url)
            return data.get('items', []) or []

        try:
            items = fetch(bool(gb_lang))
            if not items and gb_lang:
                items = fetch(False)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                self._google_quota_blocked = True
                print("[!] Google Books alcanzó el límite de consultas; se usa sólo Open Library en esta sesión.")
            else:
                print(f"[!] Error buscando en Google Books '{query}': {exc}")
            return []
        except Exception as exc:
            print(f"[!] Error buscando en Google Books '{query}': {exc}")
            return []

        candidates = []
        for item in items:
            info = item.get('volumeInfo') or {}
            title = (info.get('title') or '').strip()
            subtitle = (info.get('subtitle') or '').strip()
            if subtitle and subtitle.lower() not in title.lower():
                title = f"{title}: {subtitle}"
            if not title:
                continue
            authors = [str(a).strip() for a in info.get('authors') or [] if str(a).strip()]
            author = ', '.join(authors[:3])
            if len(authors) > 3:
                author += ' et al.'
            published = str(info.get('publishedDate') or '')
            year_match = re.search(r'\b(1[5-9]\d{2}|20\d{2})\b', published)
            year = year_match.group(1) if year_match else None
            language = str(info.get('language') or '').lower()
            candidates.append({
                'id': item.get('id') or title,
                'name': self._local_title(title, lang),
                'author': author or None,
                'year': year,
                'source': 'Google Books',
                'languages': [language] if language else [],
            })
        return candidates

    # ------------------------------------------------------------------
    # Selección
    # ------------------------------------------------------------------
    @staticmethod
    def _normalise(text: str | None) -> str:
        return re.sub(r'\s+', ' ', (text or '').strip().lower())

    def _dedupe(self, candidates: list[dict]) -> list[dict]:
        result = []
        seen = set()
        for candidate in candidates:
            key = (
                self._normalise(candidate.get('name')),
                self._normalise(candidate.get('author')),
                str(candidate.get('year') or ''),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
        return result[:20]

    def _choose(self, candidates: list[dict], query: str,
                auto_select_year: str | None,
                pick_result_fn=None,
                title_hint: str | None = None) -> dict | None:
        if not candidates:
            return None

        title_hint = (title_hint or query or '').strip()
        year_matches = []
        if auto_select_year:
            year_matches = [
                c for c in candidates
                if str(c.get('year') or '') == str(auto_select_year)
            ]

        if year_matches and len(year_matches) == 1 and not pick_result_fn:
            return year_matches[0]

        if pick_result_fn and len(candidates) > 1 and not year_matches:
            chosen = pick_result_fn(candidates)
            return chosen

        def score(candidate: dict) -> float:
            similarity = SequenceMatcher(
                None, self._normalise(title_hint), self._normalise(candidate.get('name'))
            ).ratio()
            value = similarity * 0.75
            if auto_select_year and str(candidate.get('year') or '') == str(auto_select_year):
                value += 0.15
            languages = [str(item).lower() for item in candidate.get('languages') or []]
            if any(item in ('spa', 'es', 'es-mx') for item in languages):
                value += 0.10
            # Preferir títulos razonablemente cortos frente a ediciones bilingües
            # que se hayan escapado del separador "/".
            words = len(self._normalise(candidate.get('name')).split())
            if words > 12:
                value -= 0.05
            return value

        pool = year_matches or candidates
        best = max(pool, key=score)
        return best

    def search_book(
        self,
        query: str,
        lang: str = DEFAULT_LANGUAGE,
        pick_result_fn=None,
        auto_select_year: str | None = None,
    ) -> dict | None:
        """Busca un libro y devuelve título, autor, año y candidatos alternativos.

        Prueba Open Library y, si no hay resultados, Google Books. ``query`` puede
        ser sólo el título o incluir el autor; el limpiador conserva ambos.
        """
        clean_q, detected_year = clean_book_query(query)
        if not clean_q:
            return None
        auto_select_year = auto_select_year or detected_year
        title_hint, _, _ = parse_book_filename(query)

        cache_key = f"book:v2:{clean_q.lower()}:{lang}"
        if cache_key in self.cache:
            cached = dict(self.cache[cache_key])
            # Asegurar que una caché antigua o incompleta no rompa al organizador.
            cached.setdefault('candidates', [])
            return cached

        candidates = self._search_open_library(clean_q, lang)
        if not candidates:
            candidates = self._search_google_books(clean_q, lang)
        candidates = self._dedupe(candidates)
        if not candidates:
            return None

        chosen = self._choose(
            candidates, clean_q, auto_select_year, pick_result_fn,
            title_hint=title_hint or clean_q,
        )
        if chosen is None:
            return None

        result = {
            'title': chosen.get('name') or clean_q,
            'author': chosen.get('author'),
            'year': chosen.get('year'),
            'source': chosen.get('source'),
        }
        if len(candidates) > 1:
            result['candidates'] = candidates

        self.cache[cache_key] = result
        self._save_cache()
        return result
