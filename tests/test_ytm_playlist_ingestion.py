from src.playlist_ingest import PlaylistClient, normalize_item, ingest_tracks, PlaylistIngestionError


class FakeYT:
    def __init__(self): self.calls=[]
    def get_playlist(self, playlist_id, limit=None, related=False, suggestions_limit=0):
        self.calls.append(("get_playlist",playlist_id,limit,related,suggestions_limit))
        return {"id": playlist_id, "title": "P", "tracks": [
            {"videoId":"A","title":"A","artists":[{"name":"X"}],"duration_seconds":200},
            {"videoId":"B","title":"B","artists":[{"name":"Y"}],"duration_seconds":210},
        ]}


def test_complete_playlist_is_one_ytmusic_api_call_and_no_count_lookup():
    fake=FakeYT(); client=PlaylistClient(); client._api=fake
    pl=client.fetch_complete('PL123')
    tracks=[normalize_item(x,i) for i,x in enumerate(pl['tracks'],1)]
    assert [t['playlist_position'] for t in tracks]==[1,2]
    assert not any(k in tracks[0] for k in ('ytm_views','ytm_plays','priority_count','priority_metric'))
    assert fake.calls==[("get_playlist","PL123",None,False,0)]


def test_normalize_preserves_source_order_and_raw_payload():
    item={"videoId":"B","title":"B","views":"386M","duration_seconds":278}
    out=normalize_item(item,7)
    assert out['video_id']=='B'
    assert out['playlist_position']==7
    assert out['raw']==item
    assert 'views' not in out or out['views'] is None


def test_missing_auth_is_clear_error(tmp_path):
    client=PlaylistClient(tmp_path/'missing_browser.json', require_auth=True)
    try:
        client.api
    except PlaylistIngestionError as exc:
        assert 'authentication file not found' in str(exc).lower()
    else:
        raise AssertionError('expected clear auth error')
