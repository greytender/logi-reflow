"""Constructed import sizes and one signed delivery each; not full scale proof."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from continuous import DispatchStore
from matrix_dispatch import FILES
import pilot_benchmark as pb
from verify_matrix_dispatch import audit


def run():
    results=[]
    for n in (3,12,30,40):
        with TemporaryDirectory() as tmp:
            pb.write_bundle(pb.sample(n),tmp)
            files={name:(Path(tmp)/name).read_text(encoding='utf-8') for name in FILES}
            store=DispatchStore(Path(tmp)/'scene.db')
            try:scene=store.act('start',dict(csv_files=files))
            except ValueError as e:
                results.append(dict(stores=n,start_rejected=str(e)));continue
            initial=audit(scene)
            options=[(r['stops'][0]['arrival'],vid,r['stops'][0]['id'])
                     for vid,r in scene['live_plan']['routes'].items() if r['stops']]
            time,vid,oid=min(options)
            order=next(o for o in scene['live_plan']['data']['orders'] if o['id']==oid)
            for kind,t in [('arrived',time),('unloading',time),('signed',time+order['service_minutes'])]:
                scene=store.act('receipt',dict(id=scene['id'],revision=scene['revision'],vehicle=vid,
                    order=oid,receipt=kind,time=t,source='manual_simulation'))
                audit(scene)
            results.append(dict(stores=n,sku_rows=n*10,initial=initial,after_signature=audit(scene)))
    output=Path(__file__).parent/'evidence'/'matrix-scale-audit.json'
    output.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print([(r['stores'],r.get('after_signature',{}).get('passed',False)) for r in results])


if __name__=='__main__':run()
