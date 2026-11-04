"""Reproduce synthetic receipt-aware reorder, without network or real dispatch."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import application
import execution
from store import Conflict


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/remaining-reproduction')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    store=application.ApplicationStore(args.output/'tasks.sqlite3')
    fixture=json.loads((ROOT/'examples/remaining-pickup/scenario.json').read_text(encoding='utf-8'))
    t=store.create('回收偏差改序复现（构造）',fixture['data'])
    t=store.solve(t['id'],t['revision']);t=store.adopt(t['id'],t['revision'],t['plans'][-1]['id'],True)
    vid=fixture['vehicle']
    def receipt(task,stop,count):
        return store.receipt(task['id'],task['revision'],dict(kind='stop',vehicle=vid,stop_id=stop,
            finish=600,pickup=dict(cartons=count,totes=0),source='simulated'),True)
    t=receipt(t,fixture['first'],400);facts=deepcopy(t['receipts']);plans=deepcopy(t['plans'])
    before=execution.view(t)
    t=store.replan(t['id'],t['revision']);candidate=deepcopy(t['remaining_plans'][-1])
    assert execution.view(t)==before
    assert candidate['audit']['passed'] and candidate['changed']
    assert (candidate['distance_before'],candidate['distance_after'])==(10,27)
    t=store.adopt_remaining(t['id'],t['revision'],candidate['id'],True)
    after=execution.view(t)
    assert t['receipts']==facts and t['plans']==plans
    assert after['routes'][vid]['warnings']==[]
    assert after['routes'][vid]['next_stop']=='STORE_001'
    (args.output/'remaining.csv').write_text(store.export_remaining(t['id'],t['revision']),encoding='utf-8')
    # New receipt invalidates a candidate even when using the newest task revision.
    t=store.replan(t['id'],t['revision']);stale_id=t['remaining_plans'][-1]['id']
    t=receipt(t,'STORE_001',0)
    try:store.adopt_remaining(t['id'],t['revision'],stale_id,True)
    except Conflict:pass
    else:raise AssertionError('stale candidate accepted')
    assert application.ApplicationStore(args.output/'tasks.sqlite3').read(t['id'])==t
    # Current overload cannot be cured merely by reordering future visits.
    bad=store.create('当前超容反例（构造）',application.sample());bad=store.solve(bad['id'],bad['revision'])
    bad=store.adopt(bad['id'],bad['revision'],bad['plans'][-1]['id'],True)
    bad_vid,r=next(iter(execution.view(bad)['routes'].items()))
    bad=store.receipt(bad['id'],bad['revision'],dict(kind='stop',vehicle=bad_vid,stop_id=r['next_stop'],
        finish=600,pickup=dict(cartons=10000,totes=0),source='simulated'),True)
    bad=store.replan(bad['id'],bad['revision'])
    assert not bad['remaining_plans'][-1]['audit']['passed']
    try:store.adopt_remaining(bad['id'],bad['revision'],bad['remaining_plans'][-1]['id'],True)
    except ValueError:pass
    else:raise AssertionError('current overload candidate adopted')
    record=dict(kind='synthetic_only',task_id=t['id'],before=before,after=after,candidate=candidate,
        checks=['candidate does not change execution','10 to 27km capacity tradeoff','independent audit',
                'receipts and original plan preserved','remaining CSV','stale candidate blocked',
                'SQLite reopen identical','current overload adoption blocked'])
    (args.output/'verification.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Synthetic remaining-route reproduction passed; outputs:',args.output)


if __name__=='__main__':main()
