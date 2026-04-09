# profix_demo

RTC stall analysis playground with:

- `stall_viewer.py`: local web viewer with structured LLM analysis
- `extract_profiles.py` / `extract_profiles2.py`: split raw logs into per-stall profiles
- `merge_clusters.py`: merge nearby stalls into clusters
- `plot_stalls.py`: generate a static stall overview image
- `prompt_simple.txt`: analysis prompt used by the viewer

## Setup

1. Create environment variables from `.env.example`
2. Start the viewer:

```bash
python3 stall_viewer.py --model glm
```

Available model presets:

- `gpt-5.2`
- `kimi`
- `glm`

## Notes

- `GLM_API_KEY` should use `id.secret` format
- The viewer defaults to `20260325_164012_0001.log` in the repo root
- Generated caches are written to `ai_reports/` and ignored by git
