# -*- coding: utf-8 -*-
"""Tests de la generación de .strm (sin acceso a red)."""
from media_renamer.youtube_dl import (
    DEST_JELLYFIN,
    DEST_JELLYFIN_DIRECT,
    DEST_VLC,
    STRM_SOURCE_MARKER,
    YTDLPExtractor,
    build_strm_content,
    detect_youtube_url_type,
)


def _entries(*titles) -> list:
    return [
        {'id': f'id{i}', 'title': title, 'url': f'https://www.youtube.com/watch?v=id{i}'}
        for i, title in enumerate(titles, start=1)
    ]


def _playable_lines(content: str) -> list:
    """Líneas que Jellyfin usaría (ignora las que empiezan con '#')."""
    return [line for line in content.splitlines() if line and not line.startswith('#')]


class TestBuildStrmContent:
    def test_vlc_no_incluye_marcador(self):
        content = build_strm_content('https://www.youtube.com/watch?v=X')
        assert content == (
            '#EXTM3U\n'
            '#EXTVLCOPT:network-caching=1000\n'
            'https://www.youtube.com/watch?v=X\n'
        )
        assert STRM_SOURCE_MARKER not in content

    def test_jellyfin_agrega_marcador_de_origen(self):
        content = build_strm_content('https://www.youtube.com/watch?v=X', destination=DEST_JELLYFIN)
        assert f'{STRM_SOURCE_MARKER}https://www.youtube.com/watch?v=X' in content
        # Jellyfin reproduce la primera línea sin '#', que sigue siendo la de YouTube.
        assert _playable_lines(content) == ['https://www.youtube.com/watch?v=X']

    def test_jellyfin_directo_reproduce_la_url_directa(self):
        content = build_strm_content(
            'https://www.youtube.com/watch?v=X',
            'https://rr1---sn.googlevideo.com/videoplayback?itag=18',
            DEST_JELLYFIN_DIRECT
        )
        assert f'{STRM_SOURCE_MARKER}https://www.youtube.com/watch?v=X' in content
        assert _playable_lines(content) == ['https://rr1---sn.googlevideo.com/videoplayback?itag=18']


class TestConfiguracion:
    def test_usa_la_ruta_de_yt_dlp_indicada(self):
        extractor = YTDLPExtractor(yt_dlp_path=r'C:\ruta\yt-dlp.exe')
        assert extractor._build_yt_dlp_cmd('https://x')[0] == r'C:\ruta\yt-dlp.exe'

    def test_lista_de_clientes_alternativos(self):
        assert YTDLPExtractor(clients='android, mweb ,ios')._client_list() == ['android', 'mweb', 'ios']
        assert YTDLPExtractor(clients='')._client_list() == []
        assert YTDLPExtractor(clients=['android'])._client_list() == ['android']


