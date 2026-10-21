# Vāchika Reel Opening: Plan and Reference Implementation

Version of 14 Oct 2026. Covers the animated opening, the 8D audio, the Reel structure and how all of it plugs into the Python pipeline. The code in section 12 was run end to end on synthetic footage; section 13 lists what that did and did not prove.

## Contents

1. Summary
2. Decisions
3. Open decisions
4. Reel timeline
5. Opening: element specs
6. 8D audio
7. Body of the Reel
8. Ending
9. Layout and safe zones
10. Pipeline
11. Project layout and setup
12. Code
13. Testing
14. Tuning reference
15. Fonts and Telugu
16. Export settings
17. QA checklist
18. A/B test
19. Risks
20. Build order
21. Decision history

---

## 1. Summary

Every Reel opens with the song already playing, fading in over 0.3 s. Over a blurred, dimmed copy of the official video, the Vāchika icon draws itself in ink (nib, stem, bottom loop, knot, S-curve, top swoosh), the orange dot pops in and pulses once on the first beat, the waveform bars (four bars and a small end dot) rise and then follow the song's loudness, and the wordmark is revealed left to right. A headphone line fades in at the bottom. The blur then lifts to reveal the video, the logo dissolves, and the first lyric fades in at the place where the headphone line was. The opening is 3.2 s. There is no watermark.

Everything is generated in Python (numpy, OpenCV, Pillow, scipy, FFmpeg). No motion-graphics app is needed, because the logo file already has a transparency channel and the pen order is recovered from it automatically.

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| 1 | The song starts at frame 0 with a 0.3 s fade-in. | The hook is not delayed; a longer fade would eat it. |
| 2 | The animated logo and the name open the Reel, over the playing song. | Your call. The cost is that the video is hidden for about 2 s (see section 19). |
| 3 | The headphone line appears after the logo, at the bottom, and leaves as the first lyric arrives in the same spot. | One text element at a time. |
| 4 | The first lyric fades in slowly. The official video becomes visible as the blur lifts. | Calm opening for peaceful songs. |
| 5 | No watermark anywhere. | Your call. |
| 6 | The idea of the dot detaching from the logo and returning at the end is dropped. | Your call. |
| 7 | The official video of each song is the background. | Your call. |
| 8 | Peaceful throughout: slow motion, no strobing, no hard cuts, no beat-pulsing zooms. | Theme of the channel. |
| 9 | On the dimmed video the stroke and wordmark are cream (238, 230, 219, taken from your paper background). The dot stays orange. | Black ink vanishes on a dark backdrop. |
| 10 | The opening is identical on every Reel except that the bars follow the song and the dot pulse follows its first beat. | Recognisable repeat, still tied to the music. |
| 11 | One shared pan model drives the audio and any visual that shows where the sound is. | Picture and sound cannot disagree. |

## 3. Open decisions

Each has a default in the code, so nothing blocks the build.

| Topic | Default | Alternatives |
|---|---|---|
| Ending | Last lyric, 0.5 s audio and picture fade to black, no card. | A 0.5 s icon-only end frame; or hard stop on the last lyric. |
| Headphone wording | "Best with headphones · 8D" | "Put on your headphones", "Use headphones for the full 8D experience", plus a Telugu line (needs a native check). |
| Visuals that show the 8D in the body | None. | Crop drift (flag exists), pan dot, edge glow, depth softening (section 7). |
| Lyric movement | Stable lyrics. | Per-line drift of 3 to 5 % of the width, tied to the pan. Untested on Telugu script. |
| Framing | Crop (full-bleed 9:16 window). | Blurpad (whole picture centred over a blurred copy). |
| Pan style | Orbit, 16 s per turn. | Sway (stays in front, ±65°). |
| Intro or video first | Intro on. | A/B test, section 18. |

## 4. Reel timeline

Times are seconds from the first frame. `tv` is the time of the first sung syllable, which the planner places at 3.0 s or a little later (up to one beat period later).

| Time | Audio | Picture |
|---|---|---|
| 0.00 to 0.30 | Fade-in. The first beat lands at 0.30. | Blurred, dimmed copy of the live video (50 % brightness, vignette, slow scale 1.00 to 1.04). The orange dot pops in with a small overshoot and a soft glow. |
| 0.20 to 0.80 | | The stroke draws in pen order. A slightly brighter tip travels with the pen. |
| 0.45 to 0.83 | | The four bars and the small end dot pop in left to right, 40 ms apart, 0.22 s each. Between 0.75 and 0.95 they hand over to the song's loudness, each 35 ms behind the one before. |
| first beat at or after 0.45 s | | The dot pulses once: +16 % size, glow up. (0.97 s in the test.) |
| 0.80 to 1.20 | | Wordmark reveals left to right with a ragged ink edge. It stays until 2.2. |
| 1.20 to 1.80 | | Headphone line fades in and drifts up 14 px. |
| 1.80 to 2.80 | Pan width rises from 30 % to 100 % (reaches 100 % at 3.0). | The blur lifts over 1.0 s with an ease-in-out. Brightness settles at 90 %. |
| 2.20 to 2.90 | | The logo evaporates in place: blur, 40 px upward drift, fade. |
| 2.40 to 3.00 | | Headphone line fades out. |
| `tv − 0.4` to `tv + 0.2` | | First lyric fades in at the headphone line's position. |
| 3.20 | | The opening is finished. Later frames pass through untouched. |
| from 3.2 | Full 8D. | Video with the lyrics. |
| last 0.5 s | Fade-out. | Fade to black. |

## 5. Opening: element specs

**Backdrop.** The live video, not a still frame, so picture and audio stay alive together. A blurred copy is built at quarter resolution (Gaussian, sigma 5 at that scale), multiplied by 0.50 and a vignette (strength 0.35), then scaled slowly from 1.00 to 1.04. The sharp video sits underneath; the blurred copy's opacity goes from 1 to 0 between 1.8 and 2.8 s. A soft dark pad (up to 35 %) sits behind the lockup so cream stays readable on bright footage; it fades in with the logo and out with it, and its edge is tapered to zero so no rectangle shows.

**Icon stroke.** The logo PNG's transparency gives a clean matte. The stroke is a single connected shape, so a skeleton is computed and the pen order is defined by 14 waypoints (nib, stem, bottom of loop, left side, knot, S-curve, crossing, swoosh). Each stroke pixel gets the arc-length position of its nearest point on that path. At time t, pixels with position below the progress value are visible, with a feather of 0.018 and a bright band of 0.05 at the pen tip. Progress uses a brush-like ease (slow start, fast middle, soft landing). Where the pen crosses its own path, the first pass wins. The waypoints live in `logo_prep.py` and can be overridden with a JSON file if the order ever needs changing.

**Orange dot.** Kept with its own brush texture. Pops with an overshoot over 0.3 s, soft orange glow (radius 120 px at 55 %), one pulse on the first beat at or after 0.45 s.

**Bars.** Four bars and the small end dot, each its own sprite. They pop in 40 ms apart, then their height is `pop × (0.62 + 0.55 × loudness)` where loudness is the song's per-frame level (0 to 1, from the 8D mix), delayed 35 ms per bar so the motion travels outward.

**Wordmark.** Lifted from the cream lockup image as an alpha matte (background estimated by a large morphological close and blur). Reveal edge is displaced by smoothed noise (±7 % of the width) and feathered 18 %.

**Glow.** The union of all cream ink is blurred (computed at quarter resolution) and added at 35 %, so the logo looks lit rather than pasted.

**Evaporate.** Whole lockup blurred up to sigma 15 (half resolution above sigma 4), moved up 40 px, faded to zero.

**Headphone line.** A small headphone mark drawn with fine strokes (4× supersampled) and one line of text in the brand serif at 44 px, cream, with a soft dark shadow. Centre at y = 1450.

**First lyric.** Your lyric renderer draws it. Start its fade 0.4 s before the first syllable and let it run 0.6 s (`Timeline.first_lyric_alpha`). Place it where the headphone line sat.

**Audio.** 0.3 s fade-in with a raised-cosine curve. The first beat arrives at 0.30 s because the segment starts 0.3 s before it.

## 6. 8D audio

**Model.** `PanModel` gives the sound's position at any time. In orbit mode the angle is 360° × t / period, with 0° straight ahead, 90° right ear, 180° behind, 270° left ear. In sway mode the angle swings ±65° around the front. Lateral position is `0.92 × depth(t) × sin(angle)`. Depth rises from 0.30 to 1.00 over the first 3 s, so the effect opens as the blur lifts. In orbit mode the "behind" amount is `depth × (1 − cos(angle)) / 2`.

**Signal chain** (`render_8d`):

1. Split into mid and side. Only the mid is spatialised; the side keeps the original stereo ambience at half level.
2. Behind the head: blend toward a 4.5 kHz low-pass (up to 60 %) and lose up to 12 % level.
3. Head shadow: the ear farther from the sound gets a 3 kHz low-pass, blended by how far to the side the sound is.
4. Interaural time difference: the far ear is delayed up to 0.6 ms (linear interpolation).
5. Interaural level difference: equal-power panning, compressed with `ild_depth = 0.75` so the far ear never drops out completely.
6. Room: two decorrelated 1.3 s reverb tails, wet level 7 % in front rising to 24 % behind.
7. Fade-in (0.3 s) and fade-out (0.5 s), then a peak limit at 0.89 (−1 dBFS).

**Measured** on white noise with the final settings: the left/right level difference follows the model's lateral value with correlation 0.997; the largest difference is 18.8 dB at lateral 0.89; behind the listener the share of energy above 5 kHz falls from 0.69 to 0.27. A 9 s stereo segment renders in under a second.

**By-ear checks on headphones** (cannot be done by the code):

- The first second already feels slightly wide, and widens as the picture opens.
- Orbit passes behind without a click or a sudden dull patch.
- Vocals stay intelligible at the sides. If they get thin, lower `ild_depth` to 0.6 or raise `side_gain`.
- On a phone speaker the Reel must still sound normal (the effect should be subtle, not broken).

**Mood presets.** Peaceful default: orbit, 16 s. Slower songs: 20 s. Busy or rhythmic songs: sway, 12 s, since a full orbit under strong rhythm can feel dizzy.

## 7. Body of the Reel

**Grade and scrim.** Every frame is brightness-trimmed to 90 % and gets a soft scrim that darkens toward the bottom (up to 30 %) so lyrics stay readable on any footage.

**Lyrics.** Stable by default, fade in and out about 0.35 s, in the lower third at y ≈ 1450, inside the safe zone. They are drawn after the intro, so the first lyric can fade in over the place where the headphone line was.

