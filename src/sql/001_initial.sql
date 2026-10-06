CREATE TABLE IF NOT EXISTS songs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    basename TEXT NOT NULL UNIQUE,
    serial_number INTEGER,
    video_id TEXT,

    original_mp3_path TEXT NOT NULL,
    original_lrc_path TEXT NOT NULL,
    original_json_path TEXT NOT NULL,
    original_mp3_sha256 TEXT NOT NULL,
    original_lrc_sha256 TEXT NOT NULL,
    original_json_sha256 TEXT NOT NULL,

    final_mp3_path TEXT,
    final_lrc_path TEXT,
    final_json_path TEXT,
    final_mp3_sha256 TEXT,
    final_lrc_sha256 TEXT,
    final_json_sha256 TEXT,

    title TEXT,
    artist TEXT,
    album TEXT,
    duration_ms INTEGER,

    expected_lines INTEGER DEFAULT 0,
    aligned_lines INTEGER DEFAULT 0,
    expected_words INTEGER DEFAULT 0,
    aligned_words INTEGER DEFAULT 0,
    interpolated_words INTEGER DEFAULT 0,
    missing_words INTEGER DEFAULT 0,
    unsupported_words INTEGER DEFAULT 0,

    total_chunks INTEGER DEFAULT 0,
    successful_chunks INTEGER DEFAULT 0,
    low_confidence_chunks INTEGER DEFAULT 0,
    failed_chunks INTEGER DEFAULT 0,

    mean_alignment_score REAL,
    p10_alignment_score REAL,
    minimum_alignment_score REAL,
    low_score_word_percent REAL,
    interpolated_word_percent REAL,
    failed_audio_percent REAL,
    median_anchor_shift_ms REAL,
    p90_anchor_shift_ms REAL,
    max_anchor_shift_ms REAL,

    pipeline_status TEXT NOT NULL DEFAULT 'pending',
    quality_status TEXT NOT NULL DEFAULT 'unknown',
    retry_count INTEGER DEFAULT 0,
    error_code TEXT,
    error_remark TEXT,

    pipeline_version TEXT,
    model_name TEXT,
    model_revision TEXT,
    normalizer_version TEXT,
    config_hash TEXT,
    run_id INTEGER,
    package_id TEXT,

    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,

    logical_start_ms INTEGER NOT NULL,
    logical_end_ms INTEGER NOT NULL,
    audio_start_ms INTEGER NOT NULL,
    audio_end_ms INTEGER NOT NULL,

    line_start_index INTEGER,
    line_end_index INTEGER,
    text_content TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    boundary_reason TEXT,

    word_count INTEGER DEFAULT 0,
    alignment_score REAL,
    p10_alignment_score REAL,
    minimum_alignment_score REAL,
    status TEXT NOT NULL DEFAULT 'pending',

    input_audio TEXT,
    device TEXT,
    audio_path TEXT,
    result_json_path TEXT,

    attempt_count INTEGER DEFAULT 0,
    error_code TEXT,
    error_remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY(song_id) REFERENCES songs(id) ON DELETE CASCADE,
    UNIQUE(song_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id INTEGER NOT NULL,
    chunk_id INTEGER,
    line_index INTEGER NOT NULL,
    word_index INTEGER NOT NULL,
    original_word TEXT NOT NULL,
    normalized_word TEXT NOT NULL,
    start_ms INTEGER,
    end_ms INTEGER,
    alignment_score REAL,
    source TEXT NOT NULL,
    is_interpolated INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    FOREIGN KEY(song_id) REFERENCES songs(id) ON DELETE CASCADE,
    FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS instrumental_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    section_type TEXT NOT NULL,
    detector TEXT,
    confidence REAL,
    evidence_json TEXT,
    FOREIGN KEY(song_id) REFERENCES songs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS processing_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id INTEGER,
    step TEXT NOT NULL,
    level TEXT NOT NULL,
    attempt INTEGER DEFAULT 1,
    message TEXT,
    metadata_json TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(song_id) REFERENCES songs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    pipeline_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_revision TEXT,
    language_code TEXT NOT NULL,
    normalizer_version TEXT NOT NULL,
    environment_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_songs_pipeline_status ON songs(pipeline_status);
CREATE INDEX IF NOT EXISTS idx_songs_quality_status ON songs(quality_status);
CREATE INDEX IF NOT EXISTS idx_songs_source_hash ON songs(original_mp3_sha256);
CREATE INDEX IF NOT EXISTS idx_chunks_song_status ON chunks(song_id, status);
CREATE INDEX IF NOT EXISTS idx_words_song_order ON words(song_id, line_index, word_index);
