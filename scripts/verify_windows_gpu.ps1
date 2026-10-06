$ErrorActionPreference = "Stop"
Write-Host "=== PyTorch CUDA check ==="
python -c "import torch; print('PyTorch:', torch.__version__); print('CUDA available:', torch.cuda.is_available()); print('Torch CUDA runtime:', torch.version.cuda); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE'); x=torch.randn(2048,2048,device='cuda'); print('CUDA tensor:', x.device)"
Write-Host "=== NVIDIA driver check ==="
nvidia-smi
