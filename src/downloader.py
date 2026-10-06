from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence

from PIL import Image
from mutagen.mp3 import MP3

from .artwork import ArtworkError, prepare_best_artwork


class AcquisitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class AcquisitionResult:
    temp_dir: Path
    master_mp3: Path
    info_json: Path
    artwork_jpg: Path
    duration_seconds: int
    artwork_source_url: str | None
    artwork_width: int | None
    artwork_height: int | None


class YTDlpInvoker:
    def __init__(self, config: Mapping[str, Any], project_root: str | Path) -> None:
        self.config = config
        self.project_root = Path(project_root)

    def _resolve_config_path(self, value: str | None) -> Path | None:
        if not value:
            return None
        p = Path(value)
        return p if p.is_absolute() else self.project_root / p

    def executable_prefix(self) -> list[str]:
        configured = str(self.config.get("yt_dlp_binary", "yt-dlp"))
        binary = Path(configured)
        if binary.is_absolute() or "/" in configured or "\\" in configured:
            return [str(binary)]
        if shutil.which(configured):
            return [configured]
        return [sys.executable, "-m", "yt_dlp"]

    def runtime_args(self) -> list[str]:
        runtime = str(self.config.get("js_runtime", "auto")).lower()
        runtime_path = self.config.get("js_runtime_path")
        if runtime == "none":
            return []
        if runtime == "auto":
            if shutil.which("deno"):
                runtime = "deno"
            elif shutil.which("node"):
                runtime = "node"
            elif shutil.which("qjs"):
                runtime = "quickjs"
            else:
                return []
        if runtime not in {"deno", "node", "quickjs"}:
            raise AcquisitionError(f"Unsupported js_runtime: {runtime}")
        return ["--js-runtimes", f"{runtime}:{runtime_path}"] if runtime_path else ["--js-runtimes", runtime]

    def shared_args(self) -> list[str]:
        args: list[str] = []
        cookies = self._resolve_config_path(self.config.get("cookies_file"))
        if cookies and cookies.exists() and cookies.stat().st_size > 0:
            args += ["--cookies", str(cookies)]
        ffmpeg = self._resolve_config_path(self.config.get("ffmpeg_location"))
        if ffmpeg:
            args += ["--ffmpeg-location", str(ffmpeg)]
        args += self.runtime_args()
        args += ["--socket-timeout", str(int(self.config.get("socket_timeout_seconds", 30)))]
        return args

    def run(self, extra_args: Sequence[str], *, cwd: str | Path | None = None, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
        command = self.executable_prefix() + self.shared_args() + list(extra_args)
        completed = subprocess.run(command, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout, check=False)
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="")
        return completed


class Downloader:
    def __init__(self, config: Mapping[str, Any], project_root: str | Path) -> None:
        self.config = config
        self.project_root = Path(project_root)
        self.invoker = YTDlpInvoker(config, project_root)

    def build_acquisition_command(self, *, temp_dir: Path, ytm_url: str) -> list[str]:
        output_template = temp_dir / "master.%(ext)s"
        download = self.config["download"]
        args = [
            "--no-warnings", "--no-playlist", "--no-mtime", "-x",
            "--audio-format", str(download["audio_format"]),
            "--audio-quality", str(download["audio_quality"]),
            "--write-info-json", "--write-thumbnail",
            "--convert-thumbnails", str(download["convert_thumbnail"]),
        ]
        # One source cover is sufficient for the final MP3. Downloading every thumbnail
        # variant caused large, unnecessary acquisition overhead in the field run.
        if bool(download.get("write_all_thumbnails", False)):
            args.append("--write-all-thumbnails")
        args += ["-o", str(output_template), ytm_url]
        return args

    def acquire(self, *, temp_dir: Path, ytm_url: str) -> AcquisitionResult:
        if not ytm_url:
            raise AcquisitionError("Cannot acquire a playlist entry without a YTMusic URL")
        temp_dir.mkdir(parents=True, exist_ok=True)
        result = self.invoker.run(self.build_acquisition_command(temp_dir=temp_dir, ytm_url=ytm_url), cwd=self.project_root, timeout=int(self.config.get("download_timeout_seconds", 3600)))
        if result.returncode != 0:
            raise AcquisitionError(f"yt-dlp acquisition failed with exit code {result.returncode} for {ytm_url}")
        master_mp3 = temp_dir / "master.mp3"
        info_json = temp_dir / "master.info.json"
        artwork_jpg = temp_dir / "master.jpg"
        if not master_mp3.exists():
            raise AcquisitionError(f"Missing acquisition artifact: {master_mp3}")
        if not info_json.exists():
            raise AcquisitionError(f"Missing acquisition artifact: {info_json}")

        try:
            artwork_jpg, artwork_source_url, artwork_width, artwork_height = prepare_best_artwork(
                temp_dir=temp_dir,
                info_json=info_json,
                output_path=artwork_jpg,
                force_square=bool(self.config.get("artwork", {}).get("force_square", True)),
                max_dimension=int(self.config.get("artwork", {}).get("max_dimension", 1200)),
                jpeg_quality=int(self.config.get("artwork", {}).get("jpeg_quality", 95)),
                ytm_url=ytm_url if bool(self.config.get("artwork", {}).get("og_image_enabled", True)) else None,
                cookies_file=self.config.get("cookies_file"),
                og_image_timeout_seconds=int(self.config.get("artwork", {}).get("og_image_timeout_seconds", 30)),
            )
        except ArtworkError as exc:
            raise AcquisitionError(f"Could not prepare album artwork: {exc}") from exc

        self.validate_source_artifacts(master_mp3, info_json, artwork_jpg)
        try:
            duration = int(round(MP3(master_mp3).info.length))
        except Exception as exc:
            raise AcquisitionError(f"Could not read source MP3 duration: {master_mp3}") from exc
        return AcquisitionResult(temp_dir, master_mp3, info_json, artwork_jpg, duration, artwork_source_url, artwork_width, artwork_height)

    @staticmethod
    def validate_source_artifacts(master_mp3: Path, info_json: Path, artwork_jpg: Path) -> None:
        if master_mp3.stat().st_size <= 0:
            raise AcquisitionError(f"Source MP3 is empty: {master_mp3}")
        try:
            json.loads(info_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AcquisitionError(f"Invalid source info JSON: {info_json}") from exc
        try:
            audio = MP3(master_mp3)
            if audio.info.length <= 0:
                raise AcquisitionError(f"Source MP3 duration is invalid: {master_mp3}")
        except AcquisitionError:
            raise
        except Exception as exc:
            raise AcquisitionError(f"Source MP3 cannot be opened: {master_mp3}") from exc
        try:
            with Image.open(artwork_jpg) as image:
                image.verify()
        except Exception as exc:
            raise AcquisitionError(f"Artwork cannot be decoded: {artwork_jpg}") from exc