**Options that show the 8D** (not decided; only crop drift is implemented):

| Option | What it does | Notes |
|---|---|---|
| Crop drift | The 9:16 window slides sideways with the pan (`--drift 0.04` is 4 % of the source width). | Needs the subject to stay in frame. With face tracking, limit the drift. Implemented behind a flag. |
| Pan dot | The orange dot glides along a thin hairline in a quiet band of the frame, following `pan.lateral(t)`. | Ties to the logo. Not implemented. |
| Edge glow | A soft warm gradient brightens at the left or right edge as the sound passes it. | Not implemented. |
| Depth softening | When the sound is behind, the footage softens 1 to 2 %. | Uses `pan.back(t)`. Not implemented. |

All of these read from the same `PanModel`, so adding one is a few lines in the frame loop.

## 8. Ending

Default: the last lyric fades, the audio tails and fades out over 0.5 s, the picture fades to black over the same 0.5 s. Choose the end of the segment with `snap_end` so it lands on a beat after the last lyric plus a short tail. No end card, since there is no watermark and the logo has already been shown.

Loop note: Reels replay automatically. A fade to black followed by a restart on a blurred, dim frame is a gentle loop point.

## 9. Layout and safe zones

Canvas 1080 × 1920, 30 fps.

| Item | Value |
|---|---|
| Platform UI (approximate, check on a real phone) | top 12 % (y < 230), bottom 20 % (y > 1536), right 12 % (x > 950) |
| Icon | 640 px tall, bounding box centred at x = 540, y = 790 |
| Wordmark | 600 px wide, top at y = 1150 |
| Headphone line | centred at y = 1450, text 44 px |
| Lyrics | centred around y = 1450 |

All of these are in `Layout` in `timeline.py`.

## 10. Pipeline

```
PER SONG (your existing steps)
  official video + audio  ->  synced Telugu lyrics (timestamps)  ->  hook segment chosen
  first_vocal = timestamp of the first sung line inside / just after the hook start

PER REEL  (render.py : render_reel)
  [1] decode_song          song file  -> float32 stereo, 44.1 kHz
      beat_times           -> beat times (librosa if installed, numpy fallback otherwise)
  [2] plan_segment         first_vocal + beats -> segment start, end,
                           first_vocal_rel (>= 3.0 s), beat_t (first beat >= 0.45 s)
                           start = (latest beat that leaves >= 3.0 s before the vocal) - 0.3 s
  [3] PanModel + render_8d segment audio -> 8D stereo with depth ramp, 0.3 s fade-in, 0.5 s fade-out
                           written to out/reel_audio.wav
  [4] frame_rms            8D audio -> loudness per video frame (drives the logo bars)
  [5] open_decoder         FFmpeg: video segment -> raw BGR frames at 30 fps
  [6] per frame:
        reframe_crop / reframe_blurpad     source -> 1080 x 1920
        cv2.multiply(grade)                brightness 90 % + bottom scrim
        IntroOverlay.apply(frame, t)       only while t < 3.2 s
        lyrics_fn(frame, t)                YOUR lyric renderer
        end fade (last 0.5 s)
        optional: cover PNG at --cover-time
        write frame to the encoder pipe
  [7] open_encoder         FFmpeg: frames + reel_audio.wav -> H.264 / AAC mp4

ONE TIME (logo_prep.py)
  logo PNG (transparent) + cream lockup  ->  assets/stroke.png, dot.png, bar_0..4.png,
  wordmark.png, param_map.npy (pen order), meta.json, debug_pen_chain.png
```

**Hooking into your existing pipeline.**

1. Replace your final "cut segment and mux" step with `render_reel(...)`. Pass `first_vocal` as the song time of the first sung line in your chosen hook.
2. Pass your lyric drawing function as `lyrics_fn(frame_bgr_uint8, t_seconds) -> frame`. If your renderer works on PIL images or MoviePy clips, wrap it so it returns a BGR uint8 array of the same size. `t` is seconds from the start of the Reel, not from the start of the song.
3. Keep one config flag for the intro (`intro=True/False`, or `--no-intro`). That flag is also what the A/B test uses.
4. If the 3 seconds before your hook contain sung lines, the lead-in would play vocals with no lyrics on screen. In that case either start the hook later, accept a shorter lead-in (the code then hides the headphone line earlier, or drops it if there is no room; see `Timeline.for_vocal`), or run that song with `--no-intro`.
5. If beat detection is wrong on a song (listen to the first beat against the dot pulse), use `--beat-offset` to shift the grid.

## 11. Project layout and setup

```
vachika_intro/
  assets_src/logo_white.png          transparent icon (black ink + orange dot)
  assets_src/logo_cream_lockup.png   icon + wordmark on cream paper (wordmark source)
  assets/                            generated by logo_prep.py (and assets/fonts/ for the brand serif)
  timeline.py  logo_prep.py  intro_overlay.py  audio_8d.py  beats.py  render.py
  preview.py  make_test_assets.py    (tools)
  out/                               renders
```

Install: Python 3.10 or newer, FFmpeg on the PATH, then

```
pip install numpy scipy opencv-python pillow scikit-image librosa
```

One-time asset build:

```
python logo_prep.py --logo assets_src/logo_white.png --lockup assets_src/logo_cream_lockup.png --out assets
```

Open `assets/debug_pen_chain.png`. The coloured line should run blue to red in this order: nib, down the stem, round the bottom loop, up the left of the loop, through the knot, up the S-curve, out along the top swoosh. If the order is wrong, pass your own waypoints with `--waypoints file.json`.

Preview only the opening (no pipeline needed):

```
python preview.py some_footage.mp4
```

This writes `out/intro_preview.mp4` and a contact sheet `out/intro_sheet.png`.

Render a Reel:

```
python render.py --video official.mp4 --song song.wav --out out/reel.mp4 --first-vocal 31.2 --seg-len 24 --cover-time 4.0
```

## 12. Code

Seven modules and two test tools. They are listed in dependency order.

### timeline.py (all timings, easing curves, layout)

```python
"""All timings, easing curves and layout constants for the Vachika opening.

Every number the animation uses lives here, so tuning means editing this file
and nothing else. Times are seconds from the first frame of the Reel.
"""
from dataclasses import dataclass, field

import numpy as np

W, H, FPS = 1080, 1920, 30

# Reels/Shorts UI covers roughly: top 12 %, bottom 20 %, right 12 %
SAFE = dict(top=int(H * 0.12), bottom=int(H * 0.80), right=int(W * 0.88))


# ---------------------------------------------------------------- easing --
def clamp01(x):
    return np.clip(x, 0.0, 1.0)


def seg(t, a, b):
    """0 before a, 1 after b, linear in between."""
    return clamp01((t - a) / max(b - a, 1e-9))


def smooth(x):
    x = clamp01(x)
    return x * x * (3 - 2 * x)


def ease_in_out(x):
    x = clamp01(x)
    return np.where(x < 0.5, 4 * x ** 3, 1 - (-2 * x + 2) ** 3 / 2)


def ease_out(x):
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def ease_out_back(x, s=1.4):
    x = clamp01(x)
    return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2


def pen_ease(x):
    """Brush pressure: slow start, fast middle, soft landing."""
    return smooth(smooth(clamp01(x)) * 0.35 + clamp01(x) * 0.65)


# --------------------------------------------------------------- timeline --
@dataclass
class Timeline:
    duration: float = 3.2

    # audio
    audio_fade_in: float = 0.3            # keep <= 0.3 s, longer eats the hook
    pan_depth_start: float = 0.30         # share of full width at t = 0
    pan_depth_full_at: float = 3.0        # reaches 1.0 here

    # backdrop (blurred copy of the live video, faded out to reveal it)
    blur_hold_until: float = 1.8
    blur_lift_end: float = 2.8
    blur_dim: float = 0.50                # brightness of the blurred copy
    breathe_scale: float = 0.04           # 1.00 -> 1.04 over the blur phase
    vignette: float = 0.35

    # logo
    dot_in: tuple = (0.00, 0.30)
    stroke: tuple = (0.20, 0.80)
    bars_start: float = 0.45              # first bar; others follow 40 ms apart
    bar_stagger: float = 0.04
    bar_pop: float = 0.22
    wordmark: tuple = (0.80, 1.20)
    logo_evaporate: tuple = (2.20, 2.90)

    # headphone line
    hp_in: tuple = (1.20, 1.80)
    hp_out: tuple = (2.40, 3.00)

    # first lyric (centred on the first sung syllable)
    first_vocal: float = 3.0              # segment time of the first sung line
    first_lyric_fade: float = 0.6

    def for_vocal(self, first_vocal: float) -> "Timeline":
        """Copy adjusted when the first vocal comes earlier than planned."""
        tl = Timeline(**{**self.__dict__})
        tl.first_vocal = first_vocal
        if first_vocal < 3.0:
            end = max(first_vocal - 0.1, 0.0)
            tl.hp_out = (max(end - 0.6, tl.hp_in[1]), end)
        return tl

    @property
    def hp_enabled(self) -> bool:
        return self.hp_out[1] - self.hp_in[1] > 0.3

    # --- curves, all vectorised over t -----------------------------------
    def pan_depth(self, t):
        return self.pan_depth_start + (1 - self.pan_depth_start) * smooth(seg(t, 0, self.pan_depth_full_at))

    def blur_mix(self, t):
        """1 = fully blurred/dimmed copy on top, 0 = sharp video."""
        return 1 - ease_in_out(seg(t, self.blur_hold_until, self.blur_lift_end))

    def breathe(self, t):
        return 1.0 + self.breathe_scale * smooth(seg(t, 0, self.blur_lift_end))

    def logo_alpha(self, t):
        return 1 - ease_in_out(seg(t, *self.logo_evaporate))

    def logo_presence(self, t):
        """How strongly the dark pad behind the logo should show."""
        return smooth(seg(t, 0.10, 0.70)) * self.logo_alpha(t)

    def evaporate(self, t):
        return ease_in_out(seg(t, *self.logo_evaporate))

    def hp_alpha(self, t):
        if not self.hp_enabled:
            return 0.0 * np.asarray(t, dtype=float)
        return smooth(seg(t, *self.hp_in)) * (1 - smooth(seg(t, *self.hp_out)))

    def first_lyric_alpha(self, t):
        a = self.first_vocal - 0.4
        return smooth(seg(t, a, a + self.first_lyric_fade))


# ----------------------------------------------------------------- layout --
@dataclass
class Layout:
    """Where things sit on the 1080 x 1920 canvas."""
    icon_height: int = 640                 # px, on canvas
    icon_center_y: int = 790
    wordmark_width: int = 600
    wordmark_top: int = 1150
    hp_center_y: int = 1450                # above the bottom UI band (y > 1536)
    hp_font_px: int = 44
    glow_sigma: float = 10.0
    glow_gain: float = 0.35
    dot_glow_radius: int = 120
    dot_glow_gain: float = 0.55
```

