"""Generate a PRIVATE local QR setup page; requires the optional qrcode package."""
import argparse
import html
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit


def pairing_url(base_url, token):
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Use the trusted HTTPS hub URL")
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("Use the hub root URL without a query or fragment")
    if not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", token):
        raise ValueError("Invalid microphone access key")
    return base_url.rstrip("/") + "/#" + urlencode({"key": token})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--access-file", default=".tmp_phone_access.json")
    parser.add_argument("--output", default=".tmp_phone_setup.html")
    args = parser.parse_args()
    import qrcode
    from qrcode.image.svg import SvgPathImage
    token = json.loads(Path(args.access_file).read_text())["JARVIS_MIC_TOKEN"]
    link = pairing_url(args.url, token)
    qr = qrcode.make(link, image_factory=SvgPathImage, box_size=8).to_string(encoding="unicode")
    document = """<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Private Jarvis phone setup</title>
<style>body{font:18px system-ui;max-width:680px;margin:40px auto;padding:20px;background:#101915;color:#edf8ed}
a{color:#c6f76f}svg{background:white;padding:16px;max-width:85%;height:auto}li{margin:12px 0}</style>
<h1>Connect your Android microphone</h1><p>Scan this QR code with each phone's camera, then open in Chrome.</p>
""" + qr + """<ol><li>Enter a different name for each phone (for example Living room).</li>
<li>The access key is filled in for you. Tap Connect and allow the microphone.</li>
<li>Keep the app visible and the phone unlocked. You can use Add to home screen.</li>
<li>Once the IoT shows connected, say Hey Jarvis, wait for its beep, then ask a question.</li></ol>
<p><a href=""" + '"' + html.escape(link, quote=True) + '"' + """>Open the paired app on this device</a></p>
<p><strong>Private:</strong> this QR/link grants microphone access to your Jarvis hub. Share only with
your own trusted devices. Audio streams to the Azure hub while listening; questions go to the speech provider.
All sound plays on the IoT.</p>"""
    target = Path(args.output)
    target.write_text(document, encoding="utf-8")
    print(f"Private setup page created: {target.resolve()} (do not publish)")


if __name__ == "__main__":
    main()
