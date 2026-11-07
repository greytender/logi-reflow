"""Run actual full-batch solve without committing to the demonstration database."""
from pathlib import Path
import sys, json, time, hashlib
root=Path(r'D:\桌面文件\logi-reflow-final')
sys.path.insert(0,str(root/'runtime'))
import multiwarehouse
s=multiwarehouse.MultiWarehouseStore(root/'runtime/data/application.sqlite3')
task=s.read('953c6f26280f84964c6d1ed6ab62d0b1')
revision=task['revision']
started=time.perf_counter()
candidate=s.calculate(task)
report={'source_rows':len(task['intake_source']['rows']),'stores':len(task['data']['orders']),
        'summary':candidate['summary']['optimized'],'audit':candidate['audit'],
        'elapsed_seconds':round(time.perf_counter()-started,3),
        'stored_revision_unchanged':s.read(task['id'])['revision']==revision,
        'warehouses':{wid:{'status':g['status'],'operational':g['operational_verification']['status'],
            'search':g['optimized'].get('search')} for wid,g in candidate['warehouses'].items()}}
out=Path(r'D:\桌面文件\logi-reflow-main\outputs\final-upgrade-20261107')
(out/'engine-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True,indent=2))
assert report['source_rows']==116 and report['stores']==103
assert report['summary']['served']==103 and report['summary']['unserved']==0
assert report['audit']['passed'] is True and report['stored_revision_unchanged']
