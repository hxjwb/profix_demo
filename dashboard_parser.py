import re


FRAME_RE = re.compile(
    r'^(\d+)\s+captured,\s+RTP TS:\s*(\d+),\s+Frame Size:\s*(\d+),'
    r'\s+PREV_GAP\s+(-?\d+),\s+Encode\s+(-?\d+),\s+Asemble2Decode\s+(-?\d+),'
    r'\s+Decode\s+(-?\d+),\s+DEC_GAP\s+(-?\d+)',
    re.MULTILINE,
)
BITRATE_RE = re.compile(r'^(\d+)\s+encoder_target_bps=(\d+)$', re.MULTILINE)
STALL_RTP_RE = re.compile(r'stall_rtp_ts=(\d+)')
PACKET_RE = re.compile(
    r'^\s*(?:-\s*)?([PR]):\s+seq\s+(\d+),\s+size\s+(\d+),\s+send_delta\s+(-?\d+),\s+recv_delta\s+(\d+|lost)',
)


def parse_dashboard_data(profile_text):
    frames = []
    packets = []
    current_frame = None

    for line in profile_text.splitlines():
        frame_match = FRAME_RE.match(line)
        if frame_match:
            current_frame = {
                'frame_index': len(frames),
                'ts_ms': int(frame_match.group(1)),
                'rtp_ts': int(frame_match.group(2)),
                'frame_size': int(frame_match.group(3)),
                'prev_gap': int(frame_match.group(4)),
                'encode_ms': int(frame_match.group(5)),
                'assemble_to_decode_ms': int(frame_match.group(6)),
                'decode_ms': int(frame_match.group(7)),
                'decode_gap_ms': int(frame_match.group(8)),
                'primary_packets': 0,
                'primary_lost_packets': 0,
            }
            frames.append(current_frame)
            continue

        if not current_frame:
            continue

        packet_match = PACKET_RE.match(line)
        if not packet_match:
            continue

        packet_type = packet_match.group(1)
        seq = int(packet_match.group(2))
        size = int(packet_match.group(3))
        send_delta = int(packet_match.group(4))
        recv_delta_raw = packet_match.group(5)
        if packet_type == 'P':
            current_frame['primary_packets'] += 1
            if recv_delta_raw == 'lost':
                current_frame['primary_lost_packets'] += 1

        send_time_ms = current_frame['ts_ms'] + send_delta
        recv_delta = None if recv_delta_raw == 'lost' else int(recv_delta_raw)
        packet_delay_ms = None if recv_delta is None else recv_delta - send_delta
        packets.append({
            'frame_index': current_frame['frame_index'],
            'frame_ts_ms': current_frame['ts_ms'],
            'rtp_ts': current_frame['rtp_ts'],
            'packet_type': packet_type,
            'seq': seq,
            'size': size,
            'send_delta': send_delta,
            'recv_delta': recv_delta,
            'send_time_ms': send_time_ms,
            'packet_delay_ms': packet_delay_ms,
        })

    bitrates = []
    for match in BITRATE_RE.finditer(profile_text):
        bitrates.append({
            'ts_ms': int(match.group(1)),
            'target_bps': int(match.group(2)),
        })

    stall_rtp_ts = None
    stall_match = STALL_RTP_RE.search(profile_text)
    if stall_match:
        stall_rtp_ts = int(stall_match.group(1))

    all_ts = [f['ts_ms'] for f in frames] + [b['ts_ms'] for b in bitrates] + [p['send_time_ms'] for p in packets]
    base_ts = min(all_ts) if all_ts else 0
    stall_frame_ts = None

    for frame in frames:
        frame['offset_ms'] = frame['ts_ms'] - base_ts
        if frame['primary_packets'] > 0:
            frame['loss_rate'] = round(frame['primary_lost_packets'] / frame['primary_packets'], 4)
        else:
            frame['loss_rate'] = None
        if stall_rtp_ts is not None and frame['rtp_ts'] == stall_rtp_ts:
            stall_frame_ts = frame['offset_ms']

    for bitrate in bitrates:
        bitrate['offset_ms'] = bitrate['ts_ms'] - base_ts
    for packet in packets:
        packet['send_time_offset_ms'] = packet['send_time_ms'] - base_ts

    return {
        'frames': frames,
        'bitrates': bitrates,
        'packets': packets,
        'stall_frame_offset_ms': stall_frame_ts,
        'stall_frame_index': next((f['frame_index'] for f in frames if stall_rtp_ts is not None and f['rtp_ts'] == stall_rtp_ts), None),
        'stall_rtp_ts': stall_rtp_ts,
    }
