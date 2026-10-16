"""Versioned song-manifest construction, artifact hashing and serialization."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.common.hashing import sha256_file, stable_json_hash
from src.common.utils import atomic_write_json, atomic_write_text

MANIFEST_SCHEMA_VERSION = 2
MANIFEST_RECORD_SCHEMA = "unified_song_master_v2"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def build(source_json: Any, **namespaces: Any) -> dict[str, Any]:
    """Build a canonical song record without mutating the phase-2 source JSON.

    Provider payloads stay in named namespaces. The ``unified_pipeline.manifest``
    block is the stable, versioned summary used by diagnostics and downstream tools.
    """
    data = copy.deepcopy(source_json) if isinstance(source_json, dict) else {}
    unified = data.setdefault("unified_pipeline", {})
    if not isinstance(unified, dict):
        unified = {}
        data["unified_pipeline"] = unified
    for key, value in namespaces.items():
        unified[key] = value

    provenance = _as_dict(unified.get("provenance"))
    manifest = _as_dict(unified.get("manifest"))
    manifest.setdefault("created_at_utc", _utc_now())
    manifest.update({
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "record_type": "song_processing_manifest",
        "record_schema": MANIFEST_RECORD_SCHEMA,
        "pipeline_version": str(provenance.get("pipeline_version") or data.get("project_version") or "unknown"),
        "config_sha256": provenance.get("config_sha256"),
        "source_sha256": provenance.get("source_mp3_sha256"),
        "algorithm_versions": {
            "youtube_offset": _as_dict(_as_dict(unified.get("youtube")).get("offset")).get("method_version"),
            "lyrics_strategy": _as_dict(unified.get("lyrics")).get("strategy_version"),
            "audio_8d": _as_dict(unified.get("audio_8d")).get("algorithm_version"),
            "demucs_model": _as_dict(unified.get("demucs")).get("model"),
        },
    })
    unified["schema_version"] = MANIFEST_SCHEMA_VERSION
    unified["record_schema"] = MANIFEST_RECORD_SCHEMA
    unified["manifest"] = manifest
    return data



def validate(data: dict[str, Any]) -> None:
    """Validate the required v2 manifest contract without an optional dependency."""
    unified = _as_dict(data.get("unified_pipeline"))
    if unified.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"Manifest schema_version must be {MANIFEST_SCHEMA_VERSION}")
    if unified.get("record_schema") != MANIFEST_RECORD_SCHEMA:
        raise ValueError(f"Manifest record_schema must be {MANIFEST_RECORD_SCHEMA!r}")
    manifest = _as_dict(unified.get("manifest"))
    required = (
        "created_at_utc", "schema_version", "record_type", "record_schema",
        "pipeline_version", "algorithm_versions", "artifacts",
        "validation_status", "content_fingerprint",
    )
    missing = [key for key in required if key not in manifest]
    if missing:
        raise ValueError(f"Manifest v2 is missing required fields: {', '.join(missing)}")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("Manifest schema_version does not match the supported schema")
    if manifest.get("record_type") != "song_processing_manifest":
        raise ValueError("Manifest record_type must be 'song_processing_manifest'")
    if manifest.get("record_schema") != MANIFEST_RECORD_SCHEMA:
        raise ValueError("Manifest record_schema does not match the supported schema")
    if not isinstance(manifest.get("algorithm_versions"), dict):
        raise ValueError("Manifest algorithm_versions must be an object")
    if not isinstance(manifest.get("artifacts"), dict):
        raise ValueError("Manifest artifacts must be an object")
    fingerprint = str(manifest.get("content_fingerprint") or "")
    if len(fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in fingerprint.lower()):
        raise ValueError("Manifest content_fingerprint must be a SHA-256 hex digest")
    for name, artifact in manifest["artifacts"].items():
        if not isinstance(artifact, dict):
            raise ValueError(f"Manifest artifact {name!r} must be an object")
        for key in ("path", "sha256", "exists", "self_reference"):
            if key not in artifact:
                raise ValueError(f"Manifest artifact {name!r} is missing {key!r}")
        if not isinstance(artifact["path"], str) or not artifact["path"].strip():
            raise ValueError(f"Manifest artifact {name!r} has an empty path")
        if not isinstance(artifact["exists"], bool) or not isinstance(artifact["self_reference"], bool):
            raise ValueError(f"Manifest artifact {name!r} must use boolean exists/self_reference flags")
        digest = artifact.get("sha256")
        if artifact["exists"] and not artifact["self_reference"]:
            if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest.lower()):
                raise ValueError(f"Manifest artifact {name!r} has no valid SHA-256 for an existing output")
        if artifact["self_reference"] and not artifact.get("hash_location"):
            raise ValueError(f"Manifest self-reference {name!r} must declare hash_location")

def _artifact_manifest(
    path: Path,
    data: dict[str, Any],
    project_root: Path | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    unified = _as_dict(data.get("unified_pipeline"))
    outputs = _as_dict(unified.get("outputs"))
    hashes = _as_dict(unified.get("hashes"))
    artifact_rows: dict[str, Any] = {}
    normalized_hashes = dict(hashes)

    for name, raw_path in outputs.items():
        if not isinstance(raw_path, (str, Path)) or not str(raw_path).strip():
            continue
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute() and project_root is not None:
            candidate = project_root / candidate
        try:
            is_self = candidate.resolve() == path.resolve()
        except OSError:
            is_self = False

        if is_self:
            normalized_hashes.pop(name, None)
            artifact_rows[str(name)] = {
                "path": str(raw_path),
                "sha256": None,
                "size_bytes": None,
                "exists": True,
                "self_reference": True,
                "hash_location": str(path.name + ".sha256"),
            }
            continue

        if candidate.is_file():
            digest = sha256_file(candidate)
            normalized_hashes[str(name)] = digest
            artifact_rows[str(name)] = {
                "path": str(raw_path),
                "sha256": digest,
                "size_bytes": candidate.stat().st_size,
                "exists": True,
                "self_reference": False,
            }
        else:
            normalized_hashes.pop(str(name), None)
            artifact_rows[str(name)] = {
                "path": str(raw_path),
                "sha256": None,
                "size_bytes": None,
                "exists": False,
                "self_reference": False,
            }

    return normalized_hashes, artifact_rows


def write(path: str | Path, data: dict[str, Any], *, project_root: str | Path | None = None) -> str:
    """Normalize and atomically write a manifest, refresh hashes and write self-hash.

    A JSON file cannot contain its own SHA-256 without a recursive definition. Its
    digest is therefore written to a sibling ``.json.sha256`` sidecar; every other
    extant path listed in ``unified_pipeline.outputs`` is hashed in-place here.
    Missing outputs remain explicitly represented as missing until a later stage
    updates this manifest.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    root = Path(project_root).resolve() if project_root is not None else None
    normalized = build(data)
    unified = _as_dict(normalized.get("unified_pipeline"))
    hashes, artifacts = _artifact_manifest(target, normalized, root)
    unified["hashes"] = hashes
    manifest = _as_dict(unified.get("manifest"))
    manifest["artifacts"] = artifacts
    manifest["validation_status"] = str(unified.get("status") or normalized.get("status") or "in_progress")
    manifest["content_fingerprint"] = stable_json_hash({
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source_sha256": manifest.get("source_sha256"),
        "config_sha256": manifest.get("config_sha256"),
        "artifacts": {
            key: {"path": value.get("path"), "sha256": value.get("sha256")}
            for key, value in sorted(artifacts.items())
        },
        "algorithm_versions": manifest.get("algorithm_versions", {}),
    })
    unified["manifest"] = manifest
    unified["schema_version"] = MANIFEST_SCHEMA_VERSION
    unified["record_schema"] = MANIFEST_RECORD_SCHEMA
    validate(normalized)

    atomic_write_json(target, normalized)
    digest = sha256_file(target)
    sidecar = target.with_suffix(target.suffix + ".sha256")
    atomic_write_text(sidecar, f"{digest}  {target.name}\n")
    return digest
