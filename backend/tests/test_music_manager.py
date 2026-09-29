import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.music_manager import parse_name


def test_parse_name_full_format():
    """Test parsing 'Title-Author-YYYY-MM-DD_BVxxxxx.mp3' format."""
    result = parse_name("晴天-周杰伦-2025-01-15_BV1xx411c7mD")
    assert result["title"] == "晴天"
    assert result["author"] == "周杰伦"
    assert result["date"] == "2025-01-15"
    assert result["bvid"] == "BV1xx411c7mD"


def test_parse_name_without_bvid():
    """Test parsing filename without bvid."""
    result = parse_name("晴天-周杰伦-2025-01-15")
    assert result["title"] == "晴天"
    assert result["author"] == "周杰伦"
    assert result["date"] == "2025-01-15"
    assert result["bvid"] is None


def test_parse_name_minimal():
    """Test parsing minimal filename without date."""
    result = parse_name("song")
    assert result["title"] == "song"
    assert result["author"] == ""
    assert result["date"] == ""
    assert result["bvid"] is None


def test_parse_name_three_parts_with_date():
    """Test parsing with exactly 3 date parts (no author)."""
    result = parse_name("晴天-2025-01-15")
    assert result["title"] == "晴天"
    assert result["author"] == ""
    assert result["date"] == "2025-01-15"
    assert result["bvid"] is None


def test_parse_name_complex_title():
    """Test parsing with complex title containing hyphens."""
    result = parse_name("等你下课-官方MV-周杰伦-2025-01-15_BV1xxxxx")
    assert result["title"] == "等你下课-官方MV"
    assert result["author"] == "周杰伦"
    assert result["date"] == "2025-01-15"
    assert result["bvid"] == "BV1xxxxx"


def test_parse_name_with_spaces():
    """Test parsing with space-separated bvid."""
    result = parse_name("晴天-周杰伦-2025-01-15 BV1xx411c7mD")
    assert result["title"] == "晴天"
    assert result["author"] == "周杰伦"
    assert result["date"] == "2025-01-15"
    assert result["bvid"] == "BV1xx411c7mD"


def test_parse_name_year_out_of_range():
    """Test that year outside 1990-2030 is not parsed as date."""
    result = parse_name("song-1980-01-15")
    assert result["title"] == "song-1980-01-15"
    assert result["author"] == ""
    assert result["date"] == ""


def test_parse_name_canonical_downloaded_file():
    """Canonical Artist-Title-BVID files expose artist and title separately."""
    result = parse_name("周杰伦-晴天-BV1xx411c7mD")
    assert result == {
        "title": "晴天",
        "author": "周杰伦",
        "date": "",
        "bvid": "BV1xx411c7mD",
    }


def test_parse_name_legacy_double_dash_before_bvid():
    """Files created by the old rename bug remain readable."""
    result = parse_name("周杰伦-晴天--BV1xx411c7mD")
    assert result["title"] == "晴天"
    assert result["author"] == "周杰伦"
    assert result["bvid"] == "BV1xx411c7mD"
