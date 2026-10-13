from pathlib import Path

from src.rendercore.ass_generator import ASSGenerator


class FakeConfig:
    values = {
        "lyrics.font_name": "Arial",
        "lyrics.font_size": 58,
        "lyrics.font_bold": True,
        "lyrics.outline": 3,
        "lyrics.shadow": 4,
        "lyrics.center_x": 540,
        "lyrics.center_y": 960,
        "lyrics.max_chars_single_line": 30,
        "lyrics.min_scale_x": 70,
        "lyrics.current_color": "#FFFFFF",
        "lyrics.word_fade_in_ms": 80,
        "lyrics.word_fade_out_ms": 450,
    }

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_word_by_word_ass_events_fade_and_do_not_overlap(tmp_path: Path):
    generator = ASSGenerator(FakeConfig(), tmp_path, None)
    lines = [
        {
            "start_ms": 1000,
            "end_ms": 3000,
            "text": "one two three",
            "display_text": "one two three",
            "words": [
                {"text": "one", "start_ms": 1000, "end_ms": 1400},
                {"text": "two", "start_ms": 1500, "end_ms": 1900},
                {"text": "three", "start_ms": 2050, "end_ms": 2600},
            ],
        }
    ]

    ass = generator.generate(lines, [], 0, 4000, tmp_path / "font.ttf")
    text = ass.read_text(encoding="utf-8")
    events = [line for line in text.splitlines() if line.startswith("Dialogue:")]

    assert len(events) == 3
    assert "}one" in events[0]
    assert "}two" in events[1]
    assert "}three" in events[2]
    assert "\\fad(80,420)" in events[0]
    assert "\\fad(80,450)" in events[1]
    assert "\\fad(80,450)" in events[2]

    # First word fades out exactly until the second word starts.
    assert "0:00:01.00,0:00:01.50" in events[0]
    # Second word ends at the third word's start, so they never overlap.
    assert "0:00:01.50,0:00:02.05" in events[1]


def test_word_fade_is_configurable(tmp_path: Path):
    cfg = FakeConfig()
    cfg.values["lyrics.word_fade_out_ms"] = 250
    generator = ASSGenerator(cfg, tmp_path, None)
    lines = [
        {
            "start_ms": 0,
            "end_ms": 2000,
            "text": "hello",
            "display_text": "hello",
            "words": [{"text": "hello", "start_ms": 0, "end_ms": 500}],
        }
    ]

    ass = generator.generate(lines, [], 0, 2000, tmp_path / "font.ttf")
    text = ass.read_text(encoding="utf-8")
    assert "\\fad(80,250)" in text