class TestGenerateStrmFiles:
    def test_numeracion_de_episodios(self, tmp_path):
        extractor = YTDLPExtractor()
        creados, errores = extractor.generate_strm_files(
            _entries('Uno', 'Dos'),
            str(tmp_path),
            naming_template='S{season:02d}E{ep:02d} - {title}.strm',
            season=3,
            episode_start=5,
            overwrite=True,
            log_fn=lambda _: None,
        )
        assert (creados, errores) == (2, 0)
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            'S03E05 - Uno.strm',
            'S03E06 - Dos.strm',
        ]

    def test_sin_overwrite_no_pisa_los_existentes(self, tmp_path):
        extractor = YTDLPExtractor()
        for _ in range(2):
            extractor.generate_strm_files(
                _entries('Uno'), str(tmp_path), naming_template='{title}.strm',
                log_fn=lambda _: None,
            )
        assert sorted(p.name for p in tmp_path.iterdir()) == ['Uno (1).strm', 'Uno.strm']

    def test_con_overwrite_reemplaza(self, tmp_path):
        extractor = YTDLPExtractor()
        for _ in range(2):
            extractor.generate_strm_files(
                _entries('Uno'), str(tmp_path), naming_template='{title}.strm',
                overwrite=True, log_fn=lambda _: None,
            )
        assert sorted(p.name for p in tmp_path.iterdir()) == ['Uno.strm']

    def test_plantilla_invalida_cae_al_nombre_por_defecto(self, tmp_path):
        extractor = YTDLPExtractor()
        creados, errores = extractor.generate_strm_files(
            _entries('Uno'), str(tmp_path), naming_template='{inexistente}.strm',
            overwrite=True, log_fn=lambda _: None,
        )
        assert (creados, errores) == (1, 0)
        assert [p.name for p in tmp_path.iterdir()] == ['Uno (id1).strm']

    def test_logger_roto_no_cuenta_como_error(self, tmp_path):
        """Regresión: un log que falla (consola cp1252) no debe marcar el .strm como fallido."""
        def boom(message):
            raise UnicodeEncodeError('cp1252', message, 0, 1, 'no representable')

        extractor = YTDLPExtractor(log_fn=boom)
        creados, errores = extractor.generate_strm_files(
            _entries('Uno'), str(tmp_path), naming_template='{title}.strm',
            overwrite=True, log_fn=boom,
        )
        assert (creados, errores) == (1, 0)
        assert (tmp_path / 'Uno.strm').exists()

    def test_modo_directo_sin_resolver_deja_la_fuente(self, tmp_path, monkeypatch):
        extractor = YTDLPExtractor(log_fn=lambda _: None)
        monkeypatch.setattr(extractor, 'resolve_direct_url', lambda url: None)

        creados, errores = extractor.generate_strm_files(
            _entries('Uno'), str(tmp_path), naming_template='{title}.strm',
            overwrite=True, destination=DEST_JELLYFIN_DIRECT,
        )

        assert (creados, errores) == (1, 1)
        content = (tmp_path / 'Uno.strm').read_text(encoding='utf-8')
        assert f'{STRM_SOURCE_MARKER}https://www.youtube.com/watch?v=id1' in content
        assert _playable_lines(content) == ['https://www.youtube.com/watch?v=id1']

    def test_modo_directo_escribe_la_url_resuelta(self, tmp_path, monkeypatch):
        extractor = YTDLPExtractor(log_fn=lambda _: None)
        monkeypatch.setattr(extractor, 'resolve_direct_url', lambda url: 'https://rr1.googlevideo.com/v?itag=18')

        creados, errores = extractor.generate_strm_files(
            _entries('Uno'), str(tmp_path), naming_template='{title}.strm',
            overwrite=True, destination=DEST_JELLYFIN_DIRECT,
        )

        assert (creados, errores) == (1, 0)
        content = (tmp_path / 'Uno.strm').read_text(encoding='utf-8')
        assert _playable_lines(content) == ['https://rr1.googlevideo.com/v?itag=18']

    def test_destino_vlc_no_lleva_marcador(self, tmp_path):
        extractor = YTDLPExtractor()
        extractor.generate_strm_files(
            _entries('Uno'), str(tmp_path), naming_template='{title}.strm',
            overwrite=True, destination=DEST_VLC, log_fn=lambda _: None,
        )
        content = (tmp_path / 'Uno.strm').read_text(encoding='utf-8')
        assert STRM_SOURCE_MARKER not in content

    def test_omite_videos_no_disponibles(self, tmp_path):
        """Un video borrado o privado (sin título) no debe producir un .strm inservible."""
        entradas = _entries('Uno')
        entradas += [
            {'id': 'muerto', 'title': '', 'url': 'https://www.youtube.com/watch?v=muerto',
             'available': False},
        ]
        logs = []
        extractor = YTDLPExtractor()

        creados, errores = extractor.generate_strm_files(
            entradas, str(tmp_path), naming_template='{title}.strm',
            overwrite=True, log_fn=logs.append,
        )

        assert (creados, errores) == (1, 1)
        assert [p.name for p in tmp_path.iterdir()] == ['Uno.strm']
        assert any('muerto' in linea for linea in logs)

    def test_titulo_none_usa_nombre_generico(self, tmp_path):
        """Un título None no debe romper el nombre del archivo (regresión de la GUI)."""
        entradas = [
            {'id': 'abc123', 'title': None, 'url': 'https://www.youtube.com/watch?v=abc123'},
        ]
        extractor = YTDLPExtractor()

        creados, errores = extractor.generate_strm_files(
            entradas, str(tmp_path), naming_template='{title} ({id}).strm',
            overwrite=True, log_fn=lambda _: None,
        )

        assert (creados, errores) == (1, 0)
        assert [p.name for p in tmp_path.iterdir()] == ['video_abc123 (abc123).strm']


class TestDetectYouTubeUrlType:
    def test_video_con_list_se_trata_como_playlist(self):
        """Un enlace watch?v=...&list=... extrae la lista completa, no solo el video."""
        url = 'https://www.youtube.com/watch?v=78bp2IhQlcM&list=PLn7mp_V_R5K'
        assert detect_youtube_url_type(url) == 'playlist'

    def test_video_suelto(self):
        assert detect_youtube_url_type('https://www.youtube.com/watch?v=78bp2IhQlcM') == 'video'
        assert detect_youtube_url_type('https://youtu.be/78bp2IhQlcM') == 'video'

    def test_playlist_y_canal(self):
        assert detect_youtube_url_type('https://www.youtube.com/playlist?list=PL123') == 'playlist'
        assert detect_youtube_url_type('https://www.youtube.com/@unCanal') == 'channel'

    def test_url_ajena(self):
        assert detect_youtube_url_type('https://example.com/watch?v=x') == 'unknown'
