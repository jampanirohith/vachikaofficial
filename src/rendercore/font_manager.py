from __future__ import annotations
from pathlib import Path
import zipfile, tempfile, subprocess
from .utils import Phase3Error

OFFICIAL_API='https://api.github.com/repos/EkType/Baloo2/releases/latest'

def ensure_telugu_font(font_dir:Path, filename='BalooTammudu2-ExtraBold.ttf', configured_path: str|None=None)->Path:
    font_dir.mkdir(parents=True,exist_ok=True)
    if configured_path:
        cp=Path(configured_path).expanduser().resolve()
        if cp.exists() and cp.is_file(): return cp
    target=font_dir/filename
    if target.exists() and target.stat().st_size>10000:
        return target
    try:
        p=subprocess.run(['fc-match','-f','%{file}','Baloo Tammudu 2 ExtraBold'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=10)
        if p.returncode==0 and p.stdout.strip() and Path(p.stdout.strip()).exists() and 'baloo' in Path(p.stdout.strip()).name.lower():
            return Path(p.stdout.strip())
    except Exception:
        pass
    try:
        import requests
        r=requests.get(OFFICIAL_API,timeout=30,headers={'Accept':'application/vnd.github+json','User-Agent':'phase3-font-installer'})
        r.raise_for_status(); release=r.json()
        asset=next((a for a in release.get('assets',[]) if str(a.get('name','')).lower().endswith('.zip')),None)
        if not asset: raise RuntimeError('No Baloo2 release ZIP asset found')
        with tempfile.TemporaryDirectory() as td:
            zpath=Path(td)/'baloo.zip'
            zpath.write_bytes(requests.get(asset['browser_download_url'],timeout=120).content)
            with zipfile.ZipFile(zpath) as z:
                matches=[n for n in z.namelist() if n.endswith(filename)]
                if not matches: raise RuntimeError(f'{filename} not found in release asset')
                target.write_bytes(z.read(matches[0]))
        return target
    except Exception as exc:
        # Graceful typography fallback: use a Telugu-capable Noto font if available.
        try:
            p=subprocess.run(['fc-match','-f','%{file}','Noto Sans Telugu ExtraBold'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=10)
            if p.returncode==0 and p.stdout.strip() and Path(p.stdout.strip()).exists():
                return Path(p.stdout.strip())
        except Exception:
            pass
        raise Phase3Error('LYRIC_RENDER_FAILED',f'Telugu font unavailable: {exc}') from exc