### logo_prep.py (one-time logo preparation)

```python
"""One-time logo preparation.

Input : transparent PNG of the icon (black ink + orange dot) and the cream
        lockup image (used only to lift the wordmark).
Output: assets/ with
          stroke.png, dot.png, bar_0..4.png, wordmark.png   (RGBA, cropped)
          param_map.npy   pen-order position (0..1) for every stroke pixel
          meta.json       bounding boxes, colours, waypoints used

Run:  python logo_prep.py --logo assets_src/logo_white.png \
                          --lockup assets_src/logo_cream_lockup.png \
                          --out assets
"""
import argparse
import json
import os

import cv2
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from skimage.morphology import skeletonize

CREAM_RGB = (238, 230, 219)   # sampled from the paper of the lockup image

# Pen order for the current icon, in the pixel coordinates of the 1254x1254
# logo file. They are snapped to the nearest skeleton pixel, so they only need
# to be close. Order: nib, down the stem, round the bottom loop, back up
# through the knot, up the S-curve, out along the top swoosh.
DEFAULT_WAYPOINTS = [
    (650, 50), (636, 420), (638, 700), (560, 1130), (500, 1172),
    (372, 1010), (500, 810), (632, 680), (545, 540), (585, 470),
    (632, 400), (720, 280), (765, 200), (692, 130),
]


def save_rgba(path, rgb, alpha):
    out = np.dstack([rgb[..., ::-1], alpha]).astype(np.uint8)  # to BGRA for cv2
    cv2.imwrite(path, out)


def crop_box(mask, pad=4):
    ys, xs = np.nonzero(mask)
    return (int(xs.min()) - pad, int(ys.min()) - pad,
            int(xs.max()) + pad + 1, int(ys.max()) + pad + 1)


def split_logo(logo_path):
    im = cv2.imread(logo_path, cv2.IMREAD_UNCHANGED)
    if im is None or im.ndim != 3 or im.shape[2] != 4:
        raise SystemExit("logo must be a PNG with a transparency channel")
    bgr, alpha = im[..., :3], im[..., 3]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    H, S, V = [hsv[..., i].astype(int) for i in range(3)]
    solid = alpha > 128
    orange = solid & (S > 110) & (V > 90) & (H >= 5) & (H <= 25)
    orange_near = cv2.dilate(orange.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    ink = solid & ~orange_near
    n, lab, st, cen = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    comps = [i for i in range(1, n) if st[i, 4] > 300]
    comps.sort(key=lambda i: -st[i, 4])
    stroke_id, bar_ids = comps[0], sorted(comps[1:], key=lambda i: cen[i][0])
    return bgr, alpha, orange, orange_near, lab, stroke_id, bar_ids


def ink_layer(alpha, lab, comp_id, grow=3):
    """Alpha restricted to one connected component (grown a little to keep the
    soft anti-aliased edge), tinted cream."""
    region = cv2.dilate((lab == comp_id).astype(np.uint8), np.ones((grow * 2 + 1,) * 2, np.uint8)) > 0
    return np.where(region, alpha, 0).astype(np.uint8)


def skeleton_graph(mask):
    skel = skeletonize(mask > 0)
    ys, xs = np.nonzero(skel)
    idx = -np.ones(mask.shape, np.int64)
    idx[ys, xs] = np.arange(len(ys))
    rows, cols, w = [], [], []
    for dy, dx in [(0, 1), (1, 0), (1, 1), (1, -1)]:
        y2, x2 = ys + dy, xs + dx
        ok = (y2 >= 0) & (y2 < mask.shape[0]) & (x2 >= 0) & (x2 < mask.shape[1])
        a = np.nonzero(ok)[0]
        b = idx[y2[a], x2[a]]
        keep = b >= 0
        a, b = a[keep], b[keep]
        rows += [a, b]
        cols += [b, a]
        w += [np.full(len(a) * 2, np.hypot(dy, dx))]
    g = coo_matrix((np.concatenate(w), (np.concatenate(rows), np.concatenate(cols))),
                   shape=(len(ys), len(ys))).tocsr()
    return np.c_[xs, ys], g


def pen_chain(pts, graph, waypoints):
    """Ordered skeleton points along the pen path through the waypoints."""
    tree = cKDTree(pts)
    snapped = [int(tree.query(w)[1]) for w in waypoints]
    chain = []
    for a, b in zip(snapped[:-1], snapped[1:]):
        _, pred = dijkstra(graph, directed=False, indices=a, return_predecessors=True)
        path, cur = [b], b
        while cur != a:
            cur = pred[cur]
            if cur < 0:
                raise SystemExit("waypoints are not connected on the skeleton; adjust them")
            path.append(cur)
        chain += path[::-1][1:] if chain else path[::-1]
    return pts[chain]


def build_param_map(stroke_alpha, waypoints):
    mask = (stroke_alpha > 128).astype(np.uint8)
    pts, g = skeleton_graph(mask)
    chain = pen_chain(pts, g, waypoints)
    step = np.r_[0, np.hypot(*np.diff(chain, axis=0).T)]
    arc = np.cumsum(step)
    arc /= arc[-1]
    # first occurrence wins where the pen crosses its own path
    _, first = np.unique(chain, axis=0, return_index=True)
    first.sort()
    tree = cKDTree(chain[first])
    ys, xs = np.nonzero(stroke_alpha > 0)
    _, j = tree.query(np.c_[xs, ys])
    param = np.full(stroke_alpha.shape, 2.0, np.float32)
    param[ys, xs] = arc[first][j]
    return param, chain, pts


def extract_wordmark(lockup_path, y_min=872):
    """Lift the wordmark off the textured paper as an alpha matte.

    y_min must sit below the icon's lowest point in the lockup image and above
    the macron of the wordmark."""
    im = cv2.imread(lockup_path, cv2.IMREAD_COLOR)
    lum = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).astype(np.float32)
    bg = cv2.GaussianBlur(cv2.morphologyEx(lum, cv2.MORPH_CLOSE, np.ones((41, 41), np.uint8)), (0, 0), 15)
    a = np.clip((bg - lum) / np.maximum(bg - 45.0, 1.0), 0, 1)
    a = np.clip((a - 0.22) / 0.55, 0, 1)          # drop paper grain, keep ink edge
    a[:y_min] = 0
    box = crop_box(a > 0.5, pad=6)
    x0, y0, x1, y1 = box
    return (a[y0:y1, x0:x1] * 255).astype(np.uint8), box


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logo", required=True)
    ap.add_argument("--lockup", required=True)
    ap.add_argument("--out", default="assets")
    ap.add_argument("--waypoints", help="JSON file: [[x, y], ...] in logo pixel coordinates")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    waypoints = DEFAULT_WAYPOINTS
    if args.waypoints:
        waypoints = [tuple(p) for p in json.load(open(args.waypoints))]

    bgr, alpha, orange, orange_near, lab, stroke_id, bar_ids = split_logo(args.logo)
    cream = np.zeros(bgr.shape, np.uint8)
    cream[:] = CREAM_RGB[::-1]            # BGR
    cream_rgb = cream[..., ::-1]

    meta = {"canvas": list(alpha.shape[::-1]), "cream_rgb": CREAM_RGB, "layers": {}}

    stroke_a = ink_layer(alpha, lab, stroke_id)
    layers = {"stroke": stroke_a}
    for k, i in enumerate(bar_ids):
        layers[f"bar_{k}"] = ink_layer(alpha, lab, i)

    for name, a in layers.items():
        x0, y0, x1, y1 = crop_box(a > 0)
        save_rgba(os.path.join(args.out, f"{name}.png"), cream_rgb[y0:y1, x0:x1], a[y0:y1, x0:x1])
        meta["layers"][name] = [x0, y0, x1, y1]

    # orange dot keeps its own brush texture; un-premultiply from the edge
    dot_a = np.where(orange_near, alpha, 0).astype(np.uint8)
    x0, y0, x1, y1 = crop_box(dot_a > 0)
    save_rgba(os.path.join(args.out, "dot.png"), bgr[y0:y1, x0:x1][..., ::-1], dot_a[y0:y1, x0:x1])
    meta["layers"]["dot"] = [x0, y0, x1, y1]

    param, chain, _ = build_param_map(stroke_a, waypoints)
    sx0, sy0, sx1, sy1 = meta["layers"]["stroke"]
    np.save(os.path.join(args.out, "param_map.npy"), param[sy0:sy1, sx0:sx1])
    meta["waypoints"] = [list(map(int, w)) for w in waypoints]

    wm_a, wm_box = extract_wordmark(args.lockup)
    wm_rgb = np.zeros(wm_a.shape + (3,), np.uint8)
    wm_rgb[:] = CREAM_RGB
    save_rgba(os.path.join(args.out, "wordmark.png"), wm_rgb, wm_a)
    meta["wordmark_size"] = [int(wm_a.shape[1]), int(wm_a.shape[0])]

    ys, xs = np.nonzero(alpha > 128)
    meta["icon_bbox"] = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    json.dump(meta, open(os.path.join(args.out, "meta.json"), "w"), indent=1)

    # debug picture: skeleton chain coloured by pen order
    dbg = np.full(alpha.shape + (3,), 255, np.uint8)
    dbg[stroke_a > 128] = (200, 200, 200)
    for k, (x, y) in enumerate(chain):
        c = cv2.applyColorMap(np.uint8([[int(255 * k / len(chain))]]), cv2.COLORMAP_TURBO)[0, 0]
        cv2.circle(dbg, (int(x), int(y)), 3, tuple(int(v) for v in c), -1)
    for i, (x, y) in enumerate(waypoints):
        cv2.putText(dbg, str(i), (int(x) + 8, int(y)), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
    cv2.imwrite(os.path.join(args.out, "debug_pen_chain.png"), dbg)
    print("layers:", list(meta["layers"]), "| chain points:", len(chain))


if __name__ == "__main__":
    main()
```

### intro_overlay.py (the opening, per frame)

