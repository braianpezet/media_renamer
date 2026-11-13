# -*- coding: utf-8 -*-
"""Tests del modo «solo limpiar» (plan_clean_filenames) y del parseo compartido."""
import os

import pytest

from media_renamer.matcher import parse_filename
from media_renamer.organizer import MediaOrganizer


@pytest.fixture
def organizer():
    """Organizador sin proveedor de metadatos: el modo limpiar no los usa."""
    return MediaOrganizer.__new__(MediaOrganizer)


def _limpiar(organizer, tmp_path, nombres, show_title=None):
    for nombre in nombres:
        with open(os.path.join(tmp_path, nombre), "w", encoding="utf-8") as fh:
            fh.write("x")
    plan = organizer.plan_clean_filenames(str(tmp_path), show_title=show_title)
    return {os.path.basename(m["src"]): m["rel_dest"] for m in plan["moves"]} if plan else {}


class TestReconoceLosMismosFormatosQueElOrganizador:
    """La numeración la resuelve parse_filename, no una regex propia."""

    @pytest.mark.parametrize("nombre,esperado", [
        # SxxEyy ya está normalizado: no se toca
        ("El Show S01E05.mkv", None),
        # NxNN se normaliza a SxxEyy
        ("El Show 1x05.mkv", "El Show S01E05.mkv"),
        # inglés
        ("El Show - Episode 5.mkv", "El Show - Capítulo 05.mkv"),
        ("El Show - Chapter 5.mkv", "El Show - Capítulo 05.mkv"),
        ("El Show - Ch 5.mkv", "El Show - Capítulo 05.mkv"),
        # español y abreviaturas
        ("El Show - Episodio 5.mkv", "El Show - Capítulo 05.mkv"),
        ("El Show - Ep. 5.mkv", "El Show - Capítulo 05.mkv"),
        ("El Show - Cap 5.mkv", "El Show - Capítulo 05.mkv"),
        # temporada delante -> SxxEyy
        ("El Show - Temporada 1 Episodio 5.mkv", "El Show S01E05.mkv"),
        ("El Show - Season 1 Episode 5.mkv", "El Show S01E05.mkv"),
        ("El Show - T1 E5.mkv", "El Show S01E05.mkv"),
        # número suelto
        ("El Show - 5.mkv", "El Show - Capítulo 05.mkv"),
        # dos episodios
        ("El Show - Episodes 5 y 6.mkv", "El Show - Capítulo 05-06.mkv"),
        # el título del episodio no se pierde
        ("El Show - 05 - Titulo.mkv", "El Show - Capítulo 05 - Titulo.mkv"),
    ])
    def test_numeracion(self, organizer, tmp_path, nombre, esperado):
        resultado = _limpiar(organizer, tmp_path, [nombre])
        if esperado is None:
            assert resultado == {}, f"{nombre} no debería renombrarse"
        else:
            assert resultado == {nombre: esperado}

    def test_capítulo_ya_normalizado_no_se_toca(self, organizer, tmp_path):
        assert _limpiar(organizer, tmp_path, ["El Show - Capítulo 05.mkv"]) == {}


class TestPrefijoPorArchivo:
    """El prefijo sale de cada archivo, no del primero (bug histórico)."""

    def test_cada_episodio_conserva_su_nombre(self, organizer, tmp_path):
        resultado = _limpiar(organizer, tmp_path, [
            "Piloto, Episodio 1.mkv",
            "Segundo, Episodio 2.mkv",
        ])
        assert resultado == {
            "Piloto, Episodio 1.mkv": "Piloto - Capítulo 01.mkv",
            "Segundo, Episodio 2.mkv": "Segundo - Capítulo 02.mkv",
        }

    def test_show_title_gana_sobre_el_prefijo(self, organizer, tmp_path):
        resultado = _limpiar(organizer, tmp_path, ["Episodio 1.mkv"], show_title="Mi Serie")
        assert resultado == {"Episodio 1.mkv": "Mi Serie - Capítulo 01.mkv"}

    def test_titulos_tipo_youtube_con_temporada(self, organizer, tmp_path):
        resultado = _limpiar(organizer, tmp_path, [
            "De Regreso a Oakland - Camino al Cielo - Temporada 4, Episodio 16 (abc123).strm",
        ], show_title="Camino al Cielo")
        assert resultado == {
            "De Regreso a Oakland - Camino al Cielo - Temporada 4, Episodio 16 (abc123).strm":
                "Camino al Cielo S04E16.strm",
        }


class TestSinRomperNada:
    def test_archivo_sin_numero_no_se_toca(self, organizer, tmp_path):
        assert _limpiar(organizer, tmp_path, ["conferencia final.mp4"]) == {}

    def test_conserva_el_nombre_con_etiquetas_entre_corchetes(self, organizer, tmp_path):
        resultado = _limpiar(organizer, tmp_path, ["Una Pelicula (1999) [1080p BluRay].mkv"])
        assert resultado == {"Una Pelicula (1999) [1080p BluRay].mkv": "Una Pelicula (1999).mkv"}

    def test_acronimo_conocido_se_deja_igual(self, organizer, tmp_path):
        """01PRFDT usa ACRONYM_RULES: renombrarlo rompería la detección de serie."""
        assert _limpiar(organizer, tmp_path, ["01PRFDT.mkv"]) == {}

    def test_nombre_ya_correcto_no_se_toca(self, organizer, tmp_path):
        """El título del episodio se conserva y, al estar ya bien, no hay movimiento."""
        nombres = ["El Show S01E01 - El encuentro (1a parte).mkv"]
        assert _limpiar(organizer, tmp_path, nombres) == {}

    def test_conserva_el_titulo_al_renumerar(self, organizer, tmp_path):
        resultado = _limpiar(organizer, tmp_path, ["El Show - Episodio 1 - El encuentro.mkv"])
        assert resultado == {
            "El Show - Episodio 1 - El encuentro.mkv": "El Show - Capítulo 01 - El encuentro.mkv",
        }


class TestColisiones:
    def test_dos_archivos_al_mismo_nombre_no_se_pisan(self, organizer, tmp_path):
        resultado = _limpiar(organizer, tmp_path, [
            "El Show - Episodio 5.mkv",
            "El Show - Episodio 05.mkv",
        ])
        assert resultado == {
            "El Show - Episodio 05.mkv": "El Show - Capítulo 05.mkv",
            "El Show - Episodio 5.mkv": "El Show - Capítulo 05 (2).mkv",
        }


class TestParseFilenameIngles:
    """chapter/episode quedan reconocidos como palabras, no por el número suelto."""

    def test_chapter_con_dos_episodios(self):
        # Antes esto daba e2=None y dejaba "y 6" como título local.
        assert parse_filename("Show - Chapter 5 y 6.mkv")[:3] == (None, 5, 6)

    def test_episode_con_dos_episodios(self):
        assert parse_filename("Show - Episode 5 y 6.mkv")[:3] == (None, 5, 6)

    def test_resolucion_despues_del_guion_no_es_episodio(self):
        s, e1, e2 = parse_filename("Show - Episode 5 - 1080p.mkv")[:3]
        assert (e1, e2) == (5, None)

    def test_chapter_simple(self):
        assert parse_filename("Show - Chapter 5.mkv")[:2] == (None, 5)
