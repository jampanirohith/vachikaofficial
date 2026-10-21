from __future__ import annotations
import re
from pathlib import Path
from src.orchestration.hook import validate
from src.common.hashing import sha256_file
from src.common.paths import display_path
from src.common.utils import duration_ms, run, atomic_write_json
from src.rendercore.video_grabber import VideoGrabber
from src.rendercore.video_renderer import VideoRenderer
from src.rendercore.assembler import Assembler


def crop_8d(source, out, start_ms, end_ms, fade_in_ms=0, fade_out_ms=0, prefix_silence_ms=0):
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    filters=[]
    crop_duration_ms=max(0, int(end_ms)-int(start_ms))
    prefix_silence_ms=max(0, int(prefix_silence_ms))
    expected_duration_ms=crop_duration_ms+prefix_silence_ms
    if prefix_silence_ms:
        # Delay both stereo channels, creating real silence rather than muting
        # a song that has already advanced. Lyrics/video use the same offset.
        filters.append(f"adelay={prefix_silence_ms}|{prefix_silence_ms}")
    if int(fade_in_ms)>0:
        fade=min(int(fade_in_ms),crop_duration_ms)
        filters.append(f"afade=t=in:st={prefix_silence_ms/1000:.6f}:d={fade/1000:.6f}")
    if int(fade_out_ms)>0 and crop_duration_ms>0:
        fade=min(int(fade_out_ms),crop_duration_ms)
        fade_start=prefix_silence_ms+max(0,crop_duration_ms-fade)
        filters.append(f"afade=t=out:st={fade_start/1000:.6f}:d={fade/1000:.6f}")
    cmd=['ffmpeg','-y','-v','error','-ss',f'{start_ms/1000:.6f}','-i',str(source),'-t',f'{expected_duration_ms/1000:.6f}','-map','0:a:0']
    if filters: cmd += ['-af',','.join(filters)]
    cmd += ['-c:a','libmp3lame','-b:a','320k',str(out)]
    run(cmd, timeout=900)
    if abs(duration_ms(out)-expected_duration_ms) > 250:
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


def _first_lyric_ms_in_hook(wordlevel_lrc, hook_start_ms, hook_end_ms):
    """Find the start of the first lyric line overlapping the selected hook."""
    parsed = _parse_word_lrc(wordlevel_lrc)
    candidates = [
        int(line["start_ms"]) for line in parsed
        if int(line["end_ms"]) > int(hook_start_ms) and int(line["start_ms"]) < int(hook_end_ms)
    ]
    return min(candidates) if candidates else int(hook_start_ms)



def _hide_lead_in_lyrics(lrc_lines, first_vocal_ms):
    """Hide lyric words before the first lyric selected to hand off from the intro.

    The audio/video pre-roll remains intact; this only prevents earlier caption
    words from competing with the logo animation. Word timestamps after the
    handoff are kept absolute and unchanged.
    """
    cutoff_ms = int(first_vocal_ms)
    visible = []
    for line in lrc_lines:
        words = list(line.get("words") or [])
        if not words:
            if int(line.get("start_ms", 0)) >= cutoff_ms:
                visible.append(dict(line))
            continue
        kept = [dict(word) for word in words if int(word.get("start_ms", 0)) >= cutoff_ms]
        if not kept:
            continue
        row = dict(line)
        row["words"] = kept
        row["start_ms"] = int(kept[0]["start_ms"])
        row["end_ms"] = int(kept[-1]["end_ms"])
        text = " ".join(str(word.get("text", "")).strip() for word in kept if str(word.get("text", "")).strip())
        row["text"] = text
        row["display_text"] = text
        if text:
            visible.append(row)
    return visible


