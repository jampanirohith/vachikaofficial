from src.db_songs import SongsDB


def test_isrc_duplicate_searches_only_finalized_songs(tmp_path):
    db=SongsDB(tmp_path/'songs.db')
    db.ensure_song({'song_key':'1_A','playlist_serial':1,'spotify_track_id':'A','canonical_isrc':'USABC2600001','basename':'a','title':'A','duration_ms':1,'pipeline_status':'FINALIZED'})
    db.ensure_song({'song_key':'2_B','playlist_serial':2,'spotify_track_id':'B','canonical_isrc':'USABC2600001','basename':'b','title':'B','duration_ms':1,'pipeline_status':'pending'})
    assert db.isrc_match('us-abc-26-00001',exclude='2_B')['song_key']=='1_A'
    db.update_song('1_A',pipeline_status='REPLACED_BY:2_B')
    assert db.isrc_match('USABC2600001',exclude='2_B') is None
