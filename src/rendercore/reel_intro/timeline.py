"""All timings, easing curves and layout constants for the Vachika opening.

The intro is intentionally split into two visual scenes on one output timeline:
1. a black, silent logo reveal; and
2. a blurred video/audio scene with the headphone prompt, which clears at the
   first lyric.  Times are seconds from the first frame of the final Reel.
"""
from dataclasses import dataclass

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
    # A silent, black page before the song and background video begin.
    silent_duration: float = 0.0

    # audio and overall atmosphere
    audio_fade_in: float = 0.3
    pan_depth_start: float = 0.30
    pan_depth_full_at: float = 3.0

    # Active scene: blurred source eases into the sharp video as lyrics begin.
    blur_hold_until: float = 1.8
    blur_lift_end: float = 2.8
    blur_dim: float = 0.50
    breathe_scale: float = 0.025
    vignette: float = 0.30
    video_fade_in: tuple = (0.0, 0.38)

    # Logo timings are absolute from output start, and must end in the silent page.
    dot_in: tuple = (0.05, 0.36)
    stroke: tuple = (0.16, 1.35)
    bars_start: float = 0.70
    bar_stagger: float = 0.04
    bar_pop: float = 0.24
    wordmark: tuple = (1.15, 1.88)
    logo_evaporate: tuple = (2.05, 2.40)

    # Headphone prompt belongs only to the active video/audio scene.
    hp_in: tuple = (1.20, 1.80)
    hp_out: tuple = (2.40, 3.00)

    # First lyric handoff.
    first_vocal: float = 3.0
    first_lyric_fade: float = 0.6

    def for_vocal(self, first_vocal: float) -> "Timeline":
        """Return a copy adjusted when the first vocal comes earlier than planned."""
        tl = Timeline(**{**self.__dict__})
        tl.first_vocal = first_vocal
        if first_vocal < 3.0:
            end = max(first_vocal - 0.1, 0.0)
            tl.hp_out = (max(end - 0.6, tl.hp_in[1]), end)
        return tl

    def scaled_to(self, duration: float) -> "Timeline":
        """Scale a standalone timeline uniformly; used by tests/tools outside Reel build."""
        duration = max(0.0, float(duration))
        factor = duration / max(self.duration, 1e-9)
        tl = Timeline(**{**self.__dict__})
        tl.duration = duration
        for name in ("blur_hold_until", "blur_lift_end", "bars_start",
                     "bar_stagger", "bar_pop", "pan_depth_full_at",
                     "first_vocal", "first_lyric_fade"):
            setattr(tl, name, getattr(self, name) * factor)
        for name in ("dot_in", "stroke", "wordmark", "logo_evaporate", "hp_in", "hp_out", "video_fade_in"):
            setattr(tl, name, tuple(float(v) * factor for v in getattr(self, name)))
        return tl

    @classmethod
    def with_silent_lead(cls, silent_duration: float, active_duration: float) -> "Timeline":
        """Build a two-scene opening with independent logo and music/video phases.

        ``silent_duration`` is the black-only logo page. The complete icon and
        wordmark reveal, hold briefly, and fade back to black before the source
        video/audio start. ``active_duration`` is the actual song/video lead-in
        ending on the first lyric; only this scene contains the blurred footage
        and headphone warning. The final video is sharp at the lyric handoff.
        """
        silent = max(0.0, float(silent_duration))
        active = max(0.0, float(active_duration))
        total = silent + active
        tl = cls()
        tl.duration = total
        tl.silent_duration = silent
        tl.first_vocal = total
        tl.first_lyric_fade = min(0.6, max(0.2, active * 0.15))

        # Keep logo animation fully on the black page. Proportional timings keep
        # short custom silent durations safe; the default 2.4 s page leaves a
        # calm hold before the mark softly fades away.
        if silent > 0:
            tl.dot_in = (silent * 0.025, silent * 0.16)
            tl.stroke = (silent * 0.06, silent * 0.56)
            tl.bars_start = silent * 0.28
            tl.bar_stagger = min(0.045, silent * 0.018)
            tl.bar_pop = min(0.25, silent * 0.10)
            tl.wordmark = (silent * 0.48, silent * 0.78)
            tl.logo_evaporate = (silent * 0.86, silent)
        else:
            # With no black page, skip the logo scene rather than placing it on
            # top of the video/warning scene.
            tl.dot_in = (0.0, 0.0)
            tl.stroke = (0.0, 0.0)
            tl.bars_start = 0.0
            tl.wordmark = (0.0, 0.0)
            tl.logo_evaporate = (0.0, 0.0)

        # Active scene enters from black and remains strongly blurred while the
        # warning is legible. The original video becomes sharp near the lyric.
        tl.video_fade_in = (silent, min(total, silent + min(0.45, max(0.24, active * 0.12))))
        tl.blur_hold_until = silent + active * 0.58
        tl.blur_lift_end = total

        hp_fade_in = min(0.85, max(0.30, active * 0.22))
        hp_fade_out = min(0.80, max(0.28, active * 0.20))
        hp_in_end = min(total, silent + hp_fade_in)
        hp_out_start = max(hp_in_end, total - hp_fade_out)
        tl.hp_in = (silent, hp_in_end)
        tl.hp_out = (hp_out_start, total)
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

    def video_scene_alpha(self, t):
        """Fade the active video from black in sync with the audio fade-in."""
        return ease_in_out(seg(t, *self.video_fade_in))

    def breathe(self, t):
        return 1.0 + self.breathe_scale * smooth(seg(t, self.silent_duration, self.blur_lift_end))

    def logo_alpha(self, t):
        return 1 - ease_in_out(seg(t, *self.logo_evaporate))

    def logo_presence(self, t):
        """How strongly the dark pad behind the logo should show."""
        return smooth(seg(t, self.silent_duration * 0.04, self.silent_duration * 0.24)) * self.logo_alpha(t)

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
    # Slightly smaller grouped lockup, centered as one unit with the wordmark
    # directly beneath the icon rather than leaving the logo too high on screen.
    icon_height: int = 540
    icon_center_y: int = 820
    wordmark_width: int = 500
    wordmark_top: int = 1120
    hp_center_y: int = 1280
    hp_font_px: int = 44
    glow_sigma: float = 8.0
    glow_gain: float = 0.27
    dot_glow_radius: int = 105
    dot_glow_gain: float = 0.46
