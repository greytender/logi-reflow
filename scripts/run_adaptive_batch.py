"""Run the opt-in local learning/planning upgrade and save reviewable witnesses.

Example: python -X utf8 scripts/run_adaptive_batch.py --demo
Production-like inputs require explicit dates and independently reviewed feedback.
No candidate is adopted and no receipt or dispatch is written.
"""
from argparse import ArgumentParser
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import adaptive_planning as adaptive
from forecast_learning import ForecastStore,timestamp
import multiwarehouse as mw
from scripts.diagnose_high_load_operational import fixtures


def demonstration():
    case=next(c for c in fixtures() if c['id']=='ambient_pickup')
    data=deepcopy(case['data']);wid=data['settings']['depot_id']
    raw=dict(schema_version=mw.SCHEMA,
        warehouses=[dict(id=wid,name='构造高装载学习示例',settings=data['settings'],
                         business=data['business'],operational_policy=case['policy'])],
        orders=[dict(o,warehouse_id=wid) for o in data['orders']],
        vehicles=[dict(v,warehouse_id=wid) for v in data['vehicles']],legs=data['legs'])
    base=datetime(2026,10,1,tzinfo=timezone(timedelta(hours=8)));feedback=[]
    for day in range(6):
        for order in data['orders']:
            at=base+timedelta(days=day)
            feedback.append(dict(id=f"demo-{order['id']}-{day}",warehouse_id=wid,store_id=order['id'],
                data_kind='simulated',source='simulated',verified=True,
                reference='预定义模拟回执；不是企业单据',
                decision_at=(at+timedelta(hours=8)).isoformat(),
                observed_at=(at+timedelta(hours=16)).isoformat(),
                available_at=(at+timedelta(hours=17)).isoformat(),
                nominal=deepcopy(order['pickup']),actual=dict(cartons=4,totes=2)))
    return raw,feedback


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--demo',action='store_true')
    parser.add_argument('--input',type=Path)
    parser.add_argument('--feedback',type=Path)
    parser.add_argument('--learning-db',type=Path,default=ROOT/'outputs/algorithm-upgrade/adaptive-learning-v2.sqlite3')
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/algorithm-upgrade/adaptive-batch.json')
    parser.add_argument('--decision-at')
    parser.add_argument('--training-until')
    parser.add_argument('--validation-start')
    parser.add_argument('--validation-until')
    parser.add_argument('--monitor-start')
    parser.add_argument('--monitor-until')
    parser.add_argument('--auto-rollback',action='store_true')
    args=parser.parse_args()
    if args.demo:
        if args.input or args.feedback:parser.error('--demo不能与外部输入/回执混用')
        raw,feedback=demonstration()
        decision_at=args.decision_at or '2026-11-05T08:00:00+08:00'
        windows=('2026-10-03T23:59:00+08:00','2026-10-04T00:00:00+08:00','2026-10-06T23:59:00+08:00')
    else:
        if not args.input or not args.decision_at:parser.error('需要--input与带时区的--decision-at')
        raw=json.loads(args.input.read_text(encoding='utf-8-sig'));decision_at=args.decision_at
        feedback=json.loads(args.feedback.read_text(encoding='utf-8-sig')) if args.feedback else []
        windows=(args.training_until,args.validation_start,args.validation_until)
        if any(windows) and not all(windows):parser.error('训练截止、验证开始和验证截止需要一起填写')
    if not isinstance(feedback,list):parser.error('反馈文件必须为标准学习事件列表')
    decision_time=timestamp(decision_at)
    if all(windows) and not timestamp(windows[0])<timestamp(windows[1])<=timestamp(windows[2])<decision_time:
        parser.error('训练和验证窗口必须按顺序结束于决策之前')
    monitoring=(args.monitor_start,args.monitor_until)
    if (any(monitoring) or args.auto_rollback) and not all(monitoring):
        parser.error('监控开始和结束时间需要一起填写')
    if all(monitoring) and not timestamp(monitoring[0])<timestamp(monitoring[1])<decision_time:
        parser.error('监控窗口必须按顺序结束于决策之前')
    data=mw.canonicalize(raw);store=ForecastStore(args.learning_db)
    for record in feedback:store.record(record)
    training={}
    if all(windows):
        for warehouse in data['warehouses']:
            training[warehouse['id']]=store.retrain(warehouse['id'],warehouse['settings']['data_kind'],*windows)
    monitor_reports={}
    if all(monitoring):
        for warehouse in data['warehouses']:
            monitor_reports[warehouse['id']]=store.monitor(warehouse['id'],warehouse['settings']['data_kind'],
                *monitoring,auto_rollback=args.auto_rollback)
    report=adaptive.plan_batch(data,decision_at=decision_at,learning_store=store)
    report['learning_updates']=training
    report['learning_monitor']=monitor_reports
    report['demo']=args.demo
    report['decision_at']=decision_at
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(dict(output=str(args.output),all_guarded_candidates=report['all_guarded_candidates'],
        warehouses={wid:dict(status=r['status'],model_id=r['forecast_model_id'],
            witness_verified=r['witness_verified'],elapsed_ms=r['elapsed_ms']) for wid,r in report['warehouses'].items()}),ensure_ascii=False))


if __name__=='__main__':main()
