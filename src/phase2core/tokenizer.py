from __future__ import annotations

from typing import Any

from .types import SourceWord, TokenRef


class ReferenceTokenizer:
    """Builds a reference token sequence while retaining a word-to-token map."""

    def build(self, words: list[SourceWord], tokenizer: Any) -> list[TokenRef]:
        token_refs: list[TokenRef] = []
        cursor = 0
        unk_id = getattr(tokenizer, "unk_token_id", None)
        pad_id = getattr(tokenizer, "pad_token_id", None)
        bos_id = getattr(tokenizer, "bos_token_id", None)
        eos_id = getattr(tokenizer, "eos_token_id", None)

        for word in words:
            word.token_start = cursor
            word.token_end = cursor
            if not word.normalized:
                word.supported = False
                continue
            try:
                ids = tokenizer(
                    word.normalized,
                    add_special_tokens=False,
                    return_attention_mask=False,
                )["input_ids"]
            except TypeError:
                ids = tokenizer.encode(word.normalized, add_special_tokens=False)
            if isinstance(ids, list) and ids and isinstance(ids[0], list):
                ids = ids[0]
            ids = [int(x) for x in ids]
            bad = any(x == unk_id for x in ids if unk_id is not None)
            ids = [x for x in ids if x not in {pad_id, bos_id, eos_id}]
            if not ids or bad:
                word.supported = False
                word.token_start = cursor
                word.token_end = cursor
                continue
            for token_id in ids:
                token_refs.append(TokenRef(
                    token_index=cursor,
                    token_id=token_id,
                    line_index=word.line_index,
                    word_index=word.word_index,
                ))
                cursor += 1
            word.token_end = cursor
        return token_refs
