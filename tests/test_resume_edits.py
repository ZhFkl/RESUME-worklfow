import json
import tempfile
import unittest

from session_store import SessionStore
from tex_edits import source_hash
from tools import LocalTools
from resume_fixtures import full_page_tex
from test_backend import Message, MockClient
from test_pdf_preview import actions, response
import test_pdf_preview as previews


class ResumeEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_edits_preserve_source_and_preview_then_valid_edit_invalidates_them(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory);root=store.workspace(store.create_user()['user_id'])
            output=root/'resumes'/'edit';output.mkdir(parents=True)
            original=full_page_tex('Original heading')
            async with LocalTools(root,output) as tools:
                await tools.call('resume__write_file',{'path':'resume.tex','content':original})
                document=json.loads(await tools.call('resume__read_file',{'path':'resume.tex'}))
                self.assertEqual(document['content'],original)
                self.assertEqual(document['base_sha256'],source_hash(original))
                await tools.call('resume__compile_pdf',{'tex_file':'resume.tex'})
                preview=await tools.call('resume__preview_pdf',{'tex_file':'resume.tex'})
                pdf=(output/'resume.pdf').read_bytes()
                rejected=[
                    ('resume__edit_file',{'path':'resume.tex','base_sha256':'stale','edits_json':json.dumps([{'old':'Original heading','new':'New heading'}])}),
                    ('resume__edit_file',{'path':'resume.tex','base_sha256':source_hash(original),'edits_json':json.dumps([{'old':'Original heading','new':'New heading'},{'old':'not present','new':''}])}),
                    ('resume__edit_file',{'path':'resume.tex','base_sha256':source_hash(original),'edits_json':'not json'}),
                ]
                for name,args in rejected:
                    result=await tools.call(name,args)
                    self.assertIn('工具执行出错',result)
                    self.assertEqual((output/'resume.tex').read_text(),original)
                    self.assertEqual((output/'resume.pdf').read_bytes(),pdf)
                    self.assertTrue(preview.current(root,output))
                result=json.loads(await tools.call('resume__edit_file',{'path':'resume.tex','base_sha256':source_hash(original),
                    'edits_json':json.dumps([{'old':'Original heading','new':r'\textbf{Updated heading}'}])}))
                revised=original.replace('Original heading',r'\textbf{Updated heading}')
                self.assertEqual((output/'resume.tex').read_text(),revised)
                self.assertEqual(result['base_sha256'],source_hash(revised))
                self.assertFalse((output/'resume.pdf').exists())
                self.assertFalse(preview.current(root,output))
                self.assertIn('没有本轮编译预览',await tools.call('resume__preview_pdf',{'tex_file':'resume.tex'}))
                self.assertIn('编译成功',await tools.call('resume__compile_pdf',{'tex_file':'resume.tex'}))
                fresh=await tools.call('resume__preview_pdf',{'tex_file':'resume.tex'})
                self.assertTrue(fresh.current(root,output,output/'resume.pdf'))

                # The model can choose a full rewrite of the same file after reviewing it.
                rewritten=full_page_tex('Full rewrite selected by model')
                result=await tools.call('resume__write_file',{'path':'resume.tex','content':rewritten})
                self.assertIn('已保存',result)
                self.assertEqual((output/'resume.tex').read_text(),rewritten)
                self.assertFalse((output/'resume.pdf').exists())
                self.assertFalse(fresh.current(root,output))
                self.assertIn('编译成功',await tools.call('resume__compile_pdf',{'tex_file':'resume.tex'}))
                final=await tools.call('resume__preview_pdf',{'tex_file':'resume.tex'})
                self.assertTrue(final.current(root,output,output/'resume.pdf'))

    async def test_edit_tool_cannot_change_materials_templates_or_other_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory);root=store.workspace(store.create_user()['user_id'])
            output=root/'resumes'/'current';output.mkdir(parents=True)
            previous=root/'resumes'/'previous';previous.mkdir();(previous/'resume.tex').write_text(full_page_tex())
            material=(root/'项目经历库.md').read_bytes()
            async with LocalTools(root,output) as tools:
                for path in ('项目经历库.md','模板/resume-中文模板.tex','../previous/resume.tex','/etc/passwd','missing.tex'):
                    result=await tools.call('resume__edit_file',{'path':path,'base_sha256':source_hash(full_page_tex()),'edits_json':'[]'})
                    self.assertIn('工具执行出错',result)
            self.assertEqual((root/'项目经历库.md').read_bytes(),material)
            self.assertEqual((previous/'resume.tex').read_text(),full_page_tex())


class ModelEditTests(unittest.IsolatedAsyncioTestCase):
    run_model=previews.PreviewTests.run_model

    async def test_model_must_see_screenshot_before_edit_and_recompile_same_file(self):
        test=self
        original=full_page_tex('First heading')
        edits={'path':'resume.tex','base_sha256':source_hash(original),'edits_json':json.dumps([{'old':'First heading','new':'Revised heading'}])}
        class Client(MockClient):
            async def create(client,**kwargs):
                client.calls+=1
                if client.calls==1:
                    return response(actions(('write_file',{'path':'resume.tex','content':original}),
                        ('compile_pdf',{'tex_file':'resume.tex'}),('edit_file',edits)))
                messages=kwargs['messages']
                test.assertTrue(any(p['type']=='image_url' for p in messages[-1]['content']))
                if client.calls==2:
                    test.assertIn('截图尚未查看',messages[-2]['content'])
                    return response(actions(('edit_file',edits),('compile_pdf',{'tex_file':'resume.tex'})))
                test.assertIn('编译成功',messages[-2]['content'])
                return response(Message(content='Reviewed revised PDF.'))
        store,uid,sid,tid=await self.run_model(Client)
        task=store.task(uid,sid,tid)
        self.assertEqual(task['status'],'completed',task['error'])
        output=store.workspace(uid)/'resumes'/tid
        self.assertEqual((output/'resume.tex').read_text(),original.replace('First heading','Revised heading'))
        self.assertEqual(len(list(output.glob('*.tex'))),1)
        self.assertEqual(sum(e['type']=='preview_started' for e in task['events']),2)
