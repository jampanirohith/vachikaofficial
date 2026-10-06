from __future__ import annotations
from pathlib import Path
from .utils import Phase3Error, run_cmd, ffmpeg_has_encoder, nvenc_video_args

class Assembler:
    def __init__(self,cfg,work_dir,logger): self.cfg=cfg; self.work=work_dir; self.logger=logger
    def assemble(self,video:Path,audio:Path,ass:Path,out:Path,duration_ms:int|None=None)->Path:
        out.parent.mkdir(parents=True,exist_ok=True)
        fonts_dir=Path(self.cfg.fonts_dir)
        vf=f"ass=filename='{ass.as_posix().replace(':','\\:')}':fontsdir='{fonts_dir.as_posix().replace(':','\\:')}'"
        base=['ffmpeg','-y','-v','error','-i',str(video),'-i',str(audio),'-vf',vf,'-map','0:v:0','-map','1:a:0','-shortest']
        if duration_ms is not None:
            base += ['-t', f'{int(duration_ms)/1000.0:.6f}']
        preferred_codec=self.cfg.get('render.video_codec','h264_nvenc'); codec=preferred_codec if ffmpeg_has_encoder(preferred_codec) else self.cfg.get('render.video_codec_fallback','libx264')
        audio_bitrate=self.cfg.get('render.audio_bitrate','192k')
        if codec=='h264_nvenc':
            cmd=base+nvenc_video_args(self.cfg)+['-c:a','aac','-b:a',audio_bitrate,'-movflags','+faststart',str(out)]
        else:
            cmd=base+['-c:v',codec,'-preset',str(self.cfg.get('render.preset','fast')),'-crf',str(self.cfg.get('render.crf',18)),'-pix_fmt','yuv420p','-c:a','aac','-b:a',audio_bitrate,'-movflags','+faststart',str(out)]
        try:
            run_cmd(cmd,timeout=1800)
        except Exception:
            fallback=self.cfg.get('render.video_codec_fallback','libx264')
            cmd=base+['-c:v',fallback,'-preset','fast','-crf',str(self.cfg.get('render.crf',18)),'-pix_fmt','yuv420p','-c:a','aac','-b:a',audio_bitrate,'-movflags','+faststart',str(out)]
            try: run_cmd(cmd,timeout=1800)
            except Exception as e: raise Phase3Error('ASSEMBLY_FAILED',str(e))
        return out