def _plan_reel_window(wordlevel_lrc, hook_start_ms, hook_end_ms, *, intro_enabled=True, intro_duration_ms=3800):
    """Plan a Reel timeline that inserts the opening before the selected lyric/hook."""
    hook_start_ms=int(hook_start_ms); hook_end_ms=int(hook_end_ms)
    first_vocal_ms=_first_lyric_ms_in_hook(wordlevel_lrc, hook_start_ms, hook_end_ms)
    reel_start_ms=hook_start_ms
    configured_ms=max(0,min(10000,int(intro_duration_ms or 0)))
    effective_ms=0
    detail={"enabled":False,"reason":"disabled_by_config","first_vocal_ms":first_vocal_ms}
    if intro_enabled and configured_ms>0:
        reel_start_ms=min(hook_start_ms,max(0,first_vocal_ms-configured_ms))
        relative_first_vocal_ms=first_vocal_ms-reel_start_ms
        effective_ms=min(configured_ms,max(0,relative_first_vocal_ms))
        if effective_ms<750:
            reel_start_ms=hook_start_ms
            effective_ms=0
            detail={"enabled":False,"reason":"insufficient_pre_vocal_lead_in",
                    "first_vocal_ms":first_vocal_ms}
        else:
            detail={
                "enabled":True,"reason":"opening_enabled",
                "first_vocal_ms":first_vocal_ms,
                "configured_duration_ms":configured_ms,
                "duration_ms":effective_ms,
                "lead_in_to_first_vocal_ms":relative_first_vocal_ms,
                "pre_roll_ms":hook_start_ms-reel_start_ms,
            }
    return reel_start_ms, effective_ms, first_vocal_ms, detail

def _constrain_reel_window_to_video(reel_start_ms, intro_effective_ms, first_vocal_ms,
                                    hook_start_ms, yt_offset_ms, intro_info):
    """Keep a prepended lead-in inside the available zero-based video timeline."""
    hook_start_ms = int(hook_start_ms)
    yt_offset_ms = int(yt_offset_ms)
    if hook_start_ms + yt_offset_ms < 0:
        raise RuntimeError(
            "VIDEO_SOURCE_MAPPING_STARTS_BEFORE_ZERO: the selected hook maps before the "
            "start of the source video; verify the accepted YouTube offset or select another source."
        )
    minimum_song_time_ms = max(0, -yt_offset_ms)
    if int(reel_start_ms) >= minimum_song_time_ms or int(intro_effective_ms) <= 0:
        return int(reel_start_ms), int(intro_effective_ms), intro_info

    adjusted_start = minimum_song_time_ms
    available_lead = max(0, int(first_vocal_ms) - adjusted_start)
    adjusted_intro = min(int(intro_effective_ms), available_lead)
    if adjusted_intro < 750:
        return hook_start_ms, 0, {
            "enabled": False,
            "reason": "insufficient_video_lead_in",
            "first_vocal_ms": int(first_vocal_ms),
            "source_video_start_limited": True,
        }

    detail = dict(intro_info)
    detail.update({
        "enabled": True,
        "reason": "opening_enabled_source_start_limited",
        "duration_ms": adjusted_intro,
        "lead_in_to_first_vocal_ms": available_lead,
        "pre_roll_ms": hook_start_ms - adjusted_start,
        "source_video_start_limited": True,
    })
    return adjusted_start, adjusted_intro, detail


