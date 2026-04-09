import re, os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE   = os.path.join(BASE_DIR, "20260325_164012_0001.log")
OUTPUT_DIR = os.path.join(BASE_DIR, "profiles2")
os.makedirs(OUTPUT_DIR, exist_ok=True)

TIME_RE = re.compile(r'stall_time_utc=(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)')

in_profile = False
buf = []
profiles = []

with open(LOG_FILE, encoding="utf-8") as f:
    for line in f:
        if "===PROFILE===" in line:
            in_profile = True
            buf = [line]
        elif "===PROFILE_END===" in line and in_profile:
            buf.append(line)
            profiles.append(buf)
            in_profile = False
            buf = []
        elif in_profile:
            buf.append(line)

print(f"Found {len(profiles)} profiles")

for i, p in enumerate(profiles, 1):
    content = "".join(p)
    m = TIME_RE.search(content)
    ts = m.group(1).replace(" ", "_").replace(":", "-") if m else "unknown"
    fname = f"{i:03d}_{ts}.txt"
    with open(os.path.join(OUTPUT_DIR, fname), "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  {fname}")

print(f"\nDone → {OUTPUT_DIR}")
