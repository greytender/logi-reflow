"""Reproduce the multiwarehouse contract limit with explicit synthetic inputs."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import business
import multiwarehouse as mw


def dataset():
    data = dict(schema_version=mw.SCHEMA, warehouses=[], orders=[], vehicles=[], legs={})
    for index in range(1, 9):
        wid = f'WH_{index}'
        data['warehouses'].append(dict(id=wid, name=f'边界构造仓{index}',
            settings=dict(depot_id=wid, data_kind='simulated', travel_source='合同边界测试的构造有向道路，非真实路况', window_rule='finish'),
            business=business.defaults()))
        vids = [f'{wid}_V_{i}' for i in range(1, 5)]
        for vid, cap, volume in zip(vids, [400, 600, 800, 1000], [3, 4, 6, 8]):
            data['vehicles'].append(dict(id=vid, warehouse_id=wid, max_weight_kg=cap, max_volume_m3=volume,
                fixed_cost=40, cost_per_km=2, shift_start=510, shift_end=1080))
        nodes = [wid]
        for n in range(1, 13):
            sid = f'{wid}_S_{n}'; nodes.append(sid)
            data['orders'].append(dict(id=sid, warehouse_id=wid, open=510, due=1080, close=1080,
                service_minutes=8, allowed_vehicles=vids, pickup=dict(cartons=20, totes=3),
                lines=[dict(sku='BOX', quantity=10, unit_weight_kg=7, unit_volume_m3=.072)]))
        for i, a in enumerate(nodes):
            for j, b in enumerate(nodes):
                if a != b: data['legs'][a+'>'+b] = dict(distance_km=2+abs(i-j)%9, travel_minutes=3+abs(i-j)%11)
    return data


if __name__ == '__main__':
    with TemporaryDirectory() as directory:
        store = mw.MultiWarehouseStore(Path(directory)/'batch.sqlite3')
        start = perf_counter(); task = store.create('8仓96任务边界验证', dataset())
        created_ms = (perf_counter()-start)*1000; start = perf_counter()
        task = store.solve(task['id'], task['revision']); solve_ms = (perf_counter()-start)*1000
        plan = task['plans'][-1]
        assert plan['audit']['passed'] and plan['summary']['optimized']['served'] == 96
        assert plan['summary']['optimized']['unserved'] == 0
        task = store.adopt(task['id'], task['revision'], plan['id'], True)
        assert len(task['execution_tasks']) == 8
        report = dict(data_kind='simulated', warehouses=8, tasks=96, available_vehicles=32,
            directed_legs=len(task['data']['legs']), audit=plan['audit'], summary=plan['summary'],
            create_ms=round(created_ms, 2), solve_and_persist_ms=round(solve_ms, 2), confirmed_locally=True,
            boundaries=['仅构造数据合同边界验证', '非企业全量、吞吐基准或人力节省验证', '每仓仍为分仓启发式求解，非跨仓全局优化'])
        output = ROOT/'evidence'/'multiwarehouse-capacity-verification.json'
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
