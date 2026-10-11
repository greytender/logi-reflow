import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from store import Store, Conflict

def token(state):
    return {k:state[k] for k in ('session_id','revision','candidate_id')}

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'test.sqlite3'; self.store=Store(self.path)
    def tearDown(self):self.temp.cleanup()
    def test_refresh_and_restart_preserve_adoption(self):
        before=self.store.read(); state=self.store.decide('adopt',token(before))['session']
        after=Store(self.path).read()
        self.assertEqual(after['active_plan'],before['candidate_id']); self.assertEqual(after['status'],'adopted')
        self.assertEqual(after['revision'],state['revision']); self.assertEqual(after['logs'],state['logs'])
    def test_withdraw_does_not_erase_fault_or_restore_old_route(self):
        initial=self.store.read(); adopted=self.store.decide('adopt',token(initial))['session']
        withdrawn=self.store.decide('withdraw',token(adopted))['session']
        self.assertIsNone(withdrawn['active_plan']); self.assertEqual(withdrawn['status'],'withdrawn')
        self.assertEqual(withdrawn['events'],initial['events']); self.assertEqual(withdrawn['versions'][-1]['status'],'withdrawn')
    def test_stale_page_and_wrong_candidate_are_rejected(self):
        old=self.store.read(); new=self.store.calculate('V2 在 08:26 故障',token(old))['session']
        with self.assertRaises(Conflict):self.store.decide('adopt',token(old))
        wrong={**token(new),'candidate_id':old['candidate_id']}
        with self.assertRaises(Conflict):self.store.decide('adopt',wrong)
        self.assertEqual(self.store.read()['revision'],new['revision'])
    def test_clarification_does_not_change_plan_or_log(self):
        old=self.store.read(); answer=self.store.calculate('V2 故障',token(old))
        self.assertFalse(answer['parsed']['ready']); self.assertEqual(self.store.read(),old)
    def test_recalculation_preserves_old_candidate(self):
        old=self.store.read(); new=self.store.calculate('V2 在 08:26 故障',token(old))['session']
        self.assertEqual(new['versions'][1]['status'],'superseded'); self.assertEqual(new['versions'][1]['plan'],old['versions'][1]['plan'])
        self.assertEqual(new['candidate_id'],'P2'); self.assertIsNone(new['active_plan'])
    def test_same_message_reuses_fault_event(self):
        old=self.store.read(); new=self.store.calculate('V2 在 08:25 故障',token(old))['session']
        self.assertEqual(new['events'],old['events']); self.assertEqual(new['versions'][-1]['event_id'],'E1')
        self.assertEqual(new['candidate_id'],'P2')
    def test_adopted_scene_blocks_new_fault_and_reset_archives(self):
        adopted=self.store.decide('adopt',token(self.store.read()))['session']
        with self.assertRaises(Conflict):self.store.calculate('V1 在 08:25 故障',token(adopted))
        fresh=self.store.reset(token(adopted))['session']
        self.assertNotEqual(fresh['session_id'],adopted['session_id']); self.assertEqual(fresh['archived_count'],1)
        with self.store.connect() as db:
            archived=json.loads(db.execute('SELECT document FROM sessions WHERE id=?',(adopted['session_id'],)).fetchone()[0])
        self.assertEqual(archived['active_plan'],adopted['active_plan']); self.assertEqual(archived['logs'][-1]['action'],'archive')
        with self.assertRaises(Conflict):self.store.reset(token(adopted))
        self.assertEqual(len(self.store.archive()),2)
    def test_two_tabs_cannot_adopt_twice(self):
        expected=token(self.store.read())
        def adopt():
            try:self.store.decide('adopt',expected); return 'success'
            except Conflict:return 'stale'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:adopt(),range(2)))
        self.assertCountEqual(results,['success','stale'])
        self.assertEqual(sum(a['action']=='adopt' for a in self.store.read()['logs']),1)
    def test_failed_reset_leaves_original_session_intact(self):
        old=self.store.read()
        with patch('store.new_session',side_effect=RuntimeError('controlled failure')):
            with self.assertRaises(RuntimeError):self.store.reset(token(old))
        self.assertEqual(self.store.read(),old); self.assertEqual(len(self.store.archive()),1)

if __name__=='__main__':unittest.main()
