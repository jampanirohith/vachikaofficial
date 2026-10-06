from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .types import LRCLine, SourceWord

DEFAULT_ABBREVIATIONS = {
    "Sr.": "శ్రీ",
    "Smt.": "శ్రీమతి",
    "Sri.": "శ్రీ",
}

TELUGU_RANGE = (0x0C00, 0x0C7F)
WORD_RE = re.compile(r"\S+", re.UNICODE)


@dataclass(slots=True)
class NormalizationConfig:
    version: str = "1.1.0"
    keep_latin: bool = True
    keep_digits: bool = True
    convert_numbers_to_telugu: bool = False
    expand_abbreviations: bool = True
    remove_punctuation: bool = True


ONES = {
    0: "సున్నా", 1: "ఒకటి", 2: "రెండు", 3: "మూడు", 4: "నాలుగు", 5: "ఐదు",
    6: "ఆరు", 7: "ఏడు", 8: "ఎనిమిది", 9: "తొమ్మిది", 10: "పది",
    11: "పదకొండు", 12: "పన్నెండు", 13: "పదమూడు", 14: "పద్నాలుగు", 15: "పదిహేను",
    16: "పదహారు", 17: "పదిహేడు", 18: "పద్దెనిమిది", 19: "పందొమ్మిది",
}
TENS = {
    20: "ఇరవై", 30: "ముప్పై", 40: "నలభై", 50: "యాభై", 60: "అరవై",
    70: "డెబ్బై", 80: "ఎనభై", 90: "తొంభై",
}


def number_to_telugu(value: int) -> str | None:
    if value < 0 or value > 9999:
        return None
    if value < 20:
        return ONES[value]
    if value < 100:
        base = TENS[(value // 10) * 10]
        return base if value % 10 == 0 else f"{base}{ONES[value % 10]}"
    if value < 1000:
        hundreds = value // 100
        remainder = value % 100
        prefix = "వంద" if hundreds == 1 else f"{ONES[hundreds]}వంద"
        return prefix if remainder == 0 else f"{prefix} {number_to_telugu(remainder)}"
    thousands = value // 1000
    remainder = value % 1000
    prefix = "వెయ్యి" if thousands == 1 else f"{number_to_telugu(thousands)}వేలు"
    return prefix if remainder == 0 else f"{prefix} {number_to_telugu(remainder)}"


def _is_telugu(ch: str) -> bool:
    cp = ord(ch)
    return TELUGU_RANGE[0] <= cp <= TELUGU_RANGE[1]


def _retain_char(ch: str, cfg: NormalizationConfig) -> bool:
    if _is_telugu(ch):
        return True
    if ch.isspace():
        return True
    if cfg.keep_latin and ("A" <= ch <= "Z" or "a" <= ch <= "z"):
        return True
    if cfg.keep_digits and ch.isdigit():
        return True
    return False


class TeluguNormalizer:
    def __init__(self, config: NormalizationConfig, abbreviations: dict[str, str] | None = None):
        self.config = config
        self.abbreviations = dict(DEFAULT_ABBREVIATIONS)
        if abbreviations:
            self.abbreviations.update(abbreviations)

    def normalize_word(self, original: str) -> tuple[str, list[dict[str, Any]]]:
        ops: list[dict[str, Any]] = []
        text = unicodedata.normalize("NFC", original)
        if text != original:
            ops.append({"op": "unicode_nfc"})

        if self.config.expand_abbreviations and text in self.abbreviations:
            replacement = self.abbreviations[text]
            if replacement != text:
                ops.append({"op": "abbreviation", "from": text, "to": replacement})
                text = replacement

        if self.config.convert_numbers_to_telugu:
            numeric_match = re.fullmatch(r"[^\d\w\u0C00-\u0C7F]*(\d+)[^\d\w\u0C00-\u0C7F]*", text, flags=re.UNICODE)
            if numeric_match:
                digits = numeric_match.group(1)
                converted = number_to_telugu(int(digits))
                if converted is not None:
                    ops.append({"op": "number_to_telugu", "from": digits, "to": converted})
                    text = converted

        filtered = "".join(ch for ch in text if _retain_char(ch, self.config))
        if self.config.remove_punctuation and filtered != text:
            ops.append({"op": "script_filter_or_punctuation"})
        filtered = " ".join(filtered.split())
        if filtered != text:
            ops.append({"op": "whitespace_normalize"})
        return filtered, ops

    def apply(self, lines: list[LRCLine]) -> None:
        for line in lines:
            if line.kind != "lyric":
                continue
            line.words = []
            for idx, match in enumerate(WORD_RE.finditer(line.text)):
                original = match.group(0)
                normalized, ops = self.normalize_word(original)
                supported = bool(normalized.strip())
                line.words.append(SourceWord(
                    line_index=line.line_index,
                    word_index=idx,
                    original=original,
                    normalized=normalized,
                    supported=supported,
                    normalization_ops=ops,
                ))
