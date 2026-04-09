#!/usr/bin/env python3
"""
Standalone session-level stall review.

Usage:
    python3 session_review.py <log_file> --model glm
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime

from profile_time_formatter import format_profile_text


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CASE_PROMPT_FILE = os.path.join(BASE_DIR, "session_review_prompt_case.txt")
SUMMARY_PROMPT_FILE = os.path.join(BASE_DIR, "session_review_prompt_summary.txt")
REPORTS_DIR = os.path.join(BASE_DIR, "session_reports")
CACHE_VERSION = 1

MODEL_PRESETS = {
    "gpt-5.2": {
        "provider": "poe",
        "base_url": os.environ.get("POE_BASE_URL", "https://api.poe.com/v1"),
        "api_key": os.environ.get("POE_API_KEY"),
        "model": "gpt-5.2",
    },
    "kimi": {
        "provider": "moonshot",
        "base_url": os.environ.get("KIMI_BASE_URL", "https://api.moonshot.cn/v1"),
        "api_key": os.environ.get("KIMI_API_KEY"),
        "model": "kimi-k2.5",
    },
    "kimi-k2.5": {
        "provider": "moonshot",
        "base_url": os.environ.get("KIMI_BASE_URL", "https://api.moonshot.cn/v1"),
        "api_key": os.environ.get("KIMI_API_KEY"),
        "model": "kimi-k2.5",
    },
    "glm": {
        "provider": "zhipu",
        "base_url": os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
        "api_key": os.environ.get("GLM_API_KEY"),
        "model": "glm-5.1",
    },
    "glm-5.1": {
        "provider": "zhipu",
        "base_url": os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
        "api_key": os.environ.get("GLM_API_KEY"),
        "model": "glm-5.1",
    },
}

TIME_RE = re.compile(r"stall_time_utc=(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)")
GAP_RE = re.compile(r"stall_gap_ms=(\d+)")
STALL_RTP_RE = re.compile(r"stall_rtp_ts=(\d+)")
FRAME_COUNT_RE = re.compile(r"frame_count_in_window=(\d+)")
FRAME_LINE_RE = re.compile(
    r"^([+\-]?\d+)ms captured, RTP TS: (\d+), Frame Size: (\d+), PREV_GAP (-?\d+), Encode (\d+), Asemble2Decode (\d+), Decode (\d+), DEC_GAP (-?\d+)$"
)
PACKET_LINE_RE = re.compile(
    r"^(?P<prefix>\s*-\s*R:|P:) seq (?P<seq>\d+), size (?P<size>\d+), send_delta (?P<send>\d+), recv_delta (?P<recv>lost|\d+)$"
)
BITRATE_LINE_RE = re.compile(r"^(\d+)\s+encoder_target_bps=(\d+)$")
TWCC_LINE_RE = re.compile(r"^(\d+)\s+rtcp_feedback_kind=(\w+)$")
FEC_LINE_RE = re.compile(
    r"^(\d+)\s+FECbatch, protected_packets=\[(.*?)\], send_time=\[(.*?)\], recv_time=\[(.*?)\]$"
)


def strip_prefix(line):
    match = re.search(r"\(frame_time_window\.cc:\d+\): (.*)", line)
    return match.group(1) if match else line.rstrip("\n")


def log_info(message):
    print(f"[INFO] {message}", flush=True)


def log_step(message):
    print(f"\n== {message} ==", flush=True)


def quantile(values, q):
    if not values:
        return None
    values = sorted(values)
    idx = (len(values) - 1) * q
    low = math.floor(idx)
    high = math.ceil(idx)
    if low == high:
        return values[low]
    weight = idx - low
    return values[low] * (1 - weight) + values[high] * weight


def safe_confidence(value):
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"(\d+(?:\.\d+)?)", str(value))
    return float(match.group(1)) if match else 0.0


def parse_args():
    parser = argparse.ArgumentParser(description="Standalone session stall review")
    parser.add_argument(
        "log_file",
        nargs="?",
        default=os.path.join(BASE_DIR, "20260325_164012_0001.log"),
        help="Path to the session log file",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.2",
        help="Model preset to use, e.g. gpt-5.2, kimi, glm",
    )
    parser.add_argument(
        "--cluster-gap-s",
        type=float,
        default=1.5,
        help="Max gap between consecutive stalls to stay in the same cluster",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output markdown path",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore cache and regenerate AI outputs",
    )
    args = parser.parse_args()

    preset = MODEL_PRESETS.get(args.model)
    if not preset:
        parser.error(f"unknown --model {args.model!r}; valid options: {', '.join(sorted(MODEL_PRESETS))}")
    args.model_preset = preset
    return args


def parse_log(path):
    stalls = []
    buf = []
    in_profile = False

    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if "===PROFILE===" in line:
                in_profile = True
                buf = [line]
            elif "===PROFILE_END===" in line and in_profile:
                buf.append(line)
                content = "".join(buf)
                tm = TIME_RE.search(content)
                gm = GAP_RE.search(content)
                rtp = STALL_RTP_RE.search(content)
                frame_count = FRAME_COUNT_RE.search(content)
                clean = "\n".join(strip_prefix(raw) for raw in buf)
                stalls.append(
                    {
                        "stall_time": tm.group(1) if tm else "unknown",
                        "gap_ms": int(gm.group(1)) if gm else 0,
                        "stall_rtp_ts": int(rtp.group(1)) if rtp else None,
                        "frame_count_in_window": int(frame_count.group(1)) if frame_count else None,
                        "text_raw": clean,
                        "text": format_profile_text(clean),
                    }
                )
                in_profile = False
                buf = []
            elif in_profile:
                buf.append(line)

    if not stalls:
        raise ValueError(f"no stall profiles found in {path}")

    stalls.sort(key=lambda item: item["stall_time"])
    base_dt = datetime.strptime(stalls[0]["stall_time"], "%Y-%m-%d %H:%M:%S.%f")
    for idx, stall in enumerate(stalls):
        dt = datetime.strptime(stall["stall_time"], "%Y-%m-%d %H:%M:%S.%f")
        stall["idx"] = idx
        stall["dt"] = dt
        stall["elapsed_s"] = round((dt - base_dt).total_seconds(), 3)
        stall["features"] = extract_stall_features(stall)
    return stalls


def extract_stall_features(stall):
    frames = []
    media_recv = []
    rtx_recv = []
    media_loss = 0
    media_packet_count = 0
    rtx_packet_count = 0
    bitrate_samples = []
    twcc_count = 0
    fec_total = 0
    fec_with_recv = 0
    fec_all_missing = 0
    burst_lengths = []
    current_burst = 0

    for line in stall["text"].splitlines():
        frame_match = FRAME_LINE_RE.match(line)
        if frame_match:
            frames.append(
                {
                    "offset_ms": int(frame_match.group(1)),
                    "rtp_ts": int(frame_match.group(2)),
                    "frame_size": int(frame_match.group(3)),
                    "prev_gap": int(frame_match.group(4)),
                    "encode_ms": int(frame_match.group(5)),
                    "assemble_ms": int(frame_match.group(6)),
                    "decode_ms": int(frame_match.group(7)),
                    "dec_gap_ms": int(frame_match.group(8)),
                    "line": line,
                }
            )
            continue

        packet_match = PACKET_LINE_RE.match(line)
        if packet_match:
            recv_value = packet_match.group("recv")
            is_rtx = packet_match.group("prefix").strip().startswith("-")
            if is_rtx:
                rtx_packet_count += 1
            else:
                media_packet_count += 1

            if recv_value == "lost":
                if not is_rtx:
                    media_loss += 1
                    current_burst += 1
            else:
                recv_delta = int(recv_value)
                if is_rtx:
                    rtx_recv.append(recv_delta)
                else:
                    media_recv.append(recv_delta)
                    if current_burst:
                        burst_lengths.append(current_burst)
                        current_burst = 0
            continue

        bitrate_match = BITRATE_LINE_RE.match(line)
        if bitrate_match:
            bitrate_samples.append(int(bitrate_match.group(2)))
            continue

        twcc_match = TWCC_LINE_RE.match(line)
        if twcc_match and twcc_match.group(2) == "TWCC":
            twcc_count += 1
            continue

        fec_match = FEC_LINE_RE.match(line)
        if fec_match:
            fec_total += 1
            recv_times = [token.strip() for token in fec_match.group(4).split(",") if token.strip()]
            valid_recv = [token for token in recv_times if token != "-1"]
            if valid_recv:
                fec_with_recv += 1
            else:
                fec_all_missing += 1

    if current_burst:
        burst_lengths.append(current_burst)

    stall_gap = stall["gap_ms"]
    matching_frame = None
    if frames:
        for frame in frames:
            if frame["rtp_ts"] == stall["stall_rtp_ts"]:
                matching_frame = frame
                break
        if matching_frame is None:
            matching_frame = max(frames, key=lambda item: item["dec_gap_ms"])

    loss_rate = (media_loss / media_packet_count) if media_packet_count else 0.0
    bitrate_drop_ratio = 0.0
    if bitrate_samples and max(bitrate_samples) > 0:
        bitrate_drop_ratio = (max(bitrate_samples) - min(bitrate_samples)) / max(bitrate_samples)

    dominant_label = "mixed"
    if matching_frame:
        if matching_frame["assemble_ms"] >= max(matching_frame["encode_ms"], matching_frame["decode_ms"], 40):
            dominant_label = "assemble_wait"
        elif matching_frame["encode_ms"] >= max(matching_frame["assemble_ms"], matching_frame["decode_ms"], 20):
            dominant_label = "encode_bound"
        elif matching_frame["decode_ms"] >= max(matching_frame["assemble_ms"], matching_frame["encode_ms"], 20):
            dominant_label = "decode_bound"
        elif loss_rate >= 0.2:
            dominant_label = "network_loss"
        elif quantile(media_recv, 0.95) and quantile(media_recv, 0.95) >= 200:
            dominant_label = "network_delay"

    return {
        "stall_gap_ms": stall_gap,
        "frame_count": len(frames),
        "media_packet_count": media_packet_count,
        "media_loss_count": media_loss,
        "media_loss_rate": round(loss_rate, 4),
        "max_loss_burst": max(burst_lengths) if burst_lengths else 0,
        "media_recv_p50_ms": quantile(media_recv, 0.50),
        "media_recv_p95_ms": quantile(media_recv, 0.95),
        "media_recv_max_ms": max(media_recv) if media_recv else None,
        "rtx_packet_count": rtx_packet_count,
        "rtx_recv_p95_ms": quantile(rtx_recv, 0.95),
        "twcc_count": twcc_count,
        "bitrate_min_bps": min(bitrate_samples) if bitrate_samples else None,
        "bitrate_max_bps": max(bitrate_samples) if bitrate_samples else None,
        "bitrate_last_bps": bitrate_samples[-1] if bitrate_samples else None,
        "bitrate_drop_ratio": round(bitrate_drop_ratio, 4),
        "fec_batch_count": fec_total,
        "fec_batch_recv_ratio": round((fec_with_recv / fec_total), 4) if fec_total else None,
        "fec_batch_all_missing_ratio": round((fec_all_missing / fec_total), 4) if fec_total else None,
        "dominant_label": dominant_label,
        "matching_frame": matching_frame,
    }


def cluster_stalls(stalls, cluster_gap_s):
    clusters = []
    current = [stalls[0]]

    for prev, stall in zip(stalls, stalls[1:]):
        delta_s = (stall["dt"] - prev["dt"]).total_seconds()
        if delta_s <= cluster_gap_s:
            current.append(stall)
        else:
            clusters.append(current)
            current = [stall]
    clusters.append(current)

    cluster_infos = []
    for cluster_idx, cluster_stalls_list in enumerate(clusters):
        representative = max(cluster_stalls_list, key=lambda item: (item["gap_ms"], -item["idx"]))
        start_dt = cluster_stalls_list[0]["dt"]
        end_dt = cluster_stalls_list[-1]["dt"]
        cluster_infos.append(
            {
                "cluster_id": cluster_idx,
                "stalls": cluster_stalls_list,
                "representative": representative,
                "start_time": cluster_stalls_list[0]["stall_time"],
                "end_time": cluster_stalls_list[-1]["stall_time"],
                "elapsed_start_s": cluster_stalls_list[0]["elapsed_s"],
                "elapsed_end_s": cluster_stalls_list[-1]["elapsed_s"],
                "stall_count": len(cluster_stalls_list),
                "max_gap_ms": max(item["gap_ms"] for item in cluster_stalls_list),
                "mean_gap_ms": round(sum(item["gap_ms"] for item in cluster_stalls_list) / len(cluster_stalls_list), 1),
                "span_ms": int((end_dt - start_dt).total_seconds() * 1000),
            }
        )
    return cluster_infos


def build_session_stats(stalls, clusters):
    gaps = [stall["gap_ms"] for stall in stalls]
    cluster_sizes = [cluster["stall_count"] for cluster in clusters]
    return {
        "stall_count": len(stalls),
        "cluster_count": len(clusters),
        "session_start_time": stalls[0]["stall_time"],
        "session_end_time": stalls[-1]["stall_time"],
        "session_span_s": round(stalls[-1]["elapsed_s"] - stalls[0]["elapsed_s"], 3),
        "mean_gap_ms": round(sum(gaps) / len(gaps), 1),
        "p95_gap_ms": round(quantile(gaps, 0.95), 1),
        "max_gap_ms": max(gaps),
        "mean_cluster_size": round(sum(cluster_sizes) / len(cluster_sizes), 2),
        "largest_cluster_size": max(cluster_sizes),
    }


def read_prompt(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def build_case_prompt(cluster, stall):
    prompt = read_prompt(CASE_PROMPT_FILE)
    features = stall["features"]
    frame = features["matching_frame"]
    cluster_summary = {
        "cluster_id": cluster["cluster_id"],
        "stall_count": cluster["stall_count"],
        "start_time": cluster["start_time"],
        "end_time": cluster["end_time"],
        "span_ms": cluster["span_ms"],
        "mean_gap_ms": cluster["mean_gap_ms"],
        "max_gap_ms": cluster["max_gap_ms"],
        "representative_idx": stall["idx"],
    }
    feature_summary = {
        "dominant_label": features["dominant_label"],
        "media_loss_rate": features["media_loss_rate"],
        "max_loss_burst": features["max_loss_burst"],
        "media_recv_p95_ms": features["media_recv_p95_ms"],
        "rtx_packet_count": features["rtx_packet_count"],
        "rtx_recv_p95_ms": features["rtx_recv_p95_ms"],
        "twcc_count": features["twcc_count"],
        "bitrate_min_bps": features["bitrate_min_bps"],
        "bitrate_max_bps": features["bitrate_max_bps"],
        "bitrate_last_bps": features["bitrate_last_bps"],
        "bitrate_drop_ratio": features["bitrate_drop_ratio"],
        "fec_batch_count": features["fec_batch_count"],
        "fec_batch_recv_ratio": features["fec_batch_recv_ratio"],
        "fec_batch_all_missing_ratio": features["fec_batch_all_missing_ratio"],
        "matching_frame": {
            "offset_ms": frame["offset_ms"],
            "encode_ms": frame["encode_ms"],
            "assemble_ms": frame["assemble_ms"],
            "decode_ms": frame["decode_ms"],
            "dec_gap_ms": frame["dec_gap_ms"],
        } if frame else None,
    }
    return f"""{prompt}

