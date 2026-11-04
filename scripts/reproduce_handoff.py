import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import application
import execution
import integration_contract

def main():
    out=ROOT/'outputs/handoff-reproduction';out.mkdir(parents=True,exist_ok=True)
    s=application.ApplicationStore(out/'tasks.sqlite3');t=s.create('本地交接演练',application.sample())
    t=s.solve(t['id'],t['revision']);t=s.adopt(t['id'],t['revision'],t['plans'][-1]['id'],True)
    vid=next(iter(execution.view(t)['routes']))
    t=s.observe(t['id'],t['revision'],dict(vehicle=vid,kind='wait',node='WH',observed_at=600,source='simulated'),True)
    package=integration_contract.export_handoff(t)
    assert package==integration_contract.export_handoff(t)
    assert integration_contract.validate_handoff(package)['passed']
    assert package['dispatch_status']=='not_sent' and package['integration_status']=='not_connected'
    (out/'handoff.json').write_text(json.dumps(package,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Local handoff reproduced and validated; no external sending.')
if __name__=='__main__':main()
