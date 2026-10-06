from __future__ import annotations
from .utils import parse_timecode,fmt_ms

def prompt_or_existing(state):
 s=state.get('hook_start_ms');e=state.get('hook_end_ms')
 if s is not None and e is not None:return int(s),int(e)
 while True:
  try:start=input('Hook start (MM:SS.xxx):\n> ');end=input('Hook end (MM:SS.xxx):\n> ');a=parse_timecode(start);b=parse_timecode(end)
  except Exception as exc:print(f'Invalid timecode: {exc}');continue
  if b<=a:print('Hook end must be after hook start.');continue
  return a,b

def validate(start,end,duration):
 if not (0<=start<end<=duration):raise ValueError(f'Hook must be inside 0..{fmt_ms(duration)}')
 return end-start
