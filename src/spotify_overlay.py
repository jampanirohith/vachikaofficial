from __future__ import annotations
import mimetypes, requests
from pathlib import Path
from .spotify import SpotifyClient, apply_spotify_metadata, SpotifyError
from .hashing import sha256_file

def overlay(cfg,metadata,work):
 sc=cfg.get('spotify',{}) or {}
 if not sc.get('enabled'):return metadata,None,None
 client=SpotifyClient(sc,project_root=str(cfg.root))
 result=client.search_track(title=metadata.title,album=metadata.album,duration_seconds=metadata.duration)
 if not result:return metadata,None,None
 art=None
 art_cfg=sc.get('artwork',{}) or {}
 if art_cfg.get('enabled',True) and result.album_images:
  try:
   out=Path(work)/'spotify_artwork.bin'; out.parent.mkdir(parents=True,exist_ok=True)
   chosen=max(result.album_images,key=lambda x:((x.width or 0)*(x.height or 0),x.width or 0,x.height or 0))
   resp=requests.get(chosen.url,timeout=int(art_cfg.get('timeout_seconds',30)));resp.raise_for_status()
   if not resp.content: raise RuntimeError('Spotify artwork response was empty')
   out.write_bytes(resp.content)
   art={'path':out,'url':chosen.url,'width':chosen.width,'height':chosen.height,'sha256':sha256_file(out),'mime':resp.headers.get('Content-Type') or mimetypes.guess_type(out.name)[0] or 'image/jpeg'}
  except Exception:
   if bool(art_cfg.get('fail_on_error',False)):
    raise
   art=None
 return apply_spotify_metadata(metadata,result),result,art
