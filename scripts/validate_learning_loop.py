"""Chronological simulated pickup learning with a frozen post-promotion holdout.

Every daily forecast is produced before that day's outcomes are recorded.
Training: days 1-10; promotion validation: 11-16; untouched evaluation: 17-28.
Stable bias, unbiased inputs and a day-22 distribution shift are all retained.
Prediction accuracy and empirical upper coverage are not dispatch benefits.
"""
from argparse import ArgumentParser
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from random import Random
from shutil import copyfile
import sys
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import forecast_learning as learning

SEED=20261105
SCENARIOS=('stable_bias','unbiased','day22_drift','day22_reversal')
STRATEGIES=('frozen','rolling_monitor')
SITES=('STORE_A','STORE_B','STORE_C')


def instant(day,hour=23,minute=59):
    return (datetime(2026,11,1,tzinfo=timezone(timedelta(hours=8)))+
            timedelta(days=day-1,hours=hour,minutes=minute)).isoformat()


def design():
    return dict(seed=SEED,days=28,sites=list(SITES),scenarios=list(SCENARIOS),
        training_days=[1,10],promotion_validation_days=[11,16],untouched_evaluation_days=[17,28],
        shift_day=22,strategies=list(STRATEGIES),
        model_policy='same day16 promoted model; frozen versus predefined trailing-three-day monitor with nominal rollback; no holdout retraining or threshold tuning',
        monitor_policy='after each day17-28 outcome reveal, inspect max(17,day-2)..day; auto_rollback=True; changes subsequent predictions only',
        coverage_warning_policy='core predefined gate: at least6 records and empirical upper coverage below0.8; persist requires_review for subsequent predictions, no automatic clearing or interval recalibration',
        reversal_scope='additional directional stress after initial three-scenario diagnosis; original upward-drift case retained unchanged',
        ordering='predict all sites for the day, then reveal and record outcomes; train/validate only after cutoff',
        source='fully simulated; reviewed flags certify generator records only, not enterprise receipt authenticity')


def inputs(scenario):
    if scenario not in SCENARIOS:raise ValueError('unknown predeclared scenario')
    rng=Random(SEED)
    days=[]
    for day in range(1,29):
        daily=[]
        for index,site in enumerate(SITES):
            nominal=dict(cartons=8+3*index+day%3,totes=2+2*index+day%2)
            bias=dict(cartons=4+2*index,totes=2+index)
            if scenario=='unbiased':bias={key:0 for key in learning.TARGETS}
            elif scenario=='day22_drift' and day>=22:
                bias={key:2*value+4 for key,value in bias.items()}
            elif scenario=='day22_reversal' and day>=22:
                bias={key:-value for key,value in bias.items()}
            actual={key:max(0,nominal[key]+bias[key]+rng.choice((-1,0,0,1))) for key in learning.TARGETS}
            daily.append(dict(day=day,store_id=site,nominal=nominal,actual=actual))
        days.append(daily)
    return days


def metrics(rows):
    cells=2*len(rows)
    if not cells:return dict(observations=0,nominal_mae_units=None,learned_mae_units=None,risk_upper_coverage=None,requires_review_observations=0)
    return dict(observations=len(rows),quantity_cells=cells,
        nominal_mae_units=sum(abs(row['nominal'][key]-row['actual'][key]) for row in rows for key in learning.TARGETS)/cells,
        learned_mae_units=sum(abs(row['forecast']['expected'][key]-row['actual'][key]) for row in rows for key in learning.TARGETS)/cells,
        risk_upper_coverage=sum(row['actual'][key]<=row['forecast']['risk_upper'][key] for row in rows for key in learning.TARGETS)/cells,
        learned_source_observations=sum(row['forecast']['source']=='learned' for row in rows),
        requires_review_observations=sum(bool(row['forecast'].get('requires_review')) for row in rows))


