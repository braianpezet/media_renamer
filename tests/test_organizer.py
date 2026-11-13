# -*- coding: utf-8 -*-
"""Tests for MediaOrganizer."""
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from media_renamer.organizer import MediaOrganizer


class MockMovieClient:
    """Mock TMDb movie client for testing."""
    
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.call_count = 0
    
    def search_movie(self, query, lang="es-MX", pick_result_fn=None, auto_select_year=None):
        self.call_count += 1
        # If auto_select_year is provided, use it to differentiate responses
        if auto_select_year and f"{query} {auto_select_year}" in self.responses:
            return self.responses[f"{query} {auto_select_year}"]
        return self.responses.get(query)


class TestMediaOrganizer:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)
    
    @pytest.fixture
    def organizer(self):
        mock_client = MockMovieClient()
        return MediaOrganizer(mock_client, mock_client)
    
    def test_plan_bulk_movies_basic(self, organizer, temp_dir):
        """Test basic bulk movie planning."""
        # Create test movie files
        (temp_dir / "Inception.2010.1080p.BluRay.x264.mkv").touch()
        (temp_dir / "The.Matrix.1999.720p.WEB-DL.mkv").touch()
        (temp_dir / "Dune.2021.2160p.WEB-DL.x265.mkv").touch()
        
        # Configure mock responses
        organizer.movies.responses = {
            "Inception": {"title": "Inception", "year": "2010"},
            "The Matrix": {"title": "The Matrix", "year": "1999"},
            "Dune": {"title": "Dune", "year": "2021"},
        }
        
        plan = organizer.plan_bulk_movies(str(temp_dir))
        
        assert plan is not None
        assert plan["type"] == "bulk_movie"
        assert len(plan["moves"]) == 3
        
        # Check destination names
        dest_names = [Path(m["dest"]).name for m in plan["moves"]]
        assert "Inception (2010).mkv" in dest_names
        assert "The Matrix (1999).mkv" in dest_names
        assert "Dune (2021).mkv" in dest_names
        
        # All should go to the same directory (temp_dir)
        for move in plan["moves"]:
            assert Path(move["dest"]).parent == temp_dir
    
    def test_plan_bulk_movies_with_subtitles(self, organizer, temp_dir):
        """Test bulk movie planning with companion subtitles."""
        (temp_dir / "Inception.2010.1080p.mkv").touch()
        (temp_dir / "Inception.2010.1080p.spa.srt").touch()
        (temp_dir / "Inception.2010.1080p.eng.srt").touch()
        
        organizer.movies.responses = {
            "Inception": {"title": "Inception", "year": "2010"},
        }
        
        plan = organizer.plan_bulk_movies(str(temp_dir))
        
        assert plan is not None
        assert len(plan["moves"]) == 3  # video + 2 subtitles
        
        dest_names = [Path(m["dest"]).name for m in plan["moves"]]
        assert "Inception (2010).mkv" in dest_names
        assert "Inception (2010).spa.srt" in dest_names
        assert "Inception (2010).eng.srt" in dest_names
    
    def test_plan_bulk_movies_collision_handling(self, organizer, temp_dir):
        """Test collision handling when two files map to same canonical name."""
        # Two different source files that would resolve to same movie
        (temp_dir / "Inception.2010.1080p.mkv").touch()
        (temp_dir / "Inception.2010.720p.mkv").touch()
        
        organizer.movies.responses = {
            "Inception": {"title": "Inception", "year": "2010"},
        }
        
        plan = organizer.plan_bulk_movies(str(temp_dir))
        
        assert plan is not None
        assert len(plan["moves"]) == 2
        
        dest_names = [Path(m["dest"]).name for m in plan["moves"]]
        # One should be the original, one should have (1) suffix
        assert "Inception (2010).mkv" in dest_names
        assert "Inception (2010) (1).mkv" in dest_names
    
    def test_plan_bulk_movies_no_match(self, organizer, temp_dir):
        """Test handling of files with no TMDb match."""
        (temp_dir / "Unknown.Movie.2020.1080p.mkv").touch()
        (temp_dir / "Inception.2010.1080p.mkv").touch()
        
        organizer.movies.responses = {
            "Inception": {"title": "Inception", "year": "2010"},
            # No response for "Unknown Movie"
        }
        
        plan = organizer.plan_bulk_movies(str(temp_dir))
        
        assert plan is not None
        assert len(plan["moves"]) == 1  # Only Inception matched
        assert Path(plan["moves"][0]["dest"]).name == "Inception (2010).mkv"
    
    def test_plan_bulk_movies_empty_directory(self, organizer, temp_dir):
        """Test empty directory returns None."""
        plan = organizer.plan_bulk_movies(str(temp_dir))
        assert plan is None
    
    def test_plan_bulk_movies_custom_target_dir(self, organizer, temp_dir):
        """Test custom target directory."""
        target_dir = temp_dir / "output"
        target_dir.mkdir()
        
        (temp_dir / "Inception.2010.1080p.mkv").touch()
        organizer.movies.responses = {
            "Inception": {"title": "Inception", "year": "2010"},
        }
        
        plan = organizer.plan_bulk_movies(str(temp_dir), target_base_dir=str(target_dir))
        
        assert plan is not None
        assert plan["dest_main_folder"] == str(target_dir)
        assert Path(plan["moves"][0]["dest"]).parent == target_dir
    
    def test_plan_bulk_movies_ignores_non_video(self, organizer, temp_dir):
        """Test that non-video files are ignored."""
        (temp_dir / "Inception.2010.mkv").touch()
        (temp_dir / "README.txt").touch()
        (temp_dir / "cover.jpg").touch()
        
        organizer.movies.responses = {
            "Inception": {"title": "Inception", "year": "2010"},
        }
        
        plan = organizer.plan_bulk_movies(str(temp_dir))
        
        assert plan is not None
        assert len(plan["moves"]) == 1
        assert Path(plan["moves"][0]["dest"]).name == "Inception (2010).mkv"

    def test_plan_bulk_movies_auto_select_by_year(self, organizer, temp_dir):
        """Test auto-selection by year from filename."""
        # Two movies with similar names but different years
        (temp_dir / "Batman.2008.1080p.mkv").touch()
        (temp_dir / "Batman.2022.1080p.mkv").touch()
        
        # Mock returns different results based on the cleaned query + year
        organizer.movies.responses = {
            "Batman": {"title": "Batman", "year": "2008"},  # The Dark Knight (2008)
            "Batman 2022": {"title": "The Batman", "year": "2022"},  # The Batman (2022)
        }
        
        plan = organizer.plan_bulk_movies(str(temp_dir))
        
        assert plan is not None
        assert len(plan["moves"]) == 2
        
        dest_names = [Path(m["dest"]).name for m in plan["moves"]]
        # Should match by year from filename
        assert "Batman (2008).mkv" in dest_names
        assert "The Batman (2022).mkv" in dest_names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])