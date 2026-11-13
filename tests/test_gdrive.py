# -*- coding: utf-8 -*-
"""Tests de la integración con Google Drive (sin acceso a red)."""
import json

from media_renamer.gdrive import (
    DRIVE_SOURCE_MARKER,
    DriveVideo,
    GoogleDriveClient,
    build_strm_content,
    build_stream_url,
    clean_drive_title,
    decode_drive_listing,
    detect_episode_number,
    detect_season_from_path,
    parse_drive_target,
    write_strm_files,
)


def _playable_lines(content: str) -> list:
    return [line for line in content.splitlines() if line and not line.startswith("#")]


def _file_item(file_id: str, name: str, size: int = 0) -> list:
    item = [file_id, ["parent"], name, "video/x-matroska"]
    item.extend([0] * 9)
    item.append(size)
    return item


def _folder_item(file_id: str, name: str) -> list:
    return [file_id, ["parent"], name, "application/vnd.google-apps.folder"]


def _file_dict(file_id: str, name: str, size: int = 0) -> dict:
    return {"id": file_id, "name": name, "mime_type": "video/x-matroska", "size": size}


def _folder_dict(file_id: str, name: str) -> dict:
    return {
        "id": file_id,
        "name": name,
        "mime_type": "application/vnd.google-apps.folder",
        "size": 0,
    }


class TestParseDriveTarget:
    def test_carpeta_con_usuario(self):
        assert parse_drive_target(
            "https://drive.google.com/drive/u/0/folders/1IvKCe0eq9BSrrYzJJYYKwcUZ1q0gd3WH"
        ) == ("folder", "1IvKCe0eq9BSrrYzJJYYKwcUZ1q0gd3WH")

    def test_archivo_y_enlaces_viejos(self):
        assert parse_drive_target("https://drive.google.com/file/d/abc12345DEF/view") == (
            "file", "abc12345DEF"
        )
        assert parse_drive_target("https://drive.google.com/open?id=abc12345DEF") == (
            "folder", "abc12345DEF"
        )

    def test_id_pelado(self):
        assert parse_drive_target("1IvKCe0eq9BSrrYzJJYYKwcUZ1q0gd3WH") == (
            "folder", "1IvKCe0eq9BSrrYzJJYYKwcUZ1q0gd3WH"
        )


class TestStrmContent:
    def test_url_directa_y_marcador(self):
        content = build_strm_content("FILE1", "https://drive.google.com/file/d/FILE1/view")
        assert DRIVE_SOURCE_MARKER in content
        assert _playable_lines(content) == [build_stream_url("FILE1")]
        assert "drive.usercontent.google.com" in content
        assert "confirm=t" in content


class TestSeasonAndEpisode:
    def test_detecta_temporadas_y_especiales(self):
        assert detect_season_from_path(["S1"]) == 1
        assert detect_season_from_path(["Temporada 02"]) == 2
        assert detect_season_from_path(["Season 03"]) == 3
        assert detect_season_from_path(["Especiales"]) == 0
        assert detect_season_from_path(["Specials"]) == 0
        assert detect_season_from_path(["Películas"]) is None

    def test_usa_el_numero_final_y_no_el_del_titulo(self):
        name = "[Z-A] Boku no Hero Academia 4th Season OVAs [WEB] [1080p] - 01 [WEB].mkv"
        assert detect_episode_number(name, 4) == 1

    def test_respeta_marcadores_explicitos(self):
        assert detect_episode_number("Show - S08E11 (1080p).mkv", 8) == 8
        assert detect_episode_number("Show - Episodio 5 - Parte 2.mkv", 5) == 5

    def test_titulo_limpio(self):
        name = "[BeatZ-Anime] Boku no Hero Academia - 01 [BDRip 1080p HEVC FLAC].mkv"
        assert clean_drive_title(name, 1) == "Boku no Hero Academia"
        assert clean_drive_title("Boku no Hero Academia - S08E11 (WEB 1080p).mkv", 11) == (
            "Boku no Hero Academia"
        )