def run_scenario(scenario,db_path,strategy='frozen'):
    if strategy not in STRATEGIES:raise ValueError('unknown predeclared monitoring strategy')
    store=learning.ForecastStore(db_path)
    warehouse='SIM_'+scenario.upper()
    events=[];evaluation=[];candidate=None;validation=None
    for daily in inputs(scenario):
        day=daily[0]['day'];predictions={}
        for observation in daily:
            site=observation['store_id']
            model=store.active_model(warehouse,'simulated')
            forecast=learning.predict(model,warehouse,'simulated',site,observation['nominal'],instant(day,8))
            predictions[site]=forecast
            events.append(dict(kind='predict',day=day,store_id=site,at=instant(day,8),
                model_id=forecast['model_id'],expected=forecast['expected'],risk_upper=forecast['risk_upper'],
                requires_review=forecast.get('requires_review',False)))
        for observation in daily:
            site=observation['store_id']
            record=dict(id=f'{scenario}-{site}-{day}',warehouse_id=warehouse,store_id=site,
                data_kind='simulated',source='simulated',verified=True,
                reference=f'fixed-seed synthetic generator {SEED}; scenario {scenario}; not an enterprise receipt',
                decision_at=instant(day,8),observed_at=instant(day,16),available_at=instant(day,17),
                nominal=observation['nominal'],actual=observation['actual'])
            store.record(record)
            events.append(dict(kind='reveal_and_record',day=day,store_id=site,at=instant(day,17),
                actual=observation['actual'],record_id=record['id']))
            if day>=17:evaluation.append({**observation,'forecast':predictions[site]})
        if day==10:
            candidate=store.train(warehouse,'simulated',instant(10))
            events.append(dict(kind='train',day=day,at=instant(10),model_id=candidate['id'],
                count=candidate['training_count']))
        if day==16:
            validation=store.validate(candidate['id'],instant(11,0),instant(16))
            if validation['passed']:store.activate(candidate['id'])
            events.append(dict(kind='promotion_gate',day=day,at=instant(16),
                model_id=candidate['id'],promoted=validation['passed']))
        if day>=17 and strategy=='rolling_monitor':
            monitored=store.monitor(warehouse,'simulated',instant(max(17,day-2),0),
                                    instant(day),auto_rollback=True)
            events.append(dict(kind='monitor_after_reveal',day=day,at=instant(day),result=monitored))
    for day in range(1,29):
        daily_events=[event for event in events if event['day']==day]
        first_reveal=next(index for index,event in enumerate(daily_events) if event['kind']=='reveal_and_record')
        assert all(event['kind']=='predict' for event in daily_events[:first_reveal])
        assert first_reveal==len(SITES)
    assert candidate['training_count']==30
    assert validation['validation_count']==18
    assert len(evaluation)==36
    trained_ids=set(candidate['training_record_ids'])
    assert not any(f'{scenario}-{site}-{day}' in trained_ids for site in SITES for day in range(11,29))
    active=store.active_model(warehouse,'simulated')
    restarted=learning.ForecastStore(db_path).active_model(warehouse,'simulated')
    assert active==restarted
    return dict(scenario=scenario,strategy=strategy,data_kind='simulated',model=candidate,
        promotion_validation=validation,activated=validation['passed'],active_model=active,
        untouched_evaluation=metrics(evaluation),
        pre_shift_evaluation=metrics([row for row in evaluation if row['day']<22]),
        post_shift_evaluation=metrics([row for row in evaluation if row['day']>=22]),
        per_site={site:metrics([row for row in evaluation if row['store_id']==site]) for site in SITES},
        forecasts_and_revealed_actuals=evaluation,events=events,
        rollback_days=[event['day'] for event in events if event['kind']=='monitor_after_reveal'
                       and event['result']['rolled_back']],
        coverage_warning_days=[event['day'] for event in events if event['kind']=='monitor_after_reveal'
                               and event['result'].get('coverage_warning',False)],
        audit=dict(all_daily_predictions_before_reveal=True,training_count=30,
            validation_count=18,untouched_evaluation_count=36,restart_preserves_active_version=True))


def build(output_dir):
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    specification=design()
    manifest=dict(design=specification,design_sha256=sha256(learning.encode(specification).encode()).hexdigest(),
        input_sha256={scenario:sha256(learning.encode(inputs(scenario)).encode()).hexdigest() for scenario in SCENARIOS},
        provenance='manifest written before any training/promotion/evaluation')
    (output_dir/'learning-loop-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    results=[]
    with TemporaryDirectory(prefix='logireflow-learning-validation-') as directory:
        for scenario in SCENARIOS:
            for strategy in STRATEGIES:
                db=Path(directory)/(scenario+'-'+strategy+'.sqlite3')
                result=run_scenario(scenario,db,strategy)
                snapshot=output_dir/('learning-loop-'+scenario+'-'+strategy+'.sqlite3')
                copyfile(db,snapshot)
                result['database_snapshot']=str(snapshot)
                results.append(result)
    report=dict(schema='logireflow.learning-loop-validation.v1',manifest=manifest,scenarios=results,
        scope='fully simulated chronological forecasting validation; no dispatch, enterprise receipt or cost-saving claim',
        caveats=['Promotion validation is used to select the model, not reported as untouched final performance.',
            'Empirical upper coverage is one-sided and has no guaranteed future coverage.',
            'A coverage warning flags subsequent predictions for human review; it does not repair or guarantee the risk interval.',
            'The day22 distribution shift is retained even if the previously approved model becomes worse.',
            'No dispatch candidate was generated here; forecast MAE does not prove loading, route or operating benefit.',
            'Scenario families share a generator and are not independent enterprise observations.'])
    output=output_dir/'learning-loop.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'outputs/search-diagnosis/learning')
    args=parser.parse_args();report=build(args.output_dir)
    print(json.dumps(dict(output_dir=str(args.output_dir),scenarios=[dict(
        scenario=result['scenario'],strategy=result['strategy'],activated=result['activated'],
        rollback_days=result['rollback_days'],coverage_warning_days=result['coverage_warning_days'],
        validation=result['promotion_validation'],
        untouched=result['untouched_evaluation'],pre_shift=result['pre_shift_evaluation'],
        post_shift=result['post_shift_evaluation']) for result in report['scenarios']]),ensure_ascii=False))
