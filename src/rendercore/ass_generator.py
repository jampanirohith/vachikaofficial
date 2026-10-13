from __future__ import annotations

from pathlib import Path
import re

_INLINE_TS = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]")


def ass_time(ms: int) -> str:
    total_cs = max(0, int(ms) // 10)
    h = total_cs // 360000
    total_cs %= 360000
    m = total_cs // 6000
    total_cs %= 6000
    s = total_cs // 100
    cs = total_cs % 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_color(hexcolor: str) -> str:
    c = str(hexcolor).lstrip("#")
    if len(c) != 6:
        c = "FFFFFF"
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return f"&H00{b:02X}{g:02X}{r:02X}&"


def esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def clean_lrc_text(text: str) -> str:
    text = _INLINE_TS.sub("", str(text))
    return " ".join(text.split())


def _tokenize(text: str) -> list[str]:
    return [x for x in re.split(r"\s+", clean_lrc_text(text).strip()) if x]


class ASSGenerator:
    """Generate the lyric overlay used by the final Reel.

    The project already has word-level timestamps from alignment.  This
    renderer deliberately consumes those timestamps directly: one ASS event
    is generated per word, rather than keeping an entire lyric line on screen.
    Each word fades out before the next word starts.
    """

    def __init__(self, cfg, work_dir, logger):
        self.cfg = cfg
        self.work = Path(work_dir)
        self.logger = logger

    def _header(self) -> str:
        font_name = self.cfg.get("lyrics.font_name", "Suravaram-Regular")
        size = int(self.cfg.get("lyrics.font_size", 58))
        bold = 1 if self.cfg.get("lyrics.font_bold", True) else 0
        outline = int(self.cfg.get("lyrics.outline", 3))
        shadow = int(self.cfg.get("lyrics.shadow", 4))
        return "\n".join(
            [
                "[Script Info]",
                "ScriptType: v4.00+",
                "PlayResX: 1080",
                "PlayResY: 1920",
                "WrapStyle: 2",
                "ScaledBorderAndShadow: yes",
                "",
                "[V4+ Styles]",
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
                f"Style: Karaoke,{font_name},{size},&H00FFFFFF,&H00FFFFFF,&H00141414,&H80000000,{bold},0,0,0,100,100,0,0,1,{outline},{shadow},5,60,60,0,1",
                "",
                "[Events]",
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
                "",
            ]
        )

    def _word_timings(self, line: dict, canonical_words: list[dict]) -> list[dict]:
        lrc_words = list(line.get("words") or [])
        if lrc_words:
            return [
                {
                    "text": str(w["text"]).strip(),
                    "start_ms": int(w["start_ms"]),
                    "end_ms": int(w["end_ms"]),
                }
                for w in lrc_words
                if str(w.get("text", "")).strip()
            ]

        start = int(line["start_ms"])
        end = int(line["end_ms"])
        ws = [
            w
            for w in canonical_words
            if w.get("start_ms") is not None
            and w.get("end_ms") is not None
            and int(w["start_ms"]) >= start
            and int(w["start_ms"]) < end
        ]
        tokens = _tokenize(line.get("text", ""))
        if not ws:
            return []

        out = []
        for i, w in enumerate(ws[: len(tokens)]):
            out.append(
                {
                    "text": tokens[i],
                    "start_ms": int(w["start_ms"]),
                    "end_ms": int(w["end_ms"]),
                }
            )
        return out

    def _word_event_text(self, text: str, cx: int, cy: int, scale_x: int) -> str:
        outline = int(self.cfg.get("lyrics.outline", 3))
        shadow = int(self.cfg.get("lyrics.shadow", 4))
        color = ass_color(self.cfg.get("lyrics.current_color", "#FFFFFF"))
        return (
            f"{{\\an5\\pos({cx},{cy})\\fscx{scale_x}"
            f"\\bord{outline}\\shad{shadow}\\c{color}}}"
            f"{esc(text)}"
        )

    def _add_faded_word(
        self,
        events: list[str],
        text: str,
        start_ms: int,
        end_ms: int,
        next_start_ms: int | None,
        reel_start_ms: int,
        reel_end_ms: int,
        cx: int,
        cy: int,
        scale_x: int,
    ) -> bool:
        """Append one word event with a slow fade-out.

        The event is clipped at the next word's start so two words are never
        simultaneously visible. The fade-out therefore uses as much of the
        available gap as possible, up to the configured fade duration.
        """
        absolute_start = max(int(start_ms), int(reel_start_ms))
        spoken_end = max(absolute_start + 10, int(end_ms))
        absolute_limit = int(next_start_ms) if next_start_ms is not None else int(reel_end_ms)
        absolute_end = min(spoken_end + max(0, int(self.cfg.get("lyrics.word_fade_out_ms", 450))), absolute_limit, int(reel_end_ms))

        if absolute_end <= absolute_start:
            return False

        local_start = absolute_start - int(reel_start_ms)
        local_end = absolute_end - int(reel_start_ms)
        duration = local_end - local_start

        fade_in = min(max(0, int(self.cfg.get("lyrics.word_fade_in_ms", 80))), max(0, duration // 3))
        configured_fade_out = max(0, int(self.cfg.get("lyrics.word_fade_out_ms", 450)))
        fade_out = min(configured_fade_out, max(0, duration - fade_in))

        tags = self._word_event_text(text, cx, cy, scale_x)
        if fade_in or fade_out:
            brace = tags.find("}")
            tags = tags[:brace] + f"\\fad({fade_in},{fade_out})" + tags[brace:]

        events.append(
            f"Dialogue: 0,{ass_time(local_start)},{ass_time(local_end)},Karaoke,,0,0,0,,{tags}"
        )
        return True

    def generate(
        self,
        lrc_lines: list[dict],
        canonical_words: list[dict],
        reel_start_ms: int,
        reel_end_ms: int,
        font_path: Path,
        tracking: dict | None = None,
    ) -> Path:
        out = self.work / "lyrics" / "lyrics.ass"
        out.parent.mkdir(parents=True, exist_ok=True)

        header = self._header()
        events: list[str] = []
        cx = int(self.cfg.get("lyrics.center_x", 540))
        cy = int(self.cfg.get("lyrics.center_y", 960))
        max_chars = int(self.cfg.get("lyrics.max_chars_single_line", 30))
        min_scale = int(self.cfg.get("lyrics.min_scale_x", 70))

        used_lines = []
        for li, line in enumerate(lrc_lines):
            line_start = int(line["start_ms"])
            line_end = int(line.get("end_ms", line_start + 4000))
            if line_end <= reel_start_ms or line_start >= reel_end_ms:
                continue

            text = str(line.get("display_text") or clean_lrc_text(line.get("text", "")))
            if not text:
                continue

            words = self._word_timings(line, canonical_words)
            words = [
                w
                for w in words
                if int(w["end_ms"]) > reel_start_ms
                and int(w["start_ms"]) < reel_end_ms
                and str(w.get("text", "")).strip()
            ]

            tokens = _tokenize(text)
            if words and len(words) != len(tokens):
                words = [
                    dict(words[i], text=tokens[i])
                    for i in range(min(len(words), len(tokens)))
                ]
            else:
                words = [dict(w) for w in words]

            # A single centered word needs no line-width scaling, but keep the
            # existing limit so unusually long single tokens remain readable.
            longest_word = max((len(str(w["text"])) for w in words), default=len(text))
            scale_x = 100 if longest_word <= max_chars else max(
                min_scale, int(100 * max_chars / max(1, longest_word))
            )

            rendered_word_count = 0
            if words:
                for i, word in enumerate(words):
                    next_start = int(words[i + 1]["start_ms"]) if i + 1 < len(words) else None
                    if self._add_faded_word(
                        events,
                        str(word["text"]).strip(),
                        int(word["start_ms"]),
                        int(word["end_ms"]),
                        next_start,
                        reel_start_ms,
                        reel_end_ms,
                        cx,
                        cy,
                        scale_x,
                    ):
                        rendered_word_count += 1
            else:
                # Preserve graceful behavior for a lyric line that genuinely
                # has no word-level timing. It gets one slow-fading line event.
                local_start = max(0, line_start - reel_start_ms)
                local_end = min(reel_end_ms - reel_start_ms, line_end - reel_start_ms)
                if local_end > local_start:
                    duration = local_end - local_start
                    fade_in = min(max(0, int(self.cfg.get("lyrics.word_fade_in_ms", 80))), duration // 3)
                    fade_out = min(max(0, int(self.cfg.get("lyrics.word_fade_out_ms", 450))), max(0, duration - fade_in))
                    text_tag = self._word_event_text(text, cx, cy, scale_x)
                    brace = text_tag.find("}")
                    text_tag = text_tag[:brace] + f"\\fad({fade_in},{fade_out})" + text_tag[brace:]
                    events.append(
                        f"Dialogue: 0,{ass_time(local_start)},{ass_time(local_end)},Karaoke,,0,0,0,,{text_tag}"
                    )

            used_lines.append(
                {
                    "line_index": li,
                    "text": text,
                    "local_start_ms": max(0, line_start - reel_start_ms),
                    "local_end_ms": min(reel_end_ms - reel_start_ms, line_end - reel_start_ms),
                    "word_count": len(words),
                    "rendered_word_count": rendered_word_count,
                }
            )

        out.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
        return out
