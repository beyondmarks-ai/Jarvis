"""Inspect Git's staged snapshot without printing possible credential values.

This guard catches common mistakes; it is not a comprehensive secret scanner.
It deliberately reads the index, not just working-tree files.
"""
import hashlib
import re
import subprocess
import sys
from pathlib import PurePosixPath

FORBIDDEN_PARTS = {".venv", "venv", "__pycache__", ".esphome", "build", "dist", "backups", "esp32_backups", ".claude", ".codex"}
FORBIDDEN_SUFFIXES = {".bin", ".elf", ".map", ".pcm", ".wav", ".ulaw", ".mp3", ".pyc", ".pem", ".key", ".pfx", ".p12", ".zip"}
MODEL_HASH = "21a7976add39ee24ec96c63d96b7aaa18e24d1d9824b963e451da8feb4b78b77"
TOKEN_PATTERNS = [
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
    rb"\bgithub_pat_[A-Za-z0-9_]{40,}\b",
    rb"\bAKIA[A-Z0-9]{16}\b",
    rb"AccountKey=[A-Za-z0-9+/]{30,}={0,2}",
]
ASSIGNMENT = re.compile(
    r'''(?im)^\s*["']?(?:wifi_password|jarvis_backend_key|sarvam_api_key|jarvis_device_key|api_key|access_token)["']?\s*[:=]\s*["']?([^\r\n"',}]+)'''
)


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def check_file(name, data):
    path = PurePosixPath(name)
    lower = path.name.lower()
    example = lower in {"secrets.example.yaml", ".env.example"}
    if (set(part.lower() for part in path.parts) & FORBIDDEN_PARTS
            or path.suffix.lower() in FORBIDDEN_SUFFIXES
            or lower.startswith(".tmp")
            or lower == "local.settings.json"
            or (not example and (lower.startswith("secrets.") or lower == ".env" or lower.startswith(".env.")))):
        return "private/generated file must not be tracked"
    if len(data) > 2 * 1024 * 1024:
        return "unexpectedly large backup file"
    if name == "hey_jarvis.tflite":
        return None if hashlib.sha256(data).hexdigest() == MODEL_HASH else "wake model differs from the baseline; review before updating its expected hash"
    if any(re.search(pattern, data) for pattern in TOKEN_PATTERNS):
        return "possible embedded credential/private key"
    text = data.decode("utf-8", errors="replace")
    for match in ASSIGNMENT.finditer(text):
        value = match.group(1).strip()
        if not value.startswith(("YOUR_", "UNUSED_", "!secret", "os.environ", "require_env", "$env:", "${")):
            return "possible literal secret assignment; use a placeholder or runtime lookup"
    return None


def main():
    names = [name.decode("utf-8") for name in git("ls-files", "-z").split(b"\0") if name]
    if not names:
        raise SystemExit("No staged/tracked files to inspect. Stage the intended source files first.")
    errors = []
    for name in names:
        error = check_file(name, git("show", ":" + name))
        if error:
            errors.append(f"{name}: {error}")
    if errors:
        print("Backup safety check failed (values hidden):", file=sys.stderr)
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"PASS: inspected {len(names)} staged/tracked files; no blocked artifacts or recognized credential patterns.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
