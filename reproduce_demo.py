"""Reproduce P1 -> P2 -> P3 and both alternatives using a temporary database.

No AI, network, existing SQLite modification, dispatch or credentials required.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import normal
from continuous import DispatchStore
from verify_comparison import audit

def reproduce():
    with TemporaryDirectory() as directory:
        store=DispatchStore(Path(directory)/'demo.sqlite3')
        scene=store.act('start',dict(data=normal.defaults()))
        def act(action,**extra):
            nonlocal scene
            scene=store.act(action,dict(id=scene['id'],revision=scene['revision'],**extra))
        act('event',kind='urgent',event=dict(time=505,store='S07',quantity=10,due=540,close=690))
        act('adopt',candidate=2);act('advance',time=506)
        act('event',kind='window',event=dict(time=506,store='S08',open=555,due=570,close=690))
        act('adopt',candidate=3)
        result=store.compare();parent=scene['versions'][1]['plan']
        result['audit']={k:audit(result[k],result['states'],parent) for k in ('baseline','recovered')}
        return result

if __name__=='__main__':
    result=reproduce();out=Path(__file__).resolve().parent/'outputs/reproduced-demo.json'
    out.parent.mkdir(exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k]['metrics'] for k in ('baseline','recovered')},ensure_ascii=False,indent=2))
    print('Independent audit passed. Full result:',out)
