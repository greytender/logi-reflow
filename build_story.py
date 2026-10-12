"""Render the project's plain-language document as an offline chapter reader.

Python standard library only. No model calls, DB writes or network requests.
The Markdown document is the single source of the prose.
"""
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'docs/大白话讲清城配续航.md'


def inline(text):
    text = escape(text)
    def link(match):
        label, target = match.groups()
        if not target.startswith(('https://', 'http://')):
            resolved = (SOURCE.parent / target).resolve()
            target = 'https://github.com/greytender/logi-reflow/blob/main/' + resolved.relative_to(ROOT).as_posix()
        return f'<a href="{escape(target, quote=True)}">{label}</a>'
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link, text)
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)


def main():
    raw = SOURCE.read_text(encoding='utf-8')
    blocks = raw.strip().split('\n\n')
    title = blocks.pop(0).removeprefix('# ')
    introduction, chapters = [], []
    for block in blocks:
        if block.startswith('## '):
            chapters.append({'title': block[3:].strip(), 'paragraphs': []})
        else:
            (chapters[-1]['paragraphs'] if chapters else introduction).append(block)
    sections = '\n'.join(
        f'<section class="chapter" id="chapter-{i}" data-index="{i}" aria-labelledby="title-{i}">'
        f'<p class="chapter-position">第{i+1}章 / 共{len(chapters)}章</p>'
        f'<h2 id="title-{i}" tabindex="-1">{escape(c["title"])}</h2>'
        + ''.join(f'<p>{inline(p)}</p>' for p in c['paragraphs']) + '</section>'
        for i, c in enumerate(chapters))
    navigation = ''.join(
        f'<a href="#chapter-{i}" data-index="{i}"><span>{i+1:02d}</span>{escape(c["title"])}</a>'
        for i, c in enumerate(chapters))
    html = TEMPLATE.replace('__TITLE__', escape(title)).replace('__NAV__', navigation)
    html = html.replace('__INTRO__', ''.join(f'<p>{inline(p)}</p>' for p in introduction))
    html = html.replace('__SECTIONS__', sections).replace('__COUNT__', str(len(chapters)))
    html = html.replace('__SOURCE_SHA__', sha256(SOURCE.read_bytes()).hexdigest())
    output = ROOT / 'web/story.html'
    output.write_text(html, encoding='utf-8')
    print(json.dumps({'source': str(SOURCE), 'output': str(output), 'chapters': len(chapters),
                      'source_sha256': sha256(SOURCE.read_bytes()).hexdigest()}, ensure_ascii=False))


