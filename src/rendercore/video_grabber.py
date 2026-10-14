from __future__ import annotations
import json
import time
from pathlib import Path
from src.rendercore.utils import Phase3Error, run_cmd, ffmpeg_has_encoder, nvenc_video_args
from src.rendercore.audio_io import probe
from src.common.hashing import sha256_file

class VideoGrabber:
    def __init__(self,cfg,work_dir,logger): self.cfg=cfg;self.work=Path(work_dir);self.logger=logger

    def download_section(self, video_source, start_ms, end_ms):
        """Download/cache the complete selected source video.

        yt-dlp's ordinary download call fetches the full source; it does not make a
        guarded clip just because caller timestamps were supplied. The returned media
        therefore always uses the video's original zero-based timeline. Reel trimming
        must use the absolute mapped hook start, never a clip-relative guard offset.
        """
        outdir = self.work / 'video'
        outdir.mkdir(parents=True, exist_ok=True)
        out = outdir / 'downloaded_source.mp4'
        meta = outdir / 'downloaded_source.request.json'
        video_url = str(video_source).strip()
        if video_url.startswith(('http://', 'https://')):
            selected_url = video_url
            if 'v=' in video_url:
                video_id = video_url.split('v=', 1)[1].split('&', 1)[0]
            else:
                video_id = video_url.rstrip('/').rsplit('/', 1)[-1]
        else:
            video_id = video_url
            selected_url = f'https://www.youtube.com/watch?v={video_id}'
        height = int(self.cfg.get('ytmusic.video_max_height', 1080))
        request = {
            'video_id': str(video_id),
            'source_url': selected_url,
            'download_mode': 'full-source-video-v2',
            'max_height': height,
        }
        requested_start = max(0, int(start_ms))
        requested_end = max(requested_start, int(end_ms))
        if requested_end <= requested_start:
            raise Phase3Error('VIDEO_SECTION_DOWNLOAD_FAILED', 'Invalid requested source-video time range')
        if out.exists() and out.stat().st_size > 10000 and meta.exists():
            try:
                old = json.loads(meta.read_text(encoding='utf-8'))
                duration = int(probe(out).get('duration_ms', 0))
                if (old.get('request') == request and duration >= requested_end - 250
                        and old.get('sha256') == sha256_file(out)):
                    return out
            except Exception:
                pass
        out.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)
        try:
            import yt_dlp
            ytdlp_cfg = dict(self.cfg.get('ytmusic.yt_dlp', {}) or {})
            download_timeout = int(ytdlp_cfg.get('download_timeout_seconds', self.cfg.get('ytmusic.download_timeout_seconds', 1800)))
            deadline = time.monotonic() + max(1, download_timeout)
            from src.common.ytdlp_utils import deadline_progress_hook
            opts = {
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'format': f'bestvideo[height<={height}]+bestaudio/best[height<={height}]/best',
                'merge_output_format': 'mp4',
                'outtmpl': str(out),
                'overwrites': True,
                'retries': int(ytdlp_cfg.get('retries', 3)),
                'fragment_retries': int(ytdlp_cfg.get('fragment_retries', 3)),
                'socket_timeout': int(ytdlp_cfg.get('socket_timeout_seconds', self.cfg.get('ytmusic.timeout_seconds', 60))),
                'progress_hooks': [deadline_progress_hook(yt_dlp, deadline, selected_url)],
            }
            cookie_file = ytdlp_cfg.get('cookie_file') or self.cfg.get('ytmusic.cookies_file')
            if cookie_file:
                cp = Path(cookie_file)
                cp = cp if cp.is_absolute() else self.cfg.root / cp
                if cp.exists():
                    opts['cookiefile'] = str(cp)
            browser = ytdlp_cfg.get('cookies_from_browser')
            if browser:
                opts['cookiesfrombrowser'] = (str(browser), None, None, None)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([selected_url])
        except Exception as exc:
            out.unlink(missing_ok=True)
            raise Phase3Error('VIDEO_SECTION_DOWNLOAD_FAILED', f'yt-dlp visual download failed: {type(exc).__name__}: {exc}') from exc
        info = probe(out)
        actual = int(info.get('duration_ms', 0))
        if actual <= 0 or actual < requested_end - 250:
            out.unlink(missing_ok=True)
            raise Phase3Error('VIDEO_SECTION_DOWNLOAD_FAILED', f'Selected source video does not cover requested range ending at {requested_end} ms; duration={actual} ms')
        request_payload = {
            'request': request,
            'download_timeout_seconds_configured': int(ytdlp_cfg.get('download_timeout_seconds', self.cfg.get('ytmusic.download_timeout_seconds', 1800))),
            'duration_ms': actual,
            'sha256': sha256_file(out),
            'downloaded_at_source_timeline': True,
        }
        tmp_meta = meta.with_name(meta.name + '.tmp')
        tmp_meta.write_text(json.dumps(request_payload, indent=2) + '\n', encoding='utf-8')
        tmp_meta.replace(meta)
        return out

    def _encode_trim(self,src,dst,duration_ms,codec,trim_start_ms=0):
        if codec=='h264_nvenc': codec_args=nvenc_video_args(self.cfg)
        else: codec_args=['-c:v','libx264','-preset',str(self.cfg.get('render.preset','fast')),'-crf',str(self.cfg.get('render.crf',18)),'-pix_fmt','yuv420p']
        run_cmd(['ffmpeg','-y','-v','error','-ss',f'{trim_start_ms/1000:.6f}','-i',str(src),'-map','0:v:0','-an','-t',f'{duration_ms/1000:.6f}',*codec_args,'-movflags','+faststart',str(dst)],timeout=1800)

    def trim_exact(self,src,dst,duration_ms,trim_start_ms=0):
        dst=Path(dst);dst.parent.mkdir(parents=True,exist_ok=True);dst.unlink(missing_ok=True)
        if duration_ms<=0: raise Phase3Error('VIDEO_TRIM_FAILED','Invalid requested duration')
        info=probe(src);available=max(0,int(info.get('duration_ms',0))-int(trim_start_ms))
        if available<duration_ms-500: raise Phase3Error('VIDEO_TRIM_FAILED',f'Source segment too short: requested={duration_ms} available={available}')
        preferred=self.cfg.get('render.video_codec','h264_nvenc')
        if preferred=='h264_nvenc' and ffmpeg_has_encoder('h264_nvenc'):
            try:self._encode_trim(src,dst,duration_ms,'h264_nvenc',trim_start_ms)
            except Exception: dst.unlink(missing_ok=True);self._encode_trim(src,dst,duration_ms,'libx264',trim_start_ms)
        else:self._encode_trim(src,dst,duration_ms,'libx264',trim_start_ms)
        actual=int(probe(dst).get('duration_ms',0))
        if abs(actual-int(duration_ms))>250: raise Phase3Error('VIDEO_TRIM_FAILED',f'Expected {duration_ms}, got {actual}')
