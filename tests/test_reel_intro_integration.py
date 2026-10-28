from pathlib import Path

from src.rendercore.reel_intro import Timeline
from src.video.reel import _constrain_reel_window_to_video, _first_lyric_ms_in_hook, _hide_lead_in_lyrics, _parse_word_lrc, _plan_reel_window


def test_intro_window_prepends_lead_in_before_hook_lyric(tmp_path):
    lrc = tmp_path / "wordlevel.lrc"
    lrc.write_text("[00:10.000]hello\n[00:11.000]world\n", encoding="utf-8")

    assert _first_lyric_ms_in_hook(lrc, 10_000, 13_000) == 10_000
    start, intro_ms, first_vocal, detail = _plan_reel_window(
        lrc, 10_000, 13_000, intro_enabled=True, intro_duration_ms=3_200
    )

    assert start == 6_800
    assert intro_ms == 3_200
    assert first_vocal == 10_000
    assert detail["enabled"] is True
    assert detail["pre_roll_ms"] == 3_200


def test_intro_window_adapts_when_song_starts_too_early(tmp_path):
    lrc = tmp_path / "wordlevel.lrc"
    lrc.write_text("[00:01.500]hello\n", encoding="utf-8")

    start, intro_ms, first_vocal, detail = _plan_reel_window(
        lrc, 0, 5_000, intro_enabled=True, intro_duration_ms=3_200
    )
    assert start == 0
    assert intro_ms == 1_500
    assert first_vocal == 1_500
    assert detail["enabled"] is True
    assert detail["duration_ms"] == 1_500


def test_intro_window_disables_opening_if_lead_in_is_too_short(tmp_path):
    lrc = tmp_path / "wordlevel.lrc"
    lrc.write_text("[00:00.400]hello\n", encoding="utf-8")

    start, intro_ms, _first_vocal, detail = _plan_reel_window(
        lrc, 0, 5_000, intro_enabled=True, intro_duration_ms=3_200
    )
    assert start == 0
    assert intro_ms == 0
    assert detail["enabled"] is False
    assert detail["reason"] == "insufficient_pre_vocal_lead_in"


def test_intro_window_is_opt_out_without_changing_hook_times(tmp_path):
    lrc = tmp_path / "wordlevel.lrc"
    lrc.write_text("[00:10.000]hello\n", encoding="utf-8")

    start, intro_ms, first_vocal, detail = _plan_reel_window(
        lrc, 10_000, 13_000, intro_enabled=False, intro_duration_ms=3_200
    )
    assert start == 10_000
    assert intro_ms == 0
    assert first_vocal == 10_000
    assert detail["reason"] == "disabled_by_config"


def test_intro_hides_earlier_lyric_words_without_shifting_handoff_word(tmp_path):
    lrc = tmp_path / "wordlevel.lrc"
    lrc.write_text("[00:09.000]before[00:09.500]intro[00:10.000]hello[00:10.400]there\n", encoding="utf-8")

    parsed = _parse_word_lrc(lrc)
    visible = _hide_lead_in_lyrics(parsed, 10_000)

    assert len(visible) == 1
    assert visible[0]["display_text"] == "hello there"
    assert [word["text"] for word in visible[0]["words"]] == ["hello", "there"]
    assert visible[0]["words"][0]["start_ms"] == 10_000
    assert visible[0]["words"][1]["start_ms"] == 10_400


def test_intro_lead_in_is_shortened_when_video_mapping_starts_near_zero():
    info = {"enabled": True, "reason": "opening_enabled", "duration_ms": 3_200}
    start, intro_ms, detail = _constrain_reel_window_to_video(
        6_800, 3_200, 10_000, 10_000, -9_000, info
    )
    assert start == 9_000
    assert intro_ms == 1_000
    assert detail["source_video_start_limited"] is True
    assert detail["reason"] == "opening_enabled_source_start_limited"


def test_intro_is_disabled_when_video_boundary_leaves_too_little_time():
    info = {"enabled": True, "reason": "opening_enabled", "duration_ms": 3_200}
    start, intro_ms, detail = _constrain_reel_window_to_video(
        6_800, 3_200, 10_000, 10_000, -9_800, info
    )
    assert start == 10_000
    assert intro_ms == 0
    assert detail["reason"] == "insufficient_video_lead_in"


def test_invalid_selected_hook_video_mapping_fails_loudly():
    import pytest
    with pytest.raises(RuntimeError, match="VIDEO_SOURCE_MAPPING_STARTS_BEFORE_ZERO"):
        _constrain_reel_window_to_video(0, 0, 0, 500, -1_000, {"enabled": False})


def test_shortened_timeline_scales_all_time_markers():
    timeline = Timeline().scaled_to(1.5)
    assert timeline.duration == 1.5
    assert timeline.first_vocal == 1.40625
    assert timeline.hp_out[1] == 1.40625


