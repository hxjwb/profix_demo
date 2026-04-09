#!/usr/bin/env python3
import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Dict, Optional

from PIL import Image

BASE_DIR = Path(__file__).resolve().parent

API_BASE = "https://api.replicate.com/v1"
MODEL_VERSION = "56f2be05920413e1189c32a5fb2f767b357187c887d67114cace11c18d86ab49"
TARGET_WIDTH = 3543
TARGET_HEIGHT = 5315
TARGET_DPI = (300, 300)


def api_headers(token: str, content_type: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "codex-replicate-upscale/1.0",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def http_json(url: str, token: str, payload: Optional[dict] = None, method: str = "GET") -> dict:
    data = None
    headers = api_headers(token)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def upload_file(path: Path, token: str) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    boundary = f"----CodexBoundary{uuid.uuid4().hex}"
    content = path.read_bytes()
    metadata = json.dumps({"source": "codex-upscale"})

    body = []
    fields = [
        (
            "content",
            path.name,
            mime,
            content,
        ),
        (
            "metadata",
            None,
            "application/json",
            metadata.encode("utf-8"),
        ),
    ]
    for name, filename, field_type, value in fields:
        body.append(f"--{boundary}\r\n".encode("utf-8"))
        if filename is None:
            body.append(
                f'Content-Disposition: form-data; name="{name}"\r\n'.encode("utf-8")
            )
        else:
            body.append(
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8")
            )
        body.append(f"Content-Type: {field_type}\r\n\r\n".encode("utf-8"))
        body.append(value)
        body.append(b"\r\n")
    body.append(f"--{boundary}--\r\n".encode("utf-8"))
    data = b"".join(body)

    req = urllib.request.Request(
        f"{API_BASE}/files",
        data=data,
        headers=api_headers(token, f"multipart/form-data; boundary={boundary}"),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["urls"]["get"]


def create_prediction(file_url: str, token: str, scale: int) -> str:
    payload = {
        "version": MODEL_VERSION,
        "input": {
            "img": file_url,
            "scale": scale,
            "version": "General - v3",
            "face_enhance": False,
            "tile": 0,
        },
    }
    result = http_json(f"{API_BASE}/predictions", token, payload, method="POST")
    return result["urls"]["get"]


def wait_for_prediction(prediction_url: str, token: str) -> str:
    while True:
        result = http_json(prediction_url, token)
        status = result["status"]
        if status == "succeeded":
            output = result.get("output")
            if isinstance(output, list):
                output = output[0]
            if not output:
                raise RuntimeError("Prediction succeeded but no output URL was returned.")
            return output
        if status in {"failed", "canceled"}:
            raise RuntimeError(result.get("error") or f"Prediction {status}.")
        time.sleep(2)


def download_file(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "codex-replicate-upscale/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        dest.write_bytes(resp.read())


def finalize_image(src: Path, dest: Path) -> None:
    with Image.open(src) as img:
        img = img.convert("RGB")
        width, height = img.size
        target_ratio = TARGET_WIDTH / TARGET_HEIGHT
        current_ratio = width / height

        if current_ratio > target_ratio:
            new_width = int(round(height * target_ratio))
            left = max((width - new_width) // 2, 0)
            img = img.crop((left, 0, left + new_width, height))
        else:
            new_height = int(round(width / target_ratio))
            top = max((height - new_height) // 2, 0)
            img = img.crop((0, top, width, top + new_height))

        img = img.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS)
        img.save(dest, format="PNG", dpi=TARGET_DPI, optimize=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use Replicate Real-ESRGAN to upscale an image, then export a 3543x5315 300dpi PNG."
    )
    parser.add_argument("input", type=Path, help="Source image path")
    parser.add_argument(
        "--output",
        type=Path,
        default=BASE_DIR / "haibao_replicate_3543x5315_300dpi.png",
        help="Final output path",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=BASE_DIR,
        help="Directory for temporary files",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=4,
        choices=[2, 3, 4],
        help="Real-ESRGAN scale factor",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("REPLICATE_API_TOKEN"),
        help="Replicate API token. Falls back to REPLICATE_API_TOKEN.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.token:
        print("Missing Replicate token. Set REPLICATE_API_TOKEN or pass --token.", file=sys.stderr)
        return 2

    src = args.input.expanduser().resolve()
    out = args.output.expanduser().resolve()
    tmp_dir = args.workdir.expanduser().resolve()
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_upscaled = tmp_dir / f"{src.stem}_replicate_raw.png"

    try:
        print(f"Uploading {src} ...")
        file_url = upload_file(src, args.token)
        print("Creating prediction ...")
        prediction_url = create_prediction(file_url, args.token, args.scale)
        print("Waiting for Replicate result ...")
        result_url = wait_for_prediction(prediction_url, args.token)
        print("Downloading upscaled image ...")
        download_file(result_url, tmp_upscaled)
        print("Finalizing size and DPI ...")
        finalize_image(tmp_upscaled, out)
        print(f"Saved final image to {out}")
        return 0
    except Exception as exc:
        print(f"Upscale failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
