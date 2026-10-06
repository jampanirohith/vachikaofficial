from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

SAFE_FILENAME_RE = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json_bytes(data: Any, *, omit_paths: set[tuple[str, ...]] | None = None) -> bytes:
    omit_paths = omit_paths or set()

    def strip(value: Any, path: tuple[str, ...] = ()) -> Any:
        if path in omit_paths:
            return None
        if isinstance(value, dict):
            return {str(k): strip(v, path + (str(k),)) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
        if isinstance(value, list):
            return [strip(v, path + (str(i),)) for i, v in enumerate(value)]
        return value

    return json.dumps(
        strip(data),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def self_hash_json(
    data: Mapping[str, Any],
    field_path: tuple[str, ...] = ("phase2", "outputs", "final_json_sha256"),
) -> str:
    payload = json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))
    target: Any = payload
    for key in field_path[:-1]:
        if not isinstance(target, dict):
            break
        target = target.setdefault(key, {})
    if isinstance(target, dict):
        target[field_path[-1]] = None
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.tmp-{os.getpid()}")
    try:
        with src.open("rb") as r, tmp.open("wb") as w:
            shutil.copyfileobj(r, w, length=1024 * 1024)
            w.flush()
            os.fsync(w.fileno())
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def safe_name(name: str) -> str:
    return SAFE_FILENAME_RE.sub("_", name).strip().rstrip(".") or "untitled"


def percentile(values: Iterable[float], p: float) -> float | None:
    vals = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    rank = (len(vals) - 1) * (p / 100.0)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return vals[lo]
    frac = rank - lo
    return vals[lo] * (1 - frac) + vals[hi] * frac


def merge_intervals(intervals: Iterable[tuple[int, int]], max_gap_ms: int = 0) -> list[tuple[int, int]]:
    cleaned = sorted((int(a), int(b)) for a, b in intervals if b > a)
    merged: list[tuple[int, int]] = []
    for start, end in cleaned:
        if not merged or start > merged[-1][1] + max_gap_ms:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def interval_overlap_ms(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def require_binary(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"Required executable not found on PATH: {name}")
    return found


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def json_dump_file(path: Path, data: Any) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False, allow_nan=False) + "\n"
    atomic_write_bytes(path, text.encode("utf-8"))
