from __future__ import annotations
from pathlib import Path
import re

_INLINE_TS=re.compile(r'\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]')


def ass_time(ms:int)->str:
    total_cs=max(0,int(ms)//10); h=total_cs//360000; total_cs%=360000; m=total_cs//6000; total_cs%=6000; s=total_cs//100; cs=total_cs%100
    return f'{h}:{m:02d}:{s:02d}.{cs:02d}'

def ass_color(hexcolor:str)->str:
    c=str(hexcolor).lstrip('#')
    if len(c)!=6: c='FFFFFF'
    r,g,b=int(c[0:2],16),int(c[2:4],16),int(c[4:6],16)
    return f'&H00{b:02X}{g:02X}{r:02X}&'

def esc(s:str)->str:
    return str(s).replace('\\','\\\\').replace('{','(').replace('}',')').replace('\n',' ')

def clean_lrc_text(text:str)->str:
    text=_INLINE_TS.sub('',str(text))
    return ' '.join(text.split())

def _tokenize(text:str)->list[str]:
    return [x for x in re.split(r'\s+',clean_lrc_text(text).strip()) if x]

class ASSGenerator:
    def __init__(self,cfg,work_dir,logger): self.cfg=cfg; self.work=Path(work_dir); self.logger=logger
    def _header(self)->str:
        font_name=self.cfg.get('lyrics.font_name','Baloo Tammudu 2 ExtraBold')
        size=int(self.cfg.get('lyrics.font_size',58)); bold=1 if self.cfg.get('lyrics.font_bold',True) else 0
        outline=int(self.cfg.get('lyrics.outline',3)); shadow=int(self.cfg.get('lyrics.shadow',4))
        return '\n'.join([
            '[Script Info]','ScriptType: v4.00+','PlayResX: 1080','PlayResY: 1920','WrapStyle: 2','ScaledBorderAndShadow: yes','',
            '[V4+ Styles]',
            'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
            f'Style: Karaoke,{font_name},{size},&H00FFFFFF,&H00FFFFFF,&H00141414,&H80000000,{bold},0,0,0,100,100,0,0,1,{outline},{shadow},5,60,60,0,1','',
            '[Events]','Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',''
        ])

    def _word_timings(self,line:dict,canonical_words:list[dict]) -> list[dict]:
        lrc_words=list(line.get('words') or [])
        if lrc_words: return [{'text':str(w['text']).strip(),'start_ms':int(w['start_ms']),'end_ms':int(w['end_ms'])} for w in lrc_words if str(w.get('text','')).strip()]
        start=int(line['start_ms']); end=int(line['end_ms'])
        ws=[w for w in canonical_words if int(w['start_ms'])>=start and int(w['start_ms'])<end]
        tokens=_tokenize(line.get('text',''))
        if not ws: return []
        out=[]
        for i,w in enumerate(ws[:len(tokens)]):
            out.append({'text':tokens[i],'start_ms':int(w['start_ms']),'end_ms':int(w['end_ms'])})
        return out

    def generate(self,lrc_lines:list[dict],canonical_words:list[dict],reel_start_ms:int,reel_end_ms:int,font_path:Path,tracking:dict|None=None)->Path:
        out=self.work/'lyrics'/'lyrics.ass'; out.parent.mkdir(parents=True,exist_ok=True)
        header=self._header(); events=[]
        base=ass_color(self.cfg.get('lyrics.base_color','#FFFFFF'))
        active=ass_color(self.cfg.get('lyrics.current_color','#FFD65A'))
        done=ass_color(self.cfg.get('lyrics.completed_color','#FFFFFF'))
        cx=int(self.cfg.get('lyrics.center_x',540)); cy=int(self.cfg.get('lyrics.center_y',960))
        max_chars=int(self.cfg.get('lyrics.max_chars_single_line',30)); max_scale=int(self.cfg.get('lyrics.min_scale_x',70))
        used_lines=[]
        for li,line in enumerate(lrc_lines):
            line_start=int(line['start_ms']); line_end=int(line.get('end_ms',line_start+4000))
            if line_end<=reel_start_ms or line_start>=reel_end_ms: continue
            local_start=max(0,line_start-reel_start_ms); local_end=min(reel_end_ms-reel_start_ms,line_end-reel_start_ms)
            if local_end<=local_start: continue
            text=str(line.get('display_text') or clean_lrc_text(line.get('text','')))
            if not text: continue
            words=self._word_timings(line,canonical_words)
            # Limit word events to the selected line and hook window.
            words=[w for w in words if int(w['end_ms'])>reel_start_ms and int(w['start_ms'])<reel_end_ms]
            tokens=_tokenize(text)
            if words and len(words)!=len(tokens):
                # Keep LRC text authoritative; map timing order onto displayed tokens.
                words=[dict(words[i],text=tokens[i]) for i in range(min(len(words),len(tokens)))]
            else:
                words=[dict(w) for w in words]
            scale_x=100 if len(tokens)<=max_chars else max(max_scale,int(100*max_chars/max(1,len(tokens))))
            pre=0
            # Base state from line appearance until the first timed word.
            tag=f'{{\\an5\\pos({cx},{cy})\\fscx{scale_x}\\bord{int(self.cfg.get("lyrics.outline",3))}\\shad{int(self.cfg.get("lyrics.shadow",4))}}}'
            def render_line(active_index:int|None, completed_count:int):
                parts=[]
                for i,tok in enumerate(tokens):
                    if active_index is not None and i==active_index: color=active
                    elif i<completed_count: color=done
                    else: color=base
                    parts.append('{\\c'+color+'}'+esc(tok))
                return tag+(' '.join(parts))
            if words:
                first=max(local_start,max(0,words[0]['start_ms']-reel_start_ms))
                if first>local_start:
                    events.append(f'Dialogue: 0,{ass_time(local_start)},{ass_time(first)},Karaoke,,0,0,0,,{render_line(None,0)}')
                for i,w in enumerate(words):
                    st=max(local_start,int(w['start_ms'])-reel_start_ms)
                    en=min(local_end,int(w['end_ms'])-reel_start_ms)
                    if i+1<len(words): en=min(en,max(st+10,int(words[i+1]['start_ms'])-reel_start_ms))
                    if en<=st: continue
                    events.append(f'Dialogue: 0,{ass_time(st)},{ass_time(en)},Karaoke,,0,0,0,,{render_line(i,i)}')
            else:
                events.append(f'Dialogue: 0,{ass_time(local_start)},{ass_time(local_end)},Karaoke,,0,0,0,,{render_line(None,0)}')
            used_lines.append({'line_index':li,'text':text,'local_start_ms':local_start,'local_end_ms':local_end,'word_count':len(words)})
        out.write_text(header+'\n'.join(events)+'\n',encoding='utf-8')
        return out