```python
"""The Vachika opening, drawn frame by frame with numpy + OpenCV.

    overlay = IntroOverlay("assets", env=rms_per_frame, beat_t=0.45)
    frame   = overlay.apply(frame_bgr_uint8, t)       # t in seconds

Call it on every frame; after the intro ends it returns the frame untouched,
so it costs nothing for the rest of the Reel. Frames are BGR uint8, 1080x1920.
"""
import json
import os

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from timeline import FPS, H, W, Layout, Timeline, clamp01, ease_out, ease_out_back, pen_ease, seg, smooth

CREAM = np.array([219, 230, 238], np.float32) / 255.0         # BGR


# ------------------------------------------------------------ helpers -------
def load_rgba(path):
    im = cv2.imread(path, cv2.IMREAD_UNCHANGED).astype(np.float32) / 255.0
    return im[..., :3], im[..., 3]                              # BGR, alpha


def resize_to(arr, scale, interp=cv2.INTER_AREA):
    h, w = arr.shape[:2]
    return cv2.resize(arr, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=interp)


def paste_max_alpha(dst, src, x, y):
    """dst = union(dst, src) for single-channel alpha arrays, with clipping."""
    h, w = src.shape
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, dst.shape[1]), min(y + h, dst.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    s = src[y0 - y:y1 - y, x0 - x:x1 - x]
    d = dst[y0:y1, x0:x1]
    dst[y0:y1, x0:x1] = 1 - (1 - d) * (1 - s)


def over(dst_pm, src_pm):
    """Premultiplied RGBA 'over', in place on dst."""
    dst_pm *= (1 - src_pm[..., 3:4])
    dst_pm += src_pm


def paste_pm(dst_pm, sprite_pm, x, y):
    h, w = sprite_pm.shape[:2]
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, dst_pm.shape[1]), min(y + h, dst_pm.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    s = sprite_pm[y0 - y:y1 - y, x0 - x:x1 - x]
    d = dst_pm[y0:y1, x0:x1]
    d *= (1 - s[..., 3:4])
    d += s


def to_pm(bgr, a):
    return np.dstack([bgr * a[..., None], a]).astype(np.float32)


def radial(radius, sigma_ratio=0.42):
    r = radius
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1].astype(np.float32)
    g = np.exp(-(xx ** 2 + yy ** 2) / (2 * (r * sigma_ratio) ** 2))
    g *= clamp01(1 - np.hypot(xx, yy) / r)                       # hard zero at the edge
    return g


def find_font(candidates, size):
    for p in candidates:
        if p and os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


SERIF_FALLBACKS = [
    "assets/fonts/CormorantGaramond-Medium.ttf",      # put the brand serif here
    "C:/Windows/Fonts/georgia.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
]


def headphone_glyph(px, color=(238, 230, 219)):
    """Small headphone mark drawn with fine strokes, 4x supersampled."""
    s = 4
    size = px * s
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    lw = max(2, int(size * 0.075))
    pad = int(size * 0.12)
    d.arc([pad, pad, size - pad, size * 1.25 - pad], 180, 360, fill=color + (255,), width=lw)
    cup_w, cup_h = int(size * 0.20), int(size * 0.34)
    cy = int(size * 0.62)
    for cx in (pad, size - pad):
        d.rounded_rectangle([cx - cup_w // 2, cy, cx + cup_w // 2, cy + cup_h],
                            radius=cup_w // 2, fill=color + (255,))
    return im.resize((px, px), Image.LANCZOS)


# -------------------------------------------------------------- overlay -----
class IntroOverlay:
    def __init__(self, assets="assets", env=None, beat_t=0.6, timeline=None, layout=None,
                 hp_lines=("Best with headphones  ·  8D",), fonts=None, size=(W, H), fps=FPS):
        self.fps = fps
        self.tl = timeline or Timeline()
        self.lo = layout or Layout()
        self.W, self.H = size
        self.meta = json.load(open(os.path.join(assets, "meta.json")))
        self.env_arr = None if env is None else np.asarray(env, np.float32)
        self.beat_t = beat_t
        self._build_logo(assets)
        self._build_backdrop()
        self._build_headphone_line(hp_lines, fonts or {})

    # ---- one-time setup ----------------------------------------------------
    def _build_logo(self, assets):
        lo, m = self.lo, self.meta
        bx0, by0, bx1, by1 = m["icon_bbox"]
        s = lo.icon_height / (by1 - by0)
        self.s = s
        # canvas = s * logo + offset, icon bbox centred on x = W/2
        ox = self.W / 2 - s * (bx0 + bx1) / 2
        oy = lo.icon_center_y - s * (by0 + by1) / 2

        wm_bgr, wm_a = load_rgba(os.path.join(assets, "wordmark.png"))
        wm_scale = lo.wordmark_width / wm_a.shape[1]
        self.wm_a = resize_to(wm_a, wm_scale)
        self.wm_xy = (round(self.W / 2 - self.wm_a.shape[1] / 2), lo.wordmark_top)

        # region of interest that holds everything the logo can touch
        margin = 110
        self.roi = (max(0, self.W // 2 - lo.wordmark_width // 2 - margin),
                    max(0, round(oy + s * by0) - margin),
                    min(self.W, self.W // 2 + lo.wordmark_width // 2 + margin),
                    min(self.H, lo.wordmark_top + self.wm_a.shape[0] + margin))
        rx0, ry0 = self.roi[0], self.roi[1]
        self.rw, self.rh = self.roi[2] - rx0, self.roi[3] - ry0

        def place(name):
            x0, y0, x1, y1 = m["layers"][name]
            return round(ox + s * x0) - rx0, round(oy + s * y0) - ry0

        _, st_a = load_rgba(os.path.join(assets, "stroke.png"))
        self.stroke_a = resize_to(st_a, s)
        param = np.load(os.path.join(assets, "param_map.npy"))
        self.param = cv2.resize(param, self.stroke_a.shape[::-1], interpolation=cv2.INTER_NEAREST)
        self.stroke_xy = place("stroke")

        self.bars = []
        for k in range(5):
            _, a = load_rgba(os.path.join(assets, f"bar_{k}.png"))
            a = resize_to(a, s)
            x, y = place(f"bar_{k}")
            self.bars.append(dict(a=a, x=x, cy=y + a.shape[0] / 2))

        dot_bgr, dot_a = load_rgba(os.path.join(assets, "dot.png"))
        self.dot_bgr = resize_to(dot_bgr, s)
        self.dot_a = resize_to(dot_a, s)
        dx, dy = place("dot")
        self.dot_c = (dx + self.dot_a.shape[1] / 2, dy + self.dot_a.shape[0] / 2)
        self.dot_glow = radial(lo.dot_glow_radius)
        self.ORANGE = np.array([40, 100, 215], np.float32) / 255.0       # BGR glow tint

        rng = np.random.default_rng(7)
        n = cv2.GaussianBlur(rng.standard_normal(self.wm_a.shape).astype(np.float32), (0, 0), 7)
        self.wm_noise = n / np.abs(n).max()
        self.wm_x = np.linspace(0, 1, self.wm_a.shape[1], dtype=np.float32)[None, :]

        # soft dark blob behind the lockup, full ROI size
        yy, xx = np.mgrid[0:self.rh, 0:self.rw].astype(np.float32)
        cx, cy = self.rw / 2, (lo.icon_center_y + 180) - self.roi[1]
        g = np.exp(-(((xx - cx) / (self.rw * 0.30)) ** 2 + ((yy - cy) / (self.rh * 0.32)) ** 2))
        taper = (np.sin(np.pi * xx / self.rw) * np.sin(np.pi * yy / self.rh)) ** 0.5   # 0 at every edge
        self.blob = (g * taper).astype(np.float32)

    def _build_backdrop(self):
        sw, sh = self.W // 4, self.H // 4
        self.small = (sw, sh)
        yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
        r = np.hypot((xx - sw / 2) / (sw / 2), (yy - sh / 2) / (sh / 2)) / 1.2
        self.vig_small = (1 - self.tl.vignette * clamp01(r) ** 2)[..., None]

    def _build_headphone_line(self, lines, fonts):
        lo = self.lo
        font = find_font([fonts.get("latin")] + SERIF_FALLBACKS, lo.hp_font_px)
        glyph_px = round(lo.hp_font_px * 1.15)
        rows = []
        for text in lines:
            f = font
            if any("ఀ" <= ch <= "౿" for ch in text):            # Telugu block
                f = find_font([fonts.get("telugu")], round(lo.hp_font_px * 0.9))
            bbox = f.getbbox(text)
            rows.append((text, f, bbox[2] - bbox[0], bbox[3] - bbox[1]))
        gap = 22
        total_w = glyph_px + gap + max(r[2] for r in rows)
        total_h = max(glyph_px, sum(r[3] for r in rows) + 12 * (len(rows) - 1))
        pad = 36
        im = Image.new("RGBA", (total_w + 2 * pad, total_h + 2 * pad), (0, 0, 0, 0))
        im.alpha_composite(headphone_glyph(glyph_px), (pad, pad + (total_h - glyph_px) // 2))
        d = ImageDraw.Draw(im)
        y = pad
        for text, f, w, h in rows:
            d.text((pad + glyph_px + gap, y - f.getbbox(text)[1]), text, font=f, fill=(238, 230, 219, 255))
            y += h + 12
        # soft shadow so the line reads on bright footage
        shadow = im.split()[3].filter(ImageFilter.GaussianBlur(9))
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        sh.putalpha(shadow.point(lambda v: int(v * 0.75)))
        sh.alpha_composite(im)
        arr = np.asarray(sh, np.float32) / 255.0
        self.hp = to_pm(arr[..., [2, 1, 0]], arr[..., 3])
        self.hp_xy = (self.W // 2 - self.hp.shape[1] // 2, lo.hp_center_y - self.hp.shape[0] // 2)

    # ---- per-frame helpers -------------------------------------------------
    def env(self, t):
        if self.env_arr is None:
            return 0.5 + 0.5 * np.sin(2 * np.pi * t * 1.4)          # visible placeholder motion
        i = float(np.clip(t * self.fps, 0, len(self.env_arr) - 1))
        lo_i = int(np.floor(i))
        hi_i = min(lo_i + 1, len(self.env_arr) - 1)
        return float(self.env_arr[lo_i] * (1 - (i - lo_i)) + self.env_arr[hi_i] * (i - lo_i))

    def _logo_layer(self, t):
        """Premultiplied RGBA of the whole lockup for time t, ROI-sized."""
        tl, lo = self.tl, self.lo
        ink = np.zeros((self.rh, self.rw), np.float32)

        # stroke: reveal along pen order, brighter right at the pen tip
        k_stroke = float(seg(t, *tl.stroke))
        p = float(pen_ease(k_stroke)) * 1.04
        vis = smooth((p - self.param) / 0.018)
        paste_max_alpha(ink, self.stroke_a * vis, *self.stroke_xy)
        tip = None
        if 0 < k_stroke < 1:
            tip = np.zeros_like(ink)
            paste_max_alpha(tip, (clamp01(1 - np.abs(p - self.param) / 0.05) * self.stroke_a).astype(np.float32),
                            *self.stroke_xy)

        # bars: pop in left to right, then follow the song's loudness
        for k, b in enumerate(self.bars):
            t0 = tl.bars_start + k * tl.bar_stagger
            pop = float(ease_out_back(seg(t, t0, t0 + tl.bar_pop)))
            if pop <= 0.01:
                continue
            react = 0.62 + 0.55 * self.env(t - 0.035 * k)
            blend = float(smooth(seg(t, tl.bars_start + 0.3, tl.bars_start + 0.5)))   # hand over to the song
            h = round(b["a"].shape[0] * pop * (1 + blend * (react - 1)))
            if h < 2:
                continue
            a = cv2.resize(b["a"], (b["a"].shape[1], h), interpolation=cv2.INTER_LINEAR)
            paste_max_alpha(ink, a, b["x"], round(b["cy"] - h / 2))

        # wordmark: left-to-right ink reveal with a ragged edge
        r = float(ease_out(seg(t, *tl.wordmark))) * 1.35
        if r > 0:
            wm = self.wm_a * smooth((r - (self.wm_x + 0.07 * self.wm_noise)) / 0.18)
            paste_max_alpha(ink, wm.astype(np.float32), self.wm_xy[0] - self.roi[0], self.wm_xy[1] - self.roi[1])

        # soft glow, computed at quarter resolution
        q = (max(1, self.rw // 4), max(1, self.rh // 4))
        glow = cv2.GaussianBlur(cv2.resize(ink, q, interpolation=cv2.INTER_AREA), (0, 0), lo.glow_sigma / 4)
        glow = cv2.resize(glow, (self.rw, self.rh), interpolation=cv2.INTER_LINEAR) * lo.glow_gain
        total_a = 1 - (1 - ink) * (1 - glow)
        layer = np.empty((self.rh, self.rw, 4), np.float32)
        layer[..., :3] = total_a[..., None] * CREAM
        layer[..., 3] = total_a
        if tip is not None:
            layer[..., :3] += (tip * 0.8 * total_a)[..., None] * (1 - CREAM)

        # orange dot: glow underneath, dot on top, pulse on the first beat
        dot_k = float(seg(t, *tl.dot_in))
        dot_s = float(ease_out_back(dot_k))
        pulse = float(np.exp(-((t - self.beat_t) / 0.11) ** 2))
        if dot_s > 0.01:
            g = self.dot_glow * (lo.dot_glow_gain * float(smooth(dot_k)) * (1 + 0.8 * pulse))
            gp = np.empty(g.shape + (4,), np.float32)
            gp[..., :3] = g[..., None] * self.ORANGE
            gp[..., 3] = g
            gx, gy = round(self.dot_c[0] - g.shape[1] / 2), round(self.dot_c[1] - g.shape[0] / 2)
            x0, y0 = max(gx, 0), max(gy, 0)
            x1, y1 = min(gx + g.shape[1], self.rw), min(gy + g.shape[0], self.rh)
            reg = layer[y0:y1, x0:x1]
            reg += gp[y0 - gy:y1 - gy, x0 - gx:x1 - gx] * (1 - reg[..., 3:4])      # glow goes *under* the ink
            sc = max(dot_s * (1 + 0.16 * pulse), 0.02)
            sp = to_pm(cv2.resize(self.dot_bgr, None, fx=sc, fy=sc, interpolation=cv2.INTER_LINEAR),
                       cv2.resize(self.dot_a, None, fx=sc, fy=sc, interpolation=cv2.INTER_LINEAR))
            paste_pm(layer, sp, round(self.dot_c[0] - sp.shape[1] / 2), round(self.dot_c[1] - sp.shape[0] / 2))

        # evaporate: blur, drift upward, fade
        e = float(tl.evaporate(t))
        if e > 0.001:
            sig = 1 + 14 * e
            if sig > 4:                                   # heavy blur: do it at half resolution
                half = cv2.resize(layer, (self.rw // 2, self.rh // 2), interpolation=cv2.INTER_AREA)
                half = cv2.GaussianBlur(half, (0, 0), sig / 2)
                layer = cv2.resize(half, (self.rw, self.rh), interpolation=cv2.INTER_LINEAR)
            else:
                layer = cv2.GaussianBlur(layer, (0, 0), sig)
            layer = cv2.warpAffine(layer, np.float32([[1, 0, 0], [0, 1, -40 * e]]), (self.rw, self.rh))
            layer *= float(tl.logo_alpha(t))
        return layer

    # ---- public --------------------------------------------------------------
    def apply(self, frame, t):
        tl = self.tl
        if t >= tl.duration:
            return frame
        m = float(tl.blur_mix(t))

        if m > 0.001:
            small = cv2.resize(frame, self.small, interpolation=cv2.INTER_AREA)
            small = cv2.GaussianBlur(small, (0, 0), 5)
            k = float(tl.breathe(t))
            if k > 1.0005:
                M = cv2.getRotationMatrix2D((self.small[0] / 2, self.small[1] / 2), 0, k)
                small = cv2.warpAffine(small, M, self.small, borderMode=cv2.BORDER_REFLECT)
            small = (small.astype(np.float32) * self.vig_small * tl.blur_dim)
            blurred = cv2.resize(np.clip(small, 0, 255).astype(np.uint8), (self.W, self.H),
                                 interpolation=cv2.INTER_LINEAR)
            out = cv2.addWeighted(frame, 1 - m, blurred, m, 0)
        else:
            out = frame.copy()

        x0, y0, x1, y1 = self.roi
        roi = out[y0:y1, x0:x1].astype(np.float32) * (1 / 255.0)
        roi *= (1 - 0.35 * float(tl.logo_presence(t)) * self.blob)[..., None]
        layer = self._logo_layer(t)
        roi *= (1 - layer[..., 3:4])
        roi += layer[..., :3]
        out[y0:y1, x0:x1] = cv2.convertScaleAbs(roi, alpha=255.0)

        hp_a = float(tl.hp_alpha(t))
        if hp_a > 0.002:
            hx, hy = self.hp_xy
            hy = round(hy + 14 * (1 - float(smooth(seg(t, *tl.hp_in)))))
            sp = self.hp
            ys0, ys1 = max(hy, 0), min(hy + sp.shape[0], self.H)
            xs0, xs1 = max(hx, 0), min(hx + sp.shape[1], self.W)
            if ys1 > ys0 and xs1 > xs0:
                s_ = sp[ys0 - hy:ys1 - hy, xs0 - hx:xs1 - hx] * hp_a
                d = out[ys0:ys1, xs0:xs1].astype(np.float32) * (1 / 255.0)
                d = d * (1 - s_[..., 3:4]) + s_[..., :3]
                out[ys0:ys1, xs0:xs1] = cv2.convertScaleAbs(d, alpha=255.0)
        return out
```

