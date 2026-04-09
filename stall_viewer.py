"""
Stall Profile Viewer
Usage: python3 stall_viewer.py <log_file>
       python3 stall_viewer.py  (defaults to 20260325_164012_0001.log in same dir)
"""

import re, os, sys, json, subprocess, urllib.request, urllib.error, socket, argparse
from flask import Flask, jsonify, abort, Response, stream_with_context
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPT_FILE = os.path.join(BASE_DIR, 'prompt_simple.txt')
REPORTS_DIR = os.path.join(BASE_DIR, 'ai_reports')
CACHE_VERSION = 6
os.makedirs(REPORTS_DIR, exist_ok=True)

MODEL_PRESETS = {
    'gpt-5.2': {
        'provider': 'poe',
        'base_url': os.environ.get('POE_BASE_URL', 'https://api.poe.com/v1'),
        'api_key': os.environ.get('POE_API_KEY'),
        'model': 'gpt-5.2',
    },
    'kimi': {
        'provider': 'moonshot',
        'base_url': os.environ.get('KIMI_BASE_URL', 'https://api.moonshot.cn/v1'),
        'api_key': os.environ.get('KIMI_API_KEY'),
        'model': 'kimi-k2.5',
    },
    'kimi-k2.5': {
        'provider': 'moonshot',
        'base_url': os.environ.get('KIMI_BASE_URL', 'https://api.moonshot.cn/v1'),
        'api_key': os.environ.get('KIMI_API_KEY'),
        'model': 'kimi-k2.5',
    },
    'glm': {
        'provider': 'zhipu',
        'base_url': os.environ.get('GLM_BASE_URL', 'https://open.bigmodel.cn/api/paas/v4'),
        'api_key': os.environ.get('GLM_API_KEY'),
        'model': 'glm-5.1',
    },
    'glm-5.1': {
        'provider': 'zhipu',
        'base_url': os.environ.get('GLM_BASE_URL', 'https://open.bigmodel.cn/api/paas/v4'),
        'api_key': os.environ.get('GLM_API_KEY'),
        'model': 'glm-5.1',
    },
}


def parse_args():
    parser = argparse.ArgumentParser(description='Stall Profile Viewer')
    parser.add_argument(
        'log_file',
        nargs='?',
        default=os.path.join(BASE_DIR, '20260325_164012_0001.log'),
        help='Path to the stall log file',
    )
    parser.add_argument(
        '--model',
        default='gpt-5.2',
        help='Model preset to use, e.g. gpt-5.2 or kimi',
    )
    args = parser.parse_args()
    preset = MODEL_PRESETS.get(args.model)
    if not preset:
        valid = ', '.join(sorted(MODEL_PRESETS))
        parser.error(f'unknown --model {args.model!r}; valid options: {valid}')
    args.model_preset = preset
    return args

# ── parse log ─────────────────────────────────────────────────────────────────

def strip_prefix(line):
    m = re.search(r'\(frame_time_window\.cc:\d+\): (.*)', line)
    return m.group(1) if m else line.rstrip('\n')

def parse_log(path):
    TIME_RE = re.compile(r'stall_time_utc=(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)')
    GAP_RE  = re.compile(r'stall_gap_ms=(\d+)')

    stalls, buf, in_profile = [], [], False

    with open(path, encoding='utf-8') as f:
        for line in f:
            if '===PROFILE===' in line:
                in_profile, buf = True, [line]
            elif '===PROFILE_END===' in line and in_profile:
                buf.append(line)
                content = ''.join(buf)
                tm = TIME_RE.search(content)
                gm = GAP_RE.search(content)
                clean = '\n'.join(strip_prefix(l) for l in buf)
                stalls.append({
                    'stall_time': tm.group(1) if tm else 'unknown',
                    'gap_ms':     int(gm.group(1)) if gm else 0,
                    'text':       clean,
                })
                in_profile, buf = False, []
            elif in_profile:
                buf.append(line)

    stalls.sort(key=lambda s: s['stall_time'])
    base_dt = datetime.strptime(stalls[0]['stall_time'], '%Y-%m-%d %H:%M:%S.%f')
    for s in stalls:
        dt = datetime.strptime(s['stall_time'], '%Y-%m-%d %H:%M:%S.%f')
        s['elapsed_s'] = round((dt - base_dt).total_seconds(), 3)
    return stalls

