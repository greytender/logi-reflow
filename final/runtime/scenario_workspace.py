"""Temporary interactive scenes; reuse dispatch without touching live history."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from time import monotonic
import continuous
import csv_ai_events
import dispatch_handoff
import fixed_loading
from store import Conflict


class Workspace:
    def __init__(self, limit=12, ttl=7200):
        self.entries = {}
        self.lock = RLock()
        self.limit, self.ttl = limit, ttl

    def close(self):
        with self.lock:
            for entry in self.entries.values():
                entry['temp'].cleanup()
            self.entries.clear()

    def _prune(self):
        for sid, entry in list(self.entries.items()):
            if monotonic() - entry['touched'] > self.ttl:
                entry['temp'].cleanup()
                del self.entries[sid]

    def _entry(self, body, guard=True):
        self._prune()
        entry = self.entries.get(body.get('id'))
        if entry is None:
            raise Conflict('临时场景已失效（服务重启或闲置超过两小时），请重新开始；历史场景未改变')
        scene = entry['store'].read()
        if guard:
            continuous.DispatchStore.guard(scene, body)
        entry['touched'] = monotonic()
        return entry, scene

    @staticmethod
    def report(scene):
        plan = scene['live_plan']
        geometry = {}
        if plan.get('fixed_loading'):
            for vid, state in scene['states'].items():
                geometry[vid] = fixed_loading.check(plan['fixed_loading'], vid,
                    state['cargo'], state['completed'], state['remaining'])
        return dict(scene=scene, handoff=dispatch_handoff.report(scene),
                    geometry=geometry, local_isolated=True,
                    mode='fixed' if plan.get('fixed_loading') else 'csv',
                    persistence='temporary; expires after 2 idle hours or server restart')

    def start(self, body):
        if set(body) != {'input', 'mode'} or body['mode'] not in ('fixed', 'csv'):
            raise ValueError('开始需要input和fixed/csv模式；出发后不能切换模式')
        payload = body['input']
        if not isinstance(payload, dict) or set(payload) != {'csv_files', 'loading_profile'}:
            raise ValueError('需要已核对的同批五份输入')
        with self.lock:
            self._prune()
            if len(self.entries) >= self.limit:
                raise ValueError('临时场景数量达上限，请结束不用的场景后重试')
            tmp = TemporaryDirectory(prefix='logireflow-interactive-')
            try:
                store = continuous.DispatchStore(Path(tmp.name) / 'scene.sqlite3')
                args = dict(csv_files=payload['csv_files'])
                if body['mode'] == 'fixed':
                    args['loading_profile'] = payload['loading_profile']
                scene = store.act('start', args)
                result = self.report(scene)
            except Exception:
                tmp.cleanup()
                raise
            self.entries[scene['id']] = dict(temp=tmp, store=store, touched=monotonic())
            return result

    def act(self, body):
        action = body.get('action')
        if action not in {'refresh', 'event', 'adopt', 'discard', 'advance', 'receipt', 'end'}:
            raise ValueError('临时场景操作无效')
        with self.lock:
            entry, scene = self._entry(body, guard=action != 'refresh')
            if action == 'end':
                entry['temp'].cleanup()
                del self.entries[scene['id']]
                return dict(ended=True, local_isolated=True)
            if action == 'event':
                if body.get('reviewed') is not True:
                    raise ValueError('先核对事件字段，再生成候选')
                checked = csv_ai_events.validate_draft(body.get('draft'), scene)
                if not checked['ready']:
                    raise ValueError('字段尚不完整：' + '；'.join(checked['questions']))
                event = deepcopy(checked['event'])
                kind = 'urgent' if event.pop('type') == 'urgent_order' else 'window'
                scene = entry['store'].act('event', dict(id=scene['id'],
                    revision=scene['revision'], kind=kind, event=event))
            elif action == 'adopt':
                if body.get('reviewed') is not True:
                    raise ValueError('请独立核对候选路线、未安排订单与代价，再采用')
                scene = entry['store'].act(action, body)
            elif action != 'refresh':
                scene = entry['store'].act(action, body)
            return self.report(scene)

    def draft(self, body):
        with self.lock:
            _, scene = self._entry(body)
            return dict(**csv_ai_events.validate_draft(body.get('draft'), scene),
                        scene_id=scene['id'], scene_revision=scene['revision'],
                        parser='manual_structured_input',
                        status='字段校验，不保存候选、不采用；固定装载新货仍会被试算拒绝')

    def interpret(self, body):
        with self.lock:
            _, scene = self._entry(body)
        result = csv_ai_events.interpret(scene, body.get('message'), body.get('context'))
        with self.lock:
            self._entry(body)
        return result
