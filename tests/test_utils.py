# -*- coding: utf-8 -*-
"""Tests for utility functions."""
import pytest
from media_renamer.utils import (
    sanitize_filename,
    clean_query_string,
    clean_movie_query,
    natural_sort_key,
)


class TestSanitizeFilename:
    def test_basic(self):
        assert sanitize_filename("Movie Title") == "Movie Title"
    
    def test_invalid_chars(self):
        assert sanitize_filename('Movie<>:"/\\|?*Title') == "MovieTitle"
    
    def test_whitespace(self):
        assert sanitize_filename("  Movie   Title  ") == "Movie Title"
    
    def test_dots(self):
        assert sanitize_filename(".Movie Title.") == "Movie Title"
    
    def test_empty(self):
        assert sanitize_filename("") == ""
        assert sanitize_filename(None) == ""


class TestCleanQueryString:
    def test_basic(self):
        title, year = clean_query_string("The Matrix 1999")
        assert title == "The Matrix 1999"  # Original function keeps year in title
        assert year == "1999"
    
    def test_with_brackets(self):
        title, year = clean_query_string("The Matrix (1999) [BluRay]")
        # Original function removes bracketed content including year in parentheses
        assert title == "The Matrix"
        assert year == "1999"
    
    def test_with_tags(self):
        title, year = clean_query_string("The Matrix 1999 1080p BluRay x264")
        assert title == "The Matrix 1999"  # Year kept, tags removed
        assert year == "1999"
    
    def test_no_year(self):
        title, year = clean_query_string("The Matrix")
        assert title == "The Matrix"
        assert year is None
    
    def test_spanish_tags(self):
        title, year = clean_query_string("Matrix 1999 Latino Español 1080p")
        assert title == "Matrix 1999"  # Year kept
        assert year == "1999"


class TestCleanMovieQuery:
    def test_basic_movie(self):
        title, year = clean_movie_query("The.Matrix.1999.1080p.BluRay.x264")
        assert title == "The Matrix"
        assert year == "1999"
    
    def test_with_release_group(self):
        title, year = clean_movie_query("Inception.2010.1080p.BluRay.x264.YIFY")
        assert title == "Inception"
        assert year == "2010"
    
    def test_with_audio_codec(self):
        title, year = clean_movie_query("Dune.2021.2160p.WEB-DL.x265.10bit.HDR.DDP5.1.Atmos")
        assert title == "Dune"
        assert year == "2021"
    
    def test_with_remux_tags(self):
        title, year = clean_movie_query("Avatar.2009.REMUX.1080p.BluRay.AVC.DTS-HD.MA.5.1")
        assert title == "Avatar"
        assert year == "2009"
    
    def test_with_brackets(self):
        title, year = clean_movie_query("[The.Matrix.1999.1080p]")
        assert title == "The Matrix"
        assert year == "1999"
    
    def test_with_parentheses(self):
        title, year = clean_movie_query("The Matrix (1999) {1080p}")
        assert title == "The Matrix"
        assert year == "1999"
    
    def test_complex_real_world(self):
        # Real-world filename patterns
        test_cases = [
            ("John.Wick.Chapter.4.2023.2160p.WEB-DL.x265.10bit.HDR.DDP5.1.Atmos-NTb", "John Wick Chapter 4", "2023"),
            ("Oppenheimer.2023.IMAX.1080p.BluRay.x264.DTS-HD.MA.5.1-FRAMESiLO", "Oppenheimer IMAX", "2023"),
            ("Spider-Man.Across.the.Spider-Verse.2023.4K.2160p.WEB-DL.x265.10bit.HDR.DDP5.1.Atmos", "Spider Man Across the Spider Verse", "2023"),
            ("The.Batman.2022.1080p.BluRay.x264.DTS-HD.MA.5.1-PLAYNOW", "The Batman", "2022"),
        ]
        for filename, expected_title, expected_year in test_cases:
            title, year = clean_movie_query(filename)
            assert title == expected_title, f"Failed for {filename}: got '{title}', expected '{expected_title}'"
            assert year == expected_year, f"Failed year for {filename}: got '{year}', expected '{expected_year}'"
    
    def test_no_year(self):
        title, year = clean_movie_query("Unknown Movie 1080p BluRay")
        assert title == "Unknown Movie"
        assert year is None
    
    def test_3d_movies(self):
        title, year = clean_movie_query("Avatar.2009.3D.1080p.BluRay.HSBS.x264")
        assert title == "Avatar"
        assert year == "2009"
    
    def test_hdr_dv(self):
        title, year = clean_movie_query("Dune.2021.HDR10.2160p.WEB-DL.x265")
        assert title == "Dune"
        assert year == "2021"
        
        title, year = clean_movie_query("Dune.2021.Dolby.Vision.2160p.WEB-DL.x265")
        assert title == "Dune"
        assert year == "2021"


class TestNaturalSortKey:
    def test_numbers(self):
        assert natural_sort_key("ep2") < natural_sort_key("ep10")
        assert natural_sort_key("S01E02") < natural_sort_key("S01E10")
        assert natural_sort_key("100PRMM") > natural_sort_key("99PRMM")
    
    def test_mixed(self):
        items = ["ep10", "ep2", "ep1", "ep20", "ep3"]
        sorted_items = sorted(items, key=natural_sort_key)
        assert sorted_items == ["ep1", "ep2", "ep3", "ep10", "ep20"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])