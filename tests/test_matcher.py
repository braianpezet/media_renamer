# -*- coding: utf-8 -*-
"""Tests de parseo de nombres y del plan de series (sin acceso a red)."""
import os

import pytest

from media_renamer.matcher import parse_filename
from media_renamer.organizer import MediaOrganizer, _same_path


class _FakeTVClient:
    """Proveedor de series falso: permite probar el planificador sin salir a la red."""

    provider_name = "Fake"

    def __init__(self, episodes=None, season_counts=None, name='Camino al Cielo'):
        self.episodes = episodes or {}
        self.season_counts = season_counts or {}
        self.name = name
        self.queries = []

    def search_tv(self, query, lang=None, pick_result_fn=None):
        self.queries.append(query)
        return {
            'id': 1,
            'name': self.name,
            'year': 1984,
            'seasons': {},
            'season_counts': dict(self.season_counts),
        }

    def get_season_episodes(self, tv_id, season, lang=None):
        return dict(self.episodes.get(season, {}))


class TestParseFilenameTemporadaEpisodio:
    """Formato típico de los títulos de YouTube: «Temporada 4, Episodio 16»."""

    def test_titulo_de_youtube_con_temporada_y_episodio(self):
        s, e1, e2, titulo, ext, tag = parse_filename(
            "De Regreso a Oakland - Camino al Cielo - Temporada 4, Episodio 16.strm"
        )
        assert (s, e1, e2, titulo, ext, tag) == (4, 16, None, "", ".strm", None)

    def test_en_ingles(self):
        s, e1 = parse_filename("Serie - Season 2 Episode 5.mkv")[:2]
        assert (s, e1) == (2, 5)

    def test_formato_abreviado(self):
        s, e1, _, titulo, ext, _ = parse_filename("T4 E16 - De Regreso a Oakland.strm")
        assert (s, e1, titulo, ext) == (4, 16, "De Regreso a Oakland", ".strm")

    def test_titulo_local_no_se_llena_con_la_serie(self):
        """Si el título quedara delante, el fuzzy matching podría pisar el número leído."""
        assert parse_filename("Camino al Cielo - Temporada 1, Episodio 3.strm")[3] == ""

    def test_quita_el_id_de_youtube(self):
        titulo = parse_filename("T4 E16 - De Regreso a Oakland (abc123).strm")[3]
        assert titulo == "De Regreso a Oakland"

    def test_no_se_dispara_con_numeros_sueltos(self):
        s, e1, _, _, _, _ = parse_filename("The 5 Episodes.mkv")
        assert s is None


class TestPatronesPreviosSiguenFuncionando:
    """El patrón nuevo va después de los fuertes, así que no debe pisarlos."""

    @pytest.mark.parametrize("nombre,esperado", [
        ("S01E02 Titulo.mkv", (1, 2, None, "Titulo", ".mkv", None)),
        ("S01E02-E03 Titulo.mkv", (1, 2, 3, "Titulo", ".mkv", None)),
        ("4x69 Tortugas en Peking.mkv", (4, 69, None, "Tortugas en Peking", ".mkv", None)),
        ("SHA-01.mp4", (None, 1, None, "", ".mp4", None)),
    ])
    def test_sin_cambios(self, nombre, esperado):
        assert parse_filename(nombre) == esperado


class TestColaDeIdDeYouTube:
    """Los archivos de la pestaña de YouTube terminan en «(id)»: el id no es un episodio."""

    def test_los_digitos_del_id_no_son_episodio(self):
        # El id «RncsL3dO2tI» lleva un 3 adentro: antes se leía como capítulo 3.
        assert parse_filename(
            "A TERRA PROMETIDA Ravena morre no seu próprio feitiço (RncsL3dO2tI).strm"
        )[:3] == (None, None, None)

    def test_el_marcador_real_si_se_lee(self):
        s, e1, e2, _, _, _ = parse_filename(
            "JOSUÉ Y LA TIERRA PROMETIDA CAPITULO 39 SD Y EN HD (mRqYslL8YXo).strm"
        )
        assert (s, e1, e2) == (None, 39, None)

    def test_no_confunde_un_anio_con_un_id(self):
        # «(1984)» tiene 6 caracteres, no 11: se conserva para poder buscar la serie.
        assert parse_filename("Camino al Cielo (1984).mkv")[3] == "Camino al Cielo (1984)"