def build_reel(cfg, work, song, hook_start, hook_end, yt_video_source, yt_offset, wordlevel_lrc, words, logger):
    work=Path(work); validate(hook_start, hook_end, int(song['duration_ms']))
    hook_start=int(hook_start); hook_end=int(hook_end)
    hook_duration_ms=hook_end-hook_start
    reel_key=f"{song['song_key']}_reel_v1"
    intro_enabled=bool(cfg.get('reel_intro.enabled', True))
    intro_configured_ms=max(0, min(10000, int(cfg.get('reel_intro.duration_ms', 3800) or 0)))
    reel_start_ms, intro_effective_ms, first_vocal_ms, intro_info = _plan_reel_window(
        wordlevel_lrc, hook_start, hook_end,
        intro_enabled=intro_enabled, intro_duration_ms=intro_configured_ms,
    )
    reel_start_ms, intro_effective_ms, intro_info = _constrain_reel_window_to_video(
        reel_start_ms, intro_effective_ms, first_vocal_ms, hook_start, yt_offset, intro_info,
    )
    base_reel_duration_ms=hook_end-reel_start_ms
    silent_opening_ms=(max(0,min(6000,int(cfg.get('reel_intro.silent_duration_ms',2000) or 0)))
                       if intro_effective_ms else 0)
    reel_duration_ms=base_reel_duration_ms+silent_opening_ms
    reel_start_s=int(reel_start_ms)
    mapped_start=reel_start_s+int(yt_offset); mapped_end=hook_end+int(yt_offset)
    # All Reel artifacts are staged inside the song workspace. Nothing under
    # reels/generated is touched until the caller validates the complete song.
    stage_dir=work/'final_reel'; stage_dir.mkdir(parents=True,exist_ok=True)
    v=VideoGrabber(cfg,work,logger)
    # `reel_start_ms` can precede the user-selected hook so the intro has a lead-in.
    # Keep the established absolute source-video timeline and its MMS-derived offset.
    section = v.download_section(yt_video_source, mapped_start, mapped_end)
    trimmed = work / 'video' / 'hook_exact.mp4'
    trim_start = max(0, int(mapped_start))
    v.trim_exact(section, trimmed, base_reel_duration_ms, trim_start_ms=trim_start)
    full_bleed=bool(intro_effective_ms and cfg.get('reel_intro.full_bleed_video',False))
    configured_panel_fraction=float(cfg.get('video_layout.height_fraction',0.80))
    panel_fraction=1.0 if full_bleed else min(1.0,max(0.45,configured_panel_fraction))
    video_renderer=VideoRenderer(cfg,work,logger)
    rendered=video_renderer.render(trimmed, height_fraction=panel_fraction) if full_bleed else video_renderer.render(trimmed)

    from src.rendercore.intro_renderer import render_intro_video
    from src.rendercore.reel_intro import Timeline
    if intro_effective_ms:
        audio=crop_8d(
            song['eight_d_path'], work/'audio'/'hook_8d.mp3', reel_start_ms, hook_end,
            fade_in_ms=int(cfg.get('reel_intro.fade_in_ms', 300)),
            fade_out_ms=int(cfg.get('reel_intro.fade_out_ms', 500)),
            prefix_silence_ms=silent_opening_ms,
        )
    else:
        audio=crop_8d(song['eight_d_path'],work/'audio'/'hook_8d.mp3',reel_start_ms,hook_end)
    if intro_effective_ms:
        assets_value=cfg.get('reel_intro.assets_dir','assets/reel_intro')
        assets_dir=Path(assets_value).expanduser()
        if not assets_dir.is_absolute():
            assets_dir=Path(getattr(cfg,'root',Path.cwd()))/assets_dir
        intro_video=work/'video'/'video_9x16_intro.mp4'
        hp_lines=cfg.get('reel_intro.headphone_lines', ["USE HEADPHONES", "FOR BETTER EXPERIENCE"])
        if isinstance(hp_lines,str): hp_lines=[hp_lines]
        # Upgrade the previous default copy to the requested sample-inspired
        # two-line lockup, while preserving any custom wording from config.
        if len(hp_lines) == 1 and hp_lines[0].strip().casefold() in {
            "best with headphones · 8d", "best with headphones  ·  8d",
            "best with headphones - 8d", "best with headphones 8d",
        }:
            hp_lines=["USE HEADPHONES", "FOR BETTER EXPERIENCE"]
        intro_timeline=Timeline.with_silent_lead(silent_opening_ms/1000.0, intro_effective_ms/1000.0)
        rendered_intro=render_intro_video(
            rendered, audio, intro_video, assets_dir, cfg,
            duration_s=intro_effective_ms/1000.0,
            silent_duration_s=silent_opening_ms/1000.0,
            hp_lines=tuple(hp_lines), logger=logger,
        )
        rendered=intro_video
        intro_info.update({
            "enabled":True,
            "assets_dir":str(assets_dir),
            "rendered_video":str(intro_video),
            "render_details":rendered_intro,
            "headphone_lines":list(hp_lines),
            "fade_in_ms":int(cfg.get('reel_intro.fade_in_ms',300)),
            "fade_out_ms":int(cfg.get('reel_intro.fade_out_ms',500)),
            "silent_opening_ms":silent_opening_ms,
            "footer_enabled":bool(cfg.get('reel_intro.footer_enabled', True)),
            "footer_text":str(cfg.get('reel_intro.footer_text', 'Vachika')),
            "footer_center_y":int(cfg.get('reel_intro.footer_center_y', 1800)),
            "total_opening_ms":silent_opening_ms+intro_effective_ms,
            "timeline_duration_ms":int(round(intro_timeline.duration*1000)),
        })

    rows=_lines_from_word_lrc(wordlevel_lrc)
    rows=[r for r in rows if r['end_ms']>reel_start_ms and r['start_ms']<hook_end]
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
    if intro_effective_ms:
        lrc_lines=_hide_lead_in_lyrics(lrc_lines, first_vocal_ms)
    # Shift caption timestamps by the black/silent prefix so they remain locked
    # to the delayed song and video. Song-time metadata remains unshifted.
    subtitle_origin_ms=reel_start_ms-silent_opening_ms
    ass=ASSGenerator(cfg,work,logger).generate(lrc_lines,words,subtitle_origin_ms,hook_end,font,None,font_info['family'])

    staged_mp4=stage_dir/f"{song['basename']}_reel.mp4"
    fade_out_ms=int(cfg.get('reel_intro.fade_out_ms',500)) if intro_effective_ms else 0
    assembler=Assembler(cfg,work,logger)
    if fade_out_ms:
        assembler.assemble(rendered,audio,ass,staged_mp4,reel_duration_ms,fade_out_ms=fade_out_ms)
    else:
        assembler.assemble(rendered,audio,ass,staged_mp4,reel_duration_ms)
    validation=_validate_reel(staged_mp4,reel_duration_ms)
    (work/'validation').mkdir(parents=True,exist_ok=True)
    atomic_write_json(work/'validation'/'reel_validation.json',validation)
    if not validation['overall']:
        raise RuntimeError('REEL_VALIDATION_FAILED: '+', '.join(validation['failed_checks']))

    final_name=f"{song['basename']}_reel"
    staged_json=stage_dir/f'{final_name}.json'
    payload={
        'reel_key':reel_key,'song_key':song['song_key'],
        'hook':{'mode':'terminal','start_ms':hook_start,'end_ms':hook_end,'duration_ms':hook_duration_ms},
        'timeline':{
            'reel_start_ms':reel_start_ms,'reel_end_ms':hook_end,'duration_ms':reel_duration_ms,
            'selected_hook_offset_ms':hook_start-reel_start_ms+silent_opening_ms,
            'intro_lead_in_ms':intro_effective_ms,
            'silent_opening_ms':silent_opening_ms,
        },
        'intro':intro_info,
        'youtube':{'video_source':str(yt_video_source),'offset_ms':int(yt_offset),'mapped_start_ms':mapped_start,'mapped_end_ms':mapped_end},
        'audio':{'source_8d':song['eight_d_path'],'source_8d_sha256':sha256_file(song['eight_d_path']),'crop_start_ms':reel_start_ms,'crop_end_ms':hook_end,'prefix_silence_ms':silent_opening_ms,'output_duration_ms':duration_ms(audio),'output_sha256':sha256_file(audio),'fade_in_ms':int(cfg.get('reel_intro.fade_in_ms',300)) if intro_effective_ms else 0,'fade_out_ms':int(cfg.get('reel_intro.fade_out_ms',500)) if intro_effective_ms else 0},
        'video':{'source_video':str(section),'source_time_range_ms':{'start':mapped_start,'end':mapped_end},'exact_video':str(trimmed),'rendered':str(rendered),'width':1080,'height':1920,'panel_height_percent':round(panel_fraction*100,2),'encoder':validation['probe'].get('video_codec')},
        'lyrics':{'wordlevel_lrc':str(wordlevel_lrc),'wordlevel_lrc_sha256':sha256_file(wordlevel_lrc),'line_count':len(lrc_lines),'font':font_info['family'],'configured_font_name':font_info['configured_family'],'font_family_source':font_info['family_source'],'font_path':font_info['path'],'font_filename':font_info['filename'],'placement':'center','word_by_word':True,'time_origin_ms':subtitle_origin_ms,'song_time_origin_ms':reel_start_ms,'word_fade_in_ms':int(cfg.get('lyrics.word_fade_in_ms',80)),'word_fade_out_ms':int(cfg.get('lyrics.word_fade_out_ms',450))},
        'validation':validation,
        'output':{'path':display_path((cfg.reels_dir / f'{final_name}.mp4'), cfg.root),'json_path':display_path((cfg.reels_dir / f'{final_name}.json'), cfg.root),'duration_ms':duration_ms(staged_mp4),'sha256':sha256_file(staged_mp4)}
    }
    atomic_write_json(staged_json,payload)
    return staged_mp4,staged_json,payload

