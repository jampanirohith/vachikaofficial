from __future__ import annotations
import json, re, shutil
from pathlib import Path
from src.orchestration.hook import validate
from src.common.hashing import sha256_file
from src.common.paths import display_path
from src.common.utils import duration_ms, run, atomic_write_json
from src.rendercore.video_grabber import VideoGrabber
from src.rendercore.video_renderer import VideoRenderer
from src.rendercore.assembler import Assembler


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
    from src.rendercore.audio_io import probe
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
    # download_section currently caches the full selected source video. The hook
    # coordinates are absolute source-video timestamps after applying the offset.
    # Guard durations describe desired context, not the origin of the downloaded file.
    section = v.download_section(yt_video_source, mapped_start, mapped_end)
    trimmed = work / 'video' / 'hook_exact.mp4'
    trim_start = max(0, int(mapped_start))
    v.trim_exact(section, trimmed, hook_end-hook_start, trim_start_ms=trim_start)
    rendered=VideoRenderer(cfg,work,logger).render(trimmed)
    audio=crop_8d(song['eight_d_path'],work/'audio'/'hook_8d.mp3',hook_start,hook_end)

    rows=_lines_from_word_lrc(wordlevel_lrc)
    rows=[r for r in rows if r['end_ms']>hook_start and r['start_ms']<hook_end]
    from src.rendercore.ass_generator import ASSGenerator
    from src.rendercore.font_manager import describe_font, ensure_telugu_font
    font_config_path=cfg.get('lyrics.font_path')
    if font_config_path and not Path(font_config_path).is_absolute():
        font_config_path=getattr(cfg,'root',Path.cwd())/font_config_path
    font=ensure_telugu_font(
        cfg.fonts_dir,
        cfg.get('lyrics.font_filename','Suravaram-Regular.ttf'),
        font_config_path,
        cfg.get('lyrics.font_name','Suravaram'),
    )
    font_info = describe_font(font, cfg.get('lyrics.font_name','Suravaram'))
    lrc_lines=_parse_word_lrc(wordlevel_lrc)
    ass=ASSGenerator(cfg,work,logger).generate(lrc_lines,words,hook_start,hook_end,font,None,font_info['family'])

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
        'video':{'source_video':str(section),'source_time_range_ms':{'start':mapped_start,'end':mapped_end},'exact_video':str(trimmed),'rendered':str(rendered),'width':1080,'height':1920,'panel_height_percent':80.0,'encoder':validation['probe'].get('video_codec')},
        'lyrics':{'wordlevel_lrc':str(wordlevel_lrc),'wordlevel_lrc_sha256':sha256_file(wordlevel_lrc),'line_count':len(rows),'font':font_info['family'],'configured_font_name':font_info['configured_family'],'font_family_source':font_info['family_source'],'font_path':font_info['path'],'font_filename':font_info['filename'],'placement':'center','word_by_word':True,'word_fade_in_ms':int(cfg.get('lyrics.word_fade_in_ms',80)),'word_fade_out_ms':int(cfg.get('lyrics.word_fade_out_ms',450))},
        'validation':validation,
        'output':{'path':display_path((cfg.reels_dir / f'{final_name}.mp4'), cfg.root),'json_path':display_path((cfg.reels_dir / f'{final_name}.json'), cfg.root),'duration_ms':duration_ms(staged_mp4),'sha256':sha256_file(staged_mp4)}
    }
    atomic_write_json(staged_json,payload)
    return staged_mp4,staged_json,payload
