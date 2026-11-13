# -*- coding: utf-8 -*-
"""Cliente mínimo para TheTVDB API v4, usado para los metadatos de series."""
import json
import os
import re
import urllib.parse
import urllib.request

from .config import (
    DEFAULT_LANGUAGE,
    THETVDB_API_KEY,
    THETVDB_CACHE_FILE,
    THETVDB_PIN,
    USER_AGENT,
)
from .utils import clean_query_string, sanitize_filename


class TVDBClient:
    provider_name = "TheTVDB"
    _BASE_URL = "https://api4.thetvdb.com/v4"

    def __init__(self, api_key: str | None = None, pin: str | None = None):
        self.api_key = (api_key if api_key is not None else THETVDB_API_KEY).strip()
        self.pin = (pin if pin is not None else THETVDB_PIN).strip()
        if not self.api_key:
            raise ValueError("Falta THETVDB_API_KEY. Agregala en .env o en las variables de entorno.")
        self.cache = self._load_cache()
        self.token = self.cache.get("_token")

    def _load_cache(self) -> dict:
        if os.path.exists(THETVDB_CACHE_FILE):
            try:
                with open(THETVDB_CACHE_FILE, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            except Exception:
                pass
        return {}

    @staticmethod
    def _normalise_tv_info(info: dict) -> dict:
        """Restaura claves numéricas que JSON guarda como texto."""
        value = dict(info)
        for key in ("season_counts", "season_names", "seasons"):
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

    def _save_cache(self) -> None:
        try:
            with open(THETVDB_CACHE_FILE, "w", encoding="utf-8") as handle:
                json.dump(self.cache, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"[!] No se pudo guardar la caché de TheTVDB: {exc}")

    def _request(self, path: str, *, method: str = "GET", payload: dict | None = None) -> dict:
        url = path if path.startswith("http") else f"{self._BASE_URL}{path}"
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        if self.token and not path.endswith("/login"):
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    def _login(self) -> None:
        payload = {"apikey": self.api_key}
        if self.pin:
            payload["pin"] = self.pin
        data = self._request("/login", method="POST", payload=payload)
        self.token = data.get("data", {}).get("token")
        if not self.token:
            raise RuntimeError("TheTVDB no devolvió un token de acceso.")
        self.cache["_token"] = self.token
        self._save_cache()

    def _fetch(self, path: str) -> dict:
        if not self.token:
            self._login()
        try:
            return self._request(path)
        except Exception as exc:
            # El token dura aproximadamente un mes. Reintentar una vez con uno nuevo
            # hace que la aplicación se recupere sin intervención del usuario.
            if getattr(exc, "code", None) != 401:
                raise
            self.token = None
            self.cache.pop("_token", None)
            self._login()
            return self._request(path)

    @staticmethod
    def _language_code(lang: str) -> str:
        # TheTVDB usa códigos ISO-639-3 en sus rutas de episodios.
        return {"es": "spa", "en": "eng", "pt": "por", "fr": "fra", "de": "deu", "it": "ita"}.get(
            lang.split("-", 1)[0].lower(), lang
        )

    def search_tv(self, query: str, lang: str = DEFAULT_LANGUAGE, pick_result_fn=None) -> dict | None:
        clean_q, year_fallback = clean_query_string(query)
        cache_key = f"search_tv:v3:{clean_q.lower()}:{lang}"
        if cache_key in self.cache:
            return self._normalise_tv_info(self.cache[cache_key])

        try:
            params = urllib.parse.urlencode({"query": clean_q, "type": "series", "limit": 15})
            results = self._fetch(f"/search?{params}").get("data", [])
            if not results:
                return None

            candidates = []
            for result in results:
                tvdb_id = result.get("tvdb_id") or result.get("id")
                if not tvdb_id:
                    continue
                candidates.append({
                    "id": tvdb_id,
                    "name": result.get("name") or result.get("seriesName") or "?",
                    "year": str(result.get("year") or year_fallback or "?"),
                    "_raw": result,
                })
            if not candidates:
                return None

            selected = pick_result_fn(candidates) if pick_result_fn and len(candidates) > 1 else candidates[0]
            if selected is None:
                return None
            raw = selected["_raw"]
            tvdb_id = selected["id"]
            # La ficha básica no incluye el número de episodios de cada temporada.
            # La ficha extendida sí contiene esos episodios y permite conservar la
            # lógica existente que resuelve numeración acumulada.
            detail = self._fetch(f"/series/{tvdb_id}/extended?meta=episodes").get("data", {})
            season_counts = {}
            season_names = {}
            for episode in detail.get("episodes", []):
                season = episode.get("seasonNumber")
                if season is not None and int(season) > 0:
                    season_counts[int(season)] = season_counts.get(int(season), 0) + 1
            for season in detail.get("seasons", []):
                number = season.get("number")
                name = (season.get("name") or "").strip()
                if number not in (None, 0, "0") and name:
                    season_names[int(number)] = name
            value = {
                "id": int(tvdb_id) if str(tvdb_id).isdigit() else tvdb_id,
                "name": detail.get("name") or raw.get("name") or raw.get("seriesName"),
                "year": str(detail.get("year") or raw.get("year") or year_fallback or "") or None,
                "seasons": {},
                "season_counts": season_counts,
                "season_names": season_names,
            }
            self.cache[cache_key] = value
            self._save_cache()
            return value
        except Exception as exc:
            print(f"[!] Error buscando serie en TheTVDB '{clean_q}': {exc}")
            return None

    def get_season_episodes(self, tv_id: int | str, season_num: int, lang: str = DEFAULT_LANGUAGE) -> dict:
        cache_key = f"season_eps:{tv_id}:{season_num}:{lang}"
        if cache_key in self.cache:
            return {int(key): value for key, value in self.cache[cache_key].items()}

        language = self._language_code(lang)
        try:
            path = f"/series/{tv_id}/episodes/default/{language}?page=0"
            episodes = self._fetch(path).get("data", {}).get("episodes", [])
            # Algunas respuestas alojadas no aceptan una traducción concreta; usar
            # los episodios por defecto mantiene el renombrado operativo.
            if not episodes:
                episodes = self._fetch(f"/series/{tv_id}/episodes/default?page=0").get("data", {}).get("episodes", [])
            ep_map = {}
            for episode in episodes:
                if int(episode.get("seasonNumber") or 0) != season_num:
                    continue
                number = episode.get("number")
                title = (episode.get("name") or "").strip()
                if number is not None:
                    ep_map[int(number)] = sanitize_filename(title or f"Episodio {number}")
            self.cache[cache_key] = ep_map
            self._save_cache()
            return ep_map
        except Exception as exc:
            print(f"[!] Error cargando temporada {season_num} desde TheTVDB: {exc}")
            return {}
