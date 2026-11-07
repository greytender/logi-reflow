from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

import deepseek_agent
import intake
import intake_agent as agent
import intake_review
from store import Conflict


def generic_xlsx():
    labels=['门店名称','详细地址','常温件数','常温总重量(kg)','常温总体积(m³)','冷链件数','冷链总重量(kg)','冷链总体积(L)']
    def string(ref,text):return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'
    header='<row r="1">'+''.join(string(chr(65+i)+'1',s) for i,s in enumerate(labels))+'</row>'
    rows=[]
    for n,name,address in [(2,'隐私门店甲','隐私地址甲13800138000'),(3,'隐私门店甲','隐私地址乙')]:
        rows.append(f'<row r="{n}">'+string(f'A{n}',name)+string(f'B{n}',address)+''.join(
            f'<c r="{c}{n}"><v>{v}</v></c>' for c,v in zip('CDEFGH',[2,14,.144,1,.5,10]))+'</row>')
    output=io.BytesIO()
    with ZipFile(io.BytesIO(intake.sample_xlsx())) as src,ZipFile(output,'w',ZIP_DEFLATED) as dst:
        for part in src.namelist():
            data=src.read(part)
            if part=='xl/worksheets/sheet1.xml':data=(f'<worksheet xmlns="{intake.NS}"><sheetData>'+header+''.join(rows)+'</sheetData></worksheet>').encode()
            dst.writestr(part,data)
    return output.getvalue()


class IntakeAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.store=intake.IntakeStore(Path(self.temp.name)/'intake.db')
        self.doc=self.store.create('test','generic.xlsx',generic_xlsx())
        self.reviews=intake_review.ReviewStore(self.store.path,self.store);self.agent=agent.IntakeAgent(self.reviews)
        self.config=self.reviews.read(self.doc['id'])['config']

    def tearDown(self):self.temp.cleanup()

    def body(self,**more):
        return dict(id=self.doc['id'],revision=0,config=deepcopy(self.config),mode='local',request_id='request_12345',**more)

    def finish(self,job):
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            result=self.agent.read(self.doc['id'],job['id'])
            if result['status']!='running':return result
            time.sleep(.01)
        self.fail('background job did not finish')

    def apply_body(self,job,**more):
        return dict(id=self.doc['id'],job_id=job['id'],config=deepcopy(self.config),reviewed=True,
            change_ids=[c['id'] for c in job['proposal']['changes']],include_store_ids=True,**more)

    def test_generic_headers_units_and_whole_rows_survive_adoption(self):
        job=self.finish(self.agent.start(self.body()))
        self.assertEqual(job['status'],'succeeded',job.get('error'))
        self.assertEqual(job['proposal']['selected_rows'],2)
        self.assertEqual(self.reviews.read(self.doc['id'])['revision'],0)
        saved=self.agent.apply(self.apply_body(job))
        self.assertEqual(saved['config']['rows'],[2,3])
        first,second=saved['assessment']['rows'];self.assertEqual(first['normalized']['regular_weight_kg'],14)
        self.assertEqual(first['normalized']['cold_volume_m3'],.01)
        self.assertNotEqual(first['identity']['store_id'],second['identity']['store_id'])
        self.assertEqual(first['identity']['order_id'],'')
        self.assertEqual(saved['agent_provenance']['job_id'],job['id'])
        self.assertEqual(self.store.read(self.doc['id']),self.doc)
        self.assertEqual(self.agent.apply(self.apply_body(job))['revision'],1)
        restored=agent.IntakeAgent(self.reviews).read(self.doc['id'],job['id'])
        self.assertEqual(restored['applied_revision'],1)

    def test_model_context_redacts_business_rows_and_external_ids(self):
        context=json.dumps(agent.model_context(self.doc,self.config,''),ensure_ascii=False)
        self.assertNotIn('隐私',context);self.assertNotIn('13800138000',context)
        self.assertNotIn(self.doc['filename'],context)
        self.assertIn('常温总重量',context)

    def test_explicit_units_respect_regular_and_cold_groups_and_conflicts(self):
        notes='常规重量 kg；冷链重量 g；全部体积 L'
        self.assertEqual(agent.user_unit(notes,'regular_weight'),'kg')
        self.assertEqual(agent.user_unit(notes,'cold_weight'),'g')
        self.assertEqual(agent.user_unit(notes,'cold_volume'),'L')
        self.assertIsNone(agent.user_unit('重量 kg；重量 g','regular_weight'))
        self.assertIsNone(agent.user_unit('重量不是kg','regular_weight'))
        proposal=agent.suggestions(self.doc,self.config,dict(units=[dict(field='cold_weight',unit='kg',evidence=['user'])]),notes)
        self.assertEqual(proposal['changes'],[])

    def test_extra_metadata_is_ignored_but_model_execution_fields_are_rejected(self):
        proposal=agent.suggestions(self.doc,self.config,dict(summary='test',fields=[],confidence=.9))
        self.assertEqual(proposal['changes'],[])
        with self.assertRaises(ValueError):agent.suggestions(self.doc,self.config,dict(summary='test',actions=['dispatch']))

    def test_unit_guess_and_single_piece_mapping_are_not_adopted(self):
        output=dict(fields=[],units=[dict(field='regular_weight',unit='g',evidence=['D1'],reason='猜测')])
        p=agent.suggestions(self.doc,self.config,output)
        self.assertFalse(p['changes']);self.assertTrue(any('依据' in q for q in p['questions']))
        doc=deepcopy(self.doc);doc['sheets'][0]['rows'][0]['cells'][3]['value']='单件重量(kg)'
        doc['sheets'][0]['rows'][0]['cells'][3]['effective_value']='单件重量(kg)'
        output=dict(fields=[dict(field='regular_weight',column=4,evidence=['D1'],reason='单件')])
        p=agent.suggestions(doc,self.config,output)
        self.assertFalse(p['changes']);self.assertTrue(any('单件量' in q for q in p['questions']))

    def test_invalid_refs_unknown_fields_and_dropped_data_fail_closed(self):
        for output in [dict(fields=[dict(field='address',column=2,evidence=['Z999'])]),
                       dict(fields=[dict(field='dispatch',column=2,evidence=['B1'])]),dict(actions=['dispatch']),
                       dict(data_start_row=999)]:
            with self.assertRaises(ValueError):agent.suggestions(self.doc,self.config,output)
        p=agent.suggestions(self.doc,self.config,dict(data_start_row=3))
        self.assertEqual(p['selected_rows'],3);self.assertTrue(any('保留原行' in q for q in p['questions']))

    def test_stale_revision_and_unsaved_edits_cannot_be_overwritten(self):
        job=self.finish(self.agent.start(self.body()))
        body=self.apply_body(job);body['config']['note']='new local edit'
        with self.assertRaises(Conflict):self.agent.apply(body)
        self.reviews.save(self.doc['id'],0,self.config)
        with self.assertRaises(Conflict):self.agent.apply(self.apply_body(job))

    def test_selective_adoption_and_explicit_manual_units_are_recorded(self):
        job=self.finish(self.agent.start(self.body()))
        body=self.apply_body(job);body['change_ids']=[k for k in body['change_ids'] if k!='units:cold_volume']
        body['unit_overrides']={'cold_volume':'cm3'}
        saved=self.agent.apply(body)
        self.assertEqual(saved['config']['units']['cold_volume'],'cm3')
        self.assertEqual(saved['agent_provenance']['manual_units'],{'cold_volume':'cm3'})

    def test_idempotent_start_never_calls_provider_twice_and_failure_keeps_draft(self):
        output=agent.local_output(self.doc,self.config);sent=[]
        def transport(request,timeout):
            sent.append(json.loads(request.data))
            return io.BytesIO(json.dumps(dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(output)))],usage={})).encode())
        planner=deepseek_agent.DeepSeekPlanner('sk-secret-not-for-storage',transport)
        body=self.body();body['mode']='model'
        job=self.finish(self.agent.start(body,planner))
        self.assertEqual(self.agent.start(body,planner)['id'],job['id']);self.assertEqual(len(sent),1)
        self.assertEqual(sent[0]['response_format'],{'type':'json_object'});self.assertEqual(sent[0]['max_tokens'],4500)
        self.assertNotIn(planner.key,json.dumps(job))
        body['request_id']='another_request'
        with patch.object(planner,'complete',return_value='bad json'):
            failed=self.finish(self.agent.start(body,planner))
        self.assertEqual(failed['status'],'failed');self.assertEqual(self.reviews.read(self.doc['id'])['revision'],0)

    def test_restart_fails_abandoned_job_without_automatic_replay(self):
        job=dict(id='abandoned',document_id=self.doc['id'],request_id='abandoned',status='running')
        with self.reviews.connect() as db:db.execute('INSERT INTO intake_agent_jobs VALUES (?,?,?,?)',('abandoned',self.doc['id'],'abandoned',json.dumps(job)))
        restored=agent.IntakeAgent(self.reviews).read(self.doc['id'],'abandoned')
        self.assertEqual(restored['status'],'failed');self.assertIn('重启',restored['error'])

    def test_source_bound_jobs_and_review_confirmation(self):
        job=self.finish(self.agent.start(self.body()))
        other=self.store.create('other','other.xlsx',generic_xlsx())
        with self.assertRaises(ValueError):self.agent.read(other['id'],job['id'])
        body=self.apply_body(job);body['reviewed']=False
        with self.assertRaises(ValueError):self.agent.apply(body)

    def test_official_blanks_and_cached_numbers_remain_unknown(self):
        document=self.store.create('official','source.xlsx',intake.sample_xlsx());c=intake_review.default_config(document)
        proposal=agent.suggestions(document,c,agent.local_output(document,c))
        self.assertFalse(any(k['kind']=='units' for k in proposal['changes']))
        self.assertTrue(any('不能自动按零' in q for q in proposal['questions']))
        self.assertIsNone(intake_review.assess(document,c)['rows'][1]['normalized']['cold_quantity'])


if __name__=='__main__':unittest.main()