### audio_8d.py (pan model, 8D render, loudness)

```python
"""8D rendering for peaceful songs: slow orbit, soft front/back cue, no jumps.

One PanModel drives both the audio here and any visual that needs to know
where the sound is (pan indicator, crop drift), so picture and sound agree.
"""
import numpy as np
from scipy.signal import butter, fftconvolve, lfilter

from timeline import Timeline, smooth


class PanModel:
    """Sound position over time.

    orbit : full circle, 0 deg = straight ahead, 90 = right ear, 180 = behind
    sway  : swings +-sway_deg around the front, never goes behind
    """

    def __init__(self, mode="orbit", period=16.0, sway_deg=65.0, width=0.92, timeline=None):
        self.mode, self.period, self.sway, self.width = mode, period, np.radians(sway_deg), width
        self.tl = timeline or Timeline()

    def _theta(self, t):
        t = np.asarray(t, dtype=np.float64)
        if self.mode == "orbit":
            return 2 * np.pi * t / self.period
        return self.sway * np.sin(2 * np.pi * t / self.period)

    def lateral(self, t):
        """-1 (left ear) .. +1 (right ear), scaled by the opening depth ramp."""
        return self.width * self.tl.pan_depth(t) * np.sin(self._theta(t))

    def back(self, t):
        """0 = in front .. 1 = directly behind."""
        if self.mode != "orbit":
            return np.zeros_like(np.asarray(t, dtype=np.float64))
        return self.tl.pan_depth(t) * 0.5 * (1 - np.cos(self._theta(t)))


def _lp(x, fc, sr, order=2):
    b, a = butter(order, fc / (sr / 2))
    return lfilter(b, a, x).astype(np.float32)


def _frac_delay(x, d):
    """Delay x by d samples (array, >= 0) with linear interpolation."""
    n = len(x)
    idx = np.arange(n) - d
    return np.interp(idx, np.arange(n), x, left=0.0).astype(np.float32)


def _reverb_ir(sr, rt60, seed):
    n = int(rt60 * sr)
    rng = np.random.default_rng(seed)
    ir = rng.standard_normal(n) * np.exp(-6.9 * np.arange(n) / n)       # -60 dB at rt60
    ir[: int(0.012 * sr)] *= np.linspace(0, 1, int(0.012 * sr))          # soft onset
    return (ir / np.sqrt(np.sum(ir ** 2))).astype(np.float32)


def render_8d(stereo, sr, pan, fade_in=0.3, fade_out=0.5, side_gain=0.5,
              wet=(0.07, 0.24), rt60=1.3, itd_ms=0.6, ild_depth=0.75):
    """stereo: float32 (n, 2). Returns float32 (n, 2)."""
    n = len(stereo)
    t = np.arange(n) / sr
    lat = pan.lateral(t).astype(np.float32)
    back = pan.back(t).astype(np.float32)

    mid = 0.5 * (stereo[:, 0] + stereo[:, 1])
    side = 0.5 * (stereo[:, 0] - stereo[:, 1])

    # back of the head: slightly darker and quieter
    mid_b = (1 - 0.6 * back) * mid + 0.6 * back * _lp(mid, 4500, sr)
    mid_b *= 1 - 0.12 * back

    # head shadow: the far ear loses highs; the near ear is untouched
    dark = _lp(mid_b, 3000, sr)
    w_l, w_r = np.clip(lat, 0, 1), np.clip(-lat, 0, 1)                   # left far when source is right
    left = (1 - w_l) * mid_b + w_l * dark
    right = (1 - w_r) * mid_b + w_r * dark

    # interaural time difference: the far ear hears it a touch later
    itd = itd_ms * 1e-3 * sr
    left = _frac_delay(left, w_l * itd)
    right = _frac_delay(right, w_r * itd)

    # interaural level difference, equal power, unity at centre
    ang = (lat * ild_depth + 1) * np.pi / 4          # ild_depth < 1 keeps the far ear audible
    left *= np.cos(ang) * np.sqrt(2)
    right *= np.sin(ang) * np.sqrt(2)

    # room: more of it when the sound is behind
    wet_gain = wet[0] + (wet[1] - wet[0]) * back
    rev_l = fftconvolve(mid, _reverb_ir(sr, rt60, 1))[:n].astype(np.float32) * wet_gain
    rev_r = fftconvolve(mid, _reverb_ir(sr, rt60, 2))[:n].astype(np.float32) * wet_gain

    out = np.stack([left + rev_l + side_gain * side, right + rev_r - side_gain * side], axis=1)

    # fades: short fade-in keeps the hook, fade-out for a clean ending
    g = np.ones(n, np.float32)
    nin, nout = int(fade_in * sr), int(fade_out * sr)
    if nin:
        g[:nin] *= (0.5 - 0.5 * np.cos(np.pi * np.arange(nin) / nin)).astype(np.float32)
    if nout:
        g[-nout:] *= (0.5 + 0.5 * np.cos(np.pi * np.arange(nout) / nout)).astype(np.float32)
    out *= g[:, None]

    peak = float(np.abs(out).max())
    if peak > 0.89:
        out *= 0.89 / peak
    return out.astype(np.float32)


def frame_rms(stereo, sr, fps, smooth_frames=3):
    """Per-video-frame loudness, 0..1, for the logo bars."""
    mono = np.abs(stereo).mean(axis=1)
    hop = sr / fps
    n = int(len(mono) / hop)
    r = np.array([np.sqrt(np.mean(mono[int(i * hop):int((i + 1) * hop)] ** 2)) for i in range(n)])
    r = np.convolve(r, np.ones(smooth_frames) / smooth_frames, mode="same")
    ref = np.percentile(r, 95) + 1e-9
    return np.clip(r / ref, 0, 1).astype(np.float32)
```