---

## Cluster 摘要

{json.dumps(cluster_summary, ensure_ascii=False, indent=2)}

## 当前代表 stall 摘要

{json.dumps({
    "stall_idx": stall["idx"],
    "stall_time": stall["stall_time"],
    "stall_gap_ms": stall["gap_ms"],
    "elapsed_s": stall["elapsed_s"],
    "frame_count_in_window": stall["frame_count_in_window"],
}, ensure_ascii=False, indent=2)}

## 当前代表 stall 计算特征

{json.dumps(feature_summary, ensure_ascii=False, indent=2)}

## 当前代表 stall profile

```text
{stall["text"]}
```
"""


def build_summary_prompt(session_stats, clusters, case_reports):
    prompt = read_prompt(SUMMARY_PROMPT_FILE)
    cluster_digest = []
    for cluster, case_report in zip(clusters, case_reports):
        rep = cluster["representative"]
        cluster_digest.append(
            {
                "cluster_id": cluster["cluster_id"],
                "stall_count": cluster["stall_count"],
                "start_time": cluster["start_time"],
                "end_time": cluster["end_time"],
                "span_ms": cluster["span_ms"],
                "mean_gap_ms": cluster["mean_gap_ms"],
                "max_gap_ms": cluster["max_gap_ms"],
                "representative_stall": {
                    "stall_idx": rep["idx"],
                    "stall_time": rep["stall_time"],
                    "gap_ms": rep["gap_ms"],
                    "dominant_label": rep["features"]["dominant_label"],
                    "media_loss_rate": rep["features"]["media_loss_rate"],
                    "media_recv_p95_ms": rep["features"]["media_recv_p95_ms"],
                    "bitrate_drop_ratio": rep["features"]["bitrate_drop_ratio"],
                    "fec_batch_all_missing_ratio": rep["features"]["fec_batch_all_missing_ratio"],
                },
                "case_report": case_report,
            }
        )

    return f"""{prompt}

