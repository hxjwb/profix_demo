"""
Merge stall profiles into clusters.
Cluster boundary: gap between consecutive stall_time_utc > GAP_THRESHOLD_S seconds.
Within each cluster:
  - [event_context]: codec line deduped, each stall's metadata kept separately
  - [timeline_digest]: frame entries sorted by captured-ts, deduped; associated
    packet (P:/R:) lines follow their frame, deduped per frame
  - [control_digest]: entries sorted by leading timestamp, deduped by content
"""

import re
import os
from datetime import datetime
from collections import OrderedDict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(BASE_DIR, "profiles")
OUTPUT_DIR  = os.path.join(BASE_DIR, "clusters")
GAP_THRESHOLD_S = 3.0

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

LOG_RE = re.compile(r'\(frame_time_window\.cc:(\d+)\): (.*)')

def src_and_content(line):
    """Return (source_line_number, content_str) or (None, None)."""
    m = LOG_RE.search(line)
    if m:
        return int(m.group(1)), m.group(2).rstrip()
    return None, None

def leading_ts(content):
    """Return leading integer timestamp from a content string, or None."""
    m = re.match(r'^(\d+)\s', content)
    return int(m.group(1)) if m else None

# ---------------------------------------------------------------------------
# load profiles
# ---------------------------------------------------------------------------

def load_profiles(profile_dir):
    profiles = []
    for fname in sorted(os.listdir(profile_dir)):
        if not fname.endswith('.txt'):
            continue
        # filename: 2026-02-24_17-53-53.659.txt
        stem = fname[:-4]               # 2026-02-24_17-53-53.659
        date_str, time_str = stem.split('_', 1)
        time_str = time_str.replace('-', ':', 2)   # 17:53:53.659
        stall_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S.%f")

        with open(os.path.join(profile_dir, fname), 'r', encoding='utf-8') as f:
            lines = [l.rstrip('\n') for l in f]

        profiles.append({'fname': fname, 'stall_dt': stall_dt, 'lines': lines})

    profiles.sort(key=lambda p: p['stall_dt'])
    return profiles

# ---------------------------------------------------------------------------
# cluster
# ---------------------------------------------------------------------------

def cluster_profiles(profiles, gap_s):
    clusters = []
    cur = [profiles[0]]
    for p in profiles[1:]:
        if (p['stall_dt'] - cur[-1]['stall_dt']).total_seconds() > gap_s:
            clusters.append(cur)
            cur = []
        cur.append(p)
    clusters.append(cur)
    return clusters

# ---------------------------------------------------------------------------
# parse one profile into sections
# ---------------------------------------------------------------------------

def parse_profile(lines):
    """
    Returns:
      codec_line     : str  (line 853, common)
      stall_blocks   : list of dict {rtp_ts_line, time_line, window_line}
      digest_frames  : OrderedDict { captured_ts(int) -> [content_str, ...] }
                       first element is the frame line, rest are P:/R: lines
      control_events : OrderedDict { ts(int) -> content_str }
    """
    codec_line   = None
    stall_blocks = []       # list of dicts
    cur_stall    = {}
    digest_frames  = OrderedDict()
    control_events = OrderedDict()
    cur_frame_ts   = None
    section        = None

    for raw in lines:
        src, content = src_and_content(raw)
        if src is None or not content:
            continue

        if src == 850:    # ===PROFILE===
            section = 'header'
        elif src == 1021: # ===PROFILE_END===
            section = None
        elif src == 852:  # [event_context]
            section = 'event_context'
        elif src == 876:  # [timeline_digest]
            section = 'timeline_digest'
            cur_frame_ts = None
        elif src == 953:  # [control_digest]
            section = 'control_digest'

        elif src == 853:  # codec=... line
            codec_line = content

        elif src == 859:  # stall_rtp_ts=... stall_gap_ms=... (per-stall)
            cur_stall = {'rtp_ts_line': content}

        elif src == 868:  # stall_time_utc=... (per-stall, follows 859)
            cur_stall['time_line'] = content

        elif src == 870:  # window_rtp_ts=... (per-stall, follows 868)
            cur_stall['window_line'] = content
            stall_blocks.append(dict(cur_stall))
            cur_stall = {}

        elif src == 921 and section == 'timeline_digest':
            ts = leading_ts(content)
            if ts is not None:
                cur_frame_ts = ts
                if ts not in digest_frames:
                    digest_frames[ts] = [content]   # first element = frame line

        elif src == 699 and section == 'timeline_digest':
            if cur_frame_ts is not None and cur_frame_ts in digest_frames:
                if content not in digest_frames[cur_frame_ts]:
                    digest_frames[cur_frame_ts].append(content)

        elif src == 1019 and section == 'control_digest':
            ts = leading_ts(content)
            if ts is not None and ts not in control_events:
                control_events[ts] = content

    return codec_line, stall_blocks, digest_frames, control_events

