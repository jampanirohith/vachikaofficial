from types import SimpleNamespace

from mutagen.id3 import ID3

from src.mp3_metadata import embed


def _metadata(release_date="2007-01-01"):
    return SimpleNamespace(
        title="Bommanu Geesthey", artists="Artist", album="Album",
        album_artist="Album Artist", release_date=release_date,
        track_number="1", disc_number="1", isrc="INTEST1234567",
        genre="Telugu", copyright=None, publisher="Label", composer=None,
        bpm=None, language="tel",
    )


def test_embed_writes_tdrc_without_using_id3_timestamp_as_key(tmp_path):
    source = tmp_path / "source.mp3"
    dest = tmp_path / "final.mp3"
    source.write_bytes(b"")

    embed(source, dest, _metadata(), None, [], {})

    tags = ID3(dest)
    assert str(tags["TDRC"]) == "2007-01-01"
    assert tags["TIT2"].text == ["Bommanu Geesthey"]
    assert tags["TPE1"].text == ["Artist"]


def test_embed_can_replace_existing_tdrc(tmp_path):
    source = tmp_path / "source.mp3"
    dest = tmp_path / "final.mp3"
    source.write_bytes(b"")

    embed(source, dest, _metadata("2007"), None, [], {})
    embed(dest, dest, _metadata("2008-05-06"), None, [], {})

    tags = ID3(dest)
    assert str(tags["TDRC"]) == "2008-05-06"


def test_embed_replaces_managed_txxx_values(tmp_path):
    source = tmp_path / "source.mp3"
    dest = tmp_path / "final.mp3"
    source.write_bytes(b"")

    embed(source, dest, _metadata(), None, [], {"SPOTIFY_ISRC": "FIRST"})
    embed(dest, dest, _metadata(), None, [], {"SPOTIFY_ISRC": "SECOND"})

    tags = ID3(dest)
    assert tags.getall("TXXX:SPOTIFY_ISRC")[0].text == ["SECOND"]
    assert len(tags.getall("TXXX:SPOTIFY_ISRC")) == 1
