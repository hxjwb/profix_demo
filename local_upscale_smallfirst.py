#!/usr/bin/env python3
import argparse
from pathlib import Path

import cv2
from PIL import Image

BASE_DIR = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Downscale first, run local EDSR x4, then export 3543x5315 at 300dpi."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--model",
        type=Path,
        default=BASE_DIR / "models" / "EDSR_x4.pb",
    )
    parser.add_argument("--prep-height", type=int, default=1800)
    args = parser.parse_args()

    img = cv2.imread(str(args.input), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"Failed to read source image: {args.input}")

    h, w = img.shape[:2]
    prep_h = args.prep_height
    prep_w = int(round(prep_h * w / h))
    prep = cv2.resize(img, (prep_w, prep_h), interpolation=cv2.INTER_AREA)

    sr = cv2.dnn_superres.DnnSuperResImpl_create()
    sr.readModel(str(args.model))
    sr.setModel("edsr", 4)
    up = sr.upsample(prep)

    rgb = cv2.cvtColor(up, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)

    target_w, target_h = 3543, 5315
    target_ratio = target_w / target_h
    cur_w, cur_h = pil.size
    cur_ratio = cur_w / cur_h

    if cur_ratio > target_ratio:
        new_w = int(round(cur_h * target_ratio))
        left = max((cur_w - new_w) // 2, 0)
        pil = pil.crop((left, 0, left + new_w, cur_h))
    else:
        new_h = int(round(cur_w / target_ratio))
        top = max((cur_h - new_h) // 2, 0)
        pil = pil.crop((0, top, cur_w, top + new_h))

    pil = pil.resize((target_w, target_h), Image.Resampling.LANCZOS)
    pil.save(str(args.output), format="PNG", dpi=(300, 300), optimize=True)

    print(f"saved {args.output}")
    print(f"prep_size {prep_w}x{prep_h}")
    print(f"up_size {cur_w}x{cur_h}")
    print(f"final_size {target_w}x{target_h}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
