from pathlib import Path

root=Path(r'D:\桌面文件\logi-reflow-final')
p=root/'index.html'
p.write_text(p.read_text(encoding='utf-8').replace('24 张技术图','25 张技术图'),encoding='utf-8')
p=root/'workspace.html'
s=p.read_text(encoding='utf-8')
if 'central-operations.js' not in s:
    s=s.replace('<script src="central.js" defer></script>','<script src="central.js" defer></script><script src="central-operations.js" defer></script>')
if 'id="delivery-settings"' not in s:
    s=s.replace('<button id="refresh"','<button id="delivery-settings" class="quiet-button">配送设置</button><button id="refresh"')
p.write_text(s,encoding='utf-8')
p=root/'central.js'
s=p.read_text(encoding='utf-8')
s=s.replace('实际签收与返仓数据尚未录入；方案核定不代表企业派单。','签收与返仓以登记来源为准；方案核定不代表企业派单。')
s=s.replace("tasks.map(t=>`<div class=", "tasks.filter(t=>t.id!=='9b5e49566ff64e8780843e964852dd3e').map(t=>`<div class=")
p.write_text(s,encoding='utf-8')
# A complete offline HTML artifact: all diagrams, styles and rendering logic
# are embedded; no CDN, diagram service, or network dependency is required.
s=(root/'technical.html').read_text(encoding='utf-8')
s=s.replace('<link rel="stylesheet" href="technical.css">','<style>'+(root/'technical.css').read_text(encoding='utf-8')+'</style>')
s=s.replace('<script src="technical-graphs.js" defer></script>','').replace('<script src="technical.js" defer></script>','')
s=s.replace('</body>','<script>'+(root/'technical-graphs.js').read_text(encoding='utf-8')+'</script><script>'+(root/'technical.js').read_text(encoding='utf-8')+'</script></body>')
(root/'algorithm.html').write_text(s,encoding='utf-8')
print('Workspace actions connected; self-contained algorithm HTML built.')
