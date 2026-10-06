$ErrorActionPreference = "Stop"
python .\main.py --doctor
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python .\main.py
