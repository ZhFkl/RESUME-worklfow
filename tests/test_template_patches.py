import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from starlette.datastructures import UploadFile
from backend.session_store import SessionStore
from backend.task_manager import TaskManager
from backend.template_importer import TemplateImporter, apply_edits, source_hash
from backend.template_library import TemplateLibrary
from backend.uploaded_files import save_upload
from test_backend import MockClient, Message
from test_template_library import SAMPLE


def response(data):
    return SimpleNamespace(choices=[SimpleNamespace(message=Message(content=json.dumps(data)))])


class PatchValidationTests(unittest.TestCase):
    def test_multiple_edits_use_original_offsets_and_preserve_other_text(self):
        patch_data = {'base_sha256':source_hash(SAMPLE), 'edits':[
            {'old':'Sample University', 'new':'New University'},
            {'old':'Sample Name', 'new':'Sample Name with a longer header'},
        ]}
        self.assertEqual(apply_edits(SAMPLE, patch_data), SAMPLE.replace('Sample University','New University').replace('Sample Name','Sample Name with a longer header'))

    def test_rejects_stale_ambiguous_overlapping_and_invalid_edits(self):
        cases = [
            {'base_sha256':'stale','edits':[{'old':'Sample Name','new':'New Name'}]},
            {'edits':[{'old':'Sample','new':'New'}]},
            {'edits':[{'old':'Sample Name','new':'New Name'},{'old':'Name','new':'Person'}]},
            {'edits':[{'old':'missing','new':'replacement'}]},
            {'edits':[{'old':'','new':'insert'}]},
            {'edits':[{'old':'Sample Name','new':'Sample Name'}]},
            {'edits':[{'old':SAMPLE,'new':SAMPLE+'\n'}]},
            {'edits':[{'old':r'\end{document}','new':''}]},
            {'edits':[{'old':'Sample Name','new':'x'*120001}]},
            {'edits':[]},
            {'latex':SAMPLE},
        ]
        for invalid in cases:
            with self.subTest(invalid=list(invalid)):
                with self.assertRaises(ValueError):
                    apply_edits(SAMPLE, {'base_sha256':source_hash(SAMPLE),**invalid})


class CompileRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_patch_does_not_partially_apply_and_valid_patch_is_compiled(self):
        broken = SAMPLE.replace(r'\begin{document}',r'\begin{document}\UnknownTemplateCommand')
        test = self
        class Client(MockClient):
            repairs = 0
            reviews = 0
            async def create(self, **kwargs):
                message = kwargs['messages'][-1]['content']
                if isinstance(message, list) and '刚编译' in message[0].get('text',''):
                    type(self).reviews += 1
                    reviewed = json.loads(kwargs['messages'][-2]['content'])
                    test.assertEqual(reviewed['latex'], SAMPLE)
                    return response({'approved':True,'feedback':''})
                if isinstance(message, str):
                    request = json.loads(message)
                    test.assertEqual(request['mode'],'repair')
                    test.assertEqual(request['current_source'],broken)
                    test.assertEqual(request['base_sha256'],source_hash(broken))
                    test.assertIn('编译失败',request['feedback'])
                    test.assertEqual(len(kwargs['messages']),2)
                    type(self).repairs += 1
                    edits = [{'old':r'\UnknownTemplateCommand','new':''}]
                    if type(self).repairs == 1:
                        edits += [{'old':'NOT PRESENT','new':'must not apply'}]
                    else:
                        test.assertIn('未唯一匹配',request['feedback'])
                    return response({'base_sha256':request['base_sha256'],'edits':edits})
                return response({'latex':broken,'notes':'Original layout notes'})
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory);uid = store.create_user()['user_id'];sid = store.create_session(uid)['session_id']
            upload = UploadFile(io.BytesIO(Path('模板/resume-en-template.pdf').read_bytes()),filename='reference.pdf')
            ident = await save_upload(store,uid,sid,upload)
            manager = TaskManager(store,None,template_importer=TemplateImporter(store,client_factory=Client))
            with patch.dict(os.environ,AGENT_API_KEY='test',AGENT_MODEL='test'):
                tid = manager.submit_template(uid,sid,ident,'Repair check')
                await asyncio.gather(*manager.running)
            task = store.task(uid,sid,tid)
            self.assertEqual(task['status'],'completed',task.get('error'))
            self.assertEqual(Client.repairs,2);self.assertEqual(Client.reviews,1)
            item = TemplateLibrary(store).get(uid,'custom-'+tid)
            self.assertEqual(item['path'].read_text(),SAMPLE)
            self.assertEqual(item['path'].with_suffix('.pdf').exists(),True)
            self.assertEqual(item['description'],'Original layout notes')
            self.assertEqual([e['round'] for e in task['events'] if e['type']=='template_compiling'],[1,3])
            self.assertEqual(len([e for e in task['events'] if e['type']=='template_patch_applied']),1)
            self.assertEqual(len([e for e in task['events'] if e['type']=='template_patch_rejected']),1)
            await manager.close()