def test_silent_black_intro_and_active_video_phase_are_separate():
    timeline = Timeline.with_silent_lead(silent_duration=2.4, active_duration=3.8)

    assert timeline.silent_duration == 2.4
    assert abs(timeline.duration - 6.2) < 1e-9
    # The complete logo animates and fades out on black before the song/video
    # scene begins. The warning belongs only to scene two.
    assert timeline.wordmark[1] < timeline.logo_evaporate[0]
    assert timeline.logo_evaporate[1] == timeline.silent_duration
    assert timeline.hp_in[0] == timeline.silent_duration
    assert timeline.video_fade_in[0] == timeline.silent_duration
    assert timeline.hp_alpha(timeline.silent_duration - 0.01) == 0.0
    assert timeline.hp_alpha(timeline.silent_duration + 0.9) > 0.9
    assert timeline.blur_hold_until > timeline.silent_duration
    assert timeline.blur_lift_end == timeline.duration
    assert timeline.hp_out[1] == timeline.duration


def test_logo_and_headphone_warning_render_on_separate_scenes():
    import numpy as np
    from src.rendercore.reel_intro.overlay import IntroOverlay
    from src.rendercore.reel_intro.timeline import H, W

    timeline = Timeline.with_silent_lead(silent_duration=2.4, active_duration=3.8)
    overlay = IntroOverlay(
        "assets/reel_intro", env=np.zeros(200, dtype=np.float32), beat_t=0.7,
        timeline=timeline, footer_enabled=False,
    )
    black = np.zeros((H, W, 3), dtype=np.uint8)

    # Scene one contains only the animated logo. There is no warning yet.
    logo_scene = overlay.apply(black, 1.45)
    assert int(logo_scene.sum()) > 0
    assert timeline.hp_alpha(1.45) == 0.0

    # Scene two contains only the warning on black when given a black source;
    # the logo must not persist into it.
    warning_scene = overlay.apply(black, timeline.silent_duration + 0.9)
    assert not np.any(warning_scene[:1050])
    assert int(warning_scene[1180:1420].sum()) > 0


def test_footer_fades_in_only_after_the_two_intro_scenes():
    import numpy as np
    from src.rendercore.reel_intro.overlay import IntroOverlay
    from src.rendercore.reel_intro.timeline import H, W

    timeline = Timeline.with_silent_lead(silent_duration=2.4, active_duration=3.8)
    overlay = IntroOverlay(
        "assets/reel_intro", env=np.zeros(200, dtype=np.float32), beat_t=0.7,
        timeline=timeline, footer_enabled=True,
    )
    black = np.zeros((H, W, 3), dtype=np.uint8)

    # The footer does not compete with either opening scene; it fades into the
    # lower black band once the video/lyrics have reached the handoff.
    last_warning = overlay.apply(black, timeline.duration - 0.01)
    first_handoff = overlay.apply(black, timeline.duration)
    halfway = overlay.apply(black, timeline.duration + 0.19)
    full = overlay.apply(black, timeline.duration + 0.8)
    assert int(last_warning.sum()) == 0
    assert int(first_handoff.sum()) == 0
    assert 0 < int(halfway.sum()) < int(full.sum())


def test_zero_silent_duration_preserves_immediate_media_start():
    timeline = Timeline.with_silent_lead(silent_duration=0.0, active_duration=3.8)

    assert timeline.silent_duration == 0.0
    assert timeline.duration == 3.8
    assert timeline.hp_in[0] == 0.0
    assert timeline.video_fade_in[0] == 0.0


def test_headphone_prompt_divider_and_persistent_instagram_footer():
    import numpy as np
    from src.rendercore.reel_intro import H, W, IntroOverlay

    timeline = Timeline.with_silent_lead(silent_duration=2.0, active_duration=3.8)
    overlay = IntroOverlay(
        "assets/reel_intro",
        timeline=timeline,
        footer_enabled=True,
        footer_text="Vachika",
        footer_center_y=1800,
    )

    # The headphone sprite contains a visible vertical divider before the copy.
    alpha = overlay.hp[..., 3]
    divider = alpha[:, overlay.hp_divider_x:overlay.hp_divider_x + 3]
    assert divider.shape[1] == 3
    assert int(np.count_nonzero(divider > 0.5)) > 10

    black = np.zeros((H, W, 3), dtype=np.uint8)
    opening = overlay.apply(black.copy(), 0.5)
    assert not np.any(opening[1750:1850])  # no footer during the silent black opening

    finished = overlay.apply(black.copy(), timeline.duration + 0.2)
    assert np.any(finished[1750:1850])  # brand footer survives after the intro animation
    assert not np.any(finished[:1680])   # footer does not paint over the video panel
