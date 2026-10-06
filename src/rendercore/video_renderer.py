from __future__ import annotations
from pathlib import Path
import subprocess
from .utils import Phase3Error, ffmpeg_has_encoder, nvenc_video_args, run_cmd

class VideoRenderer:
    """Render a static, centered 80%-height video panel on a 1080x1920 canvas.

    There is deliberately no face/body detection, smart crop, subject tracking,
    or dynamic pan. The source video is uniformly zoomed to the configured panel
    height, horizontally centered, and letterboxed vertically on a black 9:16
    canvas. NVIDIA NVENC is preferred for encoding.
    """
    def __init__(self,cfg,work_dir,logger):
        self.cfg=cfg; self.work=Path(work_dir); self.logger=logger

    def _encode_args(self, codec: str):
        if codec=='h264_nvenc': return nvenc_video_args(self.cfg)
        return ['-c:v','libx264','-preset',str(self.cfg.get('render.preset','fast')),'-crf',str(self.cfg.get('render.crf',18)),'-pix_fmt','yuv420p']

    def _run(self,input_video:Path,out:Path,codec:str)->Path:
        W=int(self.cfg.get('video_layout.output_width',1080)); H=int(self.cfg.get('video_layout.output_height',1920))
        fraction=float(self.cfg.get('video_layout.height_fraction',0.80))
        fraction=min(0.95,max(0.45,fraction))
        panel_h=max(2,int(round(H*fraction))); panel_h-=panel_h%2
        # Scale to the panel height, crop the center horizontally when wider than
        # the panel, then center the panel vertically on the 9:16 canvas.
        vf=(
            f"scale=-2:{panel_h},"
            f"crop=min(iw\\,{W}):{panel_h}:(iw-min(iw\\,{W}))/2:0,"
            f"pad={W}:{H}:0:(oh-ih)/2:color=black,format=yuv420p"
        )
        cmd=['ffmpeg','-y','-v','error','-i',str(input_video),'-vf',vf,'-an',*self._encode_args(codec),str(out)]
        if self.logger:
            self.logger.info('VideoRenderer: using %s (static centered %.0f%% height panel)',codec,fraction*100)
        try:
            run_cmd(cmd,timeout=1800)
        except Exception as exc:
            raise Phase3Error('VIDEO_RENDER_FAILED',str(exc)) from exc
        return out

    def render(self,input_video:Path,tracking:dict|None=None,lyrics_plan:dict|None=None)->Path:
        out=self.work/'video'/'video_9x16.mp4'; out.parent.mkdir(parents=True,exist_ok=True)
        preferred=self.cfg.get('render.video_codec','h264_nvenc')
        if preferred=='h264_nvenc' and ffmpeg_has_encoder('h264_nvenc'):
            try:
                self._run(input_video,out,'h264_nvenc'); return out
            except Phase3Error as exc:
                if out.exists():
                    try: out.unlink()
                    except Exception: pass
                if self.logger:
                    self.logger.warning('NVENC static render failed; falling back to CPU libx264: %s',exc)
        self._run(input_video,out,'libx264')
        return out
