from pathlib import Path
import shutil, sqlite3, json, sys

base = Path(r'D:\桌面文件')
target, source = base/'logi-reflow-final', base/'logi-reflow-new'
backup = base/'logi-reflow-main/outputs/final-before-central-20261107'
backup.mkdir(parents=True, exist_ok=True)
for p in target.iterdir():
    if p.is_file() and not (backup/p.name).exists(): shutil.copy2(p, backup/p.name)
runtime = target/'runtime'
runtime.mkdir(exist_ok=True)
for p in source.glob('*.py'): shutil.copy2(p, runtime/p.name)
for name in ['adaptive_planning.py', 'forecast_learning.py']:
    shutil.copy2(base/'logi-reflow-main'/name, runtime/name)
for name in ['application.sqlite3','intake.sqlite3','sessions.sqlite3']:
    dest=runtime/'data'/name
    dest.parent.mkdir(exist_ok=True)
    if not dest.exists():
        with sqlite3.connect(f'file:{(source/"data"/name).as_posix()}?mode=ro', uri=True) as src, sqlite3.connect(dest) as dst: src.backup(dst)
shutil.copytree(source/'docs/reference-materials', runtime/'docs/reference-materials', dirs_exist_ok=True)
assets=['application.css','intake.html','intake.css','intake.js','intake-review.js','intake-agent.js','intake-dispatch.js',
        'journey.html','journey.css','journey.js','driving3d.css','driving3d.js','route-map3d.css','route-map3d.js',
        'spatial.html','spatial-page.css','spatial-page.js','solutions.html','solutions.js','scenarios.html']
for name in assets: shutil.copy2(source/'web'/name,target/name)
# Extend only the missing capability-page styles; preserve final's design tokens.
original=(target/'site.css').read_text(encoding='utf-8')
new=(source/'web/site.css').read_text(encoding='utf-8')
start=new.find('.child-hero')
if start>=0 and '/* Central capability pages */' not in original:
    (target/'site.css').write_text(original+'\n/* Central capability pages */\n'+new[start:],encoding='utf-8')
sys.path.insert(0,str(runtime))
import multiwarehouse
s=multiwarehouse.MultiWarehouseStore(runtime/'data/application.sqlite3')
t=s.read('953c6f26280f84964c6d1ed6ab62d0b1')
p=multiwarehouse.current_plan(t)
print(json.dumps({'rows':len(t['intake_source']['rows']),'stores':len(t['data']['orders']),'vehicles':len(t['data']['vehicles']),'revision':t['revision'],'summary':p['summary'],'plan_audit':p['audit']},ensure_ascii=True))
