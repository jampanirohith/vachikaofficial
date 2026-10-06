from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import sha256_text


@dataclass(slots=True)
class Config:
    raw: dict[str, Any]
    base_dir: Path
    config_path: Path
    config_hash: str

    def get(self, dotted: str, default: Any = None) -> Any:
        value: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def path(self, dotted: str) -> Path:
        value = self.get(dotted)
        if value is None:
            raise KeyError(dotted)
        return (self.base_dir / str(value)).resolve()

    def validate(self) -> None:
        required_paths = [
            "paths.original_dir", "paths.final_dir", "paths.temp_dir",
            "paths.db_dir", "paths.models_dir",
        ]
        for key in required_paths:
            self.path(key)
        if int(self.get("audio.sample_rate", 16000)) != 16000:
            raise ValueError("audio.sample_rate must be 16000 for this pipeline")
        if self.get("models.language_iso3", "tel") != "tel":
            raise ValueError("This project is configured specifically for Telugu MMS adapter 'tel'")
        if float(self.get("chunking.min_seconds", 4.0)) <= 0:
            raise ValueError("chunking.min_seconds must be > 0")
        if float(self.get("chunking.max_seconds", 20.0)) < float(self.get("chunking.min_seconds", 4.0)):
            raise ValueError("chunking.max_seconds must be >= min_seconds")


def load_config(path: str | Path) -> Config:
    config_path = Path(path).resolve()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config.json must contain a JSON object")
    normalized = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    config = Config(raw, config_path.parent, config_path, sha256_text(normalized))
    config.validate()
    return config
