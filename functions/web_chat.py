"""web_chat —— Web对话窗口 · 独立HTTP服务

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import json
import os
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from nucleus.const import ChatEvent, EyeEvent
from nucleus._silent_except import silent_exc

FUNCTION_META = {
    "name": "Web对话窗口",
    "class_name": "WebChatServer",
    "always_on": True,
    "thread_mode": "background",
}

# 内存中的回复队列（★FIX: 限制上限，避免无客户端轮询时内存无限增长）
_reply_queue: deque = deque(maxlen=200)
_reply_lock = threading.Lock()

def push_reply(content: str, source: str = ""):
    with _reply_lock:
        _reply_queue.append({
            "content": content,
            "source": source,
            "timestamp": time.time(),
        })

def pop_replies():
    with _reply_lock:
        replies = list(_reply_queue)
        _reply_queue.clear()
    return replies

# 上传文件保存目录
_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "uploads")

# HTML页面
WEB_CHAT_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>💬 曈曈 · 对话窗口</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { 
    background: #0a0a0f; color: #e0e0e0; 
    font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
    height: 100vh; display: flex; flex-direction: column;
}
.header { 
    background: #111122; padding: 12px 20px;
    border-bottom: 1px solid #1a1a2e;
    display: flex; align-items: center; gap: 10px;
}
.header h1 { font-size: 1.2em; color: #00d4ff; }
.header .status { font-size: 0.8em; color: #00ff88; }
.chat-area {
    flex: 1; overflow-y: auto; padding: 20px;
    display: flex; flex-direction: column; gap: 12px;
}
.message { max-width: 80%; padding: 10px 15px; border-radius: 12px; line-height: 1.6; }
.message.user { align-self: flex-end; background: #1a3a5c; color: #e0e0e0; }
.message.bot { align-self: flex-start; background: #111122; border: 1px solid #1a1a2e; }
.message .sender { font-size: 0.75em; color: #888; margin-bottom: 4px; }
.message pre { 
    background: #0a0a0f; padding: 10px; border-radius: 8px;
    overflow-x: auto; font-family: 'Consolas', monospace; font-size: 0.9em;
    margin: 5px 0;
}
.message img { max-width: 200px; border-radius: 8px; margin: 5px 0; cursor: pointer; }
.input-area {
    background: #111122; padding: 15px 20px;
    border-top: 1px solid #1a1a2e;
}
.input-area textarea {
    width: 100%; background: #0a0a0f; color: #e0e0e0;
    border: 1px solid #1a1a2e; border-radius: 8px;
    padding: 10px; font-family: inherit; font-size: 0.95em;
    resize: vertical; min-height: 60px; max-height: 200px;
}
.input-area textarea:focus { outline: none; border-color: #00d4ff; }
.input-area .buttons {
    display: flex; justify-content: space-between; align-items: center; margin-top: 8px;
}
.input-area .buttons-left { display: flex; gap: 8px; align-items: center; }
.input-area .buttons-right { display: flex; gap: 10px; }
.input-area button {
    padding: 8px 20px; border: none; border-radius: 6px;
    cursor: pointer; font-size: 0.9em; transition: background 0.2s;
}
.btn-send { background: #00d4ff; color: #0a0a0f; }
.btn-send:hover { background: #00b8e0; }
.btn-clear { background: #1a1a2e; color: #888; }
.btn-clear:hover { background: #2a2a3e; }
.btn-upload { 
    background: #1a1a2e; color: #00d4ff; border: 1px solid #00d4ff;
    padding: 8px 15px; border-radius: 6px; cursor: pointer; font-size: 0.9em;
    transition: all 0.2s;
}
.btn-upload:hover { background: #00d4ff20; }
#fileInfo { font-size: 0.8em; color: #888; margin-left: 8px; }
.thinking { color: #ffaa00; font-style: italic; padding: 10px; }
#dropZone {
    display: none; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0, 212, 255, 0.15); border: 3px dashed #00d4ff;
    z-index: 100; justify-content: center; align-items: center;
    font-size: 1.5em; color: #00d4ff;
}
</style>
</head>
<body>

<div class="header">
    <h1>💬 曈曈</h1>
    <span class="status">● 在线</span>
</div>

<div class="chat-area" id="chatArea">
    <div class="message bot">
        <div class="sender">曈曈</div>
        你好，我是曈曈。可以和我聊天、发送代码、上传图片或PDF文件给我。
    </div>
</div>

<div class="input-area">
    <textarea id="userInput" placeholder="输入消息...（Shift+Enter换行，Enter发送）支持上传图片/PDF文件" 
              onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMessage();}"></textarea>
    <div class="buttons">
        <div class="buttons-left">
            <input type="file" id="fileInput" 
                   accept=".jpg,.jpeg,.png,.gif,.bmp,.webp,.tiff,.pdf,.txt,.md,.log,.csv,.json,.xml,.yaml,.yml,.toml,.ini,.cfg,.py,.js,.ts,.html,.css,.sql,.sh,.bat,.java,.c,.cpp,.h,.go,.rs,.swift,.kt,.r,.m"
                   style="display:none" onchange="handleFileSelect(event)" multiple>
            <button class="btn-upload" onclick="document.getElementById('fileInput').click()">📎 上传文件</button>
            <span id="fileInfo"></span>
        </div>
        <div class="buttons-right">
            <button class="btn-clear" onclick="clearChat()">清屏</button>
            <button class="btn-send" onclick="sendMessage()">发送 (Enter)</button>
        </div>
    </div>
</div>

<div id="dropZone">📁 拖放文件到此处上传</div>

<script>
let pendingFiles = [];

// 拖拽上传支持
const dropZone = document.getElementById('dropZone');
document.addEventListener('dragover', function(e) {
    e.preventDefault();
    dropZone.style.display = 'flex';
});
document.addEventListener('dragleave', function(e) {
    if (e.relatedTarget === null) {
        dropZone.style.display = 'none';
    }
});
dropZone.addEventListener('drop', function(e) {
    e.preventDefault();
    dropZone.style.display = 'none';
    if (e.dataTransfer.files.length > 0) {
        processFiles(e.dataTransfer.files);
    }
});
dropZone.addEventListener('dragover', function(e) {
    e.preventDefault();
});

function handleFileSelect(event) {
    if (event.target.files.length > 0) {
        processFiles(event.target.files);
    }
}

function processFiles(files) {
    pendingFiles = [];
    const infoEl = document.getElementById('fileInfo');
    const names = [];
    // 与 accept 属性保持一致：支持图片/PDF/文本/代码/文档等多种格式
    const allowed = ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tiff', 'pdf',
                     'txt', 'md', 'log', 'csv', 'json', 'xml', 'yaml', 'yml', 'toml', 'ini', 'cfg',
                     'py', 'js', 'ts', 'html', 'css', 'sql', 'sh', 'bat',
                     'java', 'c', 'cpp', 'h', 'go', 'rs', 'swift', 'kt', 'r', 'm'];
    for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const ext = file.name.split('.').pop().toLowerCase();
        if (allowed.includes(ext)) {
            pendingFiles.push(file);
            names.push(file.name);
        }
    }
    if (names.length > 0) {
        infoEl.textContent = '已选择: ' + names.join(', ');
    } else {
        infoEl.textContent = '不支持的文件类型（支持图片/PDF/文本/代码/文档等）';
    }
}

async function uploadFiles() {
    if (pendingFiles.length === 0) return [];
    
    const uploadedPaths = [];
    for (const file of pendingFiles) {
        const formData = new FormData();
        formData.append('file', file);
        try {
            const response = await fetch('/upload', {
                method: 'POST',
                body: formData
            });
            const data = await response.json();
            if (data.status === 'ok') {
                uploadedPaths.push(data.file_path);
            }
        } catch (e) {
            console.error('文件上传失败:', e);
        }
    }
    pendingFiles = [];
    document.getElementById('fileInfo').textContent = '';
    return uploadedPaths;
}

async function sendMessage() {
    const input = document.getElementById('userInput');
    const text = input.value.trim();
    
    // 先上传待上传的文件
    const filePaths = await uploadFiles();
    
    // 如果没有文字也没有文件，不发送
    if (!text && filePaths.length === 0) return;
    
    // 构建显示文本
    let displayText = text;
    if (filePaths.length > 0) {
        const fileNames = filePaths.map(p => p.split('/').pop().split('\\').pop());
        if (displayText) {
            displayText = displayText + '\n[已上传: ' + fileNames.join(', ') + ']';
        } else {
            displayText = '[已上传: ' + fileNames.join(', ') + ']';
        }
    }
    
    // 显示用户消息
    if (displayText) {
        addMessage('user', '你', displayText);
    }
    input.value = '';
    
    // 构建发送数据
    const sendData = { message: text || '' };
    if (filePaths.length > 0) {
        sendData.file_paths = filePaths;
    }
    
    // 发送到后端
    try {
        const response = await fetch('/send', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(sendData)
        });
        if (response.ok) {
            const thinkingId = addThinking();
            pollReplies(thinkingId);
        }
    } catch (e) {
        addMessage('bot', '曈曈', '发送失败，请检查服务是否运行');
    }
}

function escapeHtml(str) {
    return (str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function addMessage(type, sender, text) {
    const area = document.getElementById('chatArea');
    const div = document.createElement('div');
    div.className = 'message ' + type;
    
    // ★FIX: 先转义 HTML，防止用户输入/AI回复中的 <script>/<img> 等标签触发 XSS
    let content = escapeHtml(text || '');
    content = content.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre>$2</pre>');
    content = content.replace(/\n/g, '<br>');
    
    const senderEl = document.createElement('div');
    senderEl.className = 'sender';
    senderEl.textContent = sender;
    div.appendChild(senderEl);
    
    const contentEl = document.createElement('div');
    contentEl.innerHTML = content;
    div.appendChild(contentEl);
    
    area.appendChild(div);
    area.scrollTop = area.scrollHeight;
}

function addThinking() {
    const area = document.getElementById('chatArea');
    const div = document.createElement('div');
    div.className = 'thinking';
    div.id = 'thinkingIndicator';
    div.textContent = '💭 曈曈正在思考...';
    area.appendChild(div);
    area.scrollTop = area.scrollHeight;
    return 'thinkingIndicator';
}

function removeThinking(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

async function pollReplies(thinkingId) {
    let attempts = 0;
    const maxAttempts = 60;
    
    while (attempts < maxAttempts) {
        try {
            const response = await fetch('/replies');
            const data = await response.json();
            
            if (data.replies && data.replies.length > 0) {
                removeThinking(thinkingId);
                for (const reply of data.replies) {
                    addMessage('bot', '曈曈', reply.content);
                }
                return;
            }
        } catch (e) {}
        
        attempts++;
        await new Promise(r => setTimeout(r, 1000));
    }
    
    removeThinking(thinkingId);
    addMessage('bot', '曈曈', '思考超时，请稍后再试');
}

function clearChat() {
    const area = document.getElementById('chatArea');
    area.innerHTML = '';
}

document.addEventListener('keydown', function(e) {
    if (e.ctrlKey && e.key === 'Enter') {
        e.preventDefault();
        sendMessage();
    }
});

setInterval(async () => {
    try {
        const response = await fetch('/replies');
        const data = await response.json();
        if (data.replies && data.replies.length > 0) {
            const thinking = document.getElementById('thinkingIndicator');
            if (thinking) thinking.remove();
            for (const reply of data.replies) {
                addMessage('bot', '曈曈', reply.content);
            }
        }
    } catch (e) {}
}, 2000);
</script>
</body>
</html>
"""

