from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Mapping
from pathlib import Path
import json
import re

class YouTubeSearchError(RuntimeError):
    pass

@dataclass(frozen=True)
class VideoResult:
    video_id: str
    video_url: str
    title: str | None
    duration: int | None
    raw: dict[str,Any]=field(repr=False,default_factory=dict)
    search_query: str=''
    search_result_index: int=0
    search_results_fetched: int=0

LYRICS_WORD_RE = re.compile(r"\blyric(?:s|al)?\b", re.IGNORECASE)


def build_search_query(title: str, album: str | None) -> str:
    return " ".join(x for x in (str(title or '').strip(), str(album or '').strip(), 'video song hd') if x).strip()


def _parse_json(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise YouTubeSearchError('yt-dlp returned no JSON output')
    try:
        value = json.loads(text)
        if isinstance(value, dict): return value
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
            if isinstance(value, dict): return value
        except json.JSONDecodeError:
            continue
    raise YouTubeSearchError('Could not parse yt-dlp JSON output')


class YouTubeFinder:
    """Phase-1 style YouTube video finder with the user's deterministic rule.

    Search exactly: title + album + video song hd.
    Ignore any result title containing lyric, lyrics, or lyrical.
    Select the first remaining result. Duration is never inspected or scored.
    """
    def __init__(self, config: Mapping[str, Any], project_root: str | Path) -> None:
        self.config = config
        self.project_root = Path(project_root)

    def find_visual(self, title, album, duration_ms=None, artist=None):
        del duration_ms, artist
        query = build_search_query(title, album)
        if not query:
            return None
        search_cfg = self.config.get('youtube_video_search', {}) or {}
        count = max(1, min(50, int(search_cfg.get('results_to_fetch', 20))))
        try:
            import yt_dlp
            opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'extract_flat': True,
                'ignoreerrors': True,
                'noplaylist': False,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f'ytsearch{count}:{query}', download=False)
        except Exception as exc:
            raise YouTubeSearchError(f'YouTube search failed: {type(exc).__name__}: {exc}') from exc

        entries = (info or {}).get('entries') or []
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                continue
            title0 = str(entry.get('title') or '')
            if LYRICS_WORD_RE.search(title0):
                continue
            video_id = entry.get('id')
            if not video_id:
                continue
            video_id = str(video_id)
            url = str(entry.get('webpage_url') or entry.get('url') or f'https://www.youtube.com/watch?v={video_id}')
            duration = entry.get('duration')
            try: duration_s = int(round(float(duration))) if duration is not None else None
            except (TypeError, ValueError): duration_s = None
            return VideoResult(
                video_id=video_id,
                video_url=url,
                title=title0 or None,
                duration=duration_s,
                raw=dict(entry),
                search_query=query,
                search_result_index=index,
                search_results_fetched=len(entries),
            )
        return None
