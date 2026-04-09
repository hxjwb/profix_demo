import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from pathlib import Path
from datetime import datetime

# Session log time range (from log file timestamps)
session_start = datetime.strptime("2026-03-25 16:40:12.592", "%Y-%m-%d %H:%M:%S.%f")
session_end   = datetime.strptime("2026-03-25 16:41:19.182", "%Y-%m-%d %H:%M:%S.%f")
session_dur_s = (session_end - session_start).total_seconds()  # ~66.6s

# Stall events (stall_time_utc, stall_gap_ms)
stalls_raw = [
    ("2026-02-24 17:53:53.659", 223),
    ("2026-02-24 17:53:53.966", 178),
    ("2026-02-24 17:53:54.619", 207),
    ("2026-02-24 17:53:55.214", 229),
    ("2026-02-24 17:53:55.436", 163),
    ("2026-02-24 17:53:56.392", 112),
    ("2026-02-24 17:53:56.612", 127),
    ("2026-02-24 17:54:02.015", 224),
    ("2026-02-24 17:54:02.618", 328),
    ("2026-02-24 17:54:02.818", 156),
    ("2026-02-24 17:54:02.975", 158),
    ("2026-02-24 17:54:03.701", 339),
    ("2026-02-24 17:54:03.866", 114),
    ("2026-02-24 17:54:04.054", 150),
    ("2026-02-24 17:54:04.672", 288),
    ("2026-02-24 17:54:04.996", 293),
    ("2026-02-24 17:54:05.610", 225),
    ("2026-02-24 17:54:05.793", 143),
    ("2026-02-24 17:54:06.160", 116),
    ("2026-02-24 17:54:14.875", 313),
    ("2026-02-24 17:54:15.067", 155),
    ("2026-02-24 17:54:15.213", 140),
    ("2026-02-24 17:54:16.370", 330),
    ("2026-02-24 17:54:16.780", 367),
    ("2026-02-24 17:54:16.901", 110),
    ("2026-02-24 17:54:17.531", 300),
    ("2026-02-24 17:54:17.702", 115),
    ("2026-02-24 17:54:18.316", 165),
    ("2026-02-24 17:54:18.965", 102),
    ("2026-02-24 17:54:22.321", 238),
    ("2026-02-24 17:54:23.329", 216),
    ("2026-02-24 17:54:23.903", 284),
    ("2026-02-24 17:54:24.317", 322),
    ("2026-02-24 17:54:24.942", 287),
    ("2026-02-24 17:54:25.684", 682),
    ("2026-02-24 17:54:25.903", 144),
    ("2026-02-24 17:54:26.463", 126),
    ("2026-02-24 17:54:26.765", 116),
    ("2026-02-24 17:54:27.540", 112),
    ("2026-02-24 17:54:31.830", 175),
    ("2026-02-24 17:54:32.323", 305),
    ("2026-02-24 17:54:32.506", 142),
    ("2026-02-24 17:54:32.884", 172),
    ("2026-02-24 17:54:33.459", 263),
    ("2026-02-24 17:54:33.725", 221),
]

stall_base = datetime.strptime(stalls_raw[0][0], "%Y-%m-%d %H:%M:%S.%f")
stall_times_s = [(datetime.strptime(t, "%Y-%m-%d %H:%M:%S.%f") - stall_base).total_seconds() for t, _ in stalls_raw]
stall_gaps    = [g for _, g in stalls_raw]
stall_dur_s   = stall_times_s[-1]  # ~40s

# Color by severity
def stall_color(gap):
    if gap >= 400:   return "#d62728"   # red - severe
    elif gap >= 250: return "#ff7f0e"   # orange - moderate
    else:            return "#1f77b4"   # blue - mild

colors = [stall_color(g) for g in stall_gaps]

# ---- Figure ----
fig, axes = plt.subplots(3, 1, figsize=(16, 10),
                         gridspec_kw={'height_ratios': [1.2, 2.5, 2]})
fig.patch.set_facecolor('#0f1117')
for ax in axes:
    ax.set_facecolor('#1a1d27')
    for spine in ax.spines.values():
        spine.set_edgecolor('#444')

# === Panel 1: Timeline bar ===
ax0 = axes[0]
ax0.set_xlim(0, stall_dur_s)
ax0.set_ylim(0, 1)
ax0.axhspan(0, 1, color='#2a2d3a', zorder=0)
ax0.barh(0.5, stall_dur_s, height=0.35, left=0, color='#2ecc71', alpha=0.25, zorder=1)

for t, gap, c in zip(stall_times_s, stall_gaps, colors):
    ax0.axvline(t, color=c, lw=1.5, alpha=0.85, zorder=2)

