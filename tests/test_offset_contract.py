from src import youtube_sync

def test_offset_hard_bound_prefers_valid_candidate(monkeypatch):
 def fake_scan(ref,yt,sr,max_offset,step_ms=250):
  return [
   {'candidate_offset_ms':45000,'total_score':.99,'waveform_score':.99,'energy_score':.99,'peak_valley_score':.99,'transition_score':.99},
   {'candidate_offset_ms':12000,'total_score':.80,'waveform_score':.80,'energy_score':.80,'peak_valley_score':.80,'transition_score':.80},
  ]
 monkeypatch.setattr(youtube_sync,'_decode',lambda p:__import__('numpy').zeros(100000,dtype='float32'))
 monkeypatch.setattr(youtube_sync,'_scan',fake_scan)
 chosen,cands=youtube_sync.find_offset('a','b')
 assert chosen['candidate_offset_ms']==12000
 assert cands[0]['accepted'] is False
 assert cands[0]['rejection_reason']=='offset_above_30000ms'
 assert cands[1]['accepted'] is True