# ── AI inference (streaming SSE) ──────────────────────────────────────────────

def build_prompt(profile_text, stall_time, gap_ms):
    with open(PROMPT_FILE, 'r', encoding='utf-8') as f:
        system_prompt = f.read()

    return f"""{system_prompt}

---

下面是需要分析的 stall profile 原始数据：

```text
{profile_text}
```

补充信息：
- stall_time = {stall_time}
- stall_gap_ms = {gap_ms}

## 输出要求

请只输出一个 JSON 对象，不要输出 Markdown，不要输出解释性前后缀，不要使用代码块包裹。

字段结构必须严格如下：

{{
  "timeline": {{
    "headline": "时间线主导：网络时延",
    "confidence": "high | medium | low",
    "details": "详细说明时间线判断依据和关键证据"
  }},
  "network_judgment": {{
    "headline": "下层网络类型：突发丢包",
    "confidence": "high | medium | low",
    "details": "详细说明网络判断依据和关键证据"
  }},
  "rate_control": {{
    "headline": "速率控制策略：表现较好 / 表现一般 / 表现较差",
    "confidence": "high | medium | low",
    "details": "详细说明 BWE、码率、分辨率、帧率等控制行为是否合理"
  }},
  "loss_recovery": {{
    "headline": "丢包恢复策略：表现较好 / 表现一般 / 表现较差",
    "confidence": "high | medium | low",
    "details": "详细说明 NACK、RTX、FEC、关键帧恢复路径等是否有效"
  }},
  "summary": {{
    "headline": "总结判断：最可能根因是 ...",
    "confidence": "high | medium | low",
    "details": "补充说明不确定点、备选解释、后续建议"
  }},
  "code": {{
    "headline": "附加代码：不需要附加代码 / 建议补一段最小代码",
    "confidence": "high | medium | low",
    "details": "如果不需要代码，写原因；如果需要代码，说明这段代码解决什么问题以及为什么需要它",
    "needed": false,
    "code": ""
  }}
}}

额外约束：
- 所有结论尽量由日志证据支撑
- 如果证据不足，请明确写“证据不足，当前为推断”
- 每个部分都必须先给一句明确判定，格式必须像“时间线主导：网络时延”“下层网络类型：突发丢包”“速率控制策略：表现一般”
- 判定句要尽量短、直接、可高亮展示
- 每个部分都必须填写置信度和详细说明
- `code.needed` 默认写 `false`
- 默认不要生成任何图片、图表、matplotlib 代码
- 只有当一段很小的纯代码能明显帮助定位或验证结论时，才把 `code.needed` 设为 `true`
- 如果 `code.needed=false`，`code.code` 必须是空字符串
- 如果 `code.needed=true`，`code.code` 必须是最小必要代码，禁止生成图片，禁止依赖外部文件，优先给纯分析或提取逻辑
"""

def stream_poe(prompt_text):
    """Generator: yields SSE lines from Poe streaming API."""
    api_key  = ACTIVE_MODEL['api_key']
    base_url = ACTIVE_MODEL['base_url']
    model    = ACTIVE_MODEL['model']

    if not api_key:
        yield 'data: {"error":"API key not set"}\n\n'
        return

    url = base_url.rstrip('/') + '/chat/completions'
    payload_obj = {
        'model': model,
        'stream': True,
        'messages': [{'role': 'user', 'content': prompt_text}],
    }
    payload = json.dumps(payload_obj).encode('utf-8')

    req = urllib.request.Request(url, data=payload, method='POST')
    req.add_header('Authorization', f'Bearer {api_key}')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Accept', 'text/event-stream')

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw in resp:
                line = raw.decode('utf-8', errors='replace').strip()
                if not line.startswith('data:'):
                    continue
                data_str = line[5:].strip()
                if data_str == '[DONE]':
                    yield 'data: [DONE]\n\n'
                    return
                try:
                    chunk = json.loads(data_str)
                    delta = chunk['choices'][0].get('delta', {})
                    reasoning = delta.get('reasoning_content', '')
                    if reasoning:
                        yield f'data: {json.dumps({"reasoning": reasoning})}\n\n'
                    token = delta.get('content', '')
                    if token:
                        yield f'data: {json.dumps({"token": token})}\n\n'
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
    except Exception as e:
        yield f'data: {json.dumps({"error": str(e)})}\n\n'