# ---------------------------------------------------------------------------
# merge a cluster
# ---------------------------------------------------------------------------

def merge_cluster(cluster_profiles):
    codec_line      = None
    all_stall_blocks = []
    seen_stall_keys  = set()
    merged_digest    = OrderedDict()   # ts -> [frame_line, pkt1, pkt2, ...]
    merged_control   = OrderedDict()   # ts -> content

    for p in cluster_profiles:
        cl, stall_blocks, digest_frames, control_events = parse_profile(p['lines'])

        if codec_line is None and cl:
            codec_line = cl

        # stall blocks: dedup by stall_rtp_ts line
        for sb in stall_blocks:
            key = sb.get('rtp_ts_line', '')
            if key not in seen_stall_keys:
                seen_stall_keys.add(key)
                all_stall_blocks.append(sb)

        # timeline_digest: merge frames and their packet lines
        for ts, entries in digest_frames.items():
            if ts not in merged_digest:
                merged_digest[ts] = list(entries)
            else:
                # add any new packet lines not already present
                existing = merged_digest[ts]
                for e in entries[1:]:   # skip frame line (entries[0])
                    if e not in existing:
                        existing.append(e)

        # control_digest: dedup by ts
        for ts, content in control_events.items():
            if ts not in merged_control:
                merged_control[ts] = content

    # sort by timestamp
    merged_digest  = OrderedDict(sorted(merged_digest.items()))
    merged_control = OrderedDict(sorted(merged_control.items()))

    return codec_line, all_stall_blocks, merged_digest, merged_control

# ---------------------------------------------------------------------------
# write merged cluster file
# ---------------------------------------------------------------------------

def write_cluster(idx, cluster_profiles, codec_line, stall_blocks,
                  merged_digest, merged_control, output_dir):
    first_dt = cluster_profiles[0]['stall_dt']
    last_dt  = cluster_profiles[-1]['stall_dt']
    fname = (f"cluster_{idx+1:02d}"
             f"_{first_dt.strftime('%H-%M-%S.%f')[:-3]}"
             f"_{last_dt.strftime('%H-%M-%S.%f')[:-3]}.txt")
    fpath = os.path.join(output_dir, fname)

    out = []
    W = out.append

    W("===PROFILE===\n")
    W("\n")

    W("[cluster_info]\n")
    W(f"cluster_index={idx+1}  stall_count={len(cluster_profiles)}"
      f"  first_stall={first_dt}  last_stall={last_dt}\n")
    W(f"source_files=" + ",".join(p['fname'] for p in cluster_profiles) + "\n")
    W("\n")

    W("[event_context]\n")
    if codec_line:
        W(codec_line + "\n")
    W("\n")

    W("[stall_events]\n")
    for i, sb in enumerate(stall_blocks):
        W(f"--- stall {i+1} ---\n")
        if 'rtp_ts_line' in sb:  W(sb['rtp_ts_line'] + "\n")
        if 'time_line'   in sb:  W(sb['time_line']   + "\n")
        if 'window_line' in sb:  W(sb['window_line'] + "\n")
    W("\n")

    W("[timeline_digest]\n")
    for ts, entries in merged_digest.items():
        W(entries[0] + "\n")          # frame line
        for pkt in entries[1:]:
            W(pkt + "\n")             # P:/R: lines
    W("\n")

    W("[control_digest]\n")
    for ts, content in merged_control.items():
        W(content + "\n")
    W("\n")

    W("===PROFILE_END===\n")

    with open(fpath, 'w', encoding='utf-8') as f:
        f.writelines(out)

    return fname

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

os.makedirs(OUTPUT_DIR, exist_ok=True)

profiles = load_profiles(PROFILE_DIR)
clusters = cluster_profiles(profiles, GAP_THRESHOLD_S)

print(f"Total profiles : {len(profiles)}")
print(f"Clusters found : {len(clusters)}  (gap threshold={GAP_THRESHOLD_S}s)\n")

for idx, cluster in enumerate(clusters):
    codec_line, stall_blocks, merged_digest, merged_control = merge_cluster(cluster)
    fname = write_cluster(idx, cluster, codec_line, stall_blocks,
                          merged_digest, merged_control, OUTPUT_DIR)

    dur = (cluster[-1]['stall_dt'] - cluster[0]['stall_dt']).total_seconds()
    print(f"Cluster {idx+1:2d}: {len(cluster):2d} stalls  "
          f"span={dur:.3f}s  "
          f"frames={len(merged_digest)}  ctrl_events={len(merged_control)}"
          f"  → {fname}")

print(f"\nSaved to: {OUTPUT_DIR}")
