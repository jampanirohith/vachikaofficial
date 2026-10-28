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

from .timeline import FPS, H, W, Layout, Timeline, clamp01, ease_out, ease_out_back, pen_ease, seg, smooth

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
                 hp_lines=("USE HEADPHONES", "FOR BETTER EXPERIENCE"), fonts=None, size=(W, H), fps=FPS,
                 footer_enabled=True, footer_text="Vachika", footer_center_y=1800):
        self.fps = fps
        self.tl = timeline or Timeline()
        self.lo = layout or Layout()
        self.W, self.H = size
        self.meta = json.load(open(os.path.join(assets, "meta.json")))
        self.env_arr = None if env is None else np.asarray(env, np.float32)
        self.beat_t = beat_t
        self.footer_enabled = bool(footer_enabled)
        self.footer_text = str(footer_text or "Vachika")
        self.footer_center_y = int(footer_center_y)
        self._build_logo(assets)
        self._build_backdrop()
        self._build_headphone_line(hp_lines, fonts or {})
        if self.footer_enabled:
            self._build_footer(assets, fonts or {})

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
        """Create the headphone prompt with a fine divider before the copy."""
        lo = self.lo
        font = find_font([fonts.get("latin")] + SERIF_FALLBACKS, lo.hp_font_px)
        glyph_px = round(lo.hp_font_px * 1.15)
        rows = []
        for text in lines:
            f = font
            if any("\u0c00" <= ch <= "\u0c7f" for ch in text):            # Telugu block
                f = find_font([fonts.get("telugu")], round(lo.hp_font_px * 0.9))
            bbox = f.getbbox(text)
            rows.append((text, f, bbox[2] - bbox[0], bbox[3] - bbox[1]))
        icon_gap, divider_width, text_gap = 18, 3, 18
        total_w = glyph_px + icon_gap + divider_width + text_gap + max(r[2] for r in rows)
        total_h = max(glyph_px, sum(r[3] for r in rows) + 12 * (len(rows) - 1))
        pad = 36
        im = Image.new("RGBA", (total_w + 2 * pad, total_h + 2 * pad), (0, 0, 0, 0))
        im.alpha_composite(headphone_glyph(glyph_px), (pad, pad + (total_h - glyph_px) // 2))
        d = ImageDraw.Draw(im)
        divider_x = pad + glyph_px + icon_gap
        div_top = pad + max(2, int(total_h * 0.18))
        div_bottom = pad + min(total_h - 2, int(total_h * 0.82))
        d.line((divider_x, div_top, divider_x, div_bottom), fill=(238, 230, 219, 235), width=divider_width)
        self.hp_divider_x = divider_x
        y = pad
        text_x = divider_x + divider_width + text_gap
        for text, f, w, h in rows:
            d.text((text_x, y - f.getbbox(text)[1]), text, font=f, fill=(238, 230, 219, 255))
            y += h + 12
        # Soft shadow so the line reads on bright footage.
        shadow = im.split()[3].filter(ImageFilter.GaussianBlur(9))
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        sh.putalpha(shadow.point(lambda v: int(v * 0.75)))
        sh.alpha_composite(im)
        arr = np.asarray(sh, np.float32) / 255.0
        self.hp = to_pm(arr[..., [2, 1, 0]], arr[..., 3])
        self.hp_xy = (self.W // 2 - self.hp.shape[1] // 2, lo.hp_center_y - self.hp.shape[0] // 2)

    def _build_footer(self, assets, fonts):
        """Prepare the persistent Vachika | Instagram lockup for the lower band."""
        font = find_font([fonts.get("latin")] + SERIF_FALLBACKS, 31)
        text_bbox = font.getbbox(self.footer_text)
        text_w = max(1, text_bbox[2] - text_bbox[0])
        text_h = max(1, text_bbox[3] - text_bbox[1])
        icon_size, gap, divider_width, pad = 38, 22, 2, 10
        full_w = text_w + gap + divider_width + gap + icon_size
        full_h = max(text_h, icon_size)
        im = Image.new("RGBA", (full_w + 2 * pad, full_h + 2 * pad), (0, 0, 0, 0))
        draw = ImageDraw.Draw(im)
        text_y = pad + (full_h - text_h) // 2 - text_bbox[1]
        draw.text((pad, text_y), self.footer_text, font=font, fill=(238, 230, 219, 255))
        divider_x = pad + text_w + gap
        draw.line((divider_x, pad + 4, divider_x, pad + full_h - 4), fill=(238, 230, 219, 220), width=divider_width)

        icon_path = os.path.join(assets, "instagram_icon.png")
        with Image.open(icon_path).convert("RGBA") as source_icon:
            icon_alpha = source_icon.getchannel("A").resize((icon_size, icon_size), Image.LANCZOS)
        icon = Image.new("RGBA", (icon_size, icon_size), (238, 230, 219, 0))
        icon.putalpha(icon_alpha)
        icon_x = divider_x + gap
        icon_y = pad + (full_h - icon_size) // 2
        im.alpha_composite(icon, (icon_x, icon_y))

        arr = np.asarray(im, np.float32) / 255.0
        self.footer = to_pm(arr[..., [2, 1, 0]], arr[..., 3])
        self.footer_xy = (self.W // 2 - self.footer.shape[1] // 2, self.footer_center_y - self.footer.shape[0] // 2)

    def _apply_footer(self, frame, opacity=1.0):
        """Composite the brand lockup onto the lower black band with a soft fade."""
        if not self.footer_enabled or float(opacity) <= 0.002:
            return frame
        # Decoded ffmpeg frames may be read-only views backed by bytes. Copy before drawing.
        out = frame.copy()
        sprite = self.footer
        x, y = self.footer_xy
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(x + sprite.shape[1], self.W), min(y + sprite.shape[0], self.H)
        if x1 <= x0 or y1 <= y0:
            return out
        src = sprite[y0 - y:y1 - y, x0 - x:x1 - x] * float(np.clip(opacity, 0.0, 1.0))
        alpha = src[..., 3:4]
        dst = out[y0:y1, x0:x1].astype(np.float32) / 255.0
        dst = dst * (1.0 - alpha) + src[..., :3]
        out[y0:y1, x0:x1] = cv2.convertScaleAbs(dst, alpha=255.0)
        return out

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
            # This is the silent logo scene: keep the bars steady while they
            # pop in. The song has not started yet, so do not pretend they are
            # reacting to its RMS envelope during the black page.
            react = 0.92
            blend = 0.0
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

    # ---- compositing helpers --------------------------------------------------
    def _composite_logo(self, out, t):
        """Composite the scaled logo lockup onto a black frame only."""
        x0, y0, x1, y1 = self.roi
        roi = out[y0:y1, x0:x1].astype(np.float32) * (1 / 255.0)
        roi *= (1 - 0.25 * float(self.tl.logo_presence(t)) * self.blob)[..., None]
        layer = self._logo_layer(t)
        roi *= (1 - layer[..., 3:4])
        roi += layer[..., :3]
        out[y0:y1, x0:x1] = cv2.convertScaleAbs(roi, alpha=255.0)
        return out

    def _composite_headphone_prompt(self, out, t):
        hp_a = float(self.tl.hp_alpha(t))
        if hp_a <= 0.002:
            return out
        hx, hy = self.hp_xy
        hy = round(hy + 14 * (1 - float(smooth(seg(t, *self.tl.hp_in)))))
        sp = self.hp
        ys0, ys1 = max(hy, 0), min(hy + sp.shape[0], self.H)
        xs0, xs1 = max(hx, 0), min(hx + sp.shape[1], self.W)
        if ys1 <= ys0 or xs1 <= xs0:
            return out
        s_ = sp[ys0 - hy:ys1 - hy, xs0 - hx:xs1 - hx] * hp_a
        d = out[ys0:ys1, xs0:xs1].astype(np.float32) * (1 / 255.0)
        d = d * (1 - s_[..., 3:4]) + s_[..., :3]
        out[ys0:ys1, xs0:xs1] = cv2.convertScaleAbs(d, alpha=255.0)
        return out

    # ---- public --------------------------------------------------------------
    def apply(self, frame, t):
        tl = self.tl
        if t >= tl.duration:
            footer_alpha = float(smooth(seg(t, tl.duration, tl.duration + 0.38)))
            return self._apply_footer(frame, footer_alpha)

        if t < tl.silent_duration:
            # Scene one: nothing but the correctly-sized logo on pure black.
            # The footer and headphone warning are intentionally absent here.
            out = np.zeros_like(frame)
            return self._composite_logo(out, t)

        # Scene two: song and source video start together. Keep the source
        # blurred/dimmed behind the headphone prompt, then reveal it fully at
        # the lyric handoff. The logo is deliberately not drawn in this scene.
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

        scene_alpha = float(tl.video_scene_alpha(t))
        if scene_alpha < 0.999:
            out = np.clip(out.astype(np.float32) * scene_alpha, 0, 255).astype(np.uint8)
        return self._composite_headphone_prompt(out, t)
