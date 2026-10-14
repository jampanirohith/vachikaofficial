"""Resolve a Telugu-capable font and report its actual embedded family name."""
from __future__ import annotations

import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

from src.rendercore.utils import Phase3Error

OFFICIAL_API = "https://api.github.com/repos/EkType/Baloo2/releases/latest"
MIN_FONT_BYTES = 10_000


def font_family_name(path: str | Path, fallback: str | None = None) -> str:
    """Read the family name (name ID 1) from a TrueType/OpenType font.

    The ASS ``Fontname`` field needs the internal family name, not a filename or
    style name. Parsing the SFNT name table avoids recording "Suravaram-Regular"
    when the actual installed family is "Suravaram". Font collections and malformed
    files fall back to an explicitly supplied value or the file stem.
    """
    font_path = Path(path)
    try:
        data = font_path.read_bytes()
        if len(data) < 12:
            raise ValueError("font file is too small")
        table_count = struct.unpack_from(">H", data, 4)[0]
        name_offset = name_length = None
        for index in range(table_count):
            record_offset = 12 + index * 16
            if record_offset + 16 > len(data):
                break
            tag, _checksum, offset, length = struct.unpack_from(">4sIII", data, record_offset)
            if tag == b"name":
                name_offset, name_length = offset, length
                break
        if name_offset is None or name_length is None or name_offset + name_length > len(data):
            raise ValueError("font has no valid name table")
        table = data[name_offset:name_offset + name_length]
        _format, count, strings_offset = struct.unpack_from(">HHH", table, 0)
        candidates: list[tuple[int, int, str]] = []
        for index in range(count):
            record_offset = 6 + index * 12
            if record_offset + 12 > len(table):
                break
            platform, _encoding, language, name_id, length, offset = struct.unpack_from(">HHHHHH", table, record_offset)
            if name_id not in (1, 16):
                continue
            start, end = strings_offset + offset, strings_offset + offset + length
            if start < 0 or end > len(table):
                continue
            raw = table[start:end]
            try:
                text = raw.decode("utf-16-be") if platform in (0, 3) else raw.decode("mac_roman")
            except (UnicodeDecodeError, LookupError):
                continue
            text = " ".join(text.replace("\x00", "").split())
            if not text:
                continue
            # Prefer standard family (ID 1), then typographic family (ID 16),
            # then English (0x0409), then neutral-language records.
            rank = (0 if name_id == 1 else 1)
            lang_rank = 0 if language in (0, 0x0409) else 1
            candidates.append((rank, lang_rank, text))
        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1], len(item[2])))
            return candidates[0][2]
    except (OSError, ValueError, struct.error):
        pass
    return str(fallback or font_path.stem.replace("-", " ").replace("_", " ")).strip()


def _system_font(family: str) -> Path | None:
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}", family],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            check=False,
        )
        candidate = Path(result.stdout.strip())
        if result.returncode == 0 and result.stdout.strip() and candidate.is_file():
            return candidate
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def ensure_telugu_font(
    font_dir: Path,
    filename: str = "Suravaram-Regular.ttf",
    configured_path: str | None = None,
    preferred_family: str | None = None,
) -> Path:
    """Resolve a font deterministically, failing rather than silently lying.

    An explicitly configured path is authoritative: if it is missing, the user gets
    a clear error instead of an unrelated fallback font being silently substituted.
    Otherwise, prefer the configured bundled filename, then a matching system font,
    and only attempt a network download for the Baloo asset that the project knows how
    to retrieve.
    """
    font_dir = Path(font_dir)
    desired_family = str(preferred_family or Path(filename).stem).strip()

    if configured_path:
        explicit = Path(configured_path).expanduser().resolve()
        if not explicit.is_file() or explicit.stat().st_size < MIN_FONT_BYTES:
            raise Phase3Error("LYRIC_RENDER_FAILED", f"Configured font_path is missing or invalid: {explicit}")
        return explicit

    target = font_dir / filename
    if target.is_file() and target.stat().st_size >= MIN_FONT_BYTES:
        return target

    system = _system_font(desired_family)
    if system and system.stat().st_size >= MIN_FONT_BYTES:
        return system

    # The released ZIP is only known to provide the Baloo font. Do not download it
    # when the selected font is Suravaram, because that would misreport the family.
    if filename == "BalooTammudu2-ExtraBold.ttf":
        try:
            import requests
            response = requests.get(
                OFFICIAL_API,
                timeout=30,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "vachika-font-installer"},
            )
            response.raise_for_status()
            release = response.json()
            asset = next(
                (item for item in release.get("assets", []) if str(item.get("name", "")).lower().endswith(".zip")),
                None,
            )
            if not asset:
                raise RuntimeError("No Baloo2 release ZIP asset found")
            with tempfile.TemporaryDirectory() as temp_dir:
                archive_path = Path(temp_dir) / "baloo.zip"
                download = requests.get(asset["browser_download_url"], timeout=120)
                download.raise_for_status()
                archive_path.write_bytes(download.content)
                with zipfile.ZipFile(archive_path) as archive:
                    matches = [name for name in archive.namelist() if Path(name).name == filename]
                    if not matches:
                        raise RuntimeError(f"{filename} not found in release asset")
                    font_dir.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(matches[0]))
            return target
        except Exception as exc:
            # Keep going and try a system Noto Telugu font before failing.
            download_error = f"; Baloo download failed: {type(exc).__name__}: {exc}"
    else:
        download_error = ""

    for family in ("Noto Sans Telugu", "Noto Sans Telugu UI", "Suravaram", "Baloo Tammudu 2"):
        candidate = _system_font(family)
        if candidate and candidate.stat().st_size >= MIN_FONT_BYTES:
            return candidate

    raise Phase3Error(
        "LYRIC_RENDER_FAILED",
        f"No usable Telugu font found for {desired_family!r}; check assets/fonts or configure lyrics.font_path"
        f"{download_error}",
    )


def describe_font(path: str | Path, configured_family: str | None = None) -> dict[str, str]:
    """Return the resolved font file, its embedded family and provenance."""
    font_path = Path(path).resolve()
    family = font_family_name(font_path, fallback=configured_family)
    source = "font_sfnt_name_table"
    if not family:
        family = str(configured_family or font_path.stem)
        source = "configured_fallback"
    return {
        "path": str(font_path),
        "family": family,
        "configured_family": str(configured_family or ""),
        "family_source": source,
        "filename": font_path.name,
    }
