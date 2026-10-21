# Vāchika Reel Intro Integration

The stand-alone `vachika_intro` prototype is integrated into the existing Vāchika reel pipeline. The production reel still uses Vāchika's selected YouTube source, accepted audio/video offset, whole-song 8D MP3, word-level LRC, Telugu font resolution, ASS lyric renderer, staged output, and final validation.

## Integration path

1. `src/video/reel.py` finds the first lyric line overlapping the selected hook.
2. When the intro is enabled, the song/video timeline starts up to `reel_intro.duration_ms` before that line, while the requested hook end stays unchanged. An additional `reel_intro.silent_duration_ms` is prepended before the song/video starts, so the logo can animate on black without consuming the song lead-in.
3. The ordinary Vāchika renderer creates the base vertical video. `src/rendercore/intro_renderer.py` decodes the rendered video and applies the imported `IntroOverlay` frame-by-frame for the opening duration.
4. The five bars use RMS levels from the already-rendered Vāchika 8D crop; the orange-dot pulse uses detected beat timing. The intro project's separate `audio_8d.py` is intentionally **not** run: Vāchika already created the 8D track, and spatialising it a second time would be incorrect.
5. Vāchika's ASS lyric overlay is still applied after the intro composite. Caption words before the first lyric selected for the hook handoff are hidden so earlier verse captions do not compete with the logo; the selected first lyric and all later words keep their original absolute song timestamps. The opening is genuinely silent and black until `silent_duration_ms` ends; then the existing 8D crop and blurred video begin together as the headphone prompt fades in. Caption timing and output duration are shifted by exactly the same silent prefix. The song gets a 300 ms fade-in after the silence and the reel end gets a 500 ms audio/video fade-out by default.
6. The output is staged and validated against the *expanded* reel duration. The manifest records both the selected hook interval and actual rendered interval. The pipeline's audio/video provenance rows use the actual rendered interval.

## Settings

`config.json.example` includes this section. Existing configs may omit it; the renderer defaults to intro enabled.

```json
"reel_intro": {
  "enabled": true,
  "assets_dir": "assets/reel_intro",
  "duration_ms": 3800,
  "silent_duration_ms": 2000,
  "headphone_lines": ["USE HEADPHONES", "FOR BETTER EXPERIENCE"],
  "fade_in_ms": 300,
  "fade_out_ms": 500,
  "full_bleed_video": false
}
```

- `enabled`: set to `false` to restore the old behavior and original hook interval.
- `duration_ms`: duration of the song/video lead-in before the first lyric, 0–10000 ms. Raising it slows the logo animation and leaves more time for the blurred-video handoff. If the source has less lead-in, the active portion is shortened to protect lyric sync.
- `silent_duration_ms`: black, silent opening before song and video begin, 0–6000 ms. The default is 2000 ms. Set it to `0` for no silent prefix.
- `headphone_lines`: one or two lines shown to the right of the headphone glyph. The updated default uses uppercase copy inspired by the supplied sample.
- `fade_in_ms` / `fade_out_ms`: audio fades at the actual reel start/end. The ending picture fade uses `fade_out_ms` too.
- `full_bleed_video`: defaults to `false` to preserve Vāchika's existing configured panel framing (80% of canvas height in the provided config). Set to `true` if the desired final body should match the intro prototype's full-height crop; this changes framing for the **entire** reel, not just the first frames.

The runtime sprites and `meta.json` are under `assets/reel_intro/`. Source logo files are retained in `assets/reel_intro/source/`, and `src/rendercore/reel_intro/logo_prep.py` regenerates the sprites. From the repository root:

```powershell
python src/rendercore/reel_intro/logo_prep.py --logo assets/reel_intro/source/logo_white.png --lockup assets/reel_intro/source/logo_cream_lockup.png --out assets/reel_intro
```

The runtime compositor uses the pre-generated files above. Only if you regenerate the sprite assets, install the extra preparation dependency first: `python -m pip install scikit-image` (the source project uses skeletonization during logo prep; ordinary Reel rendering does not).

## Edge cases and design decisions

- If the first lyric is within 750 ms of the earliest available song time, the intro is skipped rather than shrinking the full logo animation to a flash. The reason appears in the reel JSON under `intro.reason`.
- The intro cannot start before the matching YouTube video timeline reaches zero. If that boundary trims the lead-in below 750 ms, the intro is disabled; if the selected hook itself maps before source-video time zero, the pipeline stops with `VIDEO_SOURCE_MAPPING_STARTS_BEFORE_ZERO` instead of publishing a silently mis-synced reel.
- If a vocal occurs in the song/video lead-in before the selected hook's first lyric, it remains audible once the silent opening has ended; the matching earlier on-screen lyric words are hidden during the intro. The new black prefix is real silence, while the active lead-in remains the original song audio. Review the resulting preview for songs whose lead-in contains vocals. Choose a slightly later hook, shorten `duration_ms`, or disable the intro when a clean vocal-free opening is essential.
- Vāchika's original `video_layout.height_fraction` is preserved by default. The stand-alone plan describes a full-bleed 9:16 crop, while the current Vāchika build uses a centered panel; `full_bleed_video` makes this choice explicit.
- The opening assets must exist. If the intro is enabled but sprites are missing, rendering fails clearly instead of silently publishing a reel without the requested intro.
- The final Reel adds the pre-roll before the selected hook, so the exported duration can be longer than `hook.duration_ms`. `timeline.duration_ms` is the actual media duration; `hook.duration_ms` remains the chosen hook's original duration.
- A successful unit/synthetic render does not prove visual quality against every real official video. Inspect a preview on real content, especially subject framing, previous vocals in the lead-in, the font used for the English headphone line, and the 500 ms end fade.

## Files integrated

- `src/rendercore/reel_intro/overlay.py`, `timeline.py`, and `beats.py`: adapted from the stand-alone reference project with package-relative imports.
- `src/rendercore/intro_renderer.py`: frame/audio analysis and FFmpeg I/O wrapper.
- `assets/reel_intro/`: all pre-generated runtime sprites, metadata, and pen-order map.
- `assets/reel_intro/source/` and `src/rendercore/reel_intro/logo_prep.py`: original logo inputs and the asset regeneration script.
- `docs/Vachika_Reel_Opening_Plan.md`: original opening plan preserved for reference.
- `tests/test_reel_intro_integration.py`: pre-roll planning and early-track edge-case coverage.
