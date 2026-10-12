"""Frozen departure manifest: route changes never invoke a packing solver.

Only manual whole-order signing releases boxes. Warehouse additions are
explicitly unsupported until an incremental insertion contract is implemented.
"""
from copy import deepcopy
from hashlib import sha256
import json
import loading_audit


def digest(manifest):
    return sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False,
                             allow_nan=False).encode()).hexdigest()


def freeze(result):
    manifest = dict(schema='fixed_loading_v1', items=deepcopy(result['items']),
                    vehicles=deepcopy(result['vehicles']),
                    placements={vid:deepcopy(r['loading']['placements'])
                                for vid,r in result['optimized']['routes'].items()},
                    sequences={vid:r['sequence'][:] for vid,r in result['optimized']['routes'].items()})
    return dict(manifest=manifest, sha256=digest(manifest),
                source_sha256=deepcopy(result['input_sha256']),
                source='manual_departure_simulation')


def check(frozen, vid, cargo, completed, sequence):
    """Audit original coordinates and replay removal; retain unscheduled cargo."""
    manifest = frozen['manifest']
    if manifest.get('schema') != 'fixed_loading_v1' or digest(manifest) != frozen['sha256']:
        raise ValueError('固定装载快照校验失败，禁止重摆或改写箱规')
    definitions = {x['id']:x for x in manifest['items']}
    original = manifest['placements'][vid]
    owner_orders = {definitions[p['id']]['order'] for p in original}
    done = {s['id'] for s in completed}
    if set(cargo) != owner_orders - done or done - owner_orders:
        raise ValueError('固定装载与货物归属/签收历史不一致')
    if len(sequence) != len(set(sequence)) or not set(sequence) <= set(cargo):
        return dict(passed=False, errors=['固定装载仅支持原车未签收配送任务'])
    placements = [p for p in original if definitions[p['id']]['order'] in cargo]
    items = [definitions[p['id']] for p in placements]
    # Retained cargo remains physically present even for incomplete candidates.
    order = list(sequence) + [oid for oid in manifest['sequences'][vid]
                              if oid in cargo and oid not in sequence]
    vehicle = manifest['vehicles'][vid]
    initial = loading_audit.audit(items, vehicle, order, placements)
    errors = list(initial['errors']); remaining = {p['id']:p for p in placements}
    steps = []
    for oid in sequence:
        group = sorted((p for p in remaining.values() if definitions[p['id']]['order']==oid),
                       key=lambda p:(-p['z'], -p['x'], p['y'], p['id']))
        steps.append(dict(order=oid, box_ids=[p['id'] for p in group]))
        for p in group:
            for q in remaining.values():
                if q['id']==p['id']:continue
                yz = (min(p['y']+p['dy'],q['y']+q['dy']) > max(p['y'],q['y'])
                      and min(p['z']+p['dz'],q['z']+q['dz']) > max(p['z'],q['z']))
                if yz and q['x'] >= p['x']+p['dx']:
                    errors.append(p['id']+'：原摆放卸货通道被挡住')
            del remaining[p['id']]
            state = loading_audit.audit([definitions[x] for x in remaining], vehicle,
                                       order, list(remaining.values()))
            errors.extend(state['errors'])
    return dict(passed=not errors, errors=errors, manifest_sha256=frozen['sha256'],
                placements=deepcopy(placements), unload_steps=steps,
                remaining_box_ids=sorted(remaining), boxes=len(placements),
                coordinates_repacked=False)


def verify_plan(plan, reference=None):
    frozen = plan.get('fixed_loading')
    if reference is not None and frozen != reference.get('fixed_loading'):
        raise ValueError('候选改变了固定装载快照，不能采用')
    if not frozen:return
    for vid,r in plan['routes'].items():
        q=r['anchor']
        check_result=check(frozen,vid,q['cargo'],q['completed'],
                           [s['id'] for s in r['stops'] if s['id'] not in {x['id'] for x in q['completed']}])
        if not check_result['passed']:
            raise ValueError('原摆放不支持候选卸货顺序：'+str(check_result['errors'][:2]))