# ── report cache ─────────────────────────────────────────────────────────────

def _cache_path(idx):
    tag = STALLS[idx]['stall_time'].replace(' ', '_').replace(':', '-')
    return os.path.join(REPORTS_DIR, f'v{CACHE_VERSION}_{idx:03d}_{tag}.json')

def load_cache(idx):
    p = _cache_path(idx)
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as f:
            cached = json.load(f)
        if cached.get('cache_version') == CACHE_VERSION:
            return cached
    return None

def save_cache(idx, markdown, reasoning):
    with open(_cache_path(idx), 'w', encoding='utf-8') as f:
        json.dump({
            'cache_version': CACHE_VERSION,
            'markdown': markdown,
            'reasoning': reasoning,
        }, f)

# ── Flask app ─────────────────────────────────────────────────────────────────

ARGS = parse_args()
ACTIVE_MODEL = ARGS.model_preset
log_path = ARGS.log_file
print(f'Parsing {log_path} ...', flush=True)
print(f'Using model preset: {ARGS.model} -> {ACTIVE_MODEL["model"]} ({ACTIVE_MODEL["provider"]})', flush=True)
STALLS = parse_log(log_path)
print(f'Found {len(STALLS)} stall profiles', flush=True)

app = Flask(__name__)

@app.route('/api/stalls')
def api_stalls():
    return jsonify([{'idx': i, 'stall_time': s['stall_time'],
                     'gap_ms': s['gap_ms'], 'elapsed_s': s['elapsed_s']}
                    for i, s in enumerate(STALLS)])

@app.route('/api/profile/<int:idx>')
def api_profile(idx):
    if not 0 <= idx < len(STALLS):
        abort(404)
    return STALLS[idx]['text'], 200, {'Content-Type': 'text/plain; charset=utf-8'}

