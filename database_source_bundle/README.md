# Unified Project Database Bundle

Active database/source schemas for the final unified project.

- `playlist.db`: one-time Spotify playlist snapshot, exact source order, Spotify track identity, playlist membership history, lifecycle. No active popularity/ranking fields.
- `songs.db`: retained songs, source metadata, canonical ISRC, YouTube matching, lyrics, Demucs, alignment, whole-song 8D, hashes and stage state. Spotify catalog data and artwork are retained per song; ISRC is the duplicate-detection key.
- `reels.db`: manual hooks, YouTube hook mapping, complete 8D source, Reel rendering and validation.

A new playlist is fetched once by the Spotify Web API and recorded before song processing begins. The first run then exits. Later runs use the frozen snapshot.

All bundled databases are initialized and pass SQLite integrity checks.
