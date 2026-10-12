from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import continuous
import matrix_dispatch as md
import pilot_benchmark
import verify_matrix_dispatch


def small_files(directory):
    b=pilot_benchmark.sample(3)
    b['vehicles']=b['vehicles'][:2]
    for i,v in enumerate(b['vehicles']): v.update(shift_start='07:00' if i==0 else '09:00',shift_end='17:00')
    for o in b['orders']:
        o.update(open='07:00',due='11:00',close='16:00',service_minutes=11,allowed_vehicles='TRUCK_1')
    for i,r in enumerate(b['travel']): r.update(distance_km=i+1,travel_minutes=i+3)
    pilot_benchmark.write_bundle(b,directory)
    return {name:(Path(directory)/name).read_text(encoding='utf-8') for name in md.FILES}


class MatrixDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.files=small_files(self.tmp.name)
        self.store=continuous.DispatchStore(Path(self.tmp.name)/'test.db')
        self.scene=self.store.act('start',dict(csv_files=self.files))
        self.vid='TRUCK_1'
        self.first=self.scene['live_plan']['routes'][self.vid]['stops'][0]['id']

    def act(self,action,**extra):
        self.scene=self.store.act(action,dict(id=self.scene['id'],revision=self.scene['revision'],**extra))
        verify_matrix_dispatch.audit(self.scene)
        if self.scene['candidate']:
            candidate=deepcopy(self.scene);version=candidate['versions'][-1];candidate['live_plan']=version['plan']
            candidate['states']=version['states'];candidate['cargo']={o['id']:'warehouse' for o in version['plan']['data']['orders']}
            for vid,q in candidate['states'].items():
                for oid in q['cargo']:candidate['cargo'][oid]=vid
                for stop in q['completed']:
                    if not md.is_pickup(stop['id']):candidate['cargo'][stop['id']]='delivered'
            verify_matrix_dispatch.audit(candidate)
        return self.scene

    def receipt(self,kind,time,oid=None):
        return self.act('receipt',vehicle=self.vid,order=oid or self.first,receipt=kind,time=time,source='manual_simulation')

    def arrival(self):
        return self.scene['live_plan']['routes'][self.vid]['stops'][0]['road_arrival']

    def test_import_retains_contract_and_shift(self):
        p=self.scene['live_plan'];self.assertEqual(self.scene['clock'],420)
        self.assertEqual(len(p['data']['orders'][0]['lines']),10)
        self.assertEqual(len(p['data']['legs']),12)
        self.assertEqual(self.scene['states']['TRUCK_2']['available'],540)
        self.assertEqual(set(self.scene['cargo'].values()),{self.vid})

    def test_progress_directed_leg_not_coordinates_and_no_auto_sign(self):
        p=self.scene['live_plan'];stop=p['routes'][self.vid]['stops'][0];eta=stop['road_arrival']
        road=p['data']['legs']['WH>'+self.first]
        self.act('advance',time=420+(eta-420)/2)
        q=self.scene['states'][self.vid]
        self.assertAlmostEqual(q['traveled'],road['distance_km']/2)
        self.assertAlmostEqual(q['transit']['travel_minutes'],road['travel_minutes']/2)
        self.act('advance',time=eta+100)
        self.assertEqual(self.scene['states'][self.vid]['node'],self.first)
        self.assertEqual(len(self.scene['receipts']),0)
        self.assertEqual(self.scene['cargo'][self.first],self.vid)

    def test_service_time_receipt_and_restart(self):
        eta=self.arrival();original=deepcopy(self.scene['versions'][0]['plan'])
        self.receipt('arrived',eta);self.receipt('unloading',eta)
        before=self.store.read()
        with self.assertRaisesRegex(ValueError,'11'):self.receipt('signed',eta+5)
        self.assertEqual(before,self.store.read())
        self.receipt('signed',eta+11)
        self.assertEqual(self.scene['cargo'][self.first],'delivered')
        self.assertEqual(original,self.scene['versions'][0]['plan'])
        self.assertEqual(self.scene,continuous.DispatchStore(self.store.path).read())
        self.assertEqual(self.scene['live_plan']['routes']['TRUCK_2']['cost'],0)

    def test_complete_all_stops_and_directed_return(self):
        while self.scene['states'][self.vid]['remaining']:
            q=self.scene['states'][self.vid];oid=q['remaining'][0]
            stop=next(s for s in self.scene['live_plan']['routes'][self.vid]['stops'] if s['id']==oid)
            t=max(self.scene['clock'],stop['arrival'])
            for k,tm in [('arrived',t),('unloading',t),('signed',t+11)]:self.receipt(k,tm,oid)
        expected=self.scene['live_plan']['routes'][self.vid]['distance']
        self.act('advance',time=self.scene['clock']+1)
        self.act('advance',time=900)
        self.assertEqual(set(self.scene['cargo'].values()),{'delivered'})
        self.assertAlmostEqual(self.scene['states'][self.vid]['traveled'],expected)
        self.assertEqual(self.scene['states'][self.vid]['node'],'WH')
        returned=self.scene['live_plan']['routes'][self.vid]['return_at']
        self.act('advance',time=1100)
        self.assertFalse(self.scene['live_plan'].get('needs_replan',False))
        self.assertEqual(self.scene['live_plan']['routes'][self.vid]['return_at'],returned)

    def test_failed_import_rolls_back_and_preserves_old_scene(self):
        before=self.store.read();files=deepcopy(self.files);files['travel.csv']='bad'
        with self.assertRaises(ValueError):self.act('start',csv_files=files)
        self.assertEqual(before,self.store.read())

    def test_zero_leg_does_not_arrive_before_shift(self):
        p=deepcopy(self.scene['live_plan']);r=p['routes'][self.vid];r['anchor']['available']=540
        p['data']['legs']['WH>'+self.first].update(distance_km=0,travel_minutes=0)
        q=continuous.snapshot(p,500)[self.vid]
        self.assertIsNone(q['pending']);self.assertEqual(q['node'],'WH')

    def test_late_signature_kept_when_shift_infeasible(self):
        self.receipt('arrived',1015);self.receipt('unloading',1015);self.receipt('signed',1026)
        self.assertEqual(self.scene['cargo'][self.first],'delivered')
        self.assertTrue(self.scene['live_plan']['needs_replan'])

    def test_inflight_next_stop_is_locked(self):
        p=self.scene['live_plan'];q=continuous.snapshot(p,421)[self.vid]
        v=p['data']['vehicles'][0];orders={o['id']:o for o in p['data']['orders']}
        self.assertIsNone(continuous.future(q['remaining'][1:]+q['remaining'][:1],q,v,orders,p['routes'][self.vid],p['data']))

    def test_window_after_signature_preserves_history_and_replay(self):
        eta=self.arrival();self.receipt('arrived',eta);self.receipt('unloading',eta);self.receipt('signed',eta+11)
        history=deepcopy(self.scene['states'][self.vid]['completed']);target=self.scene['states'][self.vid]['remaining'][-1]
        self.act('event',kind='window',event=dict(time=self.scene['clock'],store=target,open=600,due=650,close=960))
        self.assertTrue(self.store.compare()['available'])
        candidate=self.scene['candidate'];self.act('adopt',candidate=candidate)
        self.assertEqual(history,self.scene['states'][self.vid]['completed'])
        self.assertEqual(self.scene['cargo'][self.first],'delivered')

    def test_finish_rule_rejects_late_completion_not_just_arrival(self):
        p=self.scene['live_plan'];q=self.scene['states'][self.vid];v=p['data']['vehicles'][0]
        orders={o['id']:deepcopy(o) for o in p['data']['orders']};orders[self.first]['close']=self.arrival()+5
        self.assertIsNone(continuous.future(q['remaining'],q,v,orders,p['routes'][self.vid],p['data']))
        data=deepcopy(p['data']);data['settings']['window_rule']='arrival'
        self.assertIsNotNone(continuous.future(q['remaining'],q,v,orders,p['routes'][self.vid],data))

    def test_candidate_invalidated_by_receipt_and_signed_window_rejected(self):
        eta=self.arrival();target=self.scene['states'][self.vid]['remaining'][-1]
        self.act('event',kind='window',event=dict(time=eta,store=target,open=600,due=650,close=960))
        self.receipt('arrived',eta);self.assertIsNone(self.scene['candidate'])
        self.receipt('unloading',eta);self.receipt('signed',eta+11)
        with self.assertRaisesRegex(ValueError,'已交付'):
            self.act('event',kind='window',event=dict(time=eta+11,store=self.first,open=600,due=650,close=960))

    def urgent(self,**extra):
        event=dict(time=self.scene['clock'],store='STORE_002',due=800,close=960,
            lines=[dict(sku='NEW_A',quantity=2,unit_weight_kg=7,unit_volume_m3=.03),
                   dict(sku='NEW_B',quantity=3,unit_weight_kg=9,unit_volume_m3=.05)])
        event.update(extra);return self.act('event',kind='urgent',event=event)

    def test_urgent_explicit_sku_and_warehouse_ownership(self):
        self.urgent();self.assertNotIn('E01',self.scene['cargo'])
        self.act('adopt',candidate=self.scene['candidate'])
        order=self.scene['live_plan']['data']['orders'][-1]
        self.assertEqual(order['unit_weight_kg'],41)
        self.assertAlmostEqual(order['unit_volume_m3'],.21)
        self.assertEqual(self.scene['cargo']['E01'],'warehouse')
        route=self.scene['live_plan']['routes'][self.vid]
        self.assertLess(route['sequence'].index('warehouse:E01'),route['sequence'].index('E01'))

    def test_urgent_entire_chain_and_loading_resume_window(self):
        self.urgent();self.act('adopt',candidate=self.scene['candidate']);loading_seen=False
        for _ in range(8):
            q=self.scene['states'][self.vid]
            if not q['remaining']:break
            oid=q['remaining'][0];stop=next(s for s in self.scene['live_plan']['routes'][self.vid]['stops'][len(q['completed']):] if s['id']==oid)
            t=max(self.scene['clock'],stop['arrival'])
            if md.is_pickup(oid):
                self.act('advance',time=t+3);loading_seen=True
                end=self.scene['states'][self.vid]['loading']['finish']
                self.assertEqual(self.scene['states'][self.vid]['loading']['task_id'],'warehouse:E01')
                self.assertEqual(self.scene,continuous.DispatchStore(self.store.path).read())
                self.assertEqual(self.scene['cargo']['E01'],'warehouse')
                self.act('event',kind='window',event=dict(time=t+3,store='E01',open=t,due=800,close=960))
                self.assertTrue(self.store.compare()['available'])
                self.act('adopt',candidate=self.scene['candidate'])
                self.assertEqual(self.scene['states'][self.vid]['loading']['finish'],end)
                self.act('advance',time=end)
                self.assertEqual(self.scene['cargo']['E01'],self.vid)
            else:
                for kind,tm in [('arrived',t),('unloading',t),('signed',t+11)]:self.receipt(kind,tm,oid)
        self.assertTrue(loading_seen);self.assertEqual(set(self.scene['cargo'].values()),{'delivered'})
        self.assertEqual(len(self.scene['receipts']),12)

    def test_infeasible_urgent_retains_all_original_cargo(self):
        original=deepcopy(self.scene['cargo'])
        self.urgent(lines=[dict(sku='HUGE',quantity=1000,unit_weight_kg=100,unit_volume_m3=1)])
        version=self.scene['versions'][-1];self.assertEqual(version['plan']['unserved_ids'],['E01'])
        with self.assertRaisesRegex(ValueError,'不完整'):self.act('adopt',candidate=self.scene['candidate'])
        self.assertEqual(self.store.read()['cargo'],original)

    def test_duplicate_sku_and_unknown_destination_rejected(self):
        before=self.store.read();row=dict(sku='X',quantity=1,unit_weight_kg=2,unit_volume_m3=.1)
        for extra in [dict(lines=[row,row]),dict(store='UNKNOWN'),dict(lines=[]),dict(lines=[dict(row,quantity=True)])]:
            with self.assertRaises(ValueError):self.urgent(**extra)
            self.assertEqual(before,self.store.read())

    def test_loading_late_start_gets_full_eight_minutes(self):
        self.urgent();self.act('adopt',candidate=self.scene['candidate'])
        p=deepcopy(self.scene['live_plan']);r=p['routes'][self.vid];r['sequence']=['warehouse:E01','E01']
        r['stops']=[s for s in r['stops'] if s['id'] in ('warehouse:E01','E01')]
        r['anchor'].update(node='WH',available=800,cargo=[],completed=[],transit=None,pending=None,loading=None)
        q=continuous.snapshot(p,800)[self.vid]
        self.assertEqual(q['loading']['finish'],808);self.assertNotIn('E01',q['cargo'])

    def test_independent_audit_detects_forged_cargo_time_and_cost(self):
        for path in ('cargo','cost','time'):
            bad=deepcopy(self.scene)
            if path=='cargo':bad['cargo'][self.first]='TRUCK_2'
            elif path=='cost':bad['live_plan']['metrics']['cost']+=1
            else:bad['live_plan']['routes'][self.vid]['stops'][0]['finish']+=1
            with self.assertRaises(AssertionError):verify_matrix_dispatch.audit(bad)

    def warehouse_plan(self):
        self.urgent();self.act('adopt',candidate=self.scene['candidate'])
        p=deepcopy(self.scene['live_plan']);r=p['routes'][self.vid]
        r['sequence']=['warehouse:E01','E01'];r['stops']=[s for s in r['stops'] if s['id'] in ('warehouse:E01','E01')]
        r['anchor'].update(node='WH',available=800,cargo=[],completed=[],transit=None,pending=None,loading=None)
        return p

    def test_pickup_identity_locked_during_loading_and_restart(self):
        p=self.warehouse_plan();r=p['routes'][self.vid]
        self.assertEqual(r['stops'][0]['task_id'],'warehouse:E01')
        q=md.snapshot(p,803)[self.vid]
        self.assertEqual(q['loading'],dict(task_id='warehouse:E01',load_ids=['E01'],arrival=800,finish=808))
        orders={o['id']:o for o in p['data']['orders']};v=p['data']['vehicles'][0]
        for field,value in [('task_id','warehouse:E02'),('load_ids',[])]:
            bad=deepcopy(r);bad['stops'][0][field]=value
            self.assertIsNone(md.future(['warehouse:E01','E01'],q,v,orders,bad,p['data']))
            altered=deepcopy(p);altered['routes'][self.vid]['anchor']=q
            altered['routes'][self.vid]['stops'][0][field]=value
            with self.assertRaises(ValueError):md.snapshot(altered,808)
        resumed=deepcopy(p);resumed['routes'][self.vid]['anchor']=q
        self.assertNotIn('E01',md.snapshot(resumed,807)[self.vid]['cargo'])
        done=md.snapshot(resumed,808)[self.vid]
        self.assertIn('E01',done['cargo']);self.assertEqual(done['completed'][0]['finish'],808)
        self.assertEqual(self.scene,continuous.DispatchStore(self.store.path).read())

    def test_pickup_rejects_duplicate_lot_and_cross_vehicle_delivery(self):
        p=self.warehouse_plan()
        for fault in ('duplicate_task','duplicate_lot','other_truck','unpicked_cargo','original_goods'):
            bad=deepcopy(p);r=bad['routes'][self.vid];other=bad['routes']['TRUCK_2']
            if fault in ('duplicate_task','duplicate_lot'):
                duplicate=deepcopy(r['stops'][0])
                if fault=='duplicate_lot':duplicate['task_id']='warehouse:another'
                other['stops'].append(duplicate);other['sequence'].append(duplicate['id'])
            elif fault=='other_truck':
                other['stops'].append(r['stops'].pop());other['sequence'].append(r['sequence'].pop())
            elif fault=='unpicked_cargo':r['anchor']['cargo']=['E01']
            else:r['stops'][0]['load_ids']=[self.first]
            with self.assertRaises(ValueError):md.snapshot(bad,800)
            forged=deepcopy(self.scene);forged['live_plan']=bad
            if fault=='unpicked_cargo':
                forged['states'][self.vid]=deepcopy(r['anchor']);forged['cargo']['E01']=self.vid
            with self.assertRaises(AssertionError):verify_matrix_dispatch.audit(forged)

    def test_legacy_anonymous_csv_pickup_resumes_without_migration(self):
        p=self.warehouse_plan();r=p['routes'][self.vid];r['stops'][0].pop('task_id');r['stops'][0]['id']='W';r['sequence'][0]='W'
        r['anchor']['loading']=dict(arrival=800,finish=808)
        before=deepcopy(p)
        q=md.snapshot(p,803)[self.vid]
        self.assertEqual(q['loading']['task_id'],'warehouse:E01')
        orders={o['id']:o for o in p['data']['orders']}
        future=md.future(['W','E01'],q,p['data']['vehicles'][0],orders,r,p['data'])
        self.assertEqual(future['stops'][0]['finish'],808)
        self.assertEqual(p,before)

    def next_task(self):
        q=self.scene['states'][self.vid]
        oid=q['remaining'][0]
        stop=self.scene['live_plan']['routes'][self.vid]['stops'][len(q['completed'])]
        t=max(self.scene['clock'],stop['arrival'])
        if md.is_pickup(oid):
            self.act('advance',time=max(t,stop['finish']))
        else:
            for k,tm in [('arrived',t),('unloading',t),('signed',t+11)]:self.receipt(k,tm,oid)

    def first_loading(self):
        while not md.is_pickup(self.scene['states'][self.vid]['remaining'][0]):self.next_task()
        q=self.scene['states'][self.vid]
        stop=self.scene['live_plan']['routes'][self.vid]['stops'][len(q['completed'])]
        self.act('advance',time=stop['arrival']+3)
        return self.scene['states'][self.vid]['loading']['finish']

    def test_two_urgent_full_chain_at_four_first_order_phases(self):
        for phase in ('before_loading','loading','loaded','signed'):
            with self.subTest(phase=phase):
                self.act('start',csv_files=self.files)
                self.urgent();self.act('adopt',candidate=self.scene['candidate'])
                end=None
                if phase!='before_loading':
                    end=self.first_loading()
                    if phase in ('loaded','signed'):self.act('advance',time=end)
                    if phase=='signed':
                        while self.scene['cargo']['E01']!='delivered':self.next_task()
                history=deepcopy(self.scene['states'][self.vid]['completed']);cargo=deepcopy(self.scene['cargo'])
                self.urgent(store='STORE_003')
                self.assertEqual(self.scene['cargo'],cargo);self.assertNotIn('E02',self.scene['cargo'])
                self.act('adopt',candidate=self.scene['candidate'])
                self.assertEqual(self.scene['cargo']['E02'],'warehouse')
                self.assertEqual(self.scene['states'][self.vid]['completed'],history)
                if phase=='loading':
                    self.assertEqual(self.scene['states'][self.vid]['loading']['finish'],end)
                    self.act('advance',time=end)
                    self.assertEqual(self.scene['cargo']['E01'],self.vid)
                    self.assertEqual(self.scene['cargo']['E02'],'warehouse')
                self.assertEqual(self.scene,continuous.DispatchStore(self.store.path).read())
                while self.scene['states'][self.vid]['remaining']:self.next_task()
                self.assertEqual(set(self.scene['cargo'].values()),{'delivered'})
                self.assertEqual(len(self.scene['receipts']),15)
                pickups=[s for s in self.scene['states'][self.vid]['completed'] if md.is_pickup(s['id'])]
                self.assertEqual({s['id'] for s in pickups},{'warehouse:E01','warehouse:E02'})
                for s in pickups:self.assertEqual(s['finish']-s['arrival'],8)
                self.assertGreaterEqual(pickups[1]['arrival'],pickups[0]['finish'])

    def test_two_pickups_resume_only_current_loading_and_late_second_starts_full_duration(self):
        self.urgent();self.act('adopt',candidate=self.scene['candidate']);end=self.first_loading()
        self.urgent();self.act('adopt',candidate=self.scene['candidate'])
        q=self.scene['states'][self.vid];p=self.scene['live_plan'];orders={o['id']:o for o in p['data']['orders']}
        r=md.future(['warehouse:E01','warehouse:E02','E01','E02'],q,p['data']['vehicles'][0],orders,p['routes'][self.vid],p['data'])
        self.assertIsNotNone(r);self.assertEqual(r['stops'][0]['finish'],end)
        self.assertEqual(r['stops'][1]['finish'],end+8)
        self.assertIsNone(md.future(['warehouse:E02','warehouse:E01','E01','E02'],q,p['data']['vehicles'][0],orders,p['routes'][self.vid],p['data']))
        limited=deepcopy(p['data']['vehicles'][0]);limited['max_weight_kg']=q['weight_kg']+41
        self.assertIsNone(md.future(['warehouse:E01','warehouse:E02','E01','E02'],q,limited,orders,p['routes'][self.vid],p['data']))
        self.assertIsNotNone(md.future(['warehouse:E01','E01','warehouse:E02','E02'],q,limited,orders,p['routes'][self.vid],p['data']))
        while self.scene['states'][self.vid]['remaining'][0]!='warehouse:E02':self.next_task()
        late=deepcopy(self.scene['live_plan']);anchor=late['routes'][self.vid]['anchor']
        anchor.update(node='WH',available=850,transit=None,pending=None,loading=None)
        started=md.snapshot(late,850)[self.vid]
        self.assertEqual(started['loading']['finish'],858);self.assertNotIn('E02',started['cargo'])

    def test_second_urgent_infeasible_and_third_limit_preserve_saved_cargo(self):
        self.urgent();self.act('adopt',candidate=self.scene['candidate']);original=deepcopy(self.scene['cargo'])
        self.urgent(lines=[dict(sku='HUGE',quantity=1000,unit_weight_kg=100,unit_volume_m3=1)])
        self.assertEqual(self.scene['versions'][-1]['plan']['unserved_ids'],['E02'])
        with self.assertRaisesRegex(ValueError,'不完整'):self.act('adopt',candidate=self.scene['candidate'])
        self.assertEqual(self.store.read()['cargo'],original)
        self.act('discard',candidate=self.scene['candidate'])
        self.urgent();self.act('adopt',candidate=self.scene['candidate']);before=self.store.read()
        with self.assertRaisesRegex(ValueError,'最多两个'):self.urgent()
        self.assertEqual(self.store.read(),before)

    def test_order_number_skips_original_collision_and_urgent_is_not_destination(self):
        files={name:content.replace('STORE_001','E01') for name,content in self.files.items()}
        self.act('start',csv_files=files);self.urgent();self.act('adopt',candidate=self.scene['candidate'])
        self.assertEqual(self.scene['cargo']['E02'],'warehouse')
        before=self.store.read()
        with self.assertRaisesRegex(ValueError,'已导入门店'):self.urgent(store='E02')
        self.assertEqual(self.store.read(),before)

    def test_second_order_after_legacy_w_and_window_replay(self):
        import json,sqlite3
        from contextlib import closing
        self.urgent();self.act('adopt',candidate=self.scene['candidate'])
        for p in (self.scene['live_plan'],self.scene['versions'][-1]['plan']):
            r=p['routes'][self.vid]
            r['sequence']=['W' if x=='warehouse:E01' else x for x in r['sequence']]
            for s in r['stops']:
                if s['id']=='warehouse:E01':s['id']='W';s.pop('task_id')
        with closing(sqlite3.connect(self.store.path)) as c, c:
            c.execute('UPDATE dispatch_scenes SET body=? WHERE id=?',(json.dumps(self.scene),self.scene['id']))
        self.urgent();self.act('adopt',candidate=self.scene['candidate'])
        route=self.scene['live_plan']['routes'][self.vid]
        self.assertIn('W',route['sequence']);self.assertIn('warehouse:E02',route['sequence'])
        self.act('event',kind='window',event=dict(time=self.scene['clock'],store='E02',open=600,due=800,close=960))
        self.assertTrue(self.store.compare()['available']);self.act('adopt',candidate=self.scene['candidate'])
        while self.scene['states'][self.vid]['remaining']:self.next_task()
        self.assertEqual(self.scene['cargo']['E01'],'delivered');self.assertEqual(self.scene['cargo']['E02'],'delivered')


if __name__=='__main__':unittest.main()
