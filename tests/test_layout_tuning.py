import json
from pathlib import Path
import tempfile
import unittest

from backend.layout_tuning import apply_parameters, parameters
from backend.session_store import SessionStore
from backend.tools import LocalTools
from resume_fixtures import full_page_tex
import test_pdf_preview as preview_tests
from test_pdf_preview import actions, response
from test_backend import Message, MockClient


class ParameterTests(unittest.TestCase):
    def test_partial_updates_preserve_body_and_do_not_accumulate_overrides(self):
        tex=full_page_tex().replace(r'\begin{document}',r'\linespread{1.14}\begin{document}')
        adjusted=apply_parameters(tex,'{"line_spread":1.08,"bottom_mm":10}')
        again=apply_parameters(adjusted,'{"bottom_mm":9.5}')
        self.assertEqual(parameters(again),{'line_spread':1.08,'bottom_mm':9.5})
        self.assertEqual(again.count('% BEGIN MODEL LAYOUT PARAMETERS'),1)
        self.assertEqual(tex.split(r'\begin{document}')[1],again.split(r'\begin{document}')[1])
        for raw in ['[]','{}','{"bottom_mm":1}','{"line_spread":true}','{"line_spread":NaN}',
                    '{"bottom_mm":"10mm"}','{"font_size":7}']:
            with self.assertRaises(ValueError):apply_parameters(tex,raw)


class TuningToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_feedback_and_stale_preview_with_unchanged_body(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory);root=store.workspace(store.create_user()['user_id'])
            output=root/'resumes'/'test';output.mkdir(parents=True)
            async with LocalTools(root,output) as tools:
                definition=next(t['function'] for t in tools.tools if t['function']['name']=='resume__compile_pdf')
                self.assertEqual(definition['parameters']['required'],['tex_file'])
                original=full_page_tex()
                await tools.call('resume__write_file',{'path':'resume.tex','content':original})
                await tools.call('resume__compile_pdf',{'tex_file':'resume.tex'})
                preview=await tools.call('resume__preview_pdf',{'tex_file':'resume.tex'})
                result=await tools.call('resume__compile_pdf',{'tex_file':'resume.tex','layout_json':'{"bottom_mm":9.5}'})
                self.assertIn('排版测量',result)
                self.assertEqual(len(tools.last_layout_report['history']),2)
                self.assertEqual(tools.last_layout_report['parameters']['bottom_mm'],9.5)
                self.assertEqual(original.split(r'\begin{document}')[1],(output/'resume.tex').read_text().split(r'\begin{document}')[1])
                self.assertFalse(preview.current(root,output))
                before=(output/'resume.tex').read_text()
                rejected=await tools.call('resume__compile_pdf',{'tex_file':'resume.tex','layout_json':'{"bottom_mm":-1}'})
                self.assertIn('工具执行出错',rejected)
                self.assertEqual(before,(output/'resume.tex').read_text())


class ModelTuningTests(unittest.IsolatedAsyncioTestCase):
    run_model = preview_tests.PreviewTests.run_model

    async def test_model_reads_report_and_tunes_without_rewriting_body(self):
        test=self
        class TuningClient(MockClient):
            async def create(client,**kwargs):
                client.calls+=1
                if client.calls==1:
                    return response(actions(('write_file',{'path':'resume.tex','content':full_page_tex()}),
                        ('compile_pdf',{'tex_file':'resume.tex','layout_json':'{"bottom_mm":12}'})))
                messages=kwargs['messages'];tool=messages[-2]['content']
                report=json.loads(tool.split('排版测量（根据结果选择下一组参数，不要机械重复）：\n')[1].split('\nPDF 截图')[0])
                test.assertTrue(any(p['type']=='image_url' for p in messages[-1]['content']))
                if client.calls==2:
                    test.assertFalse(report['accepted'])
                    test.assertIn('bottom_mm',report['allowed_parameters'])
                    return response(actions(('compile_pdf',{'tex_file':'resume.tex','layout_json':'{"bottom_mm":10}'})))
                test.assertTrue(report['accepted'])
                test.assertEqual(len(report['history']),2)
                return response(Message(content='Verified adjusted PDF.'))
        store,uid,sid,tid=await self.run_model(TuningClient)
        task=store.task(uid,sid,tid)
        self.assertEqual(task['status'],'completed',task['error'])
        reports=[e for e in task['events'] if e['type']=='layout_checked']
        self.assertEqual([r['accepted'] for r in reports],[False,True])
        self.assertEqual([r['parameters']['bottom_mm'] for r in reports],[12,10])
