from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from .utils import atomic_write_bytes, canonical_json_bytes, self_hash_json, sha256_file


class JSONManager:
    PHASE2_HASH_PATH = ("phase2", "outputs", "final_json_sha256")

    def load(self, path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON_INVALID: {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"JSON_INVALID: root must be an object: {path}")
        return data

    @staticmethod
    def clone(data: Mapping[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(dict(data))

    @staticmethod
    def _deep_merge(target: dict[str, Any], patch: Mapping[str, Any]) -> None:
        for key, value in patch.items():
            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, Mapping)
            ):
                JSONManager._deep_merge(target[key], value)
            else:
                target[key] = copy.deepcopy(value)

    def add_phase2(self, original: Mapping[str, Any], phase2: Mapping[str, Any]) -> dict[str, Any]:
        result = self.clone(original)
        existing = result.get("phase2")
        if not isinstance(existing, dict):
            existing = {}
            result["phase2"] = existing
        self._deep_merge(existing, phase2)
        return result

    def set_content_hash(self, data: dict[str, Any]) -> None:
        phase2 = data.setdefault("phase2", {})
        outputs = phase2.setdefault("outputs", {})
        outputs["final_json_sha256"] = None
        outputs["final_json_sha256"] = self_hash_json(data, self.PHASE2_HASH_PATH)

    def write(self, path: Path, data: dict[str, Any]) -> str:
        self.set_content_hash(data)
        text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        atomic_write_bytes(path, text.encode("utf-8"))
        return sha256_file(path)

    def validate_content_hash(self, data: Mapping[str, Any]) -> bool:
        try:
            recorded = data["phase2"]["outputs"]["final_json_sha256"]
        except (KeyError, TypeError):
            return False
        return recorded == self_hash_json(data, self.PHASE2_HASH_PATH)

    @staticmethod
    def non_phase2_equal(original: Mapping[str, Any], final: Mapping[str, Any]) -> bool:
        a = copy.deepcopy(dict(original))
        b = copy.deepcopy(dict(final))
        a.pop("phase2", None)
        b.pop("phase2", None)
        return canonical_json_bytes(a) == canonical_json_bytes(b)