ax0.set_yticks([])
ax0.set_xlabel("Elapsed time (s)", color='#aaa', fontsize=9)
ax0.tick_params(colors='#aaa', labelsize=8)
ax0.set_title(
    f"Session Timeline  |  log: 2026-03-25 16:40:12 → 16:41:19  ({session_dur_s:.1f}s)  |  "
    f"Stall window: 17:53:53 → 17:54:33  ({stall_dur_s:.0f}s)  |  45 stalls",
    color='#eee', fontsize=10, pad=8
)

# === Panel 2: Scatter plot (time vs gap) ===
ax1 = axes[1]
ax1.set_xlim(-1, stall_dur_s + 1)
ax1.set_ylim(0, max(stall_gaps) * 1.25)
ax1.set_facecolor('#1a1d27')

# Background cluster shading
clusters = [(0, 4), (8.5, 14), (20.5, 26), (28.7, 34), (38.5, 42)]
for xs, xe in clusters:
    ax1.axvspan(xs, xe, color='#ffffff', alpha=0.03, zorder=0)

ax1.axhline(100, color='#555', lw=0.8, ls='--', zorder=1)
ax1.axhline(250, color='#ff7f0e', lw=0.8, ls='--', alpha=0.5, zorder=1)
ax1.axhline(400, color='#d62728', lw=0.8, ls='--', alpha=0.5, zorder=1)
ax1.text(stall_dur_s + 0.3, 100, '100ms', color='#777', fontsize=7, va='center')
ax1.text(stall_dur_s + 0.3, 250, '250ms', color='#ff7f0e', fontsize=7, va='center', alpha=0.7)
ax1.text(stall_dur_s + 0.3, 400, '400ms', color='#d62728', fontsize=7, va='center', alpha=0.7)

sizes = [max(30, g * 0.4) for g in stall_gaps]
sc = ax1.scatter(stall_times_s, stall_gaps, c=colors, s=sizes, zorder=3, edgecolors='#ffffff22', linewidths=0.5)

# Annotate the worst stall
worst_idx = stall_gaps.index(max(stall_gaps))
ax1.annotate(f"  {stall_gaps[worst_idx]}ms",
             xy=(stall_times_s[worst_idx], stall_gaps[worst_idx]),
             color='#d62728', fontsize=8, fontweight='bold', va='center')

ax1.set_ylabel("stall_gap_ms", color='#aaa', fontsize=9)
ax1.set_xlabel("Elapsed time (s)", color='#aaa', fontsize=9)
ax1.tick_params(colors='#aaa', labelsize=8)
ax1.grid(axis='y', color='#333', lw=0.5)

# === Panel 3: Bar chart of stall_gap_ms ===
ax2 = axes[2]
bar_colors = colors
ax2.bar(range(len(stall_gaps)), stall_gaps, color=bar_colors, width=0.7, zorder=2)
ax2.axhline(np.mean(stall_gaps), color='#f1c40f', lw=1.2, ls='--', zorder=3,
            label=f"Mean: {np.mean(stall_gaps):.0f}ms")
ax2.set_xlim(-1, len(stall_gaps))
ax2.set_ylim(0, max(stall_gaps) * 1.2)
ax2.set_xlabel("Stall index (chronological)", color='#aaa', fontsize=9)
ax2.set_ylabel("stall_gap_ms", color='#aaa', fontsize=9)
ax2.tick_params(colors='#aaa', labelsize=8)
ax2.grid(axis='y', color='#333', lw=0.5, zorder=0)
ax2.legend(facecolor='#2a2d3a', edgecolor='#555', labelcolor='#eee', fontsize=8)

# Legend for colors
patches = [
    mpatches.Patch(color='#1f77b4', label='Mild  (<250ms)'),
    mpatches.Patch(color='#ff7f0e', label='Moderate (250–399ms)'),
    mpatches.Patch(color='#d62728', label='Severe  (≥400ms)'),
]
fig.legend(handles=patches, loc='upper right', bbox_to_anchor=(0.98, 0.97),
           facecolor='#2a2d3a', edgecolor='#555', labelcolor='#eee', fontsize=8)

plt.tight_layout(rect=[0, 0, 1, 1])
out_path = Path(__file__).resolve().parent / "stall_timeline.png"
plt.savefig(out_path, dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
print(f"Saved: {out_path}")

# Summary stats
print(f"\nSession log duration : {session_dur_s:.1f}s")
print(f"Stall window         : {stall_dur_s:.1f}s  (17:53:53 → 17:54:33)")
print(f"Total stalls         : {len(stall_gaps)}")
print(f"Mean gap             : {np.mean(stall_gaps):.0f}ms")
print(f"Max gap              : {max(stall_gaps)}ms")
print(f"Min gap              : {min(stall_gaps)}ms")
mild     = sum(1 for g in stall_gaps if g < 250)
moderate = sum(1 for g in stall_gaps if 250 <= g < 400)
severe   = sum(1 for g in stall_gaps if g >= 400)
print(f"Mild/Moderate/Severe : {mild}/{moderate}/{severe}")
