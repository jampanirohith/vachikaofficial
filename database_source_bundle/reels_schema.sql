PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR REPLACE INTO schema_meta(key,value) VALUES
('schema_name','unified_reels'),
('schema_version','4.0.0');

CREATE TABLE IF NOT EXISTS reels (
    reel_key TEXT PRIMARY KEY,
    song_key TEXT NOT NULL,
    reel_version INTEGER NOT NULL DEFAULT 1,
    pipeline_status TEXT NOT NULL DEFAULT 'pending',
    quality_status TEXT,
    hook_start_ms INTEGER,
    hook_end_ms INTEGER,
    hook_duration_ms INTEGER,
    youtube_video_id TEXT,
    youtube_offset_ms INTEGER,
    youtube_mapped_start_ms INTEGER,
    youtube_mapped_end_ms INTEGER,
    eight_d_source_path TEXT,
    eight_d_source_sha256 TEXT,
    eight_d_whole_song_path TEXT,
    eight_d_whole_song_sha256 TEXT,
    final_reel_path TEXT,
    final_reel_json_path TEXT,
    final_reel_sha256 TEXT,
    output_duration_ms INTEGER,
    output_width INTEGER,
    output_height INTEGER,
    fps REAL,
    video_encoder TEXT,
    audio_codec TEXT,
    audio_channels INTEGER,
    terminal INTEGER NOT NULL DEFAULT 0,
    terminal_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reels_song ON reels(song_key);
CREATE INDEX IF NOT EXISTS idx_reels_status ON reels(pipeline_status, terminal);

CREATE TABLE IF NOT EXISTS reel_hooks (
    reel_key TEXT PRIMARY KEY,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    input_method TEXT NOT NULL DEFAULT 'terminal',
    entered_at TEXT NOT NULL,
    FOREIGN KEY(reel_key) REFERENCES reels(reel_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reel_youtube_sync (
    reel_key TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    video_url TEXT,
    video_offset_ms INTEGER NOT NULL,
    mapped_start_ms INTEGER NOT NULL,
    mapped_end_ms INTEGER NOT NULL,
    guard_before_ms INTEGER NOT NULL DEFAULT 1500,
    guard_after_ms INTEGER NOT NULL DEFAULT 1500,
    status TEXT NOT NULL,
    FOREIGN KEY(reel_key) REFERENCES reels(reel_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reel_audio (
    reel_key TEXT PRIMARY KEY,
    source_8d_path TEXT NOT NULL,
    source_8d_sha256 TEXT NOT NULL,
    crop_start_ms INTEGER NOT NULL,
    crop_end_ms INTEGER NOT NULL,
    crop_duration_ms INTEGER NOT NULL,
    output_path TEXT,
    output_sha256 TEXT,
    status TEXT NOT NULL,
    FOREIGN KEY(reel_key) REFERENCES reels(reel_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reel_video (
    reel_key TEXT PRIMARY KEY,
    video_source_id TEXT NOT NULL,
    source_url TEXT,
    source_start_ms INTEGER NOT NULL,
    source_end_ms INTEGER NOT NULL,
    guard_before_ms INTEGER NOT NULL,
    guard_after_ms INTEGER NOT NULL,
    trim_start_ms INTEGER NOT NULL,
    requested_duration_ms INTEGER NOT NULL,
    actual_duration_ms INTEGER,
    width INTEGER,
    height INTEGER,
    panel_height_px INTEGER,
    panel_height_percent REAL NOT NULL DEFAULT 80.0,
    encoder TEXT,
    output_path TEXT,
    status TEXT NOT NULL,
    FOREIGN KEY(reel_key) REFERENCES reels(reel_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reel_lyrics (
    reel_key TEXT PRIMARY KEY,
    wordlevel_lrc_path TEXT NOT NULL,
    wordlevel_lrc_sha256 TEXT,
    font_name TEXT,
    font_path TEXT,
    placement TEXT NOT NULL DEFAULT 'center',
    line_behavior TEXT NOT NULL DEFAULT 'one_line_at_a_time',
    word_highlight_enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    FOREIGN KEY(reel_key) REFERENCES reels(reel_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reel_validation (
    reel_key TEXT PRIMARY KEY,
    overall INTEGER NOT NULL,
    failed_checks_json TEXT NOT NULL,
    output_exists INTEGER NOT NULL,
    output_duration_matches INTEGER NOT NULL,
    resolution_valid INTEGER NOT NULL,
    aspect_ratio_valid INTEGER NOT NULL,
    audio_stereo INTEGER NOT NULL,
    codec_valid INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(reel_key) REFERENCES reels(reel_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reel_runs (
    run_id TEXT PRIMARY KEY,
    reel_key TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    dependency_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    artifact_path TEXT,
    artifact_sha256 TEXT,
    reused INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    FOREIGN KEY(reel_key) REFERENCES reels(reel_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reel_runs_key_stage ON reel_runs(reel_key, stage_name, status);

CREATE TABLE IF NOT EXISTS reel_audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reel_key TEXT,
    event_type TEXT NOT NULL,
    stage_name TEXT,
    status TEXT,
    message TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(reel_key) REFERENCES reels(reel_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reel_audit_time ON reel_audit_events(reel_key, created_at);
