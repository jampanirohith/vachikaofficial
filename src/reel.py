from __future__ import annotations
import json, re, shutil
from pathlib import Path
from .hook import validate
from .hashing import sha256_file
from .utils import duration_ms, run, atomic_write_json
from .rendercore.video_grabber import VideoGrabber
from .rendercore.video_renderer import VideoRenderer
from .rendercore.assembler import Assembler
from .rendercore.video_grabber import VideoGrabber


def crop_8d(source, out, start_ms, end_ms):
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    run(['ffmpeg','-y','-v','error','-ss',f'{start_ms/1000:.6f}','-i',str(source),'-t',f'{(end_ms-start_ms)/1000:.6f}','-map','0:a:0','-c:a','libmp3lame','-b:a','320k',str(out)], timeout=900)
    if abs(duration_ms(out)-(end_ms-start_ms)) > 250:
        raise RuntimeError('REEL_AUDIO_CROP_FAILED: wrong duration')
    return out


def _lines_from_word_lrc(path):
    rows=[]
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        m=re.match(r'^\[(\d+):(\d{2})[.:](\d{1,3})\](.*)$', line)
        if not m: continue
        ms=int(m.group(1))*60000+int(m.group(2))*1000+int(m.group(3).ljust(3,'0'))
        text=m.group(4).strip()
        if text: rows.append({'start_ms':ms,'end_ms':ms+4000,'text':text})
    for i,r in enumerate(rows[:-1]): r['end_ms']=rows[i+1]['start_ms']
    return rows


def _parse_word_lrc(path):
    lrc_lines=[]
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        stamps=list(re.finditer(r'\[(\d+):(\d{2})[.:](\d{1,3})\]', line))
        if not stamps: continue
        words=[]
        for i,m in enumerate(stamps):
            st=int(m.group(1))*60000+int(m.group(2))*1000+int(m.group(3).ljust(3,'0'))
            nst=(int(stamps[i+1].group(1))*60000+int(stamps[i+1].group(2))*1000+int(stamps[i+1].group(3).ljust(3,'0'))) if i+1<len(stamps) else st+800
            txt=line[m.end():(stamps[i+1].start() if i+1<len(stamps) else len(line))].strip()
            if txt: words.append({'text':txt,'start_ms':st,'end_ms':max(st+30,nst)})
        if words:
            lrc_lines.append({'start_ms':words[0]['start_ms'],'end_ms':words[-1]['end_ms'],'text':' '.join(x['text'] for x in words),'display_text':' '.join(x['text'] for x in words),'words':words})
    return lrc_lines


def _validate_reel(path, expected_duration_ms):
    """Strict final Reel validation; returns JSON-safe evidence."""
    from .rendercore.audio_io import probe
    path=Path(path)
    info=probe(path) if path.exists() else {}
    checks={
        'exists': path.exists() and path.stat().st_size>10000 if path.exists() else False,
        'decode': bool(info.get('duration_ms',0)>0),
        'duration_match': abs(int(info.get('duration_ms',0))-int(expected_duration_ms))<=250,
        'resolution_valid': int(info.get('width',0))==1080 and int(info.get('height',0))==1920,
        'aspect_ratio_valid': abs((float(info.get('width',1))/max(1,float(info.get('height',1))))-9/16)<0.002,
        'audio_stereo': int(info.get('channels',0))>=2,
        'has_video': bool(info.get('width',0)),
    }
    failed=[k for k,v in checks.items() if not v]
    return {'probe':info,'checks':checks,'failed_checks':failed,'overall':len(failed)==0}


def build_reel(cfg, work, song, hook_start, hook_end, yt_video_source, yt_offset, wordlevel_lrc, words, logger):
    work=Path(work); validate(hook_start, hook_end, int(song['duration_ms']))
    mapped_start=hook_start+int(yt_offset); mapped_end=hook_end+int(yt_offset)
    reel_key=f"{song['song_key']}_reel_v1"
    # All Reel artifacts are staged inside the song workspace. Nothing under
    # reels/generated is touched until the caller validates the complete song.
    stage_dir=work/'final_reel'; stage_dir.mkdir(parents=True,exist_ok=True)
    v=VideoGrabber(cfg,work,logger)
    guard=max(0,int(cfg.get('video_match.guard_before_ms',1500)))
    trim_start=min(guard,mapped_start)
    section=v.download_section(yt_video_source,mapped_start,mapped_end)
    trimmed=work/'video'/'hook_exact.mp4'
    v.trim_exact(section,trimmed,hook_end-hook_start,trim_start_ms=trim_start)
    rendered=VideoRenderer(cfg,work,logger).render(trimmed)
    audio=crop_8d(song['eight_d_path'],work/'audio'/'hook_8d.mp3',hook_start,hook_end)

    rows=_lines_from_word_lrc(wordlevel_lrc)
    rows=[r for r in rows if r['end_ms']>hook_start and r['start_ms']<hook_end]
    from .rendercore.ass_generator import ASSGenerator
    from .rendercore.font_manager import ensure_telugu_font
    font=ensure_telugu_font(cfg.fonts_dir,cfg.get('lyrics.font_filename','BalooTammudu2-ExtraBold.ttf'),cfg.get('lyrics.font_path'))
    lrc_lines=_parse_word_lrc(wordlevel_lrc)
    ass=ASSGenerator(cfg,work,logger).generate(lrc_lines,words,hook_start,hook_end,font,None)

    staged_mp4=stage_dir/f"{song['basename']}_reel.mp4"
    Assembler(cfg,work,logger).assemble(rendered,audio,ass,staged_mp4,hook_end-hook_start)
    validation=_validate_reel(staged_mp4,hook_end-hook_start)
    (work/'validation').mkdir(parents=True,exist_ok=True)
    atomic_write_json(work/'validation'/'reel_validation.json',validation)
    if not validation['overall']:
        raise RuntimeError('REEL_VALIDATION_FAILED: '+', '.join(validation['failed_checks']))

    final_name=f"{song['basename']}_reel"
    staged_json=stage_dir/f'{final_name}.json'
    payload={
        'reel_key':reel_key,'song_key':song['song_key'],
        'hook':{'mode':'terminal','start_ms':hook_start,'end_ms':hook_end,'duration_ms':hook_end-hook_start},
        'youtube':{'video_source':str(yt_video_source),'offset_ms':int(yt_offset),'mapped_start_ms':mapped_start,'mapped_end_ms':mapped_end},
        'audio':{'source_8d':song['eight_d_path'],'source_8d_sha256':sha256_file(song['eight_d_path']),'crop_start_ms':hook_start,'crop_end_ms':hook_end,'output_duration_ms':duration_ms(audio),'output_sha256':sha256_file(audio)},
        'video':{'source_section':str(section),'exact_video':str(trimmed),'rendered':str(rendered),'width':1080,'height':1920,'panel_height_percent':80.0,'encoder':validation['probe'].get('video_codec')},
        'lyrics':{'wordlevel_lrc':str(wordlevel_lrc),'wordlevel_lrc_sha256':sha256_file(wordlevel_lrc),'line_count':len(rows),'font':'Baloo Tammudu 2 ExtraBold','placement':'center','current_word_highlighting':True},
        'validation':validation,
        'output':{'path':f'reels/generated/{final_name}.mp4','json_path':f'reels/generated/{final_name}.json','duration_ms':duration_ms(staged_mp4),'sha256':sha256_file(staged_mp4)}
    }
    atomic_write_json(staged_json,payload)
    return staged_mp4,staged_json,payload
