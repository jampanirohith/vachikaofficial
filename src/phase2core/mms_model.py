from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from .types import EmissionResult
from ..model_cache import configure_model_cache


class MMSModel:
    def __init__(self, model_name: str, language: str, device: str, fallback_device: str,
                 cache_dir: Path, allow_download: bool = True, revision: str | None = None,
                 require_cuda: bool = False, allow_cpu_fallback: bool = True):
        self.model_name = model_name
        self.language = language
        self.requested_device = device
        self.fallback_device = fallback_device
        self.cache_dir = cache_dir
        self.allow_download = allow_download
        self.revision = revision
        self.require_cuda = bool(require_cuda)
        self.allow_cpu_fallback = bool(allow_cpu_fallback)
        self.processor: Any = None
        self.model: Any = None
        self.device = "cpu"

    @staticmethod
    def _cuda_available(torch: Any) -> bool:
        try:
            return bool(torch.cuda.is_available())
        except Exception:
            return False

    def load(self) -> None:
        try:
            import torch
            from transformers import AutoModelForCTC, AutoProcessor
        except ImportError as exc:
            raise RuntimeError("MMS_MODEL_LOAD_FAILED: install torch and transformers") from exc

        configure_model_cache(self.cache_dir)
        # Keep all Hugging Face Hub artifacts in the project-owned persistent
        # cache. This directory is shared by every song in the playlist.
        kwargs = {"cache_dir": str((self.cache_dir / 'huggingface' / 'hub').resolve())}
        if self.revision:
            kwargs["revision"] = self.revision
        if not self.allow_download:
            kwargs["local_files_only"] = True

        requested = self.requested_device
        cuda_available = self._cuda_available(torch)
        if requested == "cuda" and not cuda_available:
            message = (
                "CUDA_REQUIRED: MMS requested CUDA, but the installed PyTorch build has no CUDA support. "
                "Install the pinned CUDA build with scripts/install_windows_cuda.ps1."
            )
            if self.require_cuda and not self.allow_cpu_fallback:
                raise RuntimeError(message)
            requested = self.fallback_device

        # Load the base model/processor once; the official MMS workflow then activates the language adapter.
        self.processor = AutoProcessor.from_pretrained(self.model_name, **kwargs)
        self.model = AutoModelForCTC.from_pretrained(self.model_name, **kwargs)

        try:
            self.processor.tokenizer.set_target_lang(self.language)
            adapter_kwargs = dict(kwargs)
            self.model.load_adapter(self.language, **adapter_kwargs)
        except TypeError:
            # Compatibility with versions whose load_adapter forwards only a subset of Hub kwargs.
            self.processor.tokenizer.set_target_lang(self.language)
            try:
                self.model.load_adapter(self.language)
            except Exception as exc:
                raise RuntimeError(
                    f"MMS_MODEL_LOAD_FAILED: unable to activate MMS language adapter '{self.language}': {exc}"
                ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"MMS_MODEL_LOAD_FAILED: unable to activate MMS language adapter '{self.language}': {exc}"
            ) from exc

        try:
            self.model.to(requested)
            self.device = requested
        except Exception as exc:
            if requested != self.fallback_device and self.allow_cpu_fallback:
                self.model.to(self.fallback_device)
                self.device = self.fallback_device
            else:
                raise RuntimeError(f"MMS_MODEL_LOAD_FAILED: unable to move model to {requested}: {exc}") from exc
        self.model.eval()

    def ensure_loaded(self) -> None:
        if self.processor is None or self.model is None:
            self.load()

    @property
    def tokenizer(self) -> Any:
        self.ensure_loaded()
        return self.processor.tokenizer

    @property
    def blank_token_id(self) -> int:
        self.ensure_loaded()
        token_id = getattr(self.processor.tokenizer, "pad_token_id", None)
        if token_id is None:
            token_id = getattr(self.model.config, "pad_token_id", None)
        if token_id is None:
            raise RuntimeError("MMS_MODEL_LOAD_FAILED: unable to determine CTC blank/pad token id")
        return int(token_id)

    def emissions(self, wav_path: Path) -> EmissionResult:
        self.ensure_loaded()
        import torch

        audio, sr = sf.read(wav_path, dtype="float32", always_2d=False)
        if audio.ndim != 1:
            audio = np.mean(audio, axis=1, dtype=np.float32)
        if sr != 16000:
            raise ValueError(f"MMS_INFERENCE_FAILED: expected 16000 Hz chunk, got {sr}")
        if len(audio) == 0 or not np.isfinite(audio).all():
            raise ValueError("MMS_INFERENCE_FAILED: invalid chunk audio")

        inputs = self.processor(audio, sampling_rate=sr, return_tensors="pt", padding=True)
        inputs = {k: v.to(self.device) for k, v in inputs.items() if hasattr(v, "to")}
        try:
            with torch.inference_mode():
                logits = self.model(**inputs).logits
        except RuntimeError as exc:
            if self.device == "cuda" and self.allow_cpu_fallback and self.fallback_device != "cuda":
                try:
                    self.model.to(self.fallback_device)
                    self.device = self.fallback_device
                    inputs = {k: v.to(self.device) for k, v in inputs.items() if hasattr(v, "to")}
                    with torch.inference_mode():
                        logits = self.model(**inputs).logits
                except Exception as second_exc:
                    raise RuntimeError(f"MMS_INFERENCE_FAILED: CUDA and fallback failed: {second_exc}") from second_exc
            else:
                raise RuntimeError(f"MMS_INFERENCE_FAILED: {exc}") from exc

        log_probs = torch.log_softmax(logits[0], dim=-1).detach().cpu().numpy().astype(np.float32)
        ratio = getattr(self.model.config, "inputs_to_logits_ratio", None)
        if ratio is None:
            ratio = 320
        stride_ms = float(ratio) * 1000.0 / sr
        return EmissionResult(
            log_probs=log_probs,
            frame_count=int(log_probs.shape[0]),
            class_count=int(log_probs.shape[1]),
            stride_ms=stride_ms,
            device=self.device,
        )
