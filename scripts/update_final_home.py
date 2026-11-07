from pathlib import Path
import re
root=Path(r'D:\桌面文件\logi-reflow-final')
new=Path(r'D:\桌面文件\logi-reflow-new\web')
html=(root/'index.html').read_text(encoding='utf-8')
replacement=(new/'home.html').read_text(encoding='utf-8')
# Retain the final hero's design and composition. Only its outdated entry label
# and destinations follow the now-complete central-warehouse workflow.
html=html.replace('打开三店演示','打开中央仓方案').replace('workspace.html#upload','intake.html')
start=html.index('<section id="cap-dispatch"')
end=html.index('<div class="site-width"><section class="site-cta"',start)
ns=replacement.index('<section class="site-section site-width"><div class="section-heading">')
ne=replacement.index('<div class="site-width"><section class="site-cta"',ns)
body=replacement[ns:ne].replace('<section class="site-section site-width">','<section id="cap-dispatch" class="site-section site-width">',1)
body=body.replace('自由提问可选用已连接的 DeepSeek','自由提问可选择聚合接口演示')
body=body.replace('查看工具结果，核对后确认方案。','聚合接口演示支持 DeepSeek、Qwen、GLM、Kimi；当前未连接外部模型。')
html=html[:start]+body+'''<section class="site-section site-width" id="algorithm-showcase"><div class="section-heading"><h2>从原始订单，看到算法怎样工作</h2><p>116 笔原单、103 家门店，在同一批次里核对配车、冷链、装卸与回收。</p></div><div class="assistant-panel"><div><h3>24 张技术图，贯穿完整联合调度</h3><p>数据契约、整店分组、有界插入、三维箱位、逐站装卸、独立核验，以及有护栏的学习改良。</p><a class="site-action solid" href="algorithm.html">打开算法技术图谱 ↗</a></div><div><h3>跟随一辆车，动态查看装卸</h3><p>从完整批次中选择任一车次，播放装车、行驶、到店卸货、回收和返仓。</p><a class="text-link" href="workspace.html#warehouse">进入 3D 作业演示 ↗</a></div></div></section>'''+html[end:]
html=html.replace('href="workspace.html"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m3 17','href="solutions.html?cap=dispatch"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m3 17')
html=html.replace('href="workspace.html#warehouse"','href="solutions.html?cap=loading"',1)
html=html.replace('href="workspace.html#recovery"','href="solutions.html?cap=recovery"',1)
(root/'index.html').write_text(html,encoding='utf-8')
site=(root/'site.js').read_text(encoding='utf-8')
site=site.replace('href="#cap-dispatch"','href="index.html#cap-dispatch"').replace('href="#workflow"','href="scenarios.html"').replace('算法复盘','算法技术图谱')
(root/'site.js').write_text(site,encoding='utf-8')
solution=(root/'solutions.js').read_text(encoding='utf-8')
solution=solution.replace('自由提问可选用已连接的 DeepSeek，按接口用量计费。','自由提问提供 DeepSeek、Qwen、GLM、Kimi 聚合接口演示，当前不调用外部模型。')
solution=solution.replace("key==='loading'?'?sample=physical':''","key==='loading'?'#warehouse':key==='assistant'?'#assistant':key==='recovery'?'#recovery':key==='cost'?'#routes':'#overview'")
(root/'solutions.js').write_text(solution,encoding='utf-8')
print('Home hero retained; subsequent sections and capability destinations updated.')
