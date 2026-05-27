"""Minimal web UI for video-subber — one command: video-subber-ui."""
import subprocess, sys, json, os, time, threading, shutil
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse

HTML = """<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Video Subber</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif;
  background:#f5f5f7;color:#1d1d1f;display:flex;justify-content:center;align-items:center;
  min-height:100vh;-webkit-font-smoothing:antialiased}
.app{width:100%;max-width:520px;padding:40px 24px}
h1{font-size:28px;font-weight:700;text-align:center;margin-bottom:8px}
.sub{font-size:14px;color:#86868b;text-align:center;margin-bottom:28px}
.dropzone{border:2px dashed #c7c7cc;border-radius:16px;padding:48px 24px;text-align:center;
  background:#fff;cursor:pointer;transition:border-color .2s,background .2s}
.dropzone:hover{border-color:#0071e3;background:#f0f7ff}
.dropzone .icon{font-size:36px;margin-bottom:8px}
.dropzone .title{font-size:17px;font-weight:600}
.dropzone .hint{font-size:13px;color:#86868b;margin-top:4px}
.dropzone .file{font-size:13px;color:#0071e3;font-weight:500;margin-top:8px;display:none}
.controls{margin-top:16px;background:#fff;border-radius:12px;padding:16px 20px}
.row{display:flex;align-items:center;gap:12px;padding:6px 0}
.row+.row{border-top:1px solid #f0f0f0}
.row label{font-size:14px;font-weight:500;min-width:80px}
.toggle{display:flex}
.toggle button{border:1px solid #c7c7cc;background:#fff;padding:5px 14px;font-size:13px;cursor:pointer}
.toggle button:first-child{border-radius:6px 0 0 6px}
.toggle button:last-child{border-radius:0 6px 6px 0}
.toggle button.active{background:#0071e3;color:#fff;border-color:#0071e3}
select{padding:5px 10px;border:1px solid #c7c7cc;border-radius:6px;font-size:13px}
.btn{margin-top:16px;width:100%;padding:12px;border:none;border-radius:10px;
  background:#0071e3;color:#fff;font-size:16px;font-weight:600;cursor:pointer}
.btn:disabled{opacity:.4;cursor:not-allowed}
.status{display:none;margin-top:12px;padding:12px 16px;border-radius:8px;font-size:13px}
.status.running{display:block;background:#f0f7ff;color:#0071e3}
.status.done{display:block;background:#f0fff4;color:#34c759}
.status.error{display:block;background:#fff0f0;color:#ff3b30}
.output{margin-top:8px;font-size:13px;line-height:1.6;white-space:pre-wrap}
</style></head>
<body>
<div class="app">
<h1>🎬 Video Subber</h1>
<div class="sub">拖进去，等一会，就能看懂</div>
<div class="dropzone" id="dz">
  <div class="icon">📂</div>
  <div class="title">拖视频到这里，或点击选择</div>
  <div class="hint">MP4 / TS / MKV / AVI / MOV / WMV / FLV / WebM</div>
  <div class="file" id="fn"></div>
  <input type="file" id="fileInput" accept="video/*" style="display:none">
</div>
<div class="controls">
  <div class="row"><label>字幕状态</label>
    <div class="toggle" id="subToggle">
      <button class="active" data-v="no">无字幕</button>
      <button data-v="yes">有字幕</button>
    </div>
  </div>
  <div class="row"><label>模型</label>
    <select id="model"><option value="tiny">tiny（最快，~150MB）</option>
      <option value="base">base（快，~300MB）</option>
      <option value="small">small（均衡，~900MB）</option>
      <option value="medium" selected>medium（推荐，~1.5GB）</option>
      <option value="large-v3">large-v3（最准，~3GB）</option></select></div>
  <div class="row"><label>操作</label>
    <select id="mode"><option value="burn" selected>烧录字幕到视频</option>
      <option value="srt">仅生成 SRT 文件</option></select></div>
</div>
<button class="btn" id="btn" disabled>选择文件后开始</button>
<div class="status" id="status"></div>
<div class="output" id="output"></div>
</div>
<script>
const dz=document.getElementById('dz'),fi=document.getElementById('fileInput'),
  fn=document.getElementById('fn'),btn=document.getElementById('btn'),
  st=document.getElementById('status'),out=document.getElementById('output');
let vpath='';
dz.addEventListener('click',()=>fi.click());
dz.addEventListener('dragover',e=>{e.preventDefault();dz.style.borderColor='#0071e3'});
dz.addEventListener('dragleave',()=>dz.style.borderColor='#c7c7cc');
dz.addEventListener('drop',e=>{e.preventDefault();dz.style.borderColor='#c7c7cc';
  const f=e.dataTransfer.files[0];if(f){vpath=f.path||('/tmp/'+f.name);fn.style.display='block';fn.textContent=f.name;btn.disabled=false;btn.textContent='开始翻译'}});
fi.addEventListener('change',()=>{const f=fi.files[0];if(f){vpath=f.path||('/tmp/'+f.name);fn.style.display='block';fn.textContent=f.name;btn.disabled=false;btn.textContent='开始翻译'}});
document.getElementById('subToggle').addEventListener('click',e=>{if(e.target.tagName==='BUTTON'){e.target.parentElement.querySelectorAll('button').forEach(b=>b.classList.remove('active'));e.target.classList.add('active')}});
btn.addEventListener('click',async()=>{
  const sub=document.querySelector('#subToggle .active').dataset.v;
  const model=document.getElementById('model').value;
  const burn=document.getElementById('mode').value==='burn'?'':'--no-burn';
  btn.disabled=true;btn.textContent='处理中…';
  st.className='status running';st.textContent='运行中…';out.textContent='';
  try{
    const resp=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({path:vpath,subtitles:sub,model:model,burn:burn})});
    const data=await resp.json();
    st.className=data.ok?'status done':'status error';
    st.textContent=data.ok?'✅ 完成！':'❌ 错误';
    out.textContent=data.output||'';
    if(data.ok&&data.result)out.textContent+='\\n\\n📁 '+data.result;
  }catch(e){st.className='status error';st.textContent='❌ 连接失败: '+e.message}
  btn.disabled=false;btn.textContent='再来一次';
});
</script></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200);self.send_header("Content-Type","text/html;charset=utf-8");self.end_headers()
            self.wfile.write(HTML.encode())
        else:
            self.send_response(404);self.end_headers()
    def do_POST(self):
        if self.path == "/run":
            length = int(self.headers.get("Content-Length",0))
            body = json.loads(self.rfile.read(length))
            self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers()
            try:
                result = run_video_subber(body)
                self.wfile.write(json.dumps(result,ensure_ascii=False).encode())
            except Exception as e:
                self.wfile.write(json.dumps({"ok":False,"output":str(e)},ensure_ascii=False).encode())
        else:
            self.send_response(404);self.end_headers()
    def log_message(self, format, *args): pass

def run_video_subber(opts):
    cmd = [sys.executable, "-m", "video_subber.cli", opts["path"],
           "--subtitles", opts["subtitles"], "--model", opts["model"]]
    if "--no-burn" in opts.get("burn",""):
        cmd.append("--no-burn")
    r = subprocess.run(cmd, capture_output=True, text=True, cwd="/Users/andy/Andy-agent/video-subtitle/v1")
    ok = r.returncode == 0
    return {"ok": ok, "output": r.stdout[-1000:] if r.stdout else r.stderr[-1000:],
            "result": None}

def main():
    port = 8765
    print(f"🎬 Video Subber UI → http://localhost:{port}")
    print("   拖视频进去，等一会，就能看懂。Ctrl+C 停止。")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()

if __name__ == "__main__":
    main()
