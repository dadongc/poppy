from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

CHAT_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Poppy Chat</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       height: 100vh; display: flex; flex-direction: column; background: #1a1a2e; color: #eee; }
#header { background: #16213e; padding: 12px 20px; font-size: 18px; font-weight: 600;
          border-bottom: 1px solid #0f3460; display: flex; justify-content: space-between; align-items: center; }
#header .status { font-size: 12px; color: #888; }
#header .status.online { color: #4ecca3; }
#messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
.msg { max-width: 85%; padding: 10px 14px; border-radius: 12px; line-height: 1.5; font-size: 14px;
       white-space: pre-wrap; word-break: break-word; }
.msg.user { align-self: flex-end; background: #0f3460; }
.msg.assistant { align-self: flex-start; background: #16213e; border: 1px solid #0f3460; }
.msg.tool { align-self: flex-start; background: #1a1a2e; border: 1px dashed #333; font-size: 12px; color: #aaa; }
.msg.tool.hidden { display: none; }
.msg .label { font-size: 11px; color: #4ecca3; margin-bottom: 4px; }
#step-indicator { font-size: 12px; color: #666; padding: 2px 0; text-align: center;
                  transition: opacity 0.3s; }
#step-indicator.finished { opacity: 0; }
#tool-toggle { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #888; cursor: pointer; user-select: none; }
#tool-toggle input { cursor: pointer; }
#input-area { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid #0f3460; background: #16213e; }
#input-area textarea { flex: 1; background: #1a1a2e; border: 1px solid #0f3460; color: #eee;
                        border-radius: 8px; padding: 10px; font-size: 14px; resize: none;
                        font-family: inherit; min-height: 42px; max-height: 120px; }
#input-area textarea:focus { outline: none; border-color: #4ecca3; }
#input-area button { background: #4ecca3; color: #1a1a2e; border: none; border-radius: 8px;
                     padding: 0 20px; font-weight: 600; cursor: pointer; font-size: 14px; }
#input-area button:disabled { background: #333; color: #666; cursor: not-allowed; }
.spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid #333;
           border-top-color: #4ecca3; border-radius: 50%; animation: spin 0.6s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div id="header">
  <span>Poppy Chat</span>
  <div style="display:flex;align-items:center;gap:16px;">
    <label id="tool-toggle"><input type="checkbox" onchange="toggleTools(this.checked)">显示工具调用</label>
    <span class="status" id="status">connecting...</span>
  </div>
</div>
<div id="step-indicator"></div>
<div id="messages"></div>
<div id="input-area">
  <textarea id="input" placeholder="输入消息，Enter 发送，Shift+Enter 换行" rows="1"></textarea>
  <button id="send" onclick="send()">发送</button>
</div>

<script>
const API = '/api';
const msgsEl = document.getElementById('messages');
const inputEl = document.getElementById('input');
const sendBtn = document.getElementById('send');
const statusEl = document.getElementById('status');

let sessionId = '';
let currentRunId = '';
let currentMsg = null;

let showTools = false;

function setStatus(text, online) {
  statusEl.textContent = text;
  statusEl.className = 'status' + (online ? ' online' : '');
}

let stepCurrent = 0;
let stepMax = 0;

function updateStepIndicator(text, finished) {
  const el = document.getElementById('step-indicator');
  el.textContent = text;
  el.className = finished ? 'finished' : '';
}

function toggleTools(show) {
  showTools = show;
  document.querySelectorAll('.msg.tool').forEach(el => {
    el.classList.toggle('hidden', !show);
  });
}

async function init() {
  try {
    const r = await fetch(API + '/sessions', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer chat'},
      body: JSON.stringify({title: 'Chat ' + new Date().toLocaleString()})
    });
    const d = await r.json();
    sessionId = d.session_id;
    setStatus('ready', true);
    addMsg('assistant', '你好！我是 Poppy，有什么可以帮助你的？');
  } catch(e) {
    setStatus('error: ' + e.message, false);
  }
}

function addMsg(role, content, cls) {
  const el = document.createElement('div');
  el.className = 'msg ' + (cls || role);
  if (role === 'tool') {
    el.innerHTML = '<div class="label">' + content[0] + '</div>' + content[1];
    if (!showTools) el.classList.add('hidden');
  } else {
    el.textContent = content;
  }
  msgsEl.appendChild(el);
  msgsEl.scrollTop = msgsEl.scrollHeight;
  return el;
}