### beats.py (beat times and segment planning)

```python
"""Beat times and segment planning.

librosa is used when installed. The numpy fallback is a plain onset-envelope
autocorrelation and is only meant to keep the pipeline running without it.
"""
import numpy as np
from scipy.signal import stft


def beat_times(mono, sr):
    try:
        import librosa
        _, b = librosa.beat.beat_track(y=mono.astype(np.float32), sr=sr, units="time")
        return np.asarray(b, dtype=np.float64), "librosa"
    except ImportError:
        return _fallback_beats(mono, sr), "fallback"


def _fallback_beats(mono, sr, lo_bpm=60, hi_bpm=140, hop=512):
    f, tt, Z = stft(mono, fs=sr, nperseg=1024, noverlap=1024 - hop)
    mag = np.log1p(np.abs(Z))
    flux = np.maximum(np.diff(mag, axis=1), 0).sum(axis=0)
    flux = np.maximum(flux - np.convolve(flux, np.ones(21) / 21, mode="same"), 0)
    fr = sr / hop
    ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    lags = np.arange(int(fr * 60 / hi_bpm), int(fr * 60 / lo_bpm))
    lag = int(lags[np.argmax(ac[lags])])
    # parabolic refinement -> fractional period, so the grid does not drift
    y0, y1, y2 = ac[lag - 1], ac[lag], ac[lag + 1]
    P = lag + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2 + 1e-12)
    best, best_o = -1.0, 0.0
    for o in np.arange(0, P, 0.5):
        idx = np.round(o + P * np.arange(int((len(flux) - o) / P))).astype(int)
        idx = idx[idx < len(flux)]
        sc = flux[idx].sum()
        if sc > best:
            best, best_o = sc, o
    t0 = (best_o + 1) * hop / sr                       # +1: flux[i] sits between STFT frames i and i+1
    period = P / fr
    return np.arange(t0, len(mono) / sr, period)


def plan_segment(beats, first_vocal, song_len, seg_len, lead_in=3.0, fade_lead=0.3, min_beat=0.45):
    """Choose where in the song the Reel starts.

    The first beat lands fade_lead seconds into the Reel, so the 0.3 s audio
    fade-in finishes just as it arrives. The start is the latest beat that
    still leaves lead_in seconds before the first sung line.
    """
    beats = np.asarray(beats)
    ok = beats[(beats - fade_lead >= 0) & (first_vocal - (beats - fade_lead) >= lead_in)]
    if len(ok):
        b0 = ok.max()
    else:                                                   # vocals start early in the song
        later = beats[beats - fade_lead >= 0]
        b0 = later.min() if len(later) else fade_lead
    start = max(0.0, b0 - fade_lead)
    end = min(song_len, start + seg_len)
    rel = beats - start
    rel = rel[(rel >= 0) & (rel <= end - start)]
    after = rel[rel >= min_beat]
    return dict(start=float(start), end=float(end), first_vocal_rel=float(first_vocal - start),
                beat_t=float(after[0]) if len(after) else 0.6, beats_rel=rel)


def snap_end(beats, wanted_end, song_len):
    """First beat at or after the wanted end (last lyric + a short tail)."""
    later = np.asarray(beats)[np.asarray(beats) >= wanted_end]
    return float(later[0]) if len(later) else float(song_len)
```

### render.py (the Reel pipeline)

