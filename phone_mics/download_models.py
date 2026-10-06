"""Download only the official ONNX models needed for Hey Jarvis; no training."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import urllib.request

NAMES = ("hey_jarvis_v0.1.onnx", "melspectrogram.onnx", "embedding_model.onnx")
BASE = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"


def main():
    directory = Path(os.environ.get("JARVIS_MODEL_DIR", ".tmp_phone_models"))
    directory.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name in NAMES:
        target = directory / name
        if not target.exists():
            print("Downloading", name, flush=True)
            partial = target.with_suffix(".partial")
            with urllib.request.urlopen(BASE + name, timeout=60) as response, partial.open("wb") as out:
                shutil.copyfileobj(response, out)
            if partial.stat().st_size < 1024:
                raise RuntimeError(f"Unexpected download: {name}")
            partial.replace(target)
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest.append({"name": name, "url": BASE + name, "sha256": digest})
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Ready. Models: CC BY-NC-SA 4.0 (non-commercial); code: Apache-2.0.")


if __name__ == "__main__":
    main()