@app.route('/api/ai_report/<int:idx>')
def api_ai_report(idx):
    """SSE stream: serve from cache if available, else call AI and cache result."""
    from flask import request as freq
    if not 0 <= idx < len(STALLS):
        abort(404)

    force = freq.args.get('force', '0') == '1'

    # ── cache hit ──
    if not force:
        cached = load_cache(idx)
        if cached:
            def from_cache():
                # replay as if streamed, so frontend code path is identical
                reasoning = cached.get('reasoning', '')
                chunk_size = 200
                for i in range(0, len(reasoning), chunk_size):
                    token = reasoning[i:i+chunk_size]
                    yield f'data: {json.dumps({"reasoning": token})}\n\n'
                md = cached['markdown']
                for i in range(0, len(md), chunk_size):
                    token = md[i:i+chunk_size]
                    yield f'data: {json.dumps({"token": token})}\n\n'
                yield f'data: {json.dumps({"done": True, "from_cache": True})}\n\n'
            return Response(stream_with_context(from_cache()),
                            mimetype='text/event-stream',
                            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    # ── cache miss: call AI ──
    s = STALLS[idx]
    try:
        prompt = build_prompt(s['text'], s['stall_time'], s['gap_ms'])
    except FileNotFoundError:
        return jsonify({'error': f'prompt file not found: {PROMPT_FILE}'}), 500

    collected = []
    reasoning_collected = []

    def generate():
        for chunk in stream_poe(prompt):
            yield chunk
            if chunk.startswith('data:') and '[DONE]' not in chunk:
                try:
                    obj = json.loads(chunk[5:])
                    if 'token' in obj:
                        collected.append(obj['token'])
                    if 'reasoning' in obj:
                        reasoning_collected.append(obj['reasoning'])
                except Exception:
                    pass

        full_md = ''.join(collected)
        full_reasoning = ''.join(reasoning_collected)
        save_cache(idx, full_md, full_reasoning)
        yield f'data: {json.dumps({"done": True})}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )

@app.route('/')
def index():
    return HTML

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Stall Profile Viewer</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0f1117;color:#ddd;font-family:monospace;display:flex;flex-direction:column;height:100vh}
  #header{padding:10px 16px;background:#1a1d27;border-bottom:1px solid #333;font-size:13px;color:#aaa;flex-shrink:0}
  #header b{color:#eee}
  #main{display:flex;flex:1;overflow:hidden}

  /* left: chart */
  #chart-panel{flex:0 0 58%;display:flex;flex-direction:column;border-right:1px solid #333;min-width:0}
  .hint{font-size:11px;padding:4px 12px;background:#1a1d27;border-bottom:1px solid #222;color:#557}
  #chart{flex:1;min-height:0}

  /* right: profile + AI */
  #right-panel{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}
  #right-title{padding:6px 10px;background:#1a1d27;border-bottom:1px solid #333;font-size:12px;color:#aaa;display:flex;align-items:center;gap:8px;flex-shrink:0}
  #right-title .ptitle{color:#f1c40f;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

  /* tabs */
  .tabs{display:flex;border-bottom:1px solid #333;background:#151820;flex-shrink:0}
  .tab{padding:5px 14px;font-size:11px;cursor:pointer;color:#666;border-bottom:2px solid transparent;user-select:none}
  .tab.active{color:#eee;border-bottom-color:#4a9edd}
  .tab-content{display:none;flex:1;overflow:auto}
  .tab-content.active{display:flex;flex-direction:column}

  /* raw profile */
  #profile-text{flex:1;overflow:auto;padding:12px;font-size:11px;line-height:1.6;white-space:pre;color:#c8d0e0}
  #profile-text.placeholder{color:#555;font-size:13px;padding-top:60px;text-align:center;white-space:normal}

  /* AI report */
  #ai-toolbar{padding:8px 12px;background:#151820;border-bottom:1px solid #282c3a;display:flex;align-items:center;gap:10px;flex-shrink:0}
  #btn-ai{padding:5px 14px;background:#2563eb;color:#fff;border:none;border-radius:4px;font-size:12px;cursor:pointer;font-family:monospace}
  #btn-ai:hover{background:#1d4ed8}
  #btn-ai:disabled{background:#334;color:#666;cursor:not-allowed}
  #ai-status{font-size:11px;color:#888}
  #ai-body{flex:1;overflow:auto;padding:16px 20px}

  /* markdown styles */
  #ai-body h1,#ai-body h2,#ai-body h3{color:#c9d1d9;margin:1em 0 .4em;font-family:sans-serif;border-bottom:1px solid #30363d;padding-bottom:.3em}
  #ai-body h1{font-size:1.4em} #ai-body h2{font-size:1.2em} #ai-body h3{font-size:1.05em}
  #ai-body p{margin:.5em 0;line-height:1.7;font-size:12px;color:#c8d0e0;font-family:sans-serif}
  #ai-body ul,#ai-body ol{margin:.4em 0 .4em 1.4em;font-size:12px;color:#c8d0e0;font-family:sans-serif;line-height:1.7}
  #ai-body code{background:#21262d;border-radius:3px;padding:1px 5px;font-size:11px;color:#e3b341}
  #ai-body pre{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px;overflow-x:auto;margin:.6em 0}
  #ai-body pre code{background:none;padding:0;color:#8b949e;font-size:10.5px;line-height:1.55}
  #ai-body blockquote{border-left:3px solid #4a9edd;padding-left:10px;color:#8b949e;margin:.5em 0}
  #ai-body table{border-collapse:collapse;width:100%;font-size:11px;margin:.6em 0}
  #ai-body th,#ai-body td{border:1px solid #30363d;padding:5px 9px;text-align:left;font-family:sans-serif}
  #ai-body th{background:#21262d;color:#c9d1d9}
  #ai-body .code-err{background:#2a0a0a;border:1px solid #5a1a1a;border-radius:4px;padding:10px;color:#f87171;font-size:11px;margin:.6em 0}
  #ai-streaming{white-space:pre-wrap;font-size:11px;color:#c8d0e0;line-height:1.6;font-family:monospace}
  .json-report{display:flex;flex-direction:column;gap:14px}
  .json-card{border:1px solid #30363d;border-radius:8px;padding:12px 14px;background:#11161f}
  .json-card h3{margin:0 0 8px;font-size:13px;color:#e5e7eb;font-family:sans-serif}
  .json-text{font-size:12px;line-height:1.7;color:#c8d0e0;font-family:sans-serif;white-space:pre-wrap}
  .json-list{margin:8px 0 0 18px;color:#c8d0e0;font-size:12px;line-height:1.7;font-family:sans-serif}
  .json-meta{display:inline-block;padding:2px 8px;border-radius:999px;background:#1f2937;color:#cbd5e1;font-size:11px;margin-top:8px}
  .json-headline{display:block;margin:6px 0 10px;padding:10px 12px;border-radius:8px;background:linear-gradient(135deg,#1d4ed8,#0f766e);color:#f8fafc;font-size:14px;font-weight:700;font-family:sans-serif}
  .thinking-box{margin-bottom:14px;border:1px solid #3b2f16;border-radius:8px;background:#1c1910;padding:12px 14px}
  .thinking-title{font-size:13px;color:#fcd34d;font-family:sans-serif;font-weight:700;margin-bottom:8px}
  .thinking-text{font-size:12px;line-height:1.7;color:#f3f4f6;font-family:sans-serif;white-space:pre-wrap}
</style>
</head>
<body>
<div id="header">
  <b>Stall Profile Viewer</b> &nbsp;—&nbsp;
  <span id="stat-total"></span> stalls &nbsp;|&nbsp;
  span: <span id="stat-span"></span>s &nbsp;|&nbsp;
  mean: <span id="stat-mean"></span>ms &nbsp;|&nbsp;
  max: <span id="stat-max"></span>ms
</div>
<div id="main">
  <!-- left -->
  <div id="chart-panel">
    <div class="hint">Click any point to view its profile →</div>
    <div id="chart"></div>
  </div>

  <!-- right -->
  <div id="right-panel">
    <div id="right-title">
      <span class="ptitle" id="ptitle">← click a stall</span>
    </div>
    <div class="tabs">
      <div class="tab active" data-tab="raw">Profile</div>
      <div class="tab" data-tab="ai">AI Report</div>
    </div>

    <!-- raw profile tab -->
    <div class="tab-content active" id="tab-raw">
      <div id="profile-text" class="placeholder">← Click a stall point on the chart</div>
    </div>

    <!-- AI report tab -->
    <div class="tab-content" id="tab-ai">
      <div id="ai-toolbar">
        <button id="btn-ai" disabled>✦ 生成 AI 报告</button>
        <button id="btn-regen" style="display:none;padding:5px 10px;background:#374151;color:#aaa;border:none;border-radius:4px;font-size:11px;cursor:pointer;font-family:monospace">↺ 重新生成</button>
        <span id="ai-status"></span>
      </div>
      <div id="ai-body"><div id="ai-streaming"></div></div>
    </div>
  </div>
</div>

<script>
let currentIdx = null;
let isStreaming = false;

// ── tabs ──────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
  });
});

// ── AI report ─────────────────────────────────────────────────────────────────
function startAiReport(force = false) {
  if (currentIdx === null || isStreaming) return;
  isStreaming = true;

  const btn    = document.getElementById('btn-ai');
  const btnRe  = document.getElementById('btn-regen');
  const status = document.getElementById('ai-status');
  const body   = document.getElementById('ai-body');

  btn.disabled = true;
  btnRe.style.display = 'none';
  btn.textContent = '⏳ 生成中…';
  status.textContent = force ? '重新生成中…' : '加载中…';
  body.innerHTML = '<div id="ai-streaming"></div>';

  // switch to AI tab
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.querySelector('[data-tab="ai"]').classList.add('active');
  document.getElementById('tab-ai').classList.add('active');

  let fullText = '';
  let fullReasoning = '';

  const url = `/api/ai_report/${currentIdx}` + (force ? '?force=1' : '');
  const evtSrc = new EventSource(url);

  evtSrc.onmessage = e => {
    if (e.data === '[DONE]') return;
    let obj;
    try { obj = JSON.parse(e.data); } catch { return; }

    if (obj.error) {
      status.textContent = '错误: ' + obj.error;
      evtSrc.close(); isStreaming = false;
      btn.disabled = false; btn.textContent = '✦ 生成 AI 报告';
      return;
    }
    if ('token' in obj) {
      fullText += obj.token;
      renderStreamingState(fullReasoning, fullText);
      body.scrollTop = body.scrollHeight;
    }
    if ('reasoning' in obj) {
      fullReasoning += obj.reasoning;
      renderStreamingState(fullReasoning, fullText);
      body.scrollTop = body.scrollHeight;
    }
    if (obj.done) {
      evtSrc.close();
      renderReport(fullText, fullReasoning);
      const fromCache = obj.from_cache;
      status.textContent = fromCache ? '📁 来自缓存' : '✓ 已缓存';
      btn.style.display = 'none';
      btnRe.style.display = '';
      isStreaming = false;
    }
  };

  evtSrc.onerror = () => {
    evtSrc.close(); isStreaming = false;
    status.textContent = '连接中断';
    btn.disabled = false; btn.textContent = '✦ 生成 AI 报告';
  };
}

document.getElementById('btn-ai').addEventListener('click',  () => startAiReport(false));
document.getElementById('btn-regen').addEventListener('click', () => startAiReport(true));

function renderThinkingBox(reasoning) {
  if (!reasoning || !reasoning.trim()) return '';
  return `
    <section class="thinking-box">
      <div class="thinking-title">思考过程</div>
      <div class="thinking-text">${escapeHtml(reasoning)}</div>
    </section>
  `;
}

function renderStreamingState(reasoning, content) {
  const body = document.getElementById('ai-body');
  body.innerHTML = `
    ${renderThinkingBox(reasoning)}
    <div id="ai-streaming">${escapeHtml(content)}</div>
  `;
}

function renderReport(md, reasoning) {
  const body = document.getElementById('ai-body');
  const parsed = tryParseStructuredReport(md);
  if (parsed) {
    body.innerHTML = renderThinkingBox(reasoning) + renderStructuredReport(parsed);
  } else {
    body.innerHTML = renderThinkingBox(reasoning) + marked.parse(md);
  }

  body.scrollTop = 0;
}

function escapeHtml(text) {
  return String(text ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function tryParseStructuredReport(text) {
  const raw = text.trim();
  const candidates = [raw];
  const blockMatch = raw.match(/```json\s*([\s\S]*?)```/i);
  if (blockMatch) candidates.push(blockMatch[1].trim());

  for (const candidate of candidates) {
    try {
      const obj = JSON.parse(candidate);
      if (obj && typeof obj === 'object' && obj.timeline && obj.summary && obj.code) return obj;
    } catch {}
  }
  return null;
}

function renderSection(title, section) {
  const confidence = section?.confidence ? `<div class="json-meta">confidence: ${escapeHtml(section.confidence)}</div>` : '';
  return `
    <section class="json-card">
      <h3>${escapeHtml(title)}</h3>
      <div class="json-headline">${escapeHtml(section?.headline || '')}</div>
      ${confidence}
      <div class="json-text">${escapeHtml(section?.details || '')}</div>
    </section>
  `;
}

function renderStructuredReport(data) {
  const extra = data.code || {};
  const extraNeeded = extra.needed ? 'true' : 'false';
  const extraCode = extra.code ? `<pre><code>${escapeHtml(extra.code)}</code></pre>` : '';

  return `
    <div class="json-report">
      ${renderSection('时间线（主导因素）', data.timeline)}
      ${renderSection('下层网络判断（类型）', data.network_judgment)}
      ${renderSection('速率控制策略（是否表现好）', data.rate_control)}
      ${renderSection('丢包恢复策略（是否表现好）', data.loss_recovery)}
      ${renderSection('总结', data.summary)}
      <section class="json-card">
        <h3>附加代码</h3>
        <div class="json-headline">${escapeHtml(extra.headline || '')}</div>
        ${extra.confidence ? `<div class="json-meta">confidence: ${escapeHtml(extra.confidence)}</div>` : ''}
        <div class="json-text">${escapeHtml(extra.details || '')}</div>
        <div class="json-text">needed: ${extraNeeded}</div>
        ${extraCode}
      </section>
    </div>
  `;
}

// ── chart ─────────────────────────────────────────────────────────────────────
(async () => {
  const res    = await fetch('/api/stalls');
  const stalls = await res.json();

  const gaps = stalls.map(s => s.gap_ms);
  const span = stalls[stalls.length-1].elapsed_s.toFixed(1);
  document.getElementById('stat-total').textContent = stalls.length;
  document.getElementById('stat-span').textContent  = span;
  document.getElementById('stat-mean').textContent  = (gaps.reduce((a,b)=>a+b,0)/gaps.length).toFixed(0);
  document.getElementById('stat-max').textContent   = Math.max(...gaps);

  const color  = g => g >= 400 ? '#d62728' : g >= 250 ? '#ff7f0e' : '#4a9edd';

  const trace = {
    x: stalls.map(s => s.elapsed_s), y: gaps,
    mode: 'markers', type: 'scatter',
    marker: {
      color: gaps.map(color),
      size:  gaps.map(g => Math.max(8, g * 0.045)),
      line:  {color: 'rgba(255,255,255,0.15)', width: 1}
    },
    text: stalls.map((s,i) => `#${i+1}  ${s.stall_time}<br>gap: ${s.gap_ms}ms`),
    hovertemplate: '%{text}<extra></extra>',
    customdata: stalls.map(s => s.idx),
  };

  const shapes = [250,400].map((v,i) => ({
    type:'line', x0:0, x1:+span, y0:v, y1:v,
    line:{color:i?'#d62728':'#ff7f0e', width:1, dash:'dot'}
  }));

  const layout = {
    paper_bgcolor:'#0f1117', plot_bgcolor:'#1a1d27',
    margin:{t:20,b:50,l:55,r:20},
    xaxis:{title:{text:'Elapsed time (s)',font:{color:'#888'}}, color:'#888', gridcolor:'#2a2d3a', zerolinecolor:'#333'},
    yaxis:{title:{text:'stall_gap_ms',font:{color:'#888'}},    color:'#888', gridcolor:'#2a2d3a', zerolinecolor:'#333'},
    shapes,
    annotations:[
      {x:+span,y:250,xref:'x',yref:'y',text:'250ms',showarrow:false,font:{color:'#ff7f0e',size:10},xanchor:'right'},
      {x:+span,y:400,xref:'x',yref:'y',text:'400ms',showarrow:false,font:{color:'#d62728',size:10},xanchor:'right'},
    ],
    hoverlabel:{bgcolor:'#2a2d3a',bordercolor:'#555',font:{family:'monospace',size:11}},
  };

  const chartEl = document.getElementById('chart');
  Plotly.newPlot(chartEl, [trace], layout, {responsive:true, displayModeBar:false});

  chartEl.on('plotly_click', async data => {
    const pt  = data.points[0];
    const idx = pt.customdata;
    const s   = stalls[idx];
    currentIdx = idx;

    document.getElementById('ptitle').textContent =
      `#${idx+1}  ${s.stall_time}  |  gap: ${s.gap_ms}ms`;

    // reset AI tab state
    document.getElementById('btn-ai').disabled = false;
    document.getElementById('btn-ai').style.display = '';
    document.getElementById('btn-ai').textContent = '✦ 生成 AI 报告';
    document.getElementById('btn-regen').style.display = 'none';
    document.getElementById('ai-body').innerHTML = '<div id="ai-streaming"></div>';
    document.getElementById('ai-status').textContent = '';

    // load raw profile into Profile tab
    const box = document.getElementById('profile-text');
    box.className = '';
    box.textContent = 'Loading…';
    const r = await fetch(`/api/profile/${idx}`);
    box.textContent = await r.text();
    box.scrollTop = 0;

    // switch to profile tab
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.querySelector('[data-tab="raw"]').classList.add('active');
    document.getElementById('tab-raw').classList.add('active');

    Plotly.restyle(chartEl, {
      'marker.line.color': stalls.map((_,i) => i===idx ? '#fff' : 'rgba(255,255,255,0.15)'),
      'marker.line.width': stalls.map((_,i) => i===idx ? 2 : 1),
    });
  });
})();
</script>
</body>
</html>
"""

if __name__ == '__main__':
    PORT = 5000
    # Kill any existing process on the port
    try:
        result = subprocess.run(['lsof', '-ti', f':{PORT}'], capture_output=True, text=True)
        pids = result.stdout.strip().split()
        for pid in pids:
            if pid:
                os.kill(int(pid), 9)
                print(f'Killed existing process {pid} on port {PORT}', flush=True)
        if pids and pids[0]:
            import time; time.sleep(0.5)
    except Exception:
        pass
    print(f'Open http://localhost:{PORT}', flush=True)
    app.run(debug=False, port=PORT, threaded=True)
