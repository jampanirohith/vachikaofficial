# Vāchika 8D audio engine v3

Vāchika's `src/audio/eightd.py` now renders the whole-song 8D track directly from the four clean Demucs stems. It does not spatialize the prior `_8D.mp3`, and the final Reel continues to crop the resulting `SongName_8D.mp3` through the existing pipeline.

## What changed

- Keeps each Demucs stem's stereo mid/side content instead of unconditionally reducing every stem to mono.
- Applies a smooth, independent movement trajectory to vocals, drums, bass harmonics, and other instruments.
- Adds controlled interaural level and time differences, head-shadow filtering and front/back tonal cues.
- Keeps the deepest bass centered for stable low-end/mono playback, while allowing bass harmonics to move.
- Gives the vocal a dedicated small-room stereo return and uses a quieter shared room send to glue the mix together.
- Conservatively matches source RMS, limits the final sample peak, checks mono compatibility, and writes stereo-width, left/right-correlation and windowed pan-motion metrics to `audio/8d/manifest.json`.
- Changes the cache engine fingerprint to `unified-8d-v3-binaural-room`; old v2 outputs will be rebuilt rather than reused.

## Configuration

New controls live under `audio_8d.v3` in `config.json.example`. The existing legacy values such as `other_width`, `drums_width` and `vocal_center` are intentionally not reused by v3, so old narrow v2 settings do not accidentally constrain movement. If your runtime uses `config.json`, you do not have to edit it to activate v3: the code contains the v3 defaults. To customize the sound later, copy the `audio_8d.v3` block from `config.json.example` into your `config.json` and edit the movement or room values.

The main controls are:

- `movement.vocals.width` and `movement.vocals.room_send`: gentle vocal movement and how strongly the voice sits in the room.
- `movement.drums.width`, `movement.other.width`, and their `period_s`: movement amount and orbit speed.
- `movement.bass.width` and `movement.bass.low_anchor_hz`: movement of bass harmonics while the sub-bass remains centered.
- `room.vocal_rt60_seconds`: vocal room tail length. `room.master_send` controls how much shared ambience is added to the full mix.
- `head_shadow_cutoff_hz` and `back_cutoff_hz`: tonal cues used for far-ear and rearward positions.

## Validation

`manifest.json` records finite-sample validation, mono compatibility, output peak/RMS, left/right correlation, side-to-mid width and active-window pan-balance range. Stereo balance is only one measure of spatial movement; it cannot completely quantify binaural perception, so a symmetric song may receive `subtle_or_symmetric_mix` without being rejected solely for that metric. The regression suite includes synthetic movement, stereo preservation, bass anchoring, room-return, full render and cache-version checks.
