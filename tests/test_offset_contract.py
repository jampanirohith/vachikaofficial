from pathlib import Path


def test_only_mms_lyric_offset_implementation_is_packaged():
    assert Path("src/video/lyric_offset.py").is_file()
    assert not Path("src/video/synchronization.py").exists()
    code = Path("src/orchestration/pipeline.py").read_text(encoding="utf-8")
    assert "calculate_offset_from_anchors" in code
    assert "find_reference_lyric_anchor" in code
    assert "find_youtube_lyric_anchor" in code
    assert "find_offset(" not in code
    assert "energy_match_used" not in code or "False" in code


def test_offset_schema_contains_word_evidence_not_energy_match_scores():
    schema = Path("database/songs_schema.sql").read_text(encoding="utf-8")
    assert "youtube_offset_evidence" in schema
    assert "youtube_offset_candidates" not in schema
    assert "waveform_score" not in schema
    assert "energy_score" not in schema
