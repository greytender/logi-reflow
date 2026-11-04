"""Explicit paid API smoke test using constructed data; never prints credentials."""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deepseek_agent
import multiwarehouse as mw
import platform_agent

with TemporaryDirectory() as temp:
    store = mw.MultiWarehouseStore(Path(temp)/'live.sqlite3')
    task = store.create('DeepSeek模拟联调', mw.sample())
    planner = deepseek_agent.planner()
    if planner is None:
        raise SystemExit('未配置密钥，未发起请求。')
    task, result = platform_agent.AgentWorkflow(store, planner).run(task['id'], task['revision'], 'request',
        '请帮我给这批模拟订单排车，并解释当前候选的费用；不要确认方案。')
    assert 'dispatch' in result['workflows'], result['workflows']
    assert task['adopted_plan_id'] is None
    assert result['result']['compare']['audit']['passed']
    out = Path(__file__).resolve().parents[1]/'outputs/deepseek-agent-qa'
    out.mkdir(parents=True, exist_ok=True)
    evidence = dict(provider='DeepSeek', model=planner.model, workflows=result['workflows'],
        calls=result['model_calls'], message=result['message'], traces=result['traces'],
        adopted=False, data_kind='simulated', real_enterprise_orders_sent=False)
    (out/'live-verification.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(evidence, ensure_ascii=False))
