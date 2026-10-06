"""Bounded, audio-free diagnostics. Model scores are not accuracy percentages."""
from dataclasses import dataclass, field
import math
import time
import numpy as np


@dataclass
class AudioHealth:
    frames: int = 0
    gaps: int = 0
    last_at: float = 0.

    level_dbfs: float = -96.
    clipped_percent: float = 0.
    score: float = 0.
    peak_score: float = 0.
    inference_ms: float = 0.

    def audio(self, pcm, now):
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        rms = float(np.sqrt(np.mean(samples*samples))) / 32768
        self.level_dbfs = round(max(-96., 20*math.log10(max(rms, 1e-8))), 1)
        self.clipped_percent = round(float(np.mean(np.abs(samples) >= 32000))*100, 1)
        if self.last_at and now-self.last_at > .4:
            self.gaps += 1
        self.last_at = now
        self.frames += 1

    def prediction(self, score, elapsed):
        self.score = min(1., max(0., score)) if math.isfinite(score) else 0.
        self.peak_score = max(self.peak_score, self.score)
        self.inference_ms = round(elapsed*1000, 1)

    def snapshot(self, threshold, now=None):
        now = time.monotonic() if now is None else now
        return {"frames": self.frames, "gaps": self.gaps,
                "audio_age_ms": round((now-self.last_at)*1000) if self.last_at else None,
                "level_dbfs": self.level_dbfs, "clipped_percent": self.clipped_percent,
                "score": round(self.score, 3), "peak_score": round(self.peak_score, 3),
                "threshold": threshold, "inference_ms": self.inference_ms}


@dataclass
class DistanceTest:
    distance: int
    threshold: float
    started: float = field(default_factory=time.monotonic)
    # First five seconds = room sound; then three five-second wake attempts.
    peaks: list = field(default_factory=lambda: [0., 0., 0., 0.])
    frames: list = field(default_factory=lambda: [0, 0, 0, 0])
    clipped_frames: int = 0
    gaps: int = 0
    last_at: float = 0.
    detections: list = field(default_factory=lambda: [False, False, False, False])
    armed: bool = True
    quiet_since: float | None = None

    def observe(self, health, now):
        slot = int((now-self.started)//5)
        if not 0 <= slot < 4:
            return
        self.peaks[slot] = max(self.peaks[slot], health.score)
        # One utterance lingering across a prompt boundary is not two detections.
        # Require 600 ms below half the cutoff before accepting another attempt.
        if health.score < self.threshold*.5:
            if self.quiet_since is None:
                self.quiet_since = now
            if now-self.quiet_since >= .6:
                self.armed = True
        else:
            self.quiet_since = None
        if self.armed and health.score >= self.threshold:
            self.detections[slot] = True
            self.armed = False
        self.frames[slot] += 1
        self.clipped_frames += int(health.clipped_percent > 1)
        if self.last_at and now-self.last_at > .4:
            self.gaps += 1
        self.last_at = now

    def report(self):
        sufficient = all(frames >= 40 for frames in self.frames)
        noise_trigger = self.peaks[0] >= self.threshold
        hits = self.detections[1:].copy()
        return {"type":"test_result", "cancelled":False, "distance_m":self.distance,
                "threshold":self.threshold, "background_peak":round(self.peaks[0],3),
                "attempt_peaks":[round(p,3) for p in self.peaks[1:]], "hits":hits,
                "frames":self.frames.copy(), "sufficient_audio":sufficient,
                "background_trigger":noise_trigger, "gaps":self.gaps,
                "clipped_frames":self.clipped_frames,
                "passed":sufficient and all(hits) and not noise_trigger and not self.gaps
                         and not self.clipped_frames}