```python
"""Render one Reel: 8D audio, reframed video, intro, lyrics, encode.

    python render.py --video official.mp4 --song song.wav --out reel.mp4 \
                     --first-vocal 31.2 --seg-len 24

Flow (what this file does, in order):
  1  decode the song, find beats                      beats.py
  2  choose the segment start from the first vocal    beats.plan_segment
  3  cut the audio and render the 8D mix              audio_8d.render_8d
  4  measure loudness per frame for the logo bars     audio_8d.frame_rms
  5  decode the video segment, reframe to 1080x1920   reframe()
  6  per frame: grade -> intro -> lyrics -> encoder   IntroOverlay.apply
  7  mux with the 8D audio                            ffmpeg
"""
import argparse
import json
import os
import subprocess
import time

import cv2
import numpy as np
from scipy.io import wavfile

from audio_8d import PanModel, frame_rms, render_8d
from beats import beat_times, plan_segment
from intro_overlay import IntroOverlay
from timeline import FPS, H, W, Timeline, smooth

SR = 44100


# ------------------------------------------------------------- ffmpeg io ---
def probe_size(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                   "-show_entries", "stream=width,height", "-of", "json", path])
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"])


def decode_song(path, work="out"):
    os.makedirs(work, exist_ok=True)
    tmp = os.path.join(work, "_song_44k.wav")
    subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-ac", "2", "-ar", str(SR),
                           "-c:a", "pcm_f32le", tmp])
    sr, x = wavfile.read(tmp)
    os.remove(tmp)
    return x.astype(np.float32)


def open_decoder(video, start, dur, w, h):
    cmd = ["ffmpeg", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", video,
           "-an", "-r", str(FPS), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE), w * h * 3


def open_encoder(out, audio_wav, preset="medium", crf=18):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", audio_wav,
           "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", "-c:a", "aac", "-b:a", "192k", "-shortest", out]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


# --------------------------------------------------------------- framing ---
def reframe_crop(frame, cx=0.5):
    """16:9 -> 9:16 by cropping a window centred at cx (0..1 of the width)."""
    h, w = frame.shape[:2]
    cw = int(round(h * 9 / 16))
    x0 = int(np.clip(cx * w - cw / 2, 0, w - cw))
    return cv2.resize(frame[:, x0:x0 + cw], (W, H), interpolation=cv2.INTER_CUBIC)


def reframe_blurpad(frame):
    """Whole picture in the middle, blurred enlarged copy behind it."""
    h, w = frame.shape[:2]
    bg = cv2.resize(frame, (W // 8, H // 8), interpolation=cv2.INTER_AREA)
    bg = cv2.resize(cv2.GaussianBlur(bg, (0, 0), 3), (W, H), interpolation=cv2.INTER_LINEAR)
    bg = (bg * 0.55).astype(np.uint8)
    fh = round(h * W / w)
    bg[(H - fh) // 2:(H - fh) // 2 + fh] = cv2.resize(frame, (W, fh), interpolation=cv2.INTER_AREA)
    return bg


def make_grade():
    """Constant brightness trim plus a soft scrim under the lyric area."""
    y = np.arange(H, dtype=np.float32) / H
    scrim = 1 - 0.30 * smooth((y - 0.60) / 0.40)
    g = (0.90 * scrim * 255 / 1.0).clip(0, 255).astype(np.uint8)
    return np.repeat(np.repeat(g[:, None, None], W, axis=1), 3, axis=2)


# ---------------------------------------------------------------- lyrics ---
def demo_lyrics(lines, font_path=None, y=None):
    """Stand-in for your lyric renderer: lines = [(t_start, t_end, text)]."""
    from PIL import Image, ImageDraw, ImageFont
    from timeline import Layout
    y = y or Layout().hp_center_y
    font = ImageFont.truetype(font_path or "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 54)

    def draw(frame, t):
        for a, b, text in lines:
            al = float(smooth((t - a) / 0.35) * (1 - smooth((t - (b - 0.35)) / 0.35)))
            if al <= 0.01:
                continue
            im = Image.fromarray(frame[..., ::-1])
            d = ImageDraw.Draw(im, "RGBA")
            w = d.textlength(text, font=font)
            d.text(((W - w) / 2 + 2, y + 2), text, font=font, fill=(0, 0, 0, int(150 * al)))
            d.text(((W - w) / 2, y), text, font=font, fill=(244, 238, 226, int(255 * al)))
            frame = np.asarray(im)[..., ::-1].copy()
        return frame
    return draw


# ------------------------------------------------------------------ main ---
def render_reel(video, song, out, first_vocal, seg_len=24.0, assets="assets", layout="crop",
                intro=True, pan_mode="orbit", pan_period=16.0, drift=0.0, lyrics_fn=None,
                hp_lines=("Best with headphones  ·  8D",), fonts=None, beat_offset=0.0,
                cover_time=None, preset="medium", end_fade=0.5, work="out", log=print):
    t_start = time.time()
    stereo = decode_song(song)
    mono = stereo.mean(axis=1)
    beats, how = beat_times(mono, SR)
    beats = beats + beat_offset
    log(f"[1] {len(stereo) / SR:.1f} s song, {len(beats)} beats via {how}")

    lead_in = 3.0 if intro else 0.8
    plan = plan_segment(beats, first_vocal, len(stereo) / SR, seg_len, lead_in=lead_in)
    dur = plan["end"] - plan["start"]
    log(f"[2] segment {plan['start']:.2f}-{plan['end']:.2f} s, first vocal at {plan['first_vocal_rel']:.2f} s, "
        f"dot pulse at {plan['beat_t']:.2f} s")

    tl = Timeline().for_vocal(plan["first_vocal_rel"])
    pan = PanModel(pan_mode, period=pan_period, timeline=tl)
    seg = stereo[int(plan["start"] * SR):int(plan["end"] * SR)]
    mix = render_8d(seg, SR, pan, fade_in=tl.audio_fade_in, fade_out=end_fade)
    wav = os.path.join(work, "reel_audio.wav")
    wavfile.write(wav, SR, (mix * 32767).astype(np.int16))
    log(f"[3] 8D audio rendered ({pan_mode}, period {pan_period:.0f} s)")

    env = frame_rms(mix, SR, FPS)
    overlay = IntroOverlay(assets, env=env, beat_t=plan["beat_t"], timeline=tl, hp_lines=hp_lines, fonts=fonts)
    sw, sh = probe_size(video)
    dec, fsize = open_decoder(video, plan["start"], dur, sw, sh)
    enc = open_encoder(out, wav, preset)
    grade = make_grade()
    n_frames = int(dur * FPS)
    fade_n = int(end_fade * FPS)
    cover_i = None if cover_time is None else int(cover_time * FPS)
    log(f"[5] {sw}x{sh} source, {n_frames} frames, layout={layout}, intro={'on' if intro else 'off'}")

    for i in range(n_frames):
        buf = dec.stdout.read(fsize)
        if len(buf) < fsize:
            break
        t = i / FPS
        src = np.frombuffer(buf, np.uint8).reshape(sh, sw, 3)
        if layout == "blurpad":
            f = reframe_blurpad(src)
        else:
            cx = 0.5 + drift * float(pan.lateral(t))           # drift=0 keeps the window still
            f = reframe_crop(src, cx)
        f = cv2.multiply(f, grade, scale=1 / 255.0)
        if intro:
            f = overlay.apply(f, t)
        if lyrics_fn:
            f = lyrics_fn(f, t)
        if fade_n and i >= n_frames - fade_n:
            f = (f.astype(np.float32) * (1 - (i - (n_frames - fade_n)) / fade_n)).astype(np.uint8)
        if cover_i is not None and i == cover_i:
            cv2.imwrite(os.path.splitext(out)[0] + "_cover.png", f)
        enc.stdin.write(f.tobytes())
    enc.stdin.close()
    enc.wait()
    dec.stdout.close()
    dec.wait()
    log(f"[7] wrote {out} in {time.time() - t_start:.1f} s ({n_frames / (time.time() - t_start):.1f} frames/s overall)")
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--song", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--first-vocal", type=float, required=True, help="song time of the first sung line, seconds")
    ap.add_argument("--seg-len", type=float, default=24.0)
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--layout", choices=["crop", "blurpad"], default="crop")
    ap.add_argument("--no-intro", action="store_true")
    ap.add_argument("--pan-mode", choices=["orbit", "sway"], default="orbit")
    ap.add_argument("--pan-period", type=float, default=16.0)
    ap.add_argument("--drift", type=float, default=0.0)
    ap.add_argument("--beat-offset", type=float, default=0.0, help="shift detected beats by this many seconds")
    ap.add_argument("--cover-time", type=float, help="write a PNG of this Reel time (seconds)")
    ap.add_argument("--preset", default="medium")
    ap.add_argument("--demo-lyrics", action="store_true", help="two placeholder lines, for testing only")
    a = ap.parse_args()
    os.makedirs("out", exist_ok=True)

    lyrics = None
    if a.demo_lyrics:                                   # plan once to learn where the first vocal lands
        stereo = decode_song(a.song)
        b, _ = beat_times(stereo.mean(axis=1), SR)
        pl = plan_segment(b + a.beat_offset, a.first_vocal, len(stereo) / SR, a.seg_len,
                          lead_in=0.8 if a.no_intro else 3.0)
        fv = pl["first_vocal_rel"]
        lyrics = demo_lyrics([(fv, fv + 3.0, "Demo lyric line one"), (fv + 3.2, fv + 6.2, "Demo lyric line two")])

    render_reel(a.video, a.song, a.out, a.first_vocal, a.seg_len, a.assets, a.layout, not a.no_intro,
                a.pan_mode, a.pan_period, a.drift, lyrics, beat_offset=a.beat_offset,
                cover_time=a.cover_time, preset=a.preset)


if __name__ == "__main__":
    main()
```

### preview.py (opening only)

```python
"""Render only the intro over a few seconds of footage.

  python preview.py video.mp4 --out out/intro_preview.mp4 --sheet out/intro_sheet.png
Writes an mp4 of the first 4 s and a contact sheet of key moments, so easing
and timings can be judged without running the whole pipeline.
"""
import argparse
import subprocess
import time

import cv2
import numpy as np

from intro_overlay import IntroOverlay
from timeline import FPS, H, W

SHEET_TIMES = [0.0, 0.15, 0.35, 0.6, 0.85, 1.1, 1.5, 1.9, 2.3, 2.6, 2.9, 3.3]


def reframe(frame, cx=0.5):
    h, w = frame.shape[:2]
    cw = int(h * 9 / 16)
    x0 = int(np.clip(cx * w - cw / 2, 0, w - cw))
    return cv2.resize(frame[:, x0:x0 + cw], (W, H), interpolation=cv2.INTER_CUBIC)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--out", default="out/intro_preview.mp4")
    ap.add_argument("--sheet", default="out/intro_sheet.png")
    ap.add_argument("--seconds", type=float, default=4.0)
    a = ap.parse_args()

    cap = cv2.VideoCapture(a.video)
    ov = IntroOverlay(a.assets)
    enc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "18",
                            "-pix_fmt", "yuv420p", a.out], stdin=subprocess.PIPE)
    keep, cost = {}, []
    want = {round(t * FPS): t for t in SHEET_TIMES}
    for i in range(int(a.seconds * FPS)):
        ok, f = cap.read()
        if not ok:
            break
        f = (reframe(f).astype(np.float32) * 0.9).astype(np.uint8)         # base grade
        t0 = time.perf_counter()
        out = ov.apply(f, i / FPS)
        cost.append(time.perf_counter() - t0)
        enc.stdin.write(out.tobytes())
        if i in want:
            keep[want[i]] = out
    enc.stdin.close()
    enc.wait()
    thumbs = []
    for t in SHEET_TIMES:
        im = cv2.resize(keep[t], (270, 480), interpolation=cv2.INTER_AREA)
        cv2.putText(im, f"{t:.2f}s", (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        thumbs.append(im)
    rows = [np.hstack(thumbs[r * 6:(r + 1) * 6]) for r in range(2)]
    cv2.imwrite(a.sheet, np.vstack(rows))
    c = np.array(cost[:96]) * 1000
    print(f"intro frames: mean {c.mean():.1f} ms, max {c.max():.1f} ms per frame (96 frames)")


if __name__ == "__main__":
    main()
```

### make_test_assets.py (synthetic video and song for testing)

```python
"""Synthetic test inputs: a moving 1920x1080 'official video' and a stereo song
with a steady beat, so the pipeline can be exercised without real material."""
import subprocess
import sys

import numpy as np
from scipy.io import wavfile


def make_audio(path, seconds=14.0, sr=44100, bpm=90.0, first_beat=1.37):
    t = np.arange(int(seconds * sr)) / sr
    rng = np.random.default_rng(1)
    beat = 60.0 / bpm
    # sustained chord (slow tremolo) + a soft kick on every beat + hi-hat on offbeats
    chord = sum(np.sin(2 * np.pi * f * t) for f in (220.0, 277.18, 329.63)) * 0.12
    chord *= 0.75 + 0.25 * np.sin(2 * np.pi * t / (beat * 4))
    sig = chord.copy()
    for k in range(int((seconds - first_beat) / beat) + 1):
        b0 = first_beat + k * beat
        i = int(b0 * sr)
        n = int(0.25 * sr)
        if i + n >= len(sig):
            break
        tt = np.arange(n) / sr
        sig[i:i + n] += 1.0 * np.sin(2 * np.pi * (60 + 90 * np.exp(-tt * 25)) * tt) * np.exp(-tt * 12)
        j = i + int(beat * 0.5 * sr)
        if j + 2000 < len(sig):
            sig[j:j + 2000] += 0.03 * rng.standard_normal(2000) * np.exp(-np.arange(2000) / 400)
    stereo = np.c_[sig, sig * 0.95 + 0.02 * np.sin(2 * np.pi * 330 * t)]
    stereo = (stereo / np.abs(stereo).max() * 0.8).astype(np.float32)
    wavfile.write(path, sr, stereo)
    return first_beat


def make_video(path, seconds=14.0, fps=30, w=1920, h=1080):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    for i in range(int(seconds * fps)):
        t = i / fps
        base = np.zeros((h, w, 3), np.float32)
        base[..., 0] = 120 + 70 * np.sin(xx / 300 + t * 0.5)
        base[..., 1] = 150 + 60 * np.sin(yy / 250 - t * 0.4)
        base[..., 2] = 170 + 50 * np.sin((xx + yy) / 400 + t * 0.3)
        # a bright "singer" blob moving slowly, and a window of light
        cx = w / 2 + 120 * np.sin(t * 0.6)
        base += 90 * np.exp(-(((xx - cx) / 160) ** 2 + ((yy - h * 0.45) / 280) ** 2))[..., None]
        base[:, int(w * 0.62):int(w * 0.66)] += 60
        p.stdin.write(np.clip(base, 0, 255).astype(np.uint8).tobytes())
    p.stdin.close()
    p.wait()


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "out"
    fb = make_audio(f"{out}/test_song.wav")
    make_video(f"{out}/test_video.mp4")
    print("first beat at", fb)
```

## 13. Testing

