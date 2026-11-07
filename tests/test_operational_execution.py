"""Execution estimates retain cold shells without claiming actual geometry."""
from copy import deepcopy
from math import ceil
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import execution
import multiwarehouse as mw
import observation

class OperationalExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.store=mw.MultiWarehouseStore(Path(self.temp.name)/'tasks.db')
        self.task=self.store.create('执行冷链与回收',mw.operational_sample())
        self.task=self.store.solve(self.task['id'],self.task['revision'])
        self.task=self.store.adopt(self.task['id'],self.task['revision'],mw.current_plan(self.task)['id'],True)
        self.child=next(iter(self.task['execution_tasks'].values()))
        self.vid=next(iter(execution.view(self.child)['routes']))
    def tearDown(self):self.temp.cleanup()
    def state(self):return execution.view(self.child)['routes'][self.vid]
    def sign_next(self,pickup=None):
        before=self.state();stop=before['remaining'][0];order=next(o for o in self.child['data']['orders'] if o['id']==stop['id'])
        receipt=dict(kind='stop',vehicle=self.vid,stop_id=stop['id'],finish=ceil(stop['finish']),source='simulated',note='构造回执',pickup=deepcopy(pickup if pickup is not None else order['pickup']))
        execution.append(self.child,receipt,True)
        return receipt
    def test_before_execution_forecast_includes_declared_operation_time(self):
        state=self.state();route=execution.confirmed(self.child)['result']['optimized']['routes'][self.vid]
        adjusted=route['operational']['adjusted_route']
        self.assertAlmostEqual(state['estimated_return'],adjusted['return_at'],places=3)
        self.assertGreater(state['estimated_return'],route['return_at'])
        self.assertEqual(state['current_geometry_status'],'DECLARED_PREDICTION')
        self.assertFalse(state['current_geometry_verified'])
        self.assertEqual(state['temperature_status'],'NOT_OBSERVED')
    def test_shared_shell_remains_after_all_goods_delivered_until_return(self):
        while self.state()['next_stop']:self.sign_next()
        state=self.state();policy=self.child['data']['operational_policy'];shell=policy['cold_boxes'][0]
        self.assertEqual(len(state['current_cold_boxes']),1)
        self.assertEqual(state['current_cold_boxes'][0]['remaining_delivery_stores'],[])
        self.assertGreaterEqual(state['onboard_weight_kg'],shell['tare_weight_kg'])
        self.assertGreaterEqual(state['onboard_volume_m3'],shell['length_cm']*shell['width_cm']*shell['height_cm']/1e6)
        self.assertEqual(state['current_geometry_status'],'NEEDS_OBSERVATION')
        execution.append(self.child,dict(kind='return',vehicle=self.vid,finish=ceil(state['estimated_return']),source='simulated',note='构造回仓'),True)
        state=self.state();self.assertEqual(state['onboard_weight_kg'],0)
        self.assertEqual(state['onboard_volume_m3'],0);self.assertEqual(state['current_cold_boxes'],[])
        self.assertTrue(state['returned'])
    def test_actual_pickup_deviation_retains_fact_and_withdraws_geometry_claim(self):
        receipt=self.sign_next({'cartons':3000,'totes':30})
        state=self.state()
        self.assertEqual(self.child['receipts'][0]['pickup'],receipt['pickup'])
        self.assertTrue(state['actual_pickup_deviation'])
        self.assertEqual(state['current_geometry_status'],'NEEDS_OBSERVATION')
        self.assertFalse(state['current_geometry_verified'])
        self.assertTrue(any('超重或超容' in w for w in state['warnings']))
        self.assertTrue(any('偏离' in w for w in state['warnings']))
    def test_delay_never_resets_cold_box_activation_clock(self):
        start=self.state();activated=start['current_cold_boxes'][0]['activated_at_minutes']
        observation.append(self.child,dict(kind='wait',vehicle=self.vid,node=self.child['data']['settings']['depot_id'],observed_at=700,source='simulated',note='构造延误'),True)
        state=self.state()
        self.assertEqual(state['current_cold_boxes'][0]['activated_at_minutes'],activated)
        self.assertTrue(any('声明有效时限' in w for w in state['warnings']))
        self.assertGreater(state['estimated_return'],start['estimated_return'])
        self.assertEqual(state['current_geometry_status'],'NEEDS_OBSERVATION')
    def test_view_is_read_only_and_does_not_overwrite_original_prediction(self):
        self.sign_next();before=deepcopy(self.child);self.state();self.assertEqual(self.child,before)
        self.assertEqual(execution.confirmed(self.child)['result']['optimized']['routes'],before['plans'][0]['result']['optimized']['routes'])

    def temperature(self,temperature_c=4,**extra):
        raw=dict(kind='reading',vehicle=self.vid,cold_box=self.child['data']['operational_policy']['cold_boxes'][0]['id'],
            phase='transit',time=520,temperature_c=temperature_c,source='simulated',note='示例设备读数',action='')
        raw.update(extra)
        return execution.append_temperature(self.child,raw,True)

    def test_temperature_excursion_remains_open_until_explicit_handling(self):
        before=self.state();original=deepcopy(execution.confirmed(self.child))
        anomaly=self.temperature(10)
        self.assertTrue(anomaly['excursion']);self.assertEqual(self.state()['temperature_status'],'EXCURSION_OPEN')
        self.temperature(4,time=525)
        state=self.state();self.assertEqual(state['temperature_summary']['open_excursions'],1)
        self.assertTrue(any('温度异常' in w for w in state['warnings']))
        action=dict(kind='action',vehicle=self.vid,cold_box=anomaly['cold_box'],target_id=anomaly['id'],
            time=526,source='simulated',note='司机记录',action='检查封口并补充蓄冷剂，通知门店验收')
        execution.append_temperature(self.child,action,True)
        state=self.state();self.assertEqual(state['temperature_status'],'EXCURSION_HANDLED')
        self.assertEqual(state['temperature_summary']['handled_excursions'],1)
        self.assertEqual(state['temperature_summary']['covered_phases'],['transit'])
        self.assertEqual(state['temperature_summary']['boxes'][0]['missing_phases'],['loading','unloading'])
        self.assertEqual(state['current_cold_boxes'],before['current_cold_boxes'])
        self.assertEqual(state['estimated_return'],before['estimated_return'])
        self.assertEqual(execution.confirmed(self.child),original)
        self.assertEqual(self.child.get('receipts',[]),[])

    def test_temperature_input_rejects_wrong_identity_source_and_event_time_without_mutation(self):
        for change in [dict(cold_box='UNKNOWN'),dict(vehicle='UNKNOWN'),dict(source='manual'),
                       dict(temperature_c=float('nan')),dict(temperature_c=True),dict(phase='loading',time=520),
                       dict(phase='transit',time=500)]:
            before=deepcopy(self.child)
            with self.subTest(change=change),self.assertRaises(ValueError):self.temperature(**change)
            self.assertEqual(self.child,before)
        anomaly=self.temperature(12)
        before=deepcopy(self.child)
        with self.assertRaises(ValueError):execution.append_temperature(self.child,dict(kind='action',
            vehicle=self.vid,cold_box=anomaly['cold_box'],target_id=anomaly['id'],time=519,source='simulated',note='',action='处理'),True)
        self.assertEqual(self.child,before)

    def test_retrospective_temperature_after_return_keeps_excursion_history(self):
        while self.state()['next_stop']:self.sign_next()
        end=ceil(self.state()['estimated_return'])
        execution.append(self.child,dict(kind='return',vehicle=self.vid,finish=end,source='simulated',note='返仓'),True)
        anomaly=self.temperature(9,phase='loading',time=505)
        self.assertTrue(self.state()['returned']);self.assertEqual(self.state()['onboard_weight_kg'],0)
        self.assertEqual(self.state()['temperature_status'],'EXCURSION_OPEN')
        with self.assertRaises(ValueError):self.temperature(time=end+1)
        execution.append_temperature(self.child,dict(kind='action',vehicle=self.vid,cold_box=anomaly['cold_box'],
            target_id=anomaly['id'],time=end+10,source='simulated',note='返仓复查',action='隔离该批货，通知质量负责人'),True)
        self.assertEqual(self.state()['temperature_summary']['open_excursions'],0)
        self.assertEqual(self.child['temperature_records'][0]['temperature_c'],9)

if __name__=='__main__':unittest.main()
