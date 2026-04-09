import re
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
log_path = os.path.join(BASE_DIR, "20260325_164012_0001.log")
output_dir = os.path.join(BASE_DIR, "profiles")
os.makedirs(output_dir, exist_ok=True)

with open(log_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

profile_start = None
profiles = []

for i, line in enumerate(lines):
    if "===PROFILE===" in line:
        profile_start = i
    elif "===PROFILE_END===" in line and profile_start is not None:
        profiles.append(lines[profile_start:i + 1])
        profile_start = None

print(f"Found {len(profiles)} profiles")

counters = {}
for profile_lines in profiles:
    # Extract stall_time_utc from profile content
    stall_time = None
    for line in profile_lines:
        m = re.search(r"stall_time_utc=(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)", line)
        if m:
            stall_time = m.group(1)
            break

    if stall_time:
        # Convert to filename-safe format: 2026-02-24_17-53-53.659
        filename_base = stall_time.replace(" ", "_").replace(":", "-")
    else:
        filename_base = "unknown"

    # Handle duplicates
    counters[filename_base] = counters.get(filename_base, 0) + 1
    count = counters[filename_base]
    filename = f"{filename_base}.txt" if count == 1 else f"{filename_base}_{count}.txt"

    out_path = os.path.join(output_dir, filename)
    with open(out_path, "w", encoding="utf-8") as f:
        f.writelines(profile_lines)
    print(f"  Saved: {filename} ({len(profile_lines)} lines)")

print(f"\nDone. {len(profiles)} files saved to {output_dir}")