class TestDecodeListing:
    def test_decodifica_payload_publico(self):
        payload = json.dumps([
            [
                _file_item("id1", "Serie - 01.mkv", 123),
                _folder_item("folder1", "S2"),
            ],
            None,
            None,
            None,
            [1],
        ]).replace("\\", "\\\\").replace("'", "\\'")
        html = f"<script>window['_DRIVE_ivd'] = '{payload}';</script>"
        items = decode_drive_listing(html)
        assert [item["id"] for item in items] == ["id1", "folder1"]
        assert items[0]["name"] == "Serie - 01.mkv"
        assert items[0]["size"] == 123
        assert items[1]["mime_type"] == "application/vnd.google-apps.folder"

    def test_payload_ausente(self):
        assert decode_drive_listing("<html>login</html>") == []


class TestScan:
    def test_recorre_subcarpetas_y_numera(self, monkeypatch):
        client = GoogleDriveClient(log_fn=lambda _: None)
        listings = {
            "root123456": [
                _folder_dict("f1", "S1"),
                _folder_dict("f0", "Especiales"),
                _file_dict("movie", "Pelicula suelta.mkv"),
            ],
            "f1": [
                _file_dict("a", "Serie - 02.mkv"),
                _file_dict("b", "Serie - 01.mkv"),
            ],
            "f0": [
                _file_dict("c", "OVA - 01.mkv"),
                _file_dict("d", "OVA - 01.mkv"),
            ],
        }
        monkeypatch.setattr(
            client, "_list_folder", lambda folder_id: (listings[folder_id], "Serie")
        )

        result = client.scan("https://drive.google.com/drive/folders/root123456")

        assert result.root_name == "Serie"
        assert result.folders_read == 3
        assert len(result.videos) == 5
        season_one = [v for v in result.videos if v.season == 1]
        assert [(v.name, v.episode) for v in season_one] == [
            ("Serie - 01.mkv", 1),
            ("Serie - 02.mkv", 2),
        ]
        # Los dos OVAs con el mismo número reciben S00E01 y S00E02.
        specials = [v for v in result.videos if v.season == 0]
        assert sorted(v.episode for v in specials) == [1, 2]
        # Un archivo suelto no recibe temporada ni episodio inventado.
        loose = next(v for v in result.videos if v.id == "movie")
        assert loose.season is None
        assert loose.episode is None

    def test_sin_recursivo(self, monkeypatch):
        client = GoogleDriveClient(log_fn=lambda _: None)
        monkeypatch.setattr(
            client,
            "_list_folder",
            lambda folder_id: (
                [_folder_dict("f1", "S1"), _file_dict("a", "Serie - 01.mkv")],
                "Serie",
            ),
        )
        result = client.scan("https://drive.google.com/drive/folders/root123456", recursive=False)
        assert [v.id for v in result.videos] == ["a"]


class TestWriteStrmFiles:
    def test_nombres_por_temporada(self, tmp_path):
        videos = [
            DriveVideo(id="a", name="Serie - 01.mkv", season=1, episode=1),
            DriveVideo(id="b", name="OVA - 01.mkv", season=0, episode=1),
            DriveVideo(id="c", name="Pelicula (2019).mkv"),
        ]
        created, errors = write_strm_files(videos, str(tmp_path), overwrite=True)
        assert errors == 0
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "Pelicula (2019).strm",
            "S00E01 - OVA.strm",
            "S01E01 - Serie.strm",
        ]
        content = (tmp_path / "S01E01 - Serie.strm").read_text(encoding="utf-8")
        assert _playable_lines(content) == [build_stream_url("a")]

    def test_no_pisa_si_no_se_pide(self, tmp_path):
        video = DriveVideo(id="a", name="Serie - 01.mkv", season=1, episode=1)
        write_strm_files([video], str(tmp_path), log_fn=lambda _: None)
        write_strm_files([video], str(tmp_path), log_fn=lambda _: None)
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "S01E01 - Serie (2).strm",
            "S01E01 - Serie.strm",
        ]

    def test_plantilla_personalizada(self, tmp_path):
        video = DriveVideo(id="abc", name="Serie - 03.mkv", season=2, episode=3)
        created, errors = write_strm_files(
            [video], str(tmp_path), template="{code} - {title} [{id}].strm", overwrite=True
        )
        assert errors == 0
        assert [p.name for p in tmp_path.iterdir()] == ["S02E03 - Serie [abc].strm"]
        assert created