function createSpinner() {
  const el = document.createElement('div');
  el.className = 'msg assistant';
  el.innerHTML = '<span class="spinner"></span>';
  msgsEl.appendChild(el);
  msgsEl.scrollTop = msgsEl.scrollHeight;
  return el;
}

async function send() {
  const text = inputEl.value.trim();
  if (!text || currentRunId) return;

  inputEl.value = '';
  sendBtn.disabled = true;
  addMsg('user', text);
  const spinner = createSpinner();
  setStatus('thinking...', false);

  try {
    // Start run
    const r = await fetch(API + '/runs', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer chat'},
      body: JSON.stringify({message: text, session_id: sessionId})
    });
    const d = await r.json();
    currentRunId = d.run_id;

    // Consume SSE
    const es = new EventSource(API + '/runs/' + currentRunId + '/events?token=chat');
    spinner.remove();

    es.addEventListener('llm.text_delta', e => {
      const data = JSON.parse(e.data);
      if (!currentMsg) {
        currentMsg = addMsg('assistant', '');
      }
      currentMsg.textContent += data.payload.text || '';
      msgsEl.scrollTop = msgsEl.scrollHeight;
    });

    es.addEventListener('step.started', e => {
      const data = JSON.parse(e.data);
      const step = data.payload.step || 0;
      stepCurrent = step;
      if (stepMax) {
        updateStepIndicator('⏳ Step ' + step + '/' + stepMax, false);
      } else {
        updateStepIndicator('⏳ Step ' + step, false);
      }
    });

    es.addEventListener('step.completed', e => {
      const data = JSON.parse(e.data);
      const step = data.payload.step || stepCurrent;
      stepCurrent = step;
      if (!stepMax && data.payload.max_steps) stepMax = data.payload.max_steps;
      const tools = (data.payload.tool_calls || []);
      if (tools.length) {
        updateStepIndicator('✅ Step ' + step + (stepMax ? '/' + stepMax : '') + ': ' + tools.join(', '), false);
      }
    });

    es.addEventListener('llm.tool_call_end', e => {
      const data = JSON.parse(e.data);
      const args = data.payload.arguments || {};
      const preview = Object.keys(args).length ? JSON.stringify(args, null, 2) : '';
      addMsg('tool', ['Tool: ' + (data.payload.name || '?'), preview]);
    });

    es.addEventListener('tool.completed', e => {
      const data = JSON.parse(e.data);
      const result = data.payload.result;
      const preview = typeof result === 'string' ? result.slice(0, 300) : JSON.stringify(result).slice(0, 300);
      addMsg('tool', ['Result:', preview + (preview.length >= 300 ? '...' : '')]);
    });

    es.addEventListener('tool.failed', e => {
      const data = JSON.parse(e.data);
      addMsg('tool', ['Error:', data.payload.error || '?']);
    });

    es.addEventListener('run.completed', e => {
      es.close();
      currentMsg = null;
      currentRunId = '';
      sendBtn.disabled = false;
      updateStepIndicator('', true);
      stepCurrent = 0; stepMax = 0;
      setStatus('ready', true);
      inputEl.focus();
    });

    es.addEventListener('run.failed', e => {
      es.close();
      currentMsg = null;
      currentRunId = '';
      sendBtn.disabled = false;
      updateStepIndicator('', true);
      stepCurrent = 0; stepMax = 0;
      addMsg('assistant', '[Run failed]');
      setStatus('error', false);
    });

    es.addEventListener('run.cancelled', e => {
      es.close();
      currentMsg = null;
      currentRunId = '';
      sendBtn.disabled = false;
      updateStepIndicator('', true);
      stepCurrent = 0; stepMax = 0;
      addMsg('assistant', '[Run cancelled]');
      setStatus('ready', true);
    });

    es.onerror = () => {
      es.close();
      currentMsg = null;
      currentRunId = '';
      sendBtn.disabled = false;
      updateStepIndicator('', true);
      stepCurrent = 0; stepMax = 0;
      setStatus('ready', true);
    };

  } catch(e) {
    spinner.remove();
    addMsg('assistant', 'Error: ' + e.message);
    currentRunId = '';
    sendBtn.disabled = false;
    setStatus('error', false);
  }
}

inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

init();
</script>
</body>
</html>"""


@router.get("/chat", response_class=HTMLResponse)
async def chat_page() -> str:
    return CHAT_HTML
