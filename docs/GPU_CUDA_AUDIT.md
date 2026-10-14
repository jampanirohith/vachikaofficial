# GPU and CPU-fallback policy

The packaged baseline prefers CUDA for supported PyTorch inference/separation stages, but permits CPU fallback. A CUDA installation is not required to run source parsing, SQLite, network calls, hashing, metadata writing or most FFmpeg I/O tasks. The default configuration sets:

```json
{
  "runtime": {
    "device": "cuda",
    "fallback_device": "cpu",
    "require_cuda": false,
    "allow_cpu_fallback": true
  }
}
```

HTDemucs records requested/actual device, input hash, model, segmentation policy, attempt directories and stem hashes in its manifest. The phase-2 MMS/VAD stages have their own device selection and fallback handling. The Phase 2 adapter now receives the configured `require_cuda` and `allow_cpu_fallback` settings instead of silently forcing CPU fallback. FFmpeg prefers NVENC when configured and available, then falls back to software `libx264` if encoder discovery or execution fails.

Run `python main.py --doctor` on the target machine to inspect available dependencies and `python main.py --prepare-models` to populate model caches. The project ZIP validation environment used for the current build did not run an actual GPU, NVENC or full end-to-end song job; hardware-specific performance and output quality must be checked on the target machine.
