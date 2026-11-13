# -*- coding: utf-8 -*-
"""Selección de proveedores de metadatos sin acoplar el organizador a una API."""
from .config import THETVDB_API_KEY
from .thetvdb_client import TVDBClient
from .tmdb_client import TMDBClient
from .book_client import BookClient


def available_tv_providers() -> list[str]:
    """Proveedores de series que pueden mostrarse en la interfaz."""
    providers = ["TMDb"]
    if THETVDB_API_KEY:
        providers.insert(0, "TheTVDB")
    return providers


def create_book_client() -> BookClient:
    """Proveedor de libros (Open Library + Google Books, sin API key)."""
    return BookClient()


def create_metadata_clients(tv_provider: str | None = None):
    """Devuelve (proveedor de series, proveedor de películas)."""
    movies = TMDBClient()
    selected = (tv_provider or ("TheTVDB" if THETVDB_API_KEY else "TMDb")).strip().lower()
    if selected == "thetvdb":
        if not THETVDB_API_KEY:
            raise ValueError("TheTVDB requiere configurar THETVDB_API_KEY en .env.")
        return TVDBClient(), movies
    if selected == "tmdb":
        return movies, movies
    raise ValueError(f"Proveedor de series desconocido: {tv_provider}")
