import unittest
from copy import deepcopy
import test_urgent
import normal
import timewindow


class WindowTests(unittest.TestCase):
    def audit(self,r):
        # Independent conservation/cost/window audit shared with urgent tests.
        adapted=deepcopy(r);adapted['initial']['data']=r['updated_data']
        test_urgent.UrgentTests.audit(self,adapted,include_new=False,allow_retained=True)

    def test_delayed_opening_ownership_and_completed(self):
        d=normal.defaults();before=deepcopy(d);e=dict(time=505,store='S08',open=555,due=570,close=690)
        r=timewindow.run(d,e);self.audit(r);self.assertEqual(d,before)
        self.assertEqual(r['recovered']['metrics']['served'],12)
        self.assertLess(r['recovered']['metrics']['late_minutes'],r['baseline']['metrics']['late_minutes'])
        for name in ('baseline','recovered'):
            stop=next(s for rr in r[name]['routes'].values() for s in rr['stops'] if s['id']=='S08')
            self.assertGreaterEqual(stop['arrival'],555)

    def test_unreachable_keeps_goods_and_other_work(self):
        e=dict(time=505,store='S08',open=480,due=505,close=505)
        r=timewindow.run(normal.defaults(),e)
        self.audit(r)
        for name in ('baseline','recovered'):
            p=r[name];self.assertEqual(p['unserved_ids'],['S08']);self.assertIn('S08',p['metrics']['changed_ids'])
            self.assertEqual(p['routes']['V1']['retained_ids'],['S08'])
            future=p['routes']['V1']['stops'][len(r['states']['V1']['completed']):]
            self.assertAlmostEqual(future[-1]['weight_kg'],120)
            self.assertAlmostEqual(future[-1]['volume_m3'],.8)
            delivered=[s['id'] for rr in p['routes'].values() for s in rr['stops']]
            self.assertEqual(len(delivered),11);self.assertNotIn('S08',delivered)

    def test_completed_and_invalid_windows_rejected(self):
        e=dict(time=505,store='S08',open=555,due=570,close=690)
        for change in (dict(store='S05'),dict(open=700),dict(close=560),dict(time=True),dict(store='S99'),dict(due=float('inf'))):
            with self.subTest(change=change),self.assertRaises(ValueError):timewindow.run(normal.defaults(),{**e,**change})

    def test_unchanged_window_and_changed_quantity(self):
        d=normal.defaults();o=d['orders'][7];e=dict(time=505,store='S08',**{k:o[k] for k in ('open','due','close')})
        r=timewindow.run(d,e);self.audit(r);self.assertEqual(r['recovered']['metrics']['changed_orders'],0)
        d['orders'][6]['quantity']=40;e['time']=480;r=timewindow.run(d,e);self.audit(r)


if __name__=='__main__':unittest.main()
