from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import json
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse
import requests

from PIL import Image, ImageOps


class ArtworkError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtworkCandidate:
    path: Path
    width: int
    height: int
    source_url: str | None

    @property
    def is_square(self) -> bool:
        return self.width == self.height

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def source_is_googleusercontent(self) -> bool:
        if not self.source_url:
            return False
        host = (urlparse(self.source_url).hostname or "").lower()
        return "googleusercontent.com" in host


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta":
            return
        data = {k.lower(): v or "" for k, v in attrs}
        if data:
            self.meta.append(data)


def _load_cookie_header(cookies_file: Path | None, *, host: str = "music.youtube.com") -> str | None:
    if not cookies_file or not cookies_file.exists() or cookies_file.stat().st_size == 0:
        return None
    parts: list[str] = []
    host = host.lower().split(":", 1)[0]
    try:
        for raw in cookies_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) < 7:
                continue
            domain = cols[0].lstrip(".").lower()
            if domain and not (host == domain or host.endswith("." + domain)):
                continue
            parts.append(f"{cols[5]}={cols[6]}")
    except OSError:
        return None
    return "; ".join(parts) if parts else None


def fetch_ytmusic_og_image(
    ytm_url: str,
    output_path: str | Path,
    *,
    cookies_file: str | Path | None = None,
    timeout_seconds: int = 30,
    user_agent: str | None = None,
) -> tuple[Path | None, str | None, int | None, int | None]:
    """Fetch the YouTube Music page's og:image as a direct album-art fallback.

    yt-dlp's thumbnail list can omit YouTube Music's square googleusercontent artwork.
    The page itself may still expose the square image through OpenGraph metadata. This
    function is deliberately an HTTP fallback, not another yt-dlp invocation.
    """
    output_path = Path(output_path)
    headers = {
        "User-Agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
    }
    cookie_header = _load_cookie_header(Path(cookies_file) if cookies_file else None)
    if cookie_header:
        headers["Cookie"] = cookie_header
    try:
        response = requests.get(ytm_url, headers=headers, timeout=timeout_seconds, allow_redirects=True)
        response.raise_for_status()
    except requests.RequestException:
        return None, None, None, None

    parser = _MetaParser()
    try:
        parser.feed(response.text)
    except Exception:
        return None, None, None, None

    og_image = None
    width = height = None
    for meta in parser.meta:
        prop = (meta.get("property") or meta.get("name") or "").lower()
        content = meta.get("content")
        if prop == "og:image" and content:
            og_image = content
        elif prop == "og:image:width" and content and content.isdigit():
            width = int(content)
        elif prop == "og:image:height" and content and content.isdigit():
            height = int(content)
    if not og_image or not og_image.startswith(("http://", "https://")):
        return None, None, width, height

    try:
        image_response = requests.get(og_image, headers={"User-Agent": headers["User-Agent"], "Referer": ytm_url}, timeout=timeout_seconds)
        image_response.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_response.content)
        with Image.open(output_path) as image:
            actual_width, actual_height = image.size
            image.verify()
        return output_path, og_image, actual_width, actual_height
    except (requests.RequestException, OSError, ValueError):
        output_path.unlink(missing_ok=True)
        return None, None, width, height


def _thumbnail_source_url_map(info_json: Path) -> dict[str, str]:
    try:
        data = json.loads(info_json.read_text(encoding="utf-8"))
    except Exception:
        return {}
    result: dict[str, str] = {}
    thumbnails = data.get("thumbnails")
    if not isinstance(thumbnails, list):
        return result
    for thumb in thumbnails:
        if not isinstance(thumb, Mapping):
            continue
        thumb_id = thumb.get("id")
        url = thumb.get("url")
        if thumb_id is not None and isinstance(url, str) and url:
            result[str(thumb_id)] = url
    return result


def _source_url_for_file(path: Path, source_map: Mapping[str, str]) -> str | None:
    stem = path.stem
    if stem.startswith("master."):
        thumb_id = stem[len("master.") :]
        if thumb_id in source_map:
            return source_map[thumb_id]
    return source_map.get(stem)