class WebChatHandler(BaseHTTPRequestHandler):
    """Web对话请求处理器"""
    
    def log_message(self, format, *args):
        pass
    def _check_origin(self) -> bool:
        """
        ★v24.0安全修复：只允许来自 localhost 的请求。
        防止本机恶意网页通过 CSRF 访问 5052 端口。
        175刀3：可信 Host 改由配置驱动（WEB_CHAT_ALLOWED_HOSTS，默认本机）；
        Origin 校验支持 http/https 双 scheme 白名单。
        """
        import sys as _sys
        _cfg = _sys.modules.get("config")
        allowed_hosts = tuple(getattr(_cfg, "WEB_CHAT_ALLOWED_HOSTS",
                                     ("127.0.0.1", "localhost"))) if _cfg is not None else ("127.0.0.1", "localhost")
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin', '')
        if host:
            host_name = host.split(':')[0]
            if host_name not in allowed_hosts:
                return False
        if origin:
            # 175刀3：scheme 白名单 http/https 均支持
            if not any(origin.startswith(f'{_s}://{h}') for h in allowed_hosts for _s in ("http", "https")):
                return False
        return True    
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self._serve_html()
        elif self.path == '/replies':
            self._serve_replies()
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_POST(self):
        if not self._check_origin():
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b'Forbidden')
            return
        if self.path == '/send':
            self._handle_send()
        elif self.path == '/upload':
            self._handle_upload()
        else:
            self.send_response(404)
            self.end_headers()
    
    def _serve_html(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(WEB_CHAT_HTML.encode('utf-8'))
    
    def _serve_replies(self):
        replies = pop_replies()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps({"replies": replies}, ensure_ascii=False).encode('utf-8'))
    
    def _get_current_user_name(self):
        """从信息场获取当前活跃身份"""
        try:
            if hasattr(self.server, 'info_field') and self.server.info_field:
                pulse = self.server.info_field.get_current("persona.switched")
                if pulse and isinstance(pulse, dict):
                    user = pulse.get("payload", {}).get("current_user", "")
                    if user:
                        return user
        except Exception as e:
            silent_exc(e, where="functions.web_chat::_get_current_user_name L451")
        return "访客"
    
    def _handle_upload(self):
        """处理文件上传"""
        # 确保上传目录存在
        os.makedirs(_UPLOAD_DIR, exist_ok=True)
        
        # 解析multipart/form-data
        content_type = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in content_type:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "需要multipart/form-data"}).encode('utf-8'))
            return
        
        # 读取body
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        # 提取boundary
        boundary_match = re.search(r'boundary=([^\s]+)', content_type)
        if not boundary_match:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "缺少boundary"}).encode('utf-8'))
            return
        
        boundary = boundary_match.group(1).encode('utf-8')
        
        # 简单multipart解析
        parts = body.split(b'--' + boundary)
        saved_path = None
        
        for part in parts:
            if b'filename=' in part:  # type: ignore[possibly-unbound]
                # 提取文件名
                header_match = re.search(rb'filename="([^"]+)"', part)  # type: ignore[possibly-unbound]
                if not header_match:
                    continue
                filename = header_match.group(1).decode('utf-8', errors='replace')
                
                # 找到文件内容（两个\r\n\r\n之后）
                header_end = part.find(b'\r\n\r\n')
                if header_end == -1:
                    continue
                file_content = part[header_end + 4:]
                # ★FIX: 只剥离 multipart 分帧的单个末尾 CRLF，禁止 rstrip——
                #   否则会破坏图片/PDF 等二进制文件末尾恰好为 \r\n 的字节
                if file_content.endswith(b'\r\n'):
                    file_content = file_content[:-2]
                elif file_content.endswith(b'\n'):
                    file_content = file_content[:-1]
                
                # 保存文件（★v24.0安全修复：basename 防路径穿越）
                import os.path as _osp
                _clean_filename = _osp.basename(filename)  # type: ignore[possibly-unbound]
                if not _clean_filename or _clean_filename in ('.', '..'):  # type: ignore[possibly-unbound]
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "非法文件名"}).encode('utf-8'))
                    return
                timestamp = int(time.time())
                safe_name = f"{timestamp}_{_clean_filename}"  # type: ignore[possibly-unbound]
                file_path = os.path.join(_UPLOAD_DIR, safe_name)
                # 确保最终路径在上传目录内
                _abs_upload = os.path.abspath(_UPLOAD_DIR)
                _abs_file = os.path.abspath(file_path)
                if not _abs_file.startswith(_abs_upload + os.sep):
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "路径非法"}).encode('utf-8'))
                    return
                with open(file_path, 'wb') as f:
                    f.write(file_content)
                saved_path = file_path
                break
        
        if saved_path:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "file_path": saved_path,
                "file_name": filename  # type: ignore[possibly-unbound]
            }, ensure_ascii=False).encode('utf-8'))
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "未找到文件"}).encode('utf-8'))
    
    def _handle_send(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            message = data.get('message', '')
            file_paths = data.get('file_paths', [])
            
            if (message or file_paths) and hasattr(self.server, 'info_field') and hasattr(self.server, 'pulse_core'):
                parsed = {"text": message, "code_blocks": [], "file_paths": list(file_paths)}
                
                # 识别代码块
                code_pattern = r'```(\w*)\n(.*?)```'
                matches = re.findall(code_pattern, message, re.DOTALL)
                for lang, code in matches:
                    parsed["code_blocks"].append({
                        "language": lang or "python",
                        "code": code.strip(),
                    })
                if parsed["code_blocks"]:
                    parsed["text"] = re.sub(code_pattern, '【代码片段】', message, flags=re.DOTALL).strip()
                
                user_name = self._get_current_user_name()
                
                # 发射文本消息
                if parsed["text"]:
                    chat_pulse = self.server.pulse_core.emit(
                        source_organ="Web对话",
                        event_type=ChatEvent.MESSAGE,
                        payload={
                            "content": parsed["text"],
                            "original_input": message,
                            "user_name": user_name,
                            "is_complex": len(message) > 50,
                            "code_blocks": parsed["code_blocks"],
                            "file_paths": parsed["file_paths"],
                        },
                        priority=5,
                        layer="L1"
                    )
                    self.server.info_field.publish(chat_pulse)
                
                # 处理上传的文件：直接发射视觉查询脉冲
                for fp in file_paths:
                    if not os.path.exists(fp):
                        continue
                    ext = os.path.splitext(fp)[1].lower()
                    if ext in ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff'):
                        # 图片 → OCR
                        self.server.info_field.publish(self.server.pulse_core.emit(
                            source_organ="Web对话",
                            event_type=EyeEvent.VISUAL_QUERY,
                            payload={
                                "file_path": fp,
                                "user_name": user_name,
                                "task_type": "ocr",
                            },
                            priority=7,
                            layer="L1"
                        ))
                    elif ext == '.pdf':
                        # PDF → PDF提取
                        self.server.info_field.publish(self.server.pulse_core.emit(
                            source_organ="Web对话",
                            event_type=EyeEvent.VISUAL_QUERY,
                            payload={
                                "file_path": fp,
                                "user_name": user_name,
                                "task_type": "pdf",
                            },
                            priority=7,
                            layer="L1"
                        ))
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "sent"}).encode('utf-8'))
        except Exception as e:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