class TestSamePath:
    def test_detecta_misma_ruta(self, tmp_path):
        a = str(tmp_path / "uno" / "video.strm")
        assert _same_path(a, a)
        assert _same_path(a, a.upper())

    def test_rutas_distintas(self, tmp_path):
        assert not _same_path(str(tmp_path / "a.strm"), str(tmp_path / "b.strm"))


class TestPlanTvShow:
    def test_lee_temporada_y_episodio_del_titulo_de_youtube(self, tmp_path):
        salida = tmp_path / "Youtube"
        salida.mkdir()
        (salida / "De Regreso a Oakland - Camino al Cielo - Temporada 4, Episodio 16.strm").write_text(
            "x", encoding="utf-8"
        )

        cliente = _FakeTVClient(episodes={4: {16: "De Regreso a Oakland"}}, season_counts={4: 24})
        plan = MediaOrganizer(cliente).plan_tv_show(
            str(salida), "Camino al Cielo", target_base_dir=str(salida)
        )

        assert plan["title"] == "Camino al Cielo (1984)"
        assert [m["rel_dest"] for m in plan["moves"]] == [
            os.path.join("Season 04", "S04E16 - De Regreso a Oakland.strm")
        ]

    def test_no_emite_movimientos_identicos(self, tmp_path):
        season = tmp_path / "Camino al Cielo (1984)" / "Season 04"
        season.mkdir(parents=True)
        (season / "S04E16 - De Regreso a Oakland.strm").write_text("x", encoding="utf-8")

        cliente = _FakeTVClient(episodes={4: {16: "De Regreso a Oakland"}}, season_counts={4: 24})
        plan = MediaOrganizer(cliente).plan_tv_show(
            str(season), "Camino al Cielo", target_base_dir=str(tmp_path)
        )

        assert plan["moves"] == []

    def test_sigue_emitiendo_movimientos_reales(self, tmp_path):
        salida = tmp_path / "Youtube"
        salida.mkdir()
        (salida / "video suelto.strm").write_text("x", encoding="utf-8")

        plan = MediaOrganizer(_FakeTVClient()).plan_tv_show(
            str(salida), "Camino al Cielo", target_base_dir=str(salida)
        )

        assert [m["rel_dest"] for m in plan["moves"]] == [
            os.path.join("Season 01", "S01E01 - video suelto.strm")
        ]

    def test_titulo_local_igual_a_la_serie_no_pisa_el_episodio(self, tmp_path):
        """Regresión: «Boku no Hero Academia» se parece a «My Hero Academia» y el
        fuzzy matching asignaba todos los episodios al último."""
        salida = tmp_path / "Drive"
        salida.mkdir()
        for episode in (1, 2, 3):
            (salida / f"S01E0{episode} - Boku no Hero Academia.strm").write_text(
                "x", encoding="utf-8"
            )

        cliente = _FakeTVClient(
            episodes={1: {1: "Uno", 2: "Dos", 3: "My Hero Academia"}},
            season_counts={1: 3},
            name="My Hero Academia",
        )
        plan = MediaOrganizer(cliente).plan_tv_show(
            str(salida), "Boku no Hero Academia", target_base_dir=str(salida)
        )

        assert [m["rel_dest"] for m in plan["moves"]] == [
            os.path.join("Season 01", "S01E01 - Uno.strm"),
            os.path.join("Season 01", "S01E02 - Dos.strm"),
            os.path.join("Season 01", "S01E03 - My Hero Academia.strm"),
        ]
