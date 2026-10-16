# Runtime database directory

The clean source package contains no live database. It includes `songs.json` as an empty
catalog scaffold so the catalog path and structure are visible from first extraction. On
the first database-enabled command, Vachika creates `vachika.db` here and atomically
replaces the scaffold with the generated song catalog. SQLite is authoritative; do not
edit `songs.json` by hand.

Backups and verified migration snapshots are stored under `backups/` after a user
installation has database state. Keep this directory private once it contains
playlist/song metadata or generated run state.