class WebChatServer:
    """Web对话窗口服务器"""
    
    def __init__(self, port: int = 5052):
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._running = False
    
    def start(self, info_field=None, pulse_core=None):
        import os as _os
        import sys as _sys
        _cfg = _sys.modules.get("config")
        _bind_host = _os.environ.get("TTP_WEB_CHAT_BIND_HOST") or (
            getattr(_cfg, "WEB_CHAT_BIND_HOST", "127.0.0.1") if _cfg is not None else "127.0.0.1")
        self._server = ThreadingHTTPServer((_bind_host, self.port), WebChatHandler)
        self._server.info_field = info_field
        self._server.pulse_core = pulse_core
        self._running = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        
        # 注册人脸检测监听
        if info_field:
            from nucleus.const import ChatEvent as _ChatEvent
            from nucleus.const import PersonaEvent as _PersonaEvent
            def on_persona_switched(pulse):
                user_name = pulse.get("payload", {}).get("current_user", "访客")  # ★T-118a
                self._server.current_user_name = user_name
            def on_user_presence(pulse):
                user_name = pulse.get("payload", {}).get("user_name", "访客")  # ★T-118a
                if user_name and user_name != "用户":
                    self._server.current_user_name = user_name
            def on_user_left(pulse):
                self._server.current_user_name = "访客"
            info_field.register_condition(
                organ_name="Web对话-人脸监听",
                event_types=[_PersonaEvent.SWITCHED, _ChatEvent.USER_PRESENCE_DETECTED, _ChatEvent.USER_LEFT],
                handler=lambda p: on_persona_switched(p) if p.get("event_type") == _PersonaEvent.SWITCHED else (on_user_presence(p) if p.get("event_type") == _ChatEvent.USER_PRESENCE_DETECTED else on_user_left(p))
            )
        
        # 确保上传目录存在
        os.makedirs(_UPLOAD_DIR, exist_ok=True)
        print(f"[Web对话] 对话窗口已启动 → http://localhost:{self.port}")
    
    def stop(self):
        self._running = False
        if self._server:
            self._server.shutdown()

if __name__ == "__main__":
    print("=== Web对话窗口自测 ===")
    server = WebChatServer(5052)
    server.start()
    print("请打开浏览器访问 http://localhost:5052")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        print("已退出")