# NVIDIA CUDA / GPU Audit — v1.4.28-gpu-hardening

## Scope

This audit covers the complete active source tree in the supplied project ZIP, including the legacy unified pipeline, the embedded Phase-2 alignment runtime, the render/Reel runtime, configuration, requirements, Windows setup scripts, and tests.

## Hardware/runtime result for this packaging environment

This container cannot validate the user's physical NVIDIA GPU because its installed PyTorch is:

- `torch==2.10.0+cpu`
- `torch.version.cuda == None`
- `torch.cuda.is_available() == False`
- `nvidia-smi` is unavailable here

Therefore this environment **did not execute HTDemucs, MMS/CTC, or Silero VAD on an NVIDIA GPU**. This is an environment limitation, not the configured target behavior.

The project dependency contract remains CUDA-enabled PyTorch:

- `torch==2.9.1+cu128`
- `torchaudio==2.9.1+cu128`
- PyTorch CUDA wheel index: CUDA 12.8

## Active GPU-capable stages

### 1. HTDemucs

The active unified path is `src/pipeline.py -> src/demucs.py`.

`DemucsRunner` now:

1. Reads `runtime.device` (default `cuda`).
2. Checks the active PyTorch installation with `torch.cuda.is_available()`.
3. Runs:
   `python -m demucs.separate -n htdemucs --float32 --device cuda ...`
   when CUDA is available.
4. Uses the configured CPU fallback only when CUDA is unavailable or a CUDA execution fails.
5. Retries CUDA with the configured smaller segment after a CUDA out-of-memory failure.
6. Records requested device, actual device, CUDA availability, model, segment size, attempts and stem hashes in `demucs_manifest.json`.

Demucs itself officially exposes `--device`, defaulting to CUDA when PyTorch CUDA is available. The project therefore uses the supported Demucs device mechanism rather than attempting to manipulate internal model state. 

### 2. Demucs cache correctness

This was the most important hidden GPU issue found.

The previous runner could reuse four existing WAV stems without checking whether they had been produced on CPU. That meant a machine could first process a song without CUDA, later gain a working NVIDIA/CUDA installation, and still reuse CPU stems.

The patched runner validates:

- source SHA-256
- model name
- actual device
- segment size
- every stem's SHA-256

If the stored device is CPU and the preferred runtime is now CUDA, the cache is rejected and Demucs runs again on CUDA.

### 3. Telugu MMS/CTC alignment

`src/phase2core/mms_model.py` already had the correct core behavior: after model loading it moves the MMS model to the requested device and moves inference tensors to that same device.

The unified alignment adapter passes the main runtime's CUDA-first policy into Phase 2. The final Phase-2 JSON now records:

- requested device
- actual device

This makes CPU fallback observable instead of silently ambiguous.

### 4. Silero VAD

The original active VAD path loaded Silero and kept both model and input tensor on CPU.

It is now CUDA-first:

- model is moved to `runtime.device`
- input tensor is moved to the same device
- CUDA inference failure can fall back to CPU
- actual VAD device is persisted in `activity.json`

Activity cache reuse also checks the requested device, so a prior CPU result does not permanently block later CUDA execution.

Silero VAD's official implementation accepts a Torch tensor and Torch model, so moving both to CUDA is compatible with its Torch execution path.

### 5. NVIDIA NVENC video encoding

The Reel/render subsystem already preferred `h264_nvenc`. The audit confirmed that:

- `src/rendercore/video_renderer.py` prefers NVENC.
- `src/rendercore/video_grabber.py` prefers NVENC.
- `src/rendercore/assembler.py` prefers NVENC.
- `ffmpeg_has_encoder()` checks the actual installed FFmpeg binary before selecting NVENC.
- If NVENC is unavailable or fails at runtime, the code falls back to CPU `libx264`.

This is the correct behavior because an NVIDIA GPU alone does not guarantee that the installed FFmpeg binary has a usable NVENC encoder.

## Stages intentionally left on CPU

Not every operation should be forced onto the GPU.

These remain CPU-oriented by design:

- Spotify/YT Music/LRCLIB HTTP requests
- SQLite
- JSON and filesystem operations
- SHA-256 hashing
- Mutagen/ID3 metadata
- FFmpeg audio decode/PCM file output
- MP3 encoding through `libmp3lame`
- NumPy/SciPy-oriented 8D audio transforms
- miscellaneous validation and bookkeeping

Forcing these onto CUDA would either be unsupported, require unnecessary device transfers, or provide no meaningful acceleration.

## Configuration

`config.json` now explicitly declares a CUDA-first runtime:

- `runtime.device = "cuda"`
- `runtime.fallback_device = "cpu"`
- `runtime.require_cuda = false`
- `runtime.allow_cpu_fallback = true`
- `runtime.gpu_policy = cuda_first_for_torch_models_and_nvenc`
- `render.video_codec = "h264_nvenc"`
- `render.video_codec_fallback = "libx264"`

`require_cuda=false` is intentional: the project prefers NVIDIA whenever it is genuinely available but remains runnable on machines where CUDA is unavailable. The actual device used is persisted by the compute stages.

## Windows verification

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_windows_cuda.ps1
powershell -ExecutionPolicy Bypass -File scripts/verify_windows_gpu.ps1
python main.py --doctor
```

The setup script installs the pinned CUDA 12.8 PyTorch wheels and performs a CUDA tensor smoke test. The verification script also checks `nvidia-smi`.

## Validation

The final source tree was compiled with Python `compileall` successfully.

The complete active test suite passes:

**79 passed**

A simulated CUDA-routing test also confirms that the Demucs resolver selects `cuda` when CUDA availability is reported as true.

No live Spotify/YT Music/LRCLIB acquisition was performed during packaging, and no physical NVIDIA GPU execution could be performed in this container.

## Final conclusion

The project is now CUDA-first at the compute-heavy stages where NVIDIA acceleration is actually supported:

`HTDemucs -> CUDA`
`MMS/CTC -> CUDA`
`Silero VAD -> CUDA`
`Video encoding -> NVENC`

with explicit CPU fallback and, critically, cache invalidation so CPU-generated artifacts do not silently defeat later GPU execution.

## 2026-10-07 Telugu LRC gate follow-up

The unified pipeline now rejects a selected synchronized LRC when the complete lyric text contains no Telugu-script Unicode code point in U+0C00–U+0C7F. This check runs after lyric selection, including cached LRC reuse, and before visual-source resolution or alignment.

Reason: Romanized Telugu lyrics can pass a generic synchronized-LRC check but are unsuitable for the Telugu MMS/CTC alignment path and can produce downstream timing failures such as non-monotonic word timestamps.
