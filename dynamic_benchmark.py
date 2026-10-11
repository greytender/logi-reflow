"""Small synthetic comparisons, including separately marked no-stranded controls."""
import json
from pathlib import Path
import dynamic
import normal

if __name__=='__main__':
    cases=[]
    for vehicle in ('V1','V2','V3'):
        for time in ('08:00','08:16','08:25','09:00'):
            r=dynamic.run(normal.defaults(),f'{vehicle} 在 {time} 故障')
            cases.append(dict(vehicle=vehicle,time=time,impacted=r['impacted'],
                category='affected' if r['impacted'] else 'no_stranded_control',
                baseline={**r['baseline']['metrics'],'solve_ms':r['baseline']['solve_ms']},
                recovered={**r['recovered']['metrics'],'solve_ms':r['recovered']['solve_ms']}))
    report=dict(simulated=True,scope='3 trucks, 12 stores, synthetic default data; not scale or enterprise evidence',
                cases=cases,affected_cases=sum(c['category']=='affected' for c in cases),
                no_stranded_controls=sum(c['category']=='no_stranded_control' for c in cases))
    path=Path(__file__).resolve().parent/'evidence'/'dynamic-benchmark.json'
    path.parent.mkdir(exist_ok=True); path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2))
