PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_name','unified_songs'),('schema_version','4.0.0');

CREATE TABLE IF NOT EXISTS songs(
 song_key TEXT PRIMARY KEY,
 playlist_serial INTEGER,
 spotify_track_id TEXT,
 canonical_isrc TEXT,
 ytm_video_id TEXT,
 ytm_url TEXT,
 basename TEXT NOT NULL,
 title TEXT,
 artist TEXT,
 album TEXT,
 duration_ms INTEGER NOT NULL,
 source_mp3_path TEXT,
 source_lrc_path TEXT,
 source_json_path TEXT,
 final_mp3_path TEXT,
 final_lrc_path TEXT,
 final_wordlevel_lrc_path TEXT,
 final_json_path TEXT,
 final_8d_mp3_path TEXT,
 source_mp3_sha256 TEXT,
 source_lrc_sha256 TEXT,
 source_json_sha256 TEXT,
 pipeline_status TEXT NOT NULL DEFAULT 'pending',
 quality_status TEXT,
 terminal INTEGER NOT NULL DEFAULT 0,
 terminal_reason TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_songs_isrc ON songs(canonical_isrc);
CREATE INDEX IF NOT EXISTS idx_songs_spotify_track ON songs(spotify_track_id);
CREATE INDEX IF NOT EXISTS idx_songs_status ON songs(pipeline_status,terminal);

CREATE TABLE IF NOT EXISTS metadata(
 song_key TEXT PRIMARY KEY,
 normalized_title TEXT,
 normalized_artist TEXT,
 normalized_album TEXT,
 album_artist TEXT,
 release_date TEXT,
 release_date_precision TEXT,
 track_number INTEGER,
 disc_number INTEGER,
 album_total_tracks INTEGER,
 album_type TEXT,
 explicit INTEGER,
 genre TEXT,
 composer TEXT,
 publisher TEXT,
 copyright_text TEXT,
 language TEXT,
 bpm REAL,
 compilation INTEGER,
 spotify_isrc TEXT,
 spotify_track_id TEXT,
 spotify_album_id TEXT,
 spotify_track_url TEXT,
 spotify_track_uri TEXT,
 spotify_album_url TEXT,
 spotify_artist_ids_json TEXT,
 spotify_album_images_json TEXT,
 spotify_raw_track_json TEXT,
 spotify_raw_album_json TEXT,
 spotify_raw_artists_json TEXT,
 spotify_artwork_path TEXT,
 spotify_artwork_sha256 TEXT,
 spotify_artwork_mime TEXT,
 spotify_lookup_status TEXT,
 ytm_source_video_id TEXT,
 ytm_source_url TEXT,
 ytm_source_duration_ms INTEGER,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS source_metadata(
 song_key TEXT PRIMARY KEY,
 ytm_search_query TEXT,
 ytm_selected_result_index INTEGER,
 ytm_selected_result_json TEXT,
 ytm_player_metadata_json TEXT,
 ytm_stream_format_json TEXT,
 ytm_source_info_json TEXT,
 ytm_audio_path TEXT,
 ytm_audio_sha256 TEXT,
 ytm_artwork_url TEXT,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ytmusic_audio_matches(
 song_key TEXT PRIMARY KEY,
 search_query TEXT NOT NULL,
 selected_video_id TEXT NOT NULL,
 selected_video_url TEXT NOT NULL,
 selected_title TEXT,
 selected_duration_ms INTEGER NOT NULL,
 spotify_duration_ms INTEGER NOT NULL,
 duration_delta_ms INTEGER NOT NULL,
 search_result_index INTEGER NOT NULL,
 search_results_fetched INTEGER NOT NULL,
 selection_rule TEXT NOT NULL,
 raw_search_result_json TEXT,
 stream_format_json TEXT,
 audio_path TEXT,
 audio_sha256 TEXT,
 status TEXT NOT NULL,
 FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS youtube_matches(
 song_key TEXT PRIMARY KEY,
 video_id TEXT,
 video_url TEXT,
 video_title TEXT,
 selection_query TEXT,
 selection_rule TEXT NOT NULL,
 audio_path TEXT,
 audio_sha256 TEXT,
 offset_ms INTEGER,
 offset_seconds REAL,
 match_score REAL,
 waveform_score REAL,
 energy_score REAL,
 peak_valley_score REAL,
 transition_score REAL,
 reference_duration_ms INTEGER NOT NULL DEFAULT 30000,
 max_accepted_offset_ms INTEGER NOT NULL DEFAULT 30000,
 method_version TEXT,
 selected_at TEXT,
 FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS youtube_offset_candidates(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 song_key TEXT NOT NULL,
 candidate_offset_ms INTEGER NOT NULL,
 total_score REAL NOT NULL,
 waveform_score REAL,
 energy_score REAL,
 peak_valley_score REAL,
 transition_score REAL,
 accepted INTEGER NOT NULL DEFAULT 0,
 rejection_reason TEXT,
 FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_youtube_candidates_song ON youtube_offset_candidates(song_key,total_score DESC);

CREATE TABLE IF NOT EXISTS isrc_duplicates(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 song_key TEXT NOT NULL,
 duplicate_song_key TEXT NOT NULL,
 isrc TEXT NOT NULL,
 decision TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE,
 FOREIGN KEY(duplicate_song_key) REFERENCES songs(song_key) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_isrc_duplicates_song ON isrc_duplicates(song_key);

CREATE TABLE IF NOT EXISTS lyric_sources(
 song_key TEXT PRIMARY KEY,
 provider TEXT NOT NULL,
 synced INTEGER NOT NULL,
 raw_lrc_path TEXT,
 raw_lrc_sha256 TEXT,
 line_count INTEGER,
 blank_marker_count INTEGER,
 status TEXT NOT NULL,
 reason TEXT,
 fetched_at TEXT,
 FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS demucs_runs(
 run_id TEXT PRIMARY KEY,
 song_key TEXT NOT NULL,
 model_name TEXT NOT NULL,
 model_revision TEXT,
 device TEXT NOT NULL,
 input_sha256 TEXT NOT NULL,
 duration_ms INTEGER NOT NULL,
 vocals_path TEXT,drums_path TEXT,bass_path TEXT,other_path TEXT,
 vocals_sha256 TEXT,drums_sha256 TEXT,bass_sha256 TEXT,other_sha256 TEXT,
 status TEXT NOT NULL,created_at TEXT NOT NULL,completed_at TEXT,
 FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_demucs_song ON demucs_runs(song_key,status);
CREATE TABLE IF NOT EXISTS analysis_runs(
 run_id TEXT PRIMARY KEY,song_key TEXT NOT NULL,source_audio_sha256 TEXT NOT NULL,analysis_version TEXT NOT NULL,status TEXT NOT NULL,result_path TEXT,created_at TEXT NOT NULL,completed_at TEXT,
 FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS alignment_runs(
 run_id TEXT PRIMARY KEY,song_key TEXT NOT NULL,input_mp3_sha256 TEXT NOT NULL,input_lrc_sha256 TEXT NOT NULL,demucs_run_id TEXT,model_name TEXT NOT NULL,model_revision TEXT,language TEXT NOT NULL DEFAULT 'tel',normalizer_version TEXT,alignment_method TEXT NOT NULL,config_hash TEXT,quality_status TEXT,status TEXT NOT NULL,canonical_timeline_path TEXT,wordlevel_lrc_path TEXT,created_at TEXT NOT NULL,completed_at TEXT,
 FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE,FOREIGN KEY(demucs_run_id) REFERENCES demucs_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_alignment_song ON alignment_runs(song_key,status);
CREATE TABLE IF NOT EXISTS lyric_lines(id INTEGER PRIMARY KEY AUTOINCREMENT,song_key TEXT NOT NULL,alignment_run_id TEXT NOT NULL,line_index INTEGER NOT NULL,source_start_ms INTEGER,aligned_start_ms INTEGER,aligned_end_ms INTEGER,text TEXT NOT NULL,FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE,FOREIGN KEY(alignment_run_id) REFERENCES alignment_runs(run_id) ON DELETE CASCADE,UNIQUE(alignment_run_id,line_index));
CREATE TABLE IF NOT EXISTS lyric_words(id INTEGER PRIMARY KEY AUTOINCREMENT,song_key TEXT NOT NULL,alignment_run_id TEXT NOT NULL,line_id INTEGER,word_index INTEGER NOT NULL,original_word TEXT NOT NULL,normalized_word TEXT,start_ms INTEGER NOT NULL,end_ms INTEGER NOT NULL,score REAL,source TEXT NOT NULL,interpolated INTEGER NOT NULL DEFAULT 0,review_status TEXT,review_reason TEXT,FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE,FOREIGN KEY(alignment_run_id) REFERENCES alignment_runs(run_id) ON DELETE CASCADE,FOREIGN KEY(line_id) REFERENCES lyric_lines(id));
CREATE INDEX IF NOT EXISTS idx_words_song_time ON lyric_words(song_key,start_ms,end_ms);
CREATE TABLE IF NOT EXISTS audio_8d_runs(
 run_id TEXT PRIMARY KEY,song_key TEXT NOT NULL,input_mp3_sha256 TEXT NOT NULL,demucs_run_id TEXT NOT NULL,alignment_run_id TEXT,config_hash TEXT NOT NULL,engine_version TEXT NOT NULL,duration_ms INTEGER NOT NULL,device TEXT,output_path TEXT,output_sha256 TEXT,status TEXT NOT NULL,validation_status TEXT,manifest_path TEXT,created_at TEXT NOT NULL,completed_at TEXT,
 FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE,FOREIGN KEY(demucs_run_id) REFERENCES demucs_runs(run_id),FOREIGN KEY(alignment_run_id) REFERENCES alignment_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_8d_song ON audio_8d_runs(song_key,status);
CREATE TABLE IF NOT EXISTS stage_runs(
 run_id TEXT PRIMARY KEY,song_key TEXT NOT NULL,stage_name TEXT NOT NULL,dependency_fingerprint TEXT NOT NULL,status TEXT NOT NULL,error_code TEXT,error_message TEXT,artifact_path TEXT,artifact_sha256 TEXT,started_at TEXT NOT NULL,finished_at TEXT,reused INTEGER NOT NULL DEFAULT 0,FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_stage_runs_song_stage ON stage_runs(song_key,stage_name,dependency_fingerprint,status);
CREATE TABLE IF NOT EXISTS source_artifacts(
 id INTEGER PRIMARY KEY AUTOINCREMENT,song_key TEXT NOT NULL,artifact_type TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT,size_bytes INTEGER,immutable INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS audit_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT,song_key TEXT,event_type TEXT NOT NULL,stage_name TEXT,status TEXT,message TEXT,error_code TEXT,created_at TEXT NOT NULL,FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_audit_song_time ON audit_events(song_key,created_at);
