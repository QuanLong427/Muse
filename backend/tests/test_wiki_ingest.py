import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.wiki_ingest import (
    _check_cache,
    _filter_untrusted_analysis,
    _save_raw_material,
    _update_cache,
    _validate_evidence_grounding,
    _validate_step1,
    ingest_song,
)
from services.wiki_manager import audit_wiki_quality, init_wiki


def _valid_analysis():
    evidence = [{"field": "title", "source": "raw.original_title", "value": "晴天"}]
    return {
        "song": {"title": "晴天", "overview": "来源标题为晴天。", "confidence": 1.0, "evidence": evidence},
        "artists": [
            {"name": "周杰伦", "aliases": [], "overview": "", "confidence": 0.9, "evidence": evidence}
        ],
        "albums": [],
        "genres": [],
        "connections": [
            {
                "from": "晴天",
                "to": "周杰伦",
                "type": "performed_by",
                "confidence": 0.9,
                "evidence": evidence,
            }
        ],
        "uncertainties": [],
    }


def test_validation_requires_evidence_and_confidence():
    analysis = _valid_analysis()
    assert _validate_step1(analysis)

    analysis["artists"][0]["evidence"] = []
    # Empty evidence is structurally valid, but it will be quarantined before graph writes.
    assert _validate_step1(analysis)

    analysis["artists"][0]["confidence"] = 1.2
    assert not _validate_step1(analysis)


def test_relation_must_reference_entities_in_same_result():
    analysis = _valid_analysis()
    analysis["connections"][0]["to"] = "不存在的歌手"
    assert not _validate_step1(analysis)


def test_evidence_quote_must_exist_in_raw_material():
    analysis = _valid_analysis()
    assert _validate_evidence_grounding(analysis, "original_title: 晴天")
    analysis["song"]["evidence"][0]["value"] = "模型编造的内容"
    assert not _validate_evidence_grounding(analysis, "original_title: 晴天")


def test_low_confidence_entities_do_not_enter_graph():
    analysis = _valid_analysis()
    analysis["artists"][0]["confidence"] = 0.6
    filtered = _filter_untrusted_analysis(analysis)
    assert filtered["artists"] == []
    assert filtered["connections"] == []
    assert "低置信度或弱证据" in filtered["uncertainties"][0]


def test_raw_record_is_portable_and_source_aware(tmp_path):
    absolute_audio_path = r"C:\\Users\\someone\\Music\\晴天-BV1xx411c7mD.mp3"
    raw_path = _save_raw_material(
        {
            "bvid": "BV1xx411c7mD",
            "title": "晴天",
            "artist": "某个UP主",
            "uploader": "某个UP主",
            "local_file_path": absolute_audio_path,
        },
        str(tmp_path),
    )
    content = open(raw_path, encoding="utf-8").read()
    assert absolute_audio_path not in content
    assert 'schema_version: "3.0"' in content
    assert 'source_url: "https://www.bilibili.com/video/BV1xx411c7mD"' in content
    assert 'uploader: "某个UP主"' in content


def test_cache_is_bound_to_pipeline_fingerprint(tmp_path):
    entity = tmp_path / "wiki/entities/songs/Test.md"
    entity.parent.mkdir(parents=True)
    entity.write_text("---\nverification_status: inferred\n---\n", encoding="utf-8")
    cache_path = tmp_path / ".wiki-cache.json"
    cache_path.write_text(json.dumps({"version": 2, "entries": {}}), encoding="utf-8")
    raw_path = _save_raw_material({"bvid": "BV1cache", "title": "Test"}, str(tmp_path))

    assert not _check_cache(raw_path, str(tmp_path))
    _update_cache(raw_path, "wiki/entities/songs/Test.md", str(tmp_path))
    assert _check_cache(raw_path, str(tmp_path))

    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    entry = next(iter(cache["entries"].values()))
    assert entry["schema_version"] == "3.0"
    assert entry["pipeline_version"] == "evidence-v1"


