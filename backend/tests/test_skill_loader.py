import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path

from services.skill_loader import discover_skills, load_skill, load_skill_resource


def test_discover_skills():
    """Test skill discovery finds all skills."""
    skills = discover_skills()
    names = [s["name"] for s in skills]
    assert "local-search" in names
    assert "cloud-search" in names
    assert "convert" in names
    assert "llm-wiki" in names
    assert "smart-playlist" in names


def test_discover_skills_metadata():
    """Test discovered skills have name and description."""
    skills = discover_skills()
    for skill in skills:
        assert "name" in skill
        assert "description" in skill
        assert len(skill["name"]) > 0
        assert len(skill["description"]) > 0


def test_load_skill_local():
    """Test loading local-search skill."""
    full, body = load_skill("local-search")
    assert len(full) > 0
    assert len(body) > 0
    assert "name: local-search" in full
    assert "curl" in body


def test_load_skill_cloud():
    """Test loading cloud-search skill."""
    full, body = load_skill("cloud-search")
    assert len(full) > 0
    assert len(body) > 0
    assert "name: cloud-search" in full
    assert "bilibili" in body.lower() or "B站" in body


def test_load_skill_not_found():
    """Test loading non-existent skill returns empty."""
    full, body = load_skill("nonexistent")
    assert full == ""
    assert body == ""


def test_load_llm_wiki_skill():
    full, body = load_skill("llm-wiki")
    assert "name: llm-wiki" in full
    assert "backend/services/wiki_ingest.py" in body


def test_load_smart_playlist_skill():
    full, body = load_skill("smart-playlist")
    assert "name: smart-playlist" in full
    assert "create_smart_playlist" in body
    assert "needs_confirmation" in body


def test_load_skill_resource_is_confined_to_references():
    content = load_skill_resource("llm-wiki", "workflows.md")

    assert "Wiki" in content
    try:
        load_skill_resource("llm-wiki", "../SKILL.md")
    except ValueError as exc:
        assert str(exc) == "invalid_skill_resource"
    else:
        raise AssertionError("resource traversal should be rejected")


def test_discover_explicit_root_only(tmp_path: Path):
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: Test only\n---\n\n# Test\n",
        encoding="utf-8",
    )
    assert [item["name"] for item in discover_skills(tmp_path)] == ["test-skill"]
