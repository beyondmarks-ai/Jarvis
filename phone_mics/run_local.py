"""Start a local hub. No cloud provisioning and no firmware upload."""
import argparse
import json
import os
from pathlib import Path
import secrets
import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ssl-certfile")
    parser.add_argument("--ssl-keyfile")
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1") and not (args.ssl_certfile and args.ssl_keyfile):
        parser.error("Remote phones require trusted HTTPS; supply --ssl-certfile and --ssl-keyfile")
    access_file = Path(".tmp_phone_access.json")
    if access_file.exists():
        access = json.loads(access_file.read_text(encoding="utf-8"))
    else:
        access = {"JARVIS_MIC_TOKEN": secrets.token_urlsafe(32),
                  "JARVIS_SPEAKER_TOKEN": secrets.token_urlsafe(32)}
        with access_file.open("x", encoding="utf-8") as stream:
            json.dump(access, stream, indent=2)
    for name in ("JARVIS_MIC_TOKEN", "JARVIS_SPEAKER_TOKEN"):
        os.environ.setdefault(name, access[name])
    print(f"Local access keys: {access_file.resolve()} (private; do not commit)", flush=True)
    if not os.environ.get("SARVAM_API_KEY"):
        print("Speech-provider key is not set: UI/detection work; live answers are unavailable.", flush=True)
    uvicorn.run("phone_mics.app:app", host=args.host, port=args.port, workers=1,
                ssl_certfile=args.ssl_certfile, ssl_keyfile=args.ssl_keyfile,
                ws_max_size=4096, ws_max_queue=4, access_log=False)


if __name__ == "__main__":
    main()
