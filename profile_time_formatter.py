import re


WINDOW_RE = re.compile(r'window_time_ms=\[(\d+),(\d+)\]')
LEADING_TS_RE = re.compile(r'^(\d+)(\s+.*)$')


def _base_ts(profile_text):
    match = WINDOW_RE.search(profile_text)
    if match:
        return int(match.group(1))

    candidates = []
    for line in profile_text.splitlines():
        line = line.strip()
        match = LEADING_TS_RE.match(line)
        if match:
            candidates.append(int(match.group(1)))
    return min(candidates) if candidates else None


def _format_offset(offset_ms):
    sign = '+' if offset_ms >= 0 else '-'
    return f'{sign}{abs(offset_ms)}ms'


def format_profile_text(profile_text):
    base_ts = _base_ts(profile_text)
    if base_ts is None:
        return profile_text

    formatted_lines = []
    for raw_line in profile_text.splitlines():
        line = raw_line.rstrip('\n')

        window_match = WINDOW_RE.search(line)
        if window_match:
            start_ts = int(window_match.group(1))
            end_ts = int(window_match.group(2))
            line = WINDOW_RE.sub(
                f'window_time_offset=[{_format_offset(start_ts - base_ts)},{_format_offset(end_ts - base_ts)}]',
                line,
            )
            formatted_lines.append(line)
            continue

        leading_match = LEADING_TS_RE.match(line)
        if leading_match:
            ts = int(leading_match.group(1))
            line = f'{_format_offset(ts - base_ts)}{leading_match.group(2)}'

        formatted_lines.append(line)

    return '\n'.join(formatted_lines)
