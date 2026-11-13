# -*- coding: utf-8 -*-
import os

from media_renamer.organizer import MediaOrganizer
from media_renamer.utils import clean_book_query, parse_book_filename


class FakeBookClient:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    def search_book(self, query, lang="es-MX", pick_result_fn=None, auto_select_year=None):
        self.calls.append((query, lang, auto_select_year))
        return self.results.get(query)


def _touch(path):
    path.write_bytes(b"")


def test_plan_books_uses_jellyfin_structure(tmp_path):
    _touch(tmp_path / "[digital] Gente tóxica (Bernardo Stamateas).pdf")
    _touch(tmp_path / "[escaneado] Mente de esclavo (Bernardo-Stamateas).pdf")
    fake = FakeBookClient({
        "Gente tóxica (Bernardo Stamateas)": {
            "title": "Gente tóxica",
            "author": "Bernardo Stamateas",
            "year": "2018",
            "candidates": [
                {"name": "Gente tóxica", "author": "Bernardo Stamateas", "year": "2018"},
            ],
        },
    })
    organizer = MediaOrganizer(None, None, fake)
    plan = organizer.plan_books(str(tmp_path), lang="es-MX")

    assert plan is not None
    assert plan["type"] == "book"
    rel = {os.path.basename(m["src"]): m["rel_dest"] for m in plan["moves"]}
    assert rel["[digital] Gente tóxica (Bernardo Stamateas).pdf"] == os.path.join(
        "Books", "Bernardo Stamateas", "Gente tóxica (2018)", "Gente tóxica.pdf"
    )
    # Sin metadatos, el autor entre paréntesis igual se usa para la carpeta.
    assert rel["[escaneado] Mente de esclavo (Bernardo-Stamateas).pdf"] == os.path.join(
        "Books", "Bernardo Stamateas", "Mente de esclavo", "Mente de esclavo.pdf"
    )
    assert plan["moves"][1]["confidence"] == "low"


def test_plan_books_comic_structure(tmp_path):
    _touch(tmp_path / "Plastic Man #002 (1944).cbz")
    organizer = MediaOrganizer(None, None, FakeBookClient())
    plan = organizer.plan_books(str(tmp_path), use_metadata=False)

    assert plan is not None
    assert plan["moves"][0]["rel_dest"] == os.path.join(
        "Comics", "Plastic Man (1944)", "Plastic Man #002 (1944).cbz"
    )
    assert plan["moves"][0]["confidence"] == "high"


def test_rebuild_book_move_changes_author_and_title(tmp_path):
    _touch(tmp_path / "Libro.pdf")
    organizer = MediaOrganizer(None, None, FakeBookClient())
    plan = organizer.plan_books(str(tmp_path), use_metadata=False)

    move = plan["moves"][0]
    organizer.rebuild_book_move(move, {
        "name": "Autoboicot",
        "author": "Bernardo Stamateas",
        "year": "2010",
    })
    assert move["rel_dest"] == os.path.join(
        "Books", "Bernardo Stamateas", "Autoboicot (2010)", "Autoboicot.pdf"
    )
    assert move["confidence"] == "high"


def test_book_name_parsing():
    title, author, year = parse_book_filename(
        "[digital] Dolor que fortalece (Bernardo Stamateas).pdf"
    )
    assert title == "Dolor que fortalece"
    assert author == "Bernardo Stamateas"
    assert year is None

    assert clean_book_query("[digital] Gente tóxica (Bernardo Stamateas).pdf") == (
        "Gente tóxica (Bernardo Stamateas)",
        None,
    )

    title, author, year = parse_book_filename("2001 - Odisea (1968).epub")
    assert title == "2001 - Odisea"
    assert author is None
    assert year == "1968"

    # 1984 es el título, no el año de edición.
    assert parse_book_filename("1984.epub") == ("1984", None, None)