---

## Session 统计

{json.dumps(session_stats, ensure_ascii=False, indent=2)}

## Cluster 列表与代表 stall 分析

{json.dumps(cluster_digest, ensure_ascii=False, indent=2)}
"""


def ensure_reports_dir():
    os.makedirs(REPORTS_DIR, exist_ok=True)


def file_tag_from_log(log_path):
    return os.path.splitext(os.path.basename(log_path))[0]


def cache_key(kind, model_name, payload_text):
    digest = hashlib.sha1(payload_text.encode("utf-8")).hexdigest()[:16]
    return os.path.join(REPORTS_DIR, f"v{CACHE_VERSION}_{kind}_{model_name}_{digest}.json")


def load_json_cache(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json_cache(path, payload):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def call_model_json(prompt_text, model_preset):
    api_key = model_preset["api_key"]
    if not api_key:
        raise RuntimeError("API key not set for the selected model preset")

    url = model_preset["base_url"].rstrip("/") + "/chat/completions"
    payload_obj = {
        "model": model_preset["model"],
        "stream": False,
        "messages": [{"role": "user", "content": prompt_text}],
    }
    if model_preset.get("provider") == "zhipu":
        payload_obj["thinking"] = {"type": "disabled"}

    req = urllib.request.Request(
        url,
        data=json.dumps(payload_obj).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(req, timeout=300) as response:
        raw = response.read().decode("utf-8", errors="replace")
        payload = json.loads(raw)
    text = payload["choices"][0]["message"]["content"]
    return parse_json_object(text), text


def parse_json_object(text):
    raw = text.strip()
    candidates = [raw]
    block_match = re.search(r"```json\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if block_match:
        candidates.append(block_match.group(1).strip())

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(raw[start:end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError("model did not return valid JSON")


def run_case_analysis(cluster, args):
    stall = cluster["representative"]
    prompt_text = build_case_prompt(cluster, stall)
    cache_path = cache_key(
        f"case_cluster{cluster['cluster_id']:02d}",
        args.model,
        prompt_text,
    )

    log_step(f"Cluster {cluster['cluster_id']} representative analysis")
    log_info(
        f"Representative stall #{stall['idx']} at {stall['stall_time']} gap={stall['gap_ms']}ms "
        f"cluster_size={cluster['stall_count']} span={cluster['span_ms']}ms"
    )
    log_info(
        f"Feature summary: dominant={stall['features']['dominant_label']} "
        f"loss_rate={stall['features']['media_loss_rate']:.2%} "
        f"recv_p95={stall['features']['media_recv_p95_ms']}ms "
        f"bitrate_drop={stall['features']['bitrate_drop_ratio']:.2%}"
    )

    if not args.force:
        cached = load_json_cache(cache_path)
        if cached:
            log_info(f"Loaded cached case report from {cache_path}")
            return cached["report"]

    log_info(f"Calling model {args.model_preset['model']} for cluster {cluster['cluster_id']}")
    report, raw_text = call_model_json(prompt_text, args.model_preset)
    save_json_cache(
        cache_path,
        {
            "cache_version": CACHE_VERSION,
            "kind": "case",
            "cluster_id": cluster["cluster_id"],
            "stall_idx": stall["idx"],
            "prompt": prompt_text,
            "raw_response": raw_text,
            "report": report,
        },
    )
    log_info(f"Saved case report cache to {cache_path}")
    log_info(
        "Case verdicts: "
        f"timeline={report.get('timeline', {}).get('headline', '')} | "
        f"network={report.get('network_judgment', {}).get('headline', '')} | "
        f"rate={report.get('rate_control', {}).get('headline', '')} | "
        f"recovery={report.get('loss_recovery', {}).get('headline', '')}"
    )
    return report


def run_summary_analysis(session_stats, clusters, case_reports, args):
    prompt_text = build_summary_prompt(session_stats, clusters, case_reports)
    cache_path = cache_key("summary", args.model, prompt_text)

    log_step("Session summary analysis")
    log_info(
        f"Summarizing {session_stats['stall_count']} stalls across {session_stats['cluster_count']} clusters "
        f"with model {args.model_preset['model']}"
    )

    if not args.force:
        cached = load_json_cache(cache_path)
        if cached:
            log_info(f"Loaded cached session summary from {cache_path}")
            return cached["report"]

    report, raw_text = call_model_json(prompt_text, args.model_preset)
    save_json_cache(
        cache_path,
        {
            "cache_version": CACHE_VERSION,
            "kind": "summary",
            "prompt": prompt_text,
            "raw_response": raw_text,
            "report": report,
        },
    )
    log_info(f"Saved session summary cache to {cache_path}")
    log_info(
        "Session verdicts: "
        f"overview={report.get('session_overview', {}).get('headline', '')} | "
        f"rate={report.get('rate_control_assessment', {}).get('headline', '')} | "
        f"recovery={report.get('loss_recovery_assessment', {}).get('headline', '')}"
    )
    return report


def format_section(title, data):
    headline = data.get("headline", "")
    confidence = data.get("confidence", "")
    details = data.get("details", "")
    lines = [f"## {title}", "", f"**{headline}**"]
    if confidence:
        lines.append("")
        lines.append(f"- confidence: {confidence}")
    if details:
        lines.append("")
        lines.append(details)
    lines.append("")
    return "\n".join(lines)


def md_escape(value):
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def fmt_num(value, digits=1):
    if value is None:
        return "-"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.{digits}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text
    return str(value)


def confidence_sort_key(report_section):
    return safe_confidence(report_section.get("confidence", "0"))


def build_cluster_summary_table(clusters, case_reports):
    lines = [
        "| 簇ID | 时间范围 | 卡顿数 | 持续时长(ms) | 平均Gap(ms) | 最大Gap(ms) | 代表样本 | 时间线判断 | 网络判断 |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for cluster, case_report in zip(clusters, case_reports):
        rep = cluster["representative"]
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(cluster["cluster_id"]),
                    md_escape(f"{cluster['start_time']} -> {cluster['end_time']}"),
                    md_escape(cluster["stall_count"]),
                    md_escape(cluster["span_ms"]),
                    md_escape(cluster["mean_gap_ms"]),
                    md_escape(cluster["max_gap_ms"]),
                    md_escape(f"#{rep['idx']} / {rep['gap_ms']}ms"),
                    md_escape(case_report.get("timeline", {}).get("headline", "")),
                    md_escape(case_report.get("network_judgment", {}).get("headline", "")),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def build_cluster_feature_table(clusters):
    lines = [
        "| 簇ID | 代表样本 | 主标签 | 媒体丢包率 | recv_p95(ms) | RTX数 | 码率跌幅 | FEC整批丢失比例 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for cluster in clusters:
        rep = cluster["representative"]
        features = rep["features"]
        loss_rate = f"{features['media_loss_rate'] * 100:.1f}%"
        bitrate_drop = f"{features['bitrate_drop_ratio'] * 100:.1f}%"
        fec_missing = (
            f"{features['fec_batch_all_missing_ratio'] * 100:.1f}%"
            if features["fec_batch_all_missing_ratio"] is not None
            else "-"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(cluster["cluster_id"]),
                    md_escape(f"#{rep['idx']} / {rep['stall_time']}"),
                    md_escape(features["dominant_label"]),
                    md_escape(loss_rate),
                    md_escape(fmt_num(features["media_recv_p95_ms"])),
                    md_escape(features["rtx_packet_count"]),
                    md_escape(bitrate_drop),
                    md_escape(fec_missing),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def build_key_findings(summary_report, case_reports):
    candidate_sections = [
        ("会话总览", summary_report.get("session_overview", {})),
        ("簇模式", summary_report.get("cluster_patterns", {})),
        ("速率控制", summary_report.get("rate_control_assessment", {})),
        ("丢包恢复", summary_report.get("loss_recovery_assessment", {})),
        ("跨簇发现", summary_report.get("cross_cluster_findings", {})),
        ("最终结论", summary_report.get("summary", {})),
    ]
    candidate_sections.extend(
        [
            (f"Cluster {idx} 结论", report.get("summary", {}))
            for idx, report in enumerate(case_reports)
        ]
    )
    candidate_sections.sort(key=lambda item: confidence_sort_key(item[1]), reverse=True)

    lines = []
    for label, section in candidate_sections[:6]:
        headline = section.get("headline", "").strip()
        if not headline:
            continue
        confidence = section.get("confidence", "")
        suffix = f"（置信度 {confidence}）" if confidence else ""
        lines.append(f"- {label}：{headline}{suffix}")
    return "\n".join(lines)


def build_markdown_report(log_path, session_stats, clusters, case_reports, summary_report):
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 会话级卡顿分析报告",
        "",
        "",
        "## 一、报告信息",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        f"| 日志文件 | `{os.path.abspath(log_path)}` |",
        f"| 生成时间 | `{generated_at}` |",
        f"| 会话起始 | `{session_stats['session_start_time']}` |",
        f"| 会话结束 | `{session_stats['session_end_time']}` |",
        f"| 会话跨度 | `{session_stats['session_span_s']}s` |",
        f"| 卡顿总数 | `{session_stats['stall_count']}` |",
        f"| 簇数量 | `{session_stats['cluster_count']}` |",
        f"| 平均 Gap | `{session_stats['mean_gap_ms']}ms` |",
        f"| P95 Gap | `{session_stats['p95_gap_ms']}ms` |",
        f"| 最大 Gap | `{session_stats['max_gap_ms']}ms` |",
        "",
        "## 二、关键结论摘要",
        "",
        build_key_findings(summary_report, case_reports),
        "",
    ]

    lines.extend(
        [
            "## 三、会话总体判断",
            "",
            format_section("3.1 会话总览", summary_report.get("session_overview", {})),
            format_section("3.2 簇模式分析", summary_report.get("cluster_patterns", {})),
            format_section("3.3 速率控制评估", summary_report.get("rate_control_assessment", {})),
            format_section("3.4 丢包恢复评估", summary_report.get("loss_recovery_assessment", {})),
            format_section("3.5 跨簇关联发现", summary_report.get("cross_cluster_findings", {})),
            "",
            "## 四、Cluster 总表",
            "",
            build_cluster_summary_table(clusters, case_reports),
            "",
            "## 五、代表样本特征表",
            "",
            build_cluster_feature_table(clusters),
            "",
            format_section("六、优化建议", summary_report.get("recommendations", {})),
            format_section("七、最终结论", summary_report.get("summary", {})),
            "## 八、各 Cluster 代表样本详析",
            "",
        ]
    )

    for cluster, case_report in zip(clusters, case_reports):
        stall = cluster["representative"]
        lines.extend(
            [
                f"### Cluster {cluster['cluster_id']}",
                "",
                "| 项目 | 内容 |",
                "| --- | --- |",
                f"| 时间范围 | `{cluster['start_time']}` -> `{cluster['end_time']}` |",
                f"| 卡顿数 | `{cluster['stall_count']}` |",
                f"| 持续时长 | `{cluster['span_ms']}ms` |",
                f"| 平均 Gap | `{cluster['mean_gap_ms']}ms` |",
                f"| 最大 Gap | `{cluster['max_gap_ms']}ms` |",
                f"| 代表样本 | `#{stall['idx']}` / `{stall['stall_time']}` / `{stall['gap_ms']}ms` |",
                f"| 主标签 | `{stall['features']['dominant_label']}` |",
                "",
            ]
        )
        lines.append(format_section("时间线判断", case_report.get("timeline", {})))
        lines.append(format_section("网络判断", case_report.get("network_judgment", {})))
        lines.append(format_section("速率控制", case_report.get("rate_control", {})))
        lines.append(format_section("丢包恢复", case_report.get("loss_recovery", {})))
        lines.append(format_section("代表性评估", case_report.get("cluster_representativeness", {})))
        lines.append(format_section("样本总结", case_report.get("summary", {})))

    return "\n".join(lines).strip() + "\n"


def default_output_path(log_path):
    ensure_reports_dir()
    tag = file_tag_from_log(log_path)
    return os.path.join(REPORTS_DIR, f"{tag}_session_review.md")


def print_cluster_overview(clusters):
    log_step("Cluster overview")
    for cluster in clusters:
        stall = cluster["representative"]
        log_info(
            f"Cluster {cluster['cluster_id']}: stalls={cluster['stall_count']} "
            f"range={cluster['start_time']} -> {cluster['end_time']} "
            f"span={cluster['span_ms']}ms mean_gap={cluster['mean_gap_ms']}ms max_gap={cluster['max_gap_ms']}ms "
            f"rep=stall#{stall['idx']} gap={stall['gap_ms']}ms dominant={stall['features']['dominant_label']}"
        )


def print_session_stats(session_stats):
    log_step("Session statistics")
    for key in [
        "session_start_time",
        "session_end_time",
        "session_span_s",
        "stall_count",
        "cluster_count",
        "mean_gap_ms",
        "p95_gap_ms",
        "max_gap_ms",
        "mean_cluster_size",
        "largest_cluster_size",
    ]:
        log_info(f"{key} = {session_stats[key]}")


def main():
    args = parse_args()
    ensure_reports_dir()

    log_step("Startup")
    log_info(f"log_file = {os.path.abspath(args.log_file)}")
    log_info(f"model preset = {args.model} -> {args.model_preset['model']} ({args.model_preset['provider']})")
    log_info(f"cluster_gap_s = {args.cluster_gap_s}")
    log_info(f"force = {args.force}")

    log_step("Parsing session log")
    stalls = parse_log(args.log_file)
    log_info(f"Parsed {len(stalls)} stalls")
    log_info(
        f"Stall time range: {stalls[0]['stall_time']} -> {stalls[-1]['stall_time']} "
        f"span={round(stalls[-1]['elapsed_s'] - stalls[0]['elapsed_s'], 3)}s"
    )
    log_info(
        f"Gap summary: mean={round(sum(s['gap_ms'] for s in stalls) / len(stalls), 1)}ms "
        f"max={max(s['gap_ms'] for s in stalls)}ms"
    )

    clusters = cluster_stalls(stalls, args.cluster_gap_s)
    session_stats = build_session_stats(stalls, clusters)
    print_session_stats(session_stats)
    print_cluster_overview(clusters)

    case_reports = []
    for cluster in clusters:
        case_reports.append(run_case_analysis(cluster, args))

    summary_report = run_summary_analysis(session_stats, clusters, case_reports, args)

    report_path = args.output or default_output_path(args.log_file)
    report_md = build_markdown_report(args.log_file, session_stats, clusters, case_reports, summary_report)
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(report_md)

    log_step("Done")
    log_info(f"Report written to {report_path}")
    log_info(f"Session final summary: {summary_report.get('summary', {}).get('headline', '')}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[ERROR] Interrupted by user", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
