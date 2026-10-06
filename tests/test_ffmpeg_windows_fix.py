from pathlib import Path
from src import audio_unified

def test_windows_ffmpeg_output_explicit_wav(monkeypatch,tmp_path):
 seen=[]
 class P: pass
 tmp_src=tmp_path/'song.mp3'; tmp_src.write_bytes(b'x')
 def fake_run(cmd,*a,**kw):
  seen.append(cmd)
  # emulate created explicit .wav output
  Path(cmd[-1]).write_bytes(b'x'*20000)
  return P()
 monkeypatch.setattr(audio_unified,'run',fake_run)
 monkeypatch.setattr(audio_unified,'duration_ms',lambda p:10000)
 out,meta=audio_unified.prepare_source(tmp_path/'song.mp3',tmp_path)
 assert '-f' in seen[0] and 'wav' in seen[0]
 assert str(seen[0][-1]).endswith('.wav')


def test_full_source_decode_is_lossless_working_representation(monkeypatch,tmp_path):
    seen=[]
    class Info:
        info=type('I',(),{'sample_rate':44100,'channels':2})()
    class P: pass
    def fake_mp3(*a,**kw): return Info()
    def fake_run(cmd,*a,**kw):
        seen.append(cmd)
        Path(cmd[-1]).write_bytes(b'x'*20000)
        return P()
    monkeypatch.setattr(audio_unified,'MP3',fake_mp3)
    monkeypatch.setattr(audio_unified,'run',fake_run)
    monkeypatch.setattr(audio_unified,'duration_ms',lambda p:10000)
    monkeypatch.setattr(audio_unified,'sha256_file',lambda p:'h')
    out,meta=audio_unified.prepare_full_source(tmp_path/'song.mp3',tmp_path)
    assert out.suffix=='.wav' and meta['sample_rate']==44100 and meta['channels']==2
    assert '-f' in seen[0] and 'wav' in seen[0] and '-ac' in seen[0] and '2' in seen[0]
