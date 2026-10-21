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
    # A silent, black section before the song and background video begin.
    # All animation timestamps remain on the final output timeline.
    silent_duration: float = 0.0

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

    def scaled_to(self, duration: float) -> "Timeline":
        """Uniformly scale the visual opening when the song starts too early.

        The animation still completes at the first lyric instead of letting the
        logo overlap an already-started lyric indefinitely.
        """
        duration = max(0.0, float(duration))
        factor = duration / max(self.duration, 1e-9)
        tl = Timeline(**{**self.__dict__})
        tl.duration = duration
        for name in ("blur_hold_until", "blur_lift_end", "bars_start",
                     "bar_stagger", "bar_pop", "pan_depth_full_at",
                     "first_vocal", "first_lyric_fade"):
            setattr(tl, name, getattr(self, name) * factor)
        for name in ("dot_in", "stroke", "wordmark", "logo_evaporate", "hp_in", "hp_out"):
            setattr(tl, name, tuple(float(v) * factor for v in getattr(self, name)))
        return tl

    @classmethod
    def with_silent_lead(cls, silent_duration: float, active_duration: float) -> "Timeline":
        """Build the complete opening with a black/silent logo reveal first.

        ``active_duration`` is the song/video lead-in before the first lyric.
        ``silent_duration`` is added in front of that media lead-in. Logo ink
        animation is stretched across the complete opening, while the blurred
        video and headphone prompt start exactly when the song starts.
        """
        silent = max(0.0, float(silent_duration))
        active = max(0.0, float(active_duration))
        total = silent + active
        tl = cls().scaled_to(total)
        tl.duration = total
        tl.silent_duration = silent

        # Hold a strongly blurred live-video backdrop through most of the
        # active phase, then reveal the normal picture at the lyric handoff.
        tl.blur_hold_until = silent + active * 0.58
        tl.blur_lift_end = total

        # The logo finishes its draw-on before/around the audio handoff, then
        # remains legible over the blurred picture before dissolving at the
        # first lyric. Keep these absolute times tied to the final timeline.
        logo_fade = min(0.9, max(0.35, active * 0.28))
        tl.logo_evaporate = (max(silent, total - logo_fade), total)

        # Show the headphone mark and copy together from the exact moment the
        # music and blurred video begin, then clear them just before lyrics.
        hp_in_end = min(total, silent + min(0.6, max(0.25, active * 0.20)))
        hp_fade = min(0.7, max(0.3, active * 0.22))
        tl.hp_in = (silent, hp_in_end)
        tl.hp_out = (max(hp_in_end, total - hp_fade), total)
        tl.first_vocal = total
        tl.first_lyric_fade = min(0.6, max(0.2, active * 0.15))
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
    hp_font_px: int = 46
    glow_sigma: float = 10.0
    glow_gain: float = 0.35
    dot_glow_radius: int = 120
    dot_glow_gain: float = 0.55