The code was run in a sandbox (2 CPU cores, Python 3.13) with your two logo files and a synthetic 1080p video and a synthetic stereo song at 90 BPM.

**What ran and what it showed**

| Check | Result |
|---|---|
| Logo split | Stroke, orange dot, four bars and the small end dot separated cleanly from the transparency channel (seven sprites). |
| Pen order | The chain ran nib, stem, bottom loop, knot, S-curve, swoosh (`debug_pen_chain.png`). |
| Wordmark matte | Clean after raising the paper-grain threshold and cutting below the icon. |
| Opening, visual | Contact sheets at 12 moments show the dot, stroke, bars, wordmark reveal, headphone line, blur lift and evaporate in the intended order. A rectangular dark pad behind the logo and a dark smudge before the logo appeared were found and fixed. |
| Opening, cost | 90 to 110 ms per frame on average over two runs (170 to 188 ms worst, during the evaporate), 96 frames. A first version took 190 ms; the speed-up came from float32 scalars, a smaller region, quarter-resolution glow and half-resolution blur. |
| Full Reel | 270 frames (9 s) rendered in 24 s on the `veryfast` preset, about 11 frames/s including the opening. A 5 s run without the opening (`ultrafast` preset) managed 29 frames/s. Output: 1080 × 1920, H.264, yuv420p, 30 fps, AAC stereo 44.1 kHz, audio and video both 9.000 s. |
| Options | Blurpad layout, `--no-intro`, sway mode with crop drift, and a first vocal earlier than 3 s all ran. A cover PNG was written. |
| 8D audio | Correlation 0.997 between measured level difference and the model. Maximum ±18.8 dB. High-frequency share 0.27 behind versus 0.69 in front. Fade-in and −1 dBFS peak limit confirmed. |
| Beat fallback | Within 12 ms of the true kicks on a synthetic click track at 90 BPM. |

**What was not covered**

- The librosa beat path (not installable in the sandbox). The fallback is crude and meant only to keep things running. Check librosa's beats by ear on a few real songs. It returns beats, not bar starts.
- Real Telugu rendering. No Telugu font was available in the sandbox. Pillow's shaping engine (raqm) was present there; check `PIL.features.check("raqm")` on your machine.
- Real footage with faces, cuts, burned-in titles and pillarboxing.
- Listening on headphones. The audio was measured, not heard.
- Speed on your machine, and uploads to the platforms.

## 14. Tuning reference

| What | Where | Default | Effect |
|---|---|---|---|
| Opening length | `Timeline.duration` | 3.2 s | Total opening. |
| Audio fade-in | `Timeline.audio_fade_in` | 0.3 s | Keep at 0.3 s or less. |
| Pan width at start | `Timeline.pan_depth_start` | 0.30 | How wide the first second feels. |
| Pan full width at | `Timeline.pan_depth_full_at` | 3.0 s | When the effect is complete. |
| Blur hold / lift end | `blur_hold_until`, `blur_lift_end` | 1.8 s / 2.8 s | When the video is revealed. |
| Blurred copy brightness | `Timeline.blur_dim` | 0.50 | Darker makes cream stand out more. |
| Stroke timing | `Timeline.stroke` | 0.20 to 0.80 | Draw-on speed. |
| Bar pop and stagger | `bars_start`, `bar_stagger`, `bar_pop` | 0.45, 0.04, 0.22 | Bar entrance. |
| Wordmark timing | `Timeline.wordmark` | 0.80 to 1.20 | Reveal speed. |
| Logo evaporate | `Timeline.logo_evaporate` | 2.20 to 2.90 | How long the logo stays. |
| Headphone line timing | `hp_in`, `hp_out` | 1.2 to 1.8, 2.4 to 3.0 | Visibility window. |
| Sizes and positions | `Layout` | see section 9 | Move or scale the lockup and text. |
| Glow strength | `Layout.glow_gain`, `dot_glow_gain` | 0.35, 0.55 | Lit look. |
| Dot brightness | `IntroOverlay.ORANGE`, dot sprite | brush texture | Multiply the dot sprite by about 1.15 if it looks dull on dark footage. |
| Bar reaction | `_logo_layer`: `0.62 + 0.55 × loudness` | | Bigger second number, livelier bars. |
| Orbit period | `--pan-period` | 16 s | Lower is faster. |
| Pan style | `--pan-mode` | orbit | Or sway. |
| Level difference | `render_8d(ild_depth=…)` | 0.75 | 1.0 is a harder effect, 0.6 is gentler. |
| Reverb | `wet`, `rt60` | (0.07, 0.24), 1.3 | Space behind the listener. |
| Side signal | `side_gain` | 0.5 | Keeps original stereo width. |
| Crop drift | `--drift` | 0 | 0.04 slides the window 4 % of the source width. |
| Scrim | `make_grade` | 0.90 brightness, 30 % scrim | Lyric legibility. |
| Encoder | `--preset`, `crf` in `open_encoder` | medium, 18 | Quality versus speed. |

## 15. Fonts and Telugu

- The wordmark is an image lifted from your lockup, so its typeface is exact. Use a high-resolution original if you have one; the one used here is 767 px wide.
- The headphone line needs a serif that sits well beside the wordmark. Put the font file in `assets/fonts/` and point `SERIF_FALLBACKS` (or the `fonts` argument) at it. A refined, high-contrast serif such as Cormorant Garamond is close in spirit; check the licence of whatever you pick.
- Telugu text needs a Telugu font (for example Noto Sans Telugu or Noto Serif Telugu) and correct conjunct shaping. Check `PIL.features.check("raqm")`. If it is False, the code still runs but conjuncts may render wrongly; use a shaping-aware renderer for Telugu.
- Test one real Telugu lyric line at 54 px on a phone before trusting any layout.
- Have a Telugu speaker approve any Telugu headphone line.

## 16. Export settings

- 1080 × 1920, 30 fps, H.264 (high profile, yuv420p), AAC 192 kb/s, `faststart`. These are what the encoder step already writes.
- Keep Reels short. Maximum lengths differ by platform and change over time, so check the current limits before choosing `--seg-len`.
- Keep the same file for Instagram, Facebook and YouTube Shorts unless a platform rejects it.
- Audio peaks at −1 dBFS. Do not add a second loudness stage unless a platform asks for one.

## 17. QA checklist

Automatic, per Reel (cheap to script with ffprobe and a few frame samples):

- Resolution 1080 × 1920, 30 fps, audio present, audio and video durations within one frame.
- First frame is not black and not identical to the last frame.
- Audio peak at or below −1 dBFS and no clipping.
- First beat within 50 ms of the dot pulse (measure the kick against `beat_t`).
- The first lyric's start time is at or after `tv − 0.4`.

By eye and ear, once per new song or once per batch:

- Pen order on `debug_pen_chain.png` (once per logo version).
- The headphone line is fully readable and gone before the first lyric is fully in.
- Nothing important sits in the top 12 %, bottom 20 % or right 12 %.
- Cover frame is a good frame (use `--cover-time`).
- Orbit passes behind without a click.
- Vocals stay clear at the sides.
- Watch it on a phone, once, in a real feed.

## 18. A/B test

Goal: learn whether the opening costs viewers in the first 3 seconds.

1. Pick 8 to 10 songs. Render each twice: with the opening (default) and video first (`--no-intro`). In the video-first version the code starts the segment about 0.8 s before the first lyric instead of 3.0 s.
2. Post them on alternating days so the same kind of song appears in both groups. Do not post the same song twice on one account at the same time.
3. Compare, from each platform's own insights: viewers still watching at 3 seconds, average watch time, completion rate, and replays.
4. Judge by the 3-second number first. With 8 to 10 posts per arm the result is a hint, not proof. Keep the version that wins on that number unless the other wins clearly on completion.
5. Keep the config flag so you can switch with one setting.

## 19. Risks

| Risk | What to do |
|---|---|
| The video is hidden for about 2 s, which is where most viewers decide to swipe. | The song is audible from frame 0 and the blurred video moves behind the logo. Run the A/B test. If it loses, move the logo to a 0.8 s end frame. |
| The official video and song are used as is. Platforms' automatic rights systems can mute, block or claim the Reel, and making the audio 8D does not change that. | Test one account and one post first, read the platform's rules, and expect some songs to be claimed. Not legal advice. |
| Beat detection is wrong on some songs. | Check by ear, use `--beat-offset`, and prefer librosa over the fallback. |
| Pre-roll contains sung lines. | Section 10, point 4. |
| Cropping 16:9 to 9:16 cuts off faces or burned-in text. | Track the subject (`cx` in `reframe_crop`), or use blurpad on those shots. |
| Telugu conjuncts render wrongly. | Section 15. |
| Cream logo hard to read on very bright footage. | The dark pad already helps; raise its 0.35 or lower `blur_dim`. |
| Intro cost per Reel. | About 9 to 11 s of extra render time for the 96 frames. Acceptable for batch use. Cache is possible but not needed yet. |
| Unknown platform changes to UI zones. | Re-check the safe zones on a real phone. |

## 20. Build order

1. Run `logo_prep.py`, check `debug_pen_chain.png`, preview the opening on real footage. Accept when the pen order and the wordmark look right.
2. Install librosa and listen to the beat grid on five real songs. Accept when the dot pulse sits on the beat for at least four of them.
3. Render three real Reels end to end with your lyric renderer. Accept when the first lyric arrives as the headphone line leaves and nothing is clipped by the platform UI.
4. Put the brand serif and the Telugu font in `assets/fonts/`, settle the headphone wording, and check it on a phone.
5. Decide the open items in section 3, in this order: ending, body 8D visuals, lyric movement.
6. Run the A/B test.
7. Automate: loop over songs, write QA output, publish.

## 21. Decision history

The design changed several times in discussion. This is the order, so the reasons are not lost.

1. First idea: a 1.5 s intro with ink, word and sound, ending in a waveform that expands into the song.
2. Dropped the black background (black ink is invisible on black) and the contradiction between "collapse" and "expand" at the end.
3. Considered an artwork and song-name card at the start. Rejected: it hides the video in the most valuable seconds. Credits can be small text over the footage instead.
4. Considered putting the logo at the end instead. Reasonable, but you chose the opening, so the end card was dropped.
5. An outside suggestion to hide the 8D effect behind scenery was rejected: the background is the official video, and many viewers watch muted at first.
6. A suggestion to detach the dot into a pan indicator and return it into a watermark was dropped when you removed the watermark.
7. Final: opening over the playing song, headphone line after the logo, lyric in the same spot, video revealed by the lifting blur.
