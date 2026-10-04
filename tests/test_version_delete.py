from pathlib import Path
import sqlite3
import tempfile
import unittest

from starlette.testclient import TestClient
from api import create_app
from session_store import SessionStore


def seed(store, uid, sid, names=('first','second')):
    tid=store.create_task(uid,sid,'生成测试版本')
    directory=store.workspace(uid)/'resumes'/tid;directory.mkdir(parents=True)
    paths=[]
    for name in names:
        for suffix,body in (('.pdf',b'%PDF-test'),('.tex',b'test source')):
            p=directory/(name+suffix);p.write_bytes(body);paths.append(p)
    store.complete(uid,sid,tid,[], '完成',paths)
    with store.db() as db:
        records={row['name']:dict(row) for row in db.execute('SELECT * FROM files WHERE task_id=?',(tid,))}
    return tid,directory,records


class VersionDeleteTests(unittest.TestCase):
    def test_auth_selective_delete_and_download_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(directory, allow_legacy_users=True)) as client:
                a,b=client.post('/users').json(),client.post('/users').json()
                ha,hb=({'Authorization':'Bearer '+u['token']} for u in (a,b))
                sid=client.post('/sessions',headers=ha).json()['session_id']
                store=client.app.state.store;uid=a['user_id']
                tid,output,records=seed(store,uid,sid)
                before=store.materials(uid)
                source=store.workspace(uid)/'uploads'/'original.pdf';source.parent.mkdir();source.write_bytes(b'keep original')
                pdfid=records['first.pdf']['id'];texid=records['first.tex']['id'];route='/versions/'+pdfid
                self.assertEqual(client.delete(route).status_code,401)
                self.assertEqual(client.delete(route,headers=hb).status_code,404)
                self.assertEqual(client.delete('/versions/'+texid,headers=ha).status_code,404)
                store.status(tid,'running')
                self.assertEqual(client.delete(route,headers=ha).status_code,409)
                store.status(tid,'completed')
                self.assertEqual(client.delete(route,headers=ha).status_code,200)
                self.assertFalse((output/'first.pdf').exists());self.assertFalse((output/'first.tex').exists())
                self.assertTrue((output/'second.pdf').exists());self.assertTrue((output/'second.tex').exists())
                self.assertEqual(client.get('/sessions/'+sid+'/files/'+pdfid,headers=ha).status_code,404)
                self.assertEqual(client.get('/sessions/'+sid+'/files/'+texid,headers=ha).status_code,404)
                self.assertEqual(len(client.get('/versions',headers=ha).json()['files']),1)
                self.assertEqual(client.delete(route,headers=ha).status_code,404)
                self.assertEqual(store.materials(uid),before);self.assertEqual(source.read_bytes(),b'keep original')
                self.assertEqual(client.delete('/versions/'+records['second.pdf']['id'],headers=ha).status_code,200)
                self.assertEqual(client.get('/versions',headers=ha).json()['files'],[])
            self.assertEqual(SessionStore(directory).versions(uid),[])

    def test_database_failure_restores_files(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory);uid=store.create_user()['user_id'];sid=store.create_session(uid)['session_id']
            tid,output,records=seed(store,uid,sid,('first',))
            with store.db() as db:
                db.execute("CREATE TRIGGER stop_delete BEFORE DELETE ON files BEGIN SELECT RAISE(ABORT,'forced failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                store.delete_version(uid,records['first.pdf']['id'])
            self.assertEqual((output/'first.pdf').read_bytes(),b'%PDF-test')
            self.assertEqual((output/'first.tex').read_bytes(),b'test source')
            self.assertEqual(len(store.versions(uid)),1)
            self.assertFalse(list(store.workspace(uid).glob('.delete-version-*')))

    def test_invalid_path_cannot_delete_materials(self):
        from session_store import NotFound
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory);uid=store.create_user()['user_id'];sid=store.create_session(uid)['session_id']
            _,output,records=seed(store,uid,sid,('first',))
            protected=store.workspace(uid)/'uploads'/'protected.pdf';protected.parent.mkdir();protected.write_bytes(b'keep')
            with store.db() as db:
                db.execute('UPDATE files SET path=? WHERE id=?',('uploads/protected.pdf',records['first.pdf']['id']))
            with self.assertRaises(NotFound):store.delete_version(uid,records['first.pdf']['id'])
            self.assertEqual(protected.read_bytes(),b'keep')


if __name__=='__main__':unittest.main()
