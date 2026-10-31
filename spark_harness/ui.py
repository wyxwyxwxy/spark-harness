"""界面生成：从"已加载技能 + 工具注册表"动态生成 Web 仪表盘 HTML。

核心思想：界面不是写死的，而是由技能文件驱动——每个技能渲染成一个能力面板，
列出发它提供的工具；聊天框把用户指令交给 Harness Agent。即"界面完全由技能组成"。
"""

from __future__ import annotations

import html
from typing import List

from .skill import Skill
from .tools import ToolRegistry

__all__ = ["generate_dashboard_html"]


def _esc(s: str) -> str:
    return html.escape(str(s))


def generate_dashboard_html(
    skills: List[Skill],
    registry: ToolRegistry,
    title: str = "Spark Harness",
    ui_panels: Optional[List[dict]] = None,
) -> str:
    skill_cards = []
    for s in skills:
        tool_rows = []
        for tname in s.tools:
            spec = registry.get(tname)
            if spec is None:
                tool_rows.append(
                    f'<li class="tool missing"><code>{_esc(tname)}</code> '
                    f'<span class="muted">（未注册）</span></li>'
                )
                continue
            tool_rows.append(
                f'<li class="tool"><code>{_esc(spec.name)}</code>'
                f'<span class="desc">{_esc(spec.description.split("。")[0])}</span></li>'
            )
        rows = "\n".join(tool_rows) or '<li class="muted">（无工具）</li>'
        skill_cards.append(
            f"""
      <section class="card">
        <h3>{_esc(s.title or s.name)}</h3>
        <p class="desc">{_esc(s.description)}</p>
        <ul class="tools">{rows}</ul>
      </section>"""
        )
    skills_html = "\n".join(skill_cards)

    # 插件贡献的 UI 面板（界面由技能 + 插件共同组成）
    panels_html = ""
    if ui_panels:
        blocks = []
        for p in ui_panels:
            html_frag = p.get("html", "")
            blocks.append(html_frag)
        panels_html = "\n".join(blocks)

    side_extra = f"""
    <h2>插件面板（万物皆可插件）</h2>
    {panels_html or '<p class="muted">（未加载插件）</p>'}"""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_esc(title)}</title>
<style>
  :root {{ --bg:#0f1115; --fg:#e6e6e6; --acc:#5b8cff; --card:#171a21; --mut:#8b93a1; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--fg); }}
  header {{ padding:14px 20px; border-bottom:1px solid #23262e; display:flex; align-items:center; gap:10px; }}
  header h1 {{ font-size:18px; margin:0; }}
  .wrap {{ display:grid; grid-template-columns:1fr 360px; gap:16px; padding:16px; }}
  @media(max-width:900px){{ .wrap {{ grid-template-columns:1fr; }} }}
  .chat {{ background:var(--card); border-radius:12px; padding:14px; display:flex; flex-direction:column; min-height:70vh; }}
  .log {{ flex:1; overflow:auto; white-space:pre-wrap; }}
  .log .u {{ color:var(--acc); }}
  .log .a {{ margin:8px 0 14px; }}
  .controls {{ display:flex; gap:8px; margin-top:10px; }}
  textarea {{ flex:1; resize:none; height:54px; background:#0c0e12; color:var(--fg); border:1px solid #2a2e38; border-radius:8px; padding:8px; }}
  button {{ background:var(--acc); color:#fff; border:0; border-radius:8px; padding:0 16px; cursor:pointer; }}
  .side h2 {{ font-size:15px; margin:0 0 10px; }}
  .card {{ background:var(--card); border:1px solid #23262e; border-radius:12px; padding:12px; margin-bottom:12px; }}
  .card h3 {{ margin:0 0 6px; font-size:14px; color:var(--acc); }}
  .card .desc {{ color:var(--mut); margin:0 0 8px; }}
  ul.tools {{ list-style:none; margin:0; padding:0; }}
  ul.tools li {{ padding:4px 0; border-top:1px solid #20242c; }}
  code {{ background:#0c0e12; padding:1px 6px; border-radius:5px; }}
  .desc {{ color:var(--mut); }}
  .muted {{ color:var(--mut); }}
  .tool.missing code {{ color:#ff6b6b; }}
</style>
</head>
<body>
<header><h1>⚡ {_esc(title)}</h1><span class="muted">界面由 {len(skills)} 个技能 + {len(ui_panels or [])} 个插件面板动态生成</span></header>
<div class="wrap">
  <div class="chat">
    <div class="log" id="log">欢迎使用 Spark Harness。输入指令，由 Agent 调度技能/插件中的工具执行。</div>
    <div class="controls">
      <textarea id="inp" placeholder="例如：用浏览器打开 example.com 并截一张图"></textarea>
      <button id="send">发送</button>
    </div>
  </div>
  <div class="side">
    <h2>技能面板（界面由此组成）</h2>
    {skills_html}
    {side_extra}
  </div>
</div>
<script>
  const log = document.getElementById('log');
  const inp = document.getElementById('inp');
  function add(who, text){{
    const d = document.createElement('div');
    d.className = who === 'u' ? 'u' : 'a';
    d.textContent = (who === 'u' ? '你: ' : 'Agent: ') + text;
    log.appendChild(d); log.scrollTop = log.scrollHeight;
  }}
  // 供插件面板按钮调用：把预设指令填入并发送
  window.sparkSend = function(msg){{
    inp.value = msg; send();
  }};
  async function send(){{
    const v = inp.value.trim(); if(!v) return;
    add('u', v); inp.value='';
    const btn = document.getElementById('send'); btn.disabled = true;
    try {{
      const r = await fetch('/api/chat', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{message:v}})}});
      const j = await r.json();
      add('a', j.reply || j.error || '(空)');
    }} catch(e){{ add('a', 'ERROR: '+e); }}
    finally {{ btn.disabled = false; }}
  }}
  document.getElementById('send').onclick = send;
  inp.addEventListener('keydown', e => {{ if(e.key==='Enter' && !e.shiftKey){{ e.preventDefault(); send(); }} }});
</script>
</body>
</html>"""