TEMPLATE = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="用一趟配送的故事，讲清城配续航的完整思路、最终目标、优点和不足。">
<meta name="story-source-sha256" content="__SOURCE_SHA__"><title>大白话讲清城配续航</title>
<link rel="stylesheet" href="app.css">
<style>
body{font-size:17px;line-height:1.95}a{color:var(--green);text-underline-offset:4px}
::selection{background:var(--green-soft);color:var(--ink)}
.reader-top{max-width:1180px;margin:auto;padding:24px 32px;display:flex;gap:20px;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line)}
.reader-top a{font-size:14px}.reader-top strong{font-size:18px}.reader-top span{font-size:12px;color:var(--muted)}
.reader-intro{max-width:1180px;margin:0 auto;padding:38px 32px 24px}.reader-intro h1{font-size:clamp(27px,3.3vw,38px);line-height:1.35;max-width:22ch;margin:0 0 22px;font-weight:650}
.reader-intro p{font-size:14px;max-width:78ch;color:var(--muted);margin:10px 0}.reader-intro p:first-of-type{color:var(--ink);font-size:17px}
.reader-layout{max-width:1180px;margin:0 auto;display:grid;grid-template-columns:245px minmax(0,1fr);gap:54px;padding:0 32px 56px}
.reader-aside{align-self:start;position:sticky;top:18px;padding-top:22px}summary{font-size:14px;font-weight:600;cursor:pointer}
.chapter-nav{display:flex;flex-direction:column;gap:3px;margin-top:14px}.chapter-nav a{display:flex;align-items:baseline;gap:11px;text-decoration:none;font-size:12px;padding:8px 10px;color:var(--muted);line-height:1.65;border-radius:6px}.chapter-nav a span{font-variant-numeric:tabular-nums;color:var(--muted);font-size:10px}.chapter-nav a:hover,.chapter-nav a[aria-current="step"]{background:var(--green-soft);color:var(--green)}.chapter-nav a[aria-current="step"]{font-weight:650}
.reader-aside>p{font-size:12px;color:var(--muted);border-top:1px solid var(--line);padding-top:18px;margin-top:20px}
.reader-main{min-width:0}.reader-toolbar{border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:16px 0;display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:22px}.reader-toolbar p{font-size:12px;color:var(--muted);margin:0}.reader-controls{display:flex;gap:8px;flex-wrap:wrap}.reader-controls button{font-size:12px;min-height:40px;padding:7px 12px}.reader-controls .current{background:var(--green);color:var(--surface);border-color:var(--green)}
.chapter{max-width:72ch;padding:4px 0 30px}.chapter h2{font-size:25px;line-height:1.5;font-weight:650;margin:12px 0 24px;text-wrap:balance}.chapter p{margin:0 0 21px}.chapter .chapter-position{font-size:11px;color:var(--muted);margin:16px 0 4px;font-variant-numeric:tabular-nums}.chapter p:has(>strong:first-child){margin-top:26px}.chapter strong{font-weight:650;color:var(--green)}
.reader-note{max-width:72ch;font-size:12px;color:var(--muted);margin-top:25px;padding-top:20px;border-top:1px solid var(--line)}
.reader-bottom{display:flex;align-items:center;justify-content:space-between;gap:15px;flex-wrap:wrap;font-size:13px;margin-top:24px;border-top:1px solid var(--line);padding-top:20px}
:focus-visible{outline:2px solid var(--green);outline-offset:4px}.chapter h2:focus{outline:none}
@media(max-width:860px){.reader-layout{grid-template-columns:minmax(0,1fr);gap:18px;padding:0 22px 36px}.reader-aside{position:static;padding-top:0}.reader-aside>p{display:none}.chapter-nav{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}.reader-intro{padding:28px 22px 18px}.reader-top{padding:18px 22px}.reader-top span{display:none}.chapter{max-width:none}.chapter h2{font-size:23px}.reader-toolbar{margin-top:0}}
@media(max-width:460px){body{font-size:16px}.reader-top strong{font-size:16px}.reader-top a{font-size:12px}.reader-intro h1{font-size:27px}.reader-toolbar{display:block}.reader-controls{margin-top:12px}.reader-controls button{flex:1}.chapter-nav{grid-template-columns:1fr}.chapter p{overflow-wrap:break-word}}
@media print{body{background:white;color:black;font-size:11pt}.reader-top,.reader-aside,.reader-toolbar,.reader-bottom{display:none}.reader-layout{display:block;padding:0}.reader-intro{padding:0}.chapter[hidden]{display:block!important}.chapter h2{break-after:avoid}.chapter{break-inside:auto}.reader-intro h1{font-size:22pt}a{color:inherit}.reader-note{font-size:9pt}}
</style></head><body>
<a class="skip-link" href="#reading">跳到正文</a>
<header class="reader-top"><strong>城配续航 / 思路讲解</strong><span>阅读材料 · 构造情境与真实能力分开</span><a href="/">返回现有应用</a></header>
<div class="reader-intro"><h1>__TITLE__</h1>__INTRO__</div>
<div class="reader-layout"><aside class="reader-aside"><details id="directory" open><summary>章节目录</summary><nav class="chapter-nav" aria-label="故事章节">__NAV__</nav></details><p>从“为什么做”读到“哪里还没做好”。<br>点击章节可以跳读；全文模式也可以打印保存。</p></aside>
<main class="reader-main" id="reading"><div class="reader-toolbar" id="toolbar" hidden><p id="readingStatus" aria-live="polite"></p><div class="reader-controls"><button id="previous" type="button">上一章</button><button id="next" type="button">下一章</button><button id="mode" type="button">读完整篇</button></div></div>
__SECTIONS__
<div class="reader-bottom"><a href="/">去应用核对三个反例</a><a href="https://github.com/greytender/logi-reflow/blob/main/docs/大白话讲清城配续航.md">打开同内容的完整文档</a></div>
<p class="reader-note">整理日期：2026-10-12。本文是思路讲解，不运行求解器、不调用AI、不改变配送状态。带数字的例子来自不同构造算例；既有测试记录不代表本页面重新验证了算法。</p>
</main></div>
<script>
(()=>{
const chapters=Array.from(document.querySelectorAll('.chapter')),links=Array.from(document.querySelectorAll('.chapter-nav a'));
const status=document.getElementById('readingStatus'),previous=document.getElementById('previous'),next=document.getElementById('next'),mode=document.getElementById('mode'),directory=document.getElementById('directory');
let index=0,full=false;
function readHash(){full=location.hash==='#full';const value=Number(location.hash.replace('#chapter-',''));index=Number.isInteger(value)&&value>=0&&value<chapters.length?value:0;}
function render(){chapters.forEach((chapter,i)=>chapter.hidden=!full&&i!==index);links.forEach((link,i)=>{if(!full&&i===index)link.setAttribute('aria-current','step');else link.removeAttribute('aria-current');});status.textContent=full?'完整篇 · 可使用浏览器打印保存':'第'+(index+1)+'章 / 共'+chapters.length+'章';previous.disabled=full||index===0;next.disabled=full||index===chapters.length-1;mode.textContent=full?'切回逐章':'读完整篇';mode.classList.toggle('current',full);mode.setAttribute('aria-pressed',String(full));}
function select(i){index=i;full=false;history.replaceState(null,'','#chapter-'+index);render();if(matchMedia('(max-width:860px)').matches)directory.open=false;chapters[index].querySelector('h2').focus();document.getElementById('reading').scrollIntoView({block:'start'});}
links.forEach(link=>link.addEventListener('click',event=>{event.preventDefault();select(Number(link.dataset.index));}));previous.addEventListener('click',()=>select(Math.max(0,index-1)));next.addEventListener('click',()=>select(Math.min(chapters.length-1,index+1)));mode.addEventListener('click',()=>{full=!full;history.replaceState(null,'',full?'#full':'#chapter-'+index);render();});addEventListener('hashchange',()=>{readHash();render();});document.getElementById('toolbar').hidden=false;if(matchMedia('(max-width:860px)').matches)directory.open=false;readHash();render();
})();
</script></body></html>'''


if __name__ == '__main__':
    main()