def test_wiki_audit_finds_legacy_and_provenance_problems(tmp_path):
    (tmp_path / "wiki/entities/songs").mkdir(parents=True)
    (tmp_path / "raw/songs").mkdir(parents=True)
    (tmp_path / ".wiki-schema.md").write_text("- version: 2.0\n", encoding="utf-8")
    (tmp_path / "wiki/entities/songs/Legacy.md").write_text(
        "---\ntags: [song]\n---\n# Legacy\n\n## Artists\n- [[Missing Artist]]\n",
        encoding="utf-8",
    )
    (tmp_path / "raw/songs/BV1legacy.md").write_text(
        "---\naudio_file_path: C:\\\\Music\\\\legacy.mp3\n---\n",
        encoding="utf-8",
    )

    audit = audit_wiki_quality(str(tmp_path))
    assert audit["legacy_entities"] == 1
    assert audit["legacy_raw_records"] == 1
    assert audit["issue_counts"]["missing_evidence"] == 1
    assert audit["issue_counts"]["broken_entity_link"] == 1
    assert audit["issue_counts"]["absolute_local_path"] == 1
    assert not audit["ready_for_verified_answers"]


def _write_v3_entity(path, verification_status):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
schema_version: "3.0"
entity_type: song
verification_status: {verification_status}
confidence: 0.9
---
# Test

## Evidence
- `raw.original_title`: Test
""",
        encoding="utf-8",
    )


def test_wiki_audit_does_not_treat_inferred_entities_as_verified(tmp_path):
    (tmp_path / ".wiki-schema.md").write_text("- version: 3.0\n", encoding="utf-8")
    _write_v3_entity(tmp_path / "wiki/entities/songs/Test.md", "inferred")

    audit = audit_wiki_quality(str(tmp_path))

    assert audit["structurally_valid"] is True
    assert audit["verification_counts"]["inferred"] == 1
    assert audit["ready_for_inferred_answers"] is True
    assert audit["ready_for_verified_answers"] is False


def test_wiki_audit_requires_at_least_one_verified_entity(tmp_path):
    (tmp_path / ".wiki-schema.md").write_text("- version: 3.0\n", encoding="utf-8")

    empty_audit = audit_wiki_quality(str(tmp_path))
    assert empty_audit["structurally_valid"] is True
    assert empty_audit["ready_for_verified_answers"] is False

    _write_v3_entity(tmp_path / "wiki/entities/songs/Verified.md", "verified")
    verified_audit = audit_wiki_quality(str(tmp_path))
    assert verified_audit["verification_counts"]["verified"] == 1
    assert verified_audit["ready_for_verified_answers"] is True


def test_ingest_never_renames_local_audio(tmp_path, monkeypatch):
    music_dir = tmp_path / "music"
    wiki_dir = tmp_path / "wiki"
    audio_path = music_dir / "album" / "旧歌手-测试歌曲--BV1stable.mp3"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"stable audio bytes")
    init_wiki(str(wiki_dir))
    monkeypatch.setattr("services.wiki_ingest.settings.MUSIC_DIR", str(music_dir))
    monkeypatch.setattr(
        "services.wiki_ingest._call_llm",
        lambda _prompt: json.dumps(_valid_analysis(), ensure_ascii=False),
    )

    result = ingest_song(
        {
            "title": "晴天",
            "artist": "周杰伦",
            "video_title": "周杰伦-晴天",
            "bvid": "BV1stable",
            "local_file_path": str(audio_path),
        },
        str(wiki_dir),
    )

    assert result["status"] == "ingested"
    assert audio_path.read_bytes() == b"stable audio bytes"
    assert not (audio_path.parent / "周杰伦-测试歌曲-BV1stable.mp3").exists()
    raw_content = (wiki_dir / "raw/songs/BV1stable.md").read_text(encoding="utf-8")
    assert 'local_relative_path: "album/旧歌手-测试歌曲--BV1stable.mp3"' in raw_content
