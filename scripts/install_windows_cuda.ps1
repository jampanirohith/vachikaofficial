$ErrorActionPreference = "Stop"

Write-Host "=== Unified pipeline Windows + NVIDIA CUDA environment setup ==="
python --version

Write-Host "=== NVIDIA driver diagnostic ==="
try {
    nvidia-smi
} catch {
    Write-Warning "nvidia-smi is unavailable. The project will retain CPU fallback, but CUDA cannot be verified on this machine yet."
}

Write-Host "Removing any CPU-only PyTorch build..."
python -m pip uninstall -y torch torchaudio

Write-Host "Installing the pinned official CUDA 12.8 PyTorch wheels..."
python -m pip install --upgrade pip setuptools wheel
python -m pip install --upgrade --force-reinstall `
  torch==2.9.1+cu128 `
  torchaudio==2.9.1+cu128 `
  --index-url https://download.pytorch.org/whl/cu128

Write-Host "Installing Phase 2 runtime dependencies..."
python -m pip install -r requirements.txt

Write-Host "=== Verifying CUDA ==="
python -c "import torch; print('PyTorch:', torch.__version__); print('Torch CUDA runtime:', torch.version.cuda); print('CUDA available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE'); x=torch.randn(256,256,device='cuda') if torch.cuda.is_available() else None; print('CUDA tensor:', x.device if x is not None else 'SKIPPED')"

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Setup complete. Run: python main.py --doctor"
