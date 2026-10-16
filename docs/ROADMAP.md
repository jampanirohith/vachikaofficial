# Development roadmap

## Completed in 1.4.30-src-organized

- Grouped active implementations into `providers/`, `audio/`, `video/`, `storage/`, `orchestration/`, `common/`, `diagnostics/` and `alignment/`; removed old root-level aliases after updating app, test and script imports.
- Preserved the active Telugu alignment engine (`src/phase2core/`) and media rendering engine (`src/rendercore/`).
- Centralized final song JSON writing under `unified_song_master_v2` with source/config fingerprints, method identifiers, output artifact hashes, validation status and a JSON SHA-256 sidecar.
- Resolved embedded font family names and made ASS output, Reel JSON and DB provenance agree.
- Replaced Requests' process-wide timeout monkey patch with a timeout-bearing session dedicated to `ytmusicapi`.
- Kept prior cache-provenance, Demucs attempt isolation, offset acceptance, exact Reel timeline, OAuth state, configured DB maintenance and read-only dry-run fixes under regression coverage.

## Next improvements requiring representative data

1. **8D audio quality:** run listening tests and objective checks for clipping, peak/RMS, mono compatibility, loudness and stem synchronization before changing processing defaults.
2. **Offset accuracy:** benchmark the LRCLIB/MMS dual-audio lyric-anchor method on an annotated set of official videos, edits, intros, remixes and live versions. Measure first-anchor false matches, matched-word coverage, median absolute deviation, false-accept/false-reject rates, and offset error in milliseconds. Do not reintroduce an energy-envelope fallback; uncertain offsets must be reviewed.
3. **Word timing quality:** add Telugu-specific reference examples for compound words, punctuation, elongated vowels, repeated phrases and music-only gaps; preserve warnings and evidence for review.
4. **Schema migration policy:** each logical table group has one file-backed schema source, while all groups share `vachika.db`; schema changes still need explicit unified-database migration/versioning and rollback strategy.
5. **Static checks:** run Ruff and mypy in the target development environment after installing `requirements-dev.txt`.

Threshold/default changes should not be described as improvements until validated on reproducible examples and actual output media.