def discover_thumbnail_candidates(temp_dir: str | Path, *, info_json: str | Path | None = None) -> list[ArtworkCandidate]:
    temp_dir = Path(temp_dir)
    source_map = _thumbnail_source_url_map(Path(info_json)) if info_json else {}
    candidates: list[ArtworkCandidate] = []
    for path in sorted(temp_dir.glob("master.*")):
        if not path.is_file() or path.name.endswith(".info.json") or path.suffix.lower() in {".mp3", ".json"}:
            continue
        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                width, height = image.size
                if width <= 0 or height <= 0:
                    continue
                image.verify()
        except Exception:
            continue
        candidates.append(ArtworkCandidate(path, width, height, _source_url_for_file(path, source_map)))
    return candidates


def choose_best_artwork(candidates: Iterable[ArtworkCandidate]) -> ArtworkCandidate:
    candidates = list(candidates)
    if not candidates:
        raise ArtworkError("yt-dlp did not produce any usable thumbnail image")
    squares = [candidate for candidate in candidates if candidate.is_square]
    pool = squares or candidates
    return max(pool, key=lambda candidate: (candidate.area, int(candidate.source_is_googleusercontent), min(candidate.width, candidate.height)))


def normalize_artwork(
    source: str | Path,
    destination: str | Path,
    *,
    force_square: bool = True,
    max_dimension: int = 1200,
    jpeg_quality: int = 95,
) -> Path:
    source = Path(source)
    destination = Path(destination)
    if not source.exists():
        raise ArtworkError(f"Artwork source does not exist: {source}")
    if max_dimension < 1 or not 1 <= jpeg_quality <= 100:
        raise ArtworkError("Invalid artwork normalization settings")
    try:
        with Image.open(source) as original:
            image = ImageOps.exif_transpose(original).copy()
    except Exception as exc:
        raise ArtworkError(f"Could not open artwork source: {source}") from exc

    if force_square and image.width != image.height:
        side = min(image.width, image.height)
        image = ImageOps.fit(image, (side, side), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    if max(image.width, image.height) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    if image.mode != "RGB":
        if "A" in image.getbands():
            background = Image.new("RGB", image.size, "white")
            background.paste(image.convert("RGB"), mask=image.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        image.save(destination, format="JPEG", quality=jpeg_quality, subsampling=0, optimize=True, progressive=True)
    except Exception as exc:
        raise ArtworkError(f"Could not write normalized artwork: {destination}") from exc
    finally:
        image.close()
    try:
        with Image.open(destination) as check:
            if check.width != check.height:
                raise ArtworkError(f"Normalized artwork is not square: {check.width}x{check.height}")
            check.verify()
    except ArtworkError:
        raise
    except Exception as exc:
        raise ArtworkError(f"Normalized artwork cannot be reopened: {destination}") from exc
    return destination


def prepare_best_artwork(
    *,
    temp_dir: str | Path,
    info_json: str | Path,
    output_path: str | Path,
    force_square: bool = True,
    max_dimension: int = 1200,
    jpeg_quality: int = 95,
    ytm_url: str | None = None,
    cookies_file: str | Path | None = None,
    og_image_timeout_seconds: int = 30,
) -> tuple[Path, str | None, int | None, int | None]:
    """Collect YTM page art plus all yt-dlp thumbnails, then choose the best square candidate."""
    output_path = Path(output_path)
    candidates = discover_thumbnail_candidates(temp_dir, info_json=info_json)

    # The YTMusic page can expose a higher quality square album image through OpenGraph.
    # It is collected as another candidate rather than being blindly preferred: a non-square
    # page image must not beat a valid square album-art candidate from the acquisition.
    if ytm_url:
        og_path = Path(temp_dir) / "ytm_og_image"
        fetched, source_url, width, height = fetch_ytmusic_og_image(
            ytm_url,
            og_path,
            cookies_file=cookies_file,
            timeout_seconds=og_image_timeout_seconds,
        )
        if fetched and source_url and width and height:
            candidates.append(ArtworkCandidate(fetched, width, height, source_url))

    selected = choose_best_artwork(candidates)
    result = normalize_artwork(
        selected.path,
        output_path,
        force_square=force_square,
        max_dimension=max_dimension,
        jpeg_quality=jpeg_quality,
    )
    return result, selected.source_url, selected.width, selected.height
