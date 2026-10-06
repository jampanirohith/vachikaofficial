from __future__ import annotations
from pathlib import Path
from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TALB, TPE2, TDRC, TRCK, TPOS, TSRC, TCON, TCOP, TPUB, TCOM, TBPM, TLAN, COMM, TXXX, USLT, APIC, SYLT, Encoding
from mutagen.mp3 import MP3
from .hashing import sha256_file

def _first_text(tag,key):
 try:
  fr=tag.getall(key);return str(fr[0].text[0]) if fr and getattr(fr[0],'text',None) else None
 except Exception:return None

def embed(source,dest,metadata,artwork,wordlevel_words,extra_tags):
 source=Path(source);dest=Path(dest);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(source.read_bytes())
 try: tags=ID3(dest)
 except ID3NoHeaderError: tags=ID3()
 def setone(frame):
  # Mutagen frames are not uniformly subscriptable. In particular TDRC is
  # represented by ID3TimeStamp, so frame[0] is the timestamp value rather
  # than the ID3 frame key. Always delete by the frame's canonical HashKey.
  frame_key = getattr(frame, "HashKey", None) or getattr(frame, "FrameID", None)
  if not frame_key:
   raise TypeError(f"Unsupported ID3 frame without HashKey/FrameID: {type(frame)!r}")
  tags.delall(frame_key)
  tags.add(frame)
 if metadata.title:setone(TIT2(encoding=3,text=metadata.title))
 if metadata.artists:setone(TPE1(encoding=3,text=metadata.artists))
 if metadata.album:setone(TALB(encoding=3,text=metadata.album))
 if metadata.album_artist:setone(TPE2(encoding=3,text=metadata.album_artist))
 if metadata.release_date:setone(TDRC(encoding=3,text=metadata.release_date))
 if metadata.track_number:setone(TRCK(encoding=3,text=metadata.track_number))
 if metadata.disc_number:setone(TPOS(encoding=3,text=metadata.disc_number))
 if metadata.isrc:setone(TSRC(encoding=3,text=metadata.isrc))
 if metadata.genre:setone(TCON(encoding=3,text=metadata.genre))
 if metadata.copyright:setone(TCOP(encoding=3,text=metadata.copyright))
 if metadata.publisher:setone(TPUB(encoding=3,text=metadata.publisher))
 if metadata.composer:setone(TCOM(encoding=3,text=metadata.composer))
 if metadata.bpm is not None:setone(TBPM(encoding=3,text=str(metadata.bpm)))
 if metadata.language:setone(TLAN(encoding=3,text=metadata.language))
 # Deliberately preserve all existing SYLT/USLT/APIC/custom fields. Add only dedicated frames.
 existing_apic=tags.getall('APIC')
 if artwork:
  tags.delall('APIC');
  mime=artwork.get('mime') or 'image/jpeg';tags.add(APIC(encoding=3,mime=mime,type=3,desc='Unified source artwork',data=Path(artwork['path']).read_bytes()))
 for key,value in extra_tags.items():
  if value is None:continue
  # TXXX HashKeys include the description; replace our managed value rather
  # than accumulating duplicate custom frames across retries/recovery runs.
  tags.delall(f"TXXX:{key}")
  tags.add(TXXX(encoding=3,desc=key,text=str(value)))
 if wordlevel_words:
  # SYLT expects [(text, seconds)] plus language and format metadata.
  vals=[(str(w.get('original_word') or w.get('word') or ''),int(w['start_ms'])) for w in wordlevel_words if int(w.get('start_ms',0))>=0]
  if vals:
   tags.delall('SYLT:UnifiedWordLevel')
   tags.add(SYLT(encoding=3,lang='tel',format=2,type=1,desc='UnifiedWordLevel',text=vals))
 tags.save(dest,v2_version=4)
 return {'sha256':sha256_file(dest),'preserved_existing_apic_count':len(existing_apic)}
