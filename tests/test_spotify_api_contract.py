from src.spotify import SpotifyClient


def test_full_track_collects_track_album_artists_and_isrc(monkeypatch):
    c=SpotifyClient({'client_id':'x','client_secret':'y','use_env':False,'market':'IN','max_retries':1,'timeout_seconds':5})
    monkeypatch.setattr(c,'_get_token',lambda:'tok')
    def req(method,path,params=None):
        if path.startswith('/tracks/'):
            return {'id':'T1','name':'Title','uri':'spotify:track:T1','external_urls':{'spotify':'https://open.spotify.com/track/T1'},'external_ids':{'isrc':'USABC2600001'},'duration_ms':200000,'explicit':False,'track_number':3,'disc_number':1,'artists':[{'id':'A1','name':'Artist','external_urls':{'spotify':'https://open.spotify.com/artist/A1'}}],'album':{'id':'AL1','name':'Album','album_type':'album','total_tracks':10,'release_date':'2024-01-01','release_date_precision':'day','images':[{'url':'https://i.scdn.co/image/large','width':640,'height':640}]}}
        if path.startswith('/albums/'):
            return {'id':'AL1','name':'Album','artists':[{'id':'A2','name':'Album Artist'}],'images':[{'url':'https://i.scdn.co/image/large','width':640,'height':640}], 'total_tracks':10,'album_type':'album','release_date':'2024-01-01','release_date_precision':'day','label':'Label','copyrights':[{'text':'Copyright'}]}
        if path.startswith('/artists/'):
            return {'id':path.rsplit('/',1)[1],'name':'Artist','genres':['pop']}
        raise AssertionError(path)
    monkeypatch.setattr(c,'_request',req)
    r=c.get_full_track('T1')
    assert r.track_name=='Title' and r.album_name=='Album' and r.isrc=='USABC2600001'
    assert r.album_artwork_url=='https://i.scdn.co/image/large'


def test_full_track_fetches_album_artist_details(monkeypatch):
    from src.spotify import SpotifyClient, apply_spotify_metadata
    from src.metadata import NormalizedMetadata
    c=SpotifyClient({'client_id':'x','client_secret':'y','use_env':False,'market':'IN','max_retries':1,'timeout_seconds':5})
    monkeypatch.setattr(c,'_get_token',lambda:'tok')
    calls=[]
    def req(method,path,params=None):
        calls.append(path)
        if path == '/tracks/T2':
            return {'id':'T2','name':'T2','duration_ms':1000,'external_ids':{'isrc':'GBDEF2600002'},'artists':[{'id':'A1','name':'Track Artist'}], 'album':{'id':'AL2','name':'Album','artists':[{'id':'AA2','name':'Album Artist'}],'images':[]}}
        if path == '/albums/AL2':
            return {'id':'AL2','name':'Album','artists':[{'id':'AA2','name':'Album Artist'}],'images':[],'copyrights':[{'text':'C'}],'label':'L'}
        if path == '/artists/A1': return {'id':'A1','name':'Track Artist','genres':['g1']}
        if path == '/artists/AA2': return {'id':'AA2','name':'Album Artist','genres':['g2']}
        raise AssertionError(path)
    monkeypatch.setattr(c,'_request',req)
    r=c.get_full_track('T2')
    assert {x['id'] for x in r.artist_details} == {'A1','AA2'}
    values={f.name: None for f in NormalizedMetadata.__dataclass_fields__.values()}
    values.update(title='old', title_original='old', primary_artist='old', artist='old', artists=['old'], album='old', album_artist='old', raw={})
    md=NormalizedMetadata(**values)
    out=apply_spotify_metadata(md,r)
    assert out.copyright == 'C' and out.publisher == 'L'
