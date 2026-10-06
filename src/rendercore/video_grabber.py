from __future__ import annotations
import json
from pathlib import Path
from .utils import Phase3Error, run_cmd, ffmpeg_has_encoder, nvenc_video_args
from .audio_io import probe

class VideoGrabber:
    def __init__(self,cfg,work_dir,logger): self.cfg=cfg;self.work=Path(work_dir);self.logger=logger

    def _section_request(self,video_id,start_ms,end_ms):
        gb=int(self.cfg.get('video_match.guard_before_ms',1500));ga=int(self.cfg.get('video_match.guard_after_ms',1500))
        return {'video_id':str(video_id),'requested_start_ms':max(0,int(start_ms)-gb),'requested_end_ms':int(end_ms)+ga,'guard_before_ms':gb,'guard_after_ms':ga}

    def download_section(self,video_source,start_ms,end_ms):
        outdir=self.work/'video';outdir.mkdir(parents=True,exist_ok=True);out=outdir/'downloaded_with_guard.mp4';meta=outdir/'downloaded_with_guard.request.json'
        video_url = str(video_source)
        if video_url.startswith('http://') or video_url.startswith('https://'):
            selected_url = video_url
            video_id = video_url.split('v=')[-1].split('&')[0] if 'v=' in video_url else video_url.rsplit('/',1)[-1]
        else:
            video_id = str(video_url)
            selected_url = f'https://www.youtube.com/watch?v={video_id}'
        request=self._section_request(video_id,start_ms,end_ms)
        request['source_url']=selected_url
        if out.exists() and out.stat().st_size>10000 and meta.exists():
            try:
                if json.loads(meta.read_text(encoding='utf-8'))==request and probe(out).get('duration_ms',0)>=max(1000,request['requested_end_ms']-request['requested_start_ms']-750): return out
            except Exception: pass
        out.unlink(missing_ok=True);meta.unlink(missing_ok=True)
        try:
            import yt_dlp
            height=int(self.cfg.get('ytmusic.video_max_height',1080))
            cookiefile=self.cfg.path('ytmusic.cookies_file') if self.cfg.get('ytmusic.cookies_file') else None
            opts={
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'format': f'bestvideo[height<={height}]+bestaudio/best[height<={height}]/best',
                'merge_output_format': 'mp4',
                'outtmpl': str(out),
                'overwrites': True,
            }
            if cookiefile and Path(cookiefile).exists(): opts['cookiefile']=str(cookiefile)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([selected_url])
        except Exception as exc:
            raise Phase3Error('VIDEO_SECTION_DOWNLOAD_FAILED',f'yt-dlp visual download failed: {type(exc).__name__}: {exc}') from exc
        info=probe(out);actual=int(info.get('duration_ms',0));expected=request['requested_end_ms']-request['requested_start_ms']
        if actual<max(1000,expected-1000): out.unlink(missing_ok=True);raise Phase3Error('VIDEO_SECTION_DOWNLOAD_FAILED',f'Downloaded visual video is shorter than requested guard span: {actual} vs {expected}')
        meta.write_text(json.dumps(request,indent=2)+'\n',encoding='utf-8')
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
