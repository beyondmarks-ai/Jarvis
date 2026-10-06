"""One independent openWakeWord audio history per connected microphone."""
from pathlib import Path
import numpy as np


class JarvisDetector:
    def __init__(self, directory: str):
        from openwakeword.model import Model
        root = Path(directory)
        required = [root / name for name in (
            "hey_jarvis_v0.1.onnx", "melspectrogram.onnx", "embedding_model.onnx")]
        if not all(path.is_file() for path in required):
            raise RuntimeError("Wake models missing. Run python -m phone_mics.download_models")
        self.model = Model(wakeword_models=[str(required[0])], inference_framework="onnx",
                           melspec_model_path=str(required[1]),
                           embedding_model_path=str(required[2]))
        self.epoch = -1

    def score(self, pcm: bytes, epoch: int) -> float:
        if epoch != self.epoch:
            self.model.reset()
            self.epoch = epoch
        result = self.model.predict(np.frombuffer(pcm, dtype="<i2"))
        return float(max(result.values(), default=0))
