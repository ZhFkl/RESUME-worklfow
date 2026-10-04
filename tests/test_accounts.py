import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from argon2 import PasswordHasher
from starlette.testclient import TestClient
from api import create_app
from account_store import AccountError, AccountStore
from session_store import SessionStore
from test_backend import FakeCore


class SMS:
    enabled=True
    def __init__(self):self.sent=[];self.fail=False
    async def send(self,phone,code):
        if self.fail:raise AccountError('发送失败',503)
        self.sent.append((phone,code))


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.hasher=patch('account_store.PH',PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1));self.hasher.start();self.addCleanup(self.hasher.stop)
        self.sms=SMS();self.app=create_app(self.temp.name,sms_factory=lambda:self.sms)
        self.client=TestClient(self.app);self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
        self.client.headers['X-Resume-Client']='browser'
        self.accounts=self.app.state.accounts;self.store=self.app.state.store

    def signup(self,name='tester',legacy=None):
        data={'username':name,'password':'Testing password 123','role':'admin'}
        if legacy:data['legacy_token']=legacy
        r=self.client.post('/auth/register',json=data);self.assertEqual(r.status_code,200,r.text)
        self.client.headers['X-CSRF-Token']=r.json()['csrf'];return r

    def next_sms(self):
        with self.store.db() as db:db.execute("DELETE FROM auth_limits WHERE key LIKE 'sms:minute:%'")

    def test_password_login_logout_change_and_storage(self):
        self.assertEqual(self.client.get('/materials').status_code,401)
        self.assertNotEqual(self.client.post('/users').status_code,201)
        r=self.signup();uid=r.json()['account']['user_id'];self.assertEqual(r.json()['account']['role'],'user')
        self.assertIn('httponly',r.headers['set-cookie'].lower());self.assertIn('samesite=lax',r.headers['set-cookie'].lower())
        cookie=self.client.cookies.get('resume_session')
        with self.store.db() as db:
            row=db.execute('SELECT password_hash FROM accounts WHERE user_id=?',(uid,)).fetchone()
            self.assertTrue(row[0].startswith('$argon2id$'));self.assertNotIn('Testing password',row[0])
            self.assertEqual(db.execute('SELECT token_hash FROM login_sessions').fetchone()[0],hashlib.sha256(cookie.encode()).hexdigest())
        self.assertEqual(self.client.get('/materials').status_code,200)
        changed=self.client.put('/auth/password',json={'old_password':'Testing password 123','password':'A different password 456'})
        self.assertEqual(changed.status_code,200,changed.text)
        self.client.headers['X-CSRF-Token']=changed.json()['csrf']
        self.assertEqual(self.client.post('/auth/logout').status_code,200)
        self.assertEqual(self.client.get('/materials').status_code,401)
        self.assertEqual(self.client.post('/auth/login',json={'identifier':'tester','password':'Testing password 123'}).status_code,401)
        self.assertEqual(self.client.post('/auth/login',json={'identifier':'TESTER','password':'A different password 456'}).status_code,200)
        with self.assertRaises(AccountError):self.accounts.authenticate(cookie)

    def test_duplicate_csrf_and_user_admin_boundaries(self):
        account=self.signup().json()['account'];uid=account['user_id']
        before=list((Path(self.temp.name)/'users').iterdir())
        self.assertEqual(self.client.post('/auth/register',json={'username':'TESTER','password':'Testing password 123'}).status_code,409)
        self.assertEqual(list((Path(self.temp.name)/'users').iterdir()),before)
        self.assertEqual(self.client.get('/admin/users').status_code,403)
        self.assertEqual(self.client.post('/sessions',headers={'X-CSRF-Token':'wrong'}).status_code,403)
        self.assertEqual(self.client.post('/sessions',headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.post('/sessions').status_code,201)
        with self.store.db() as db:db.execute("UPDATE accounts SET role='admin' WHERE user_id=?",(uid,))
        other=self.accounts.create('another','Testing password 123')
        token,_=self.accounts.new_session(other)
        self.assertEqual(self.client.get('/admin/users').status_code,200)
        self.assertEqual(self.client.patch('/admin/users/'+uid,json={'enabled':False}).status_code,400)
        self.assertEqual(self.client.patch('/admin/users/'+other,json={'enabled':False}).status_code,200)
        with self.assertRaises(AccountError):self.accounts.authenticate(token)
        self.assertEqual(self.client.patch('/admin/users/'+other,json={'enabled':True}).status_code,200)
        self.assertEqual(self.client.put('/admin/users/'+other+'/password',json={'password':'Admin changed password'}).status_code,200)
        self.assertEqual(self.accounts.login_password('another','Admin changed password'),other)

    def test_legacy_binding_keeps_workspace_and_disables_old_token(self):
        old=self.store.create_user();root=self.store.workspace(old['user_id']);(root/'项目经历库.md').write_text('Confirmed personal facts')
        sid=self.store.create_session(old['user_id'])['session_id']
        self.assertEqual(self.client.get('/materials',headers={'Authorization':'Bearer '+old['token']}).status_code,401)
        result=self.signup('bounduser',old['token']);self.assertEqual(result.json()['account']['user_id'],old['user_id'])
        self.assertEqual(self.client.get('/materials').json()['markdown'],'Confirmed personal facts')
        self.assertEqual(self.client.get('/sessions').json()['sessions'][0]['session_id'],sid)
        self.assertEqual((root/'项目经历库.md').read_text(),'Confirmed personal facts')
        self.assertEqual(self.client.post('/auth/register',json={'username':'hijack','password':'Testing password 123','legacy_token':old['token']}).status_code,400)

    def test_sms_registration_login_recovery_and_one_use(self):
        phone='13800138000'
        result=self.client.post('/auth/sms/send',json={'phone':phone,'purpose':'register'})
        self.assertEqual(result.status_code,200,result.text);self.assertNotIn('code',result.json())
        code=self.sms.sent[-1][1]
        self.assertEqual(self.client.post('/auth/sms/send',json={'phone':phone,'purpose':'login'}).status_code,429)
        r=self.client.post('/auth/sms/login',json={'phone':phone,'mode':'register','code':code,'password':'Testing password 123'})
        self.assertEqual(r.status_code,200,r.text);uid=r.json()['account']['user_id'];old_token=self.client.cookies.get('resume_session')
        self.assertEqual(self.client.post('/auth/sms/login',json={'phone':phone,'mode':'register','code':code}).status_code,400)
        self.next_sms();self.client.post('/auth/sms/send',json={'phone':phone,'purpose':'reset'});reset=self.sms.sent[-1][1]
        self.assertEqual(self.client.post('/auth/sms/login',json={'phone':phone,'mode':'login','code':reset}).status_code,400)
        changed=self.client.post('/auth/password/reset',json={'phone':phone,'code':reset,'password':'Recovered password 456'})
        self.assertEqual(changed.status_code,200,changed.text)
        with self.assertRaises(AccountError):self.accounts.authenticate(old_token)
        self.assertEqual(self.accounts.login_password(phone,'Recovered password 456'),uid)
        self.next_sms();self.client.post('/auth/sms/send',json={'phone':phone,'purpose':'login'})
        login=self.client.post('/auth/sms/login',json={'phone':phone,'code':self.sms.sent[-1][1]})
        self.assertEqual(login.status_code,200,login.text);self.assertEqual(login.json()['account']['user_id'],uid)

    def test_binding_expiry_attempt_limits_and_disabled_delivery(self):
        account=self.signup().json()['account'];phone='+8613900139000'
        self.client.post('/auth/sms/send',json={'phone':phone,'purpose':'bind'});code=self.sms.sent[-1][1]
        r=self.client.put('/auth/phone',json={'phone':phone,'code':code})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['account']['phone'],phone)
        code=self.accounts.issue_code(phone,'login')
        wrong='000000' if code!='000000' else '111111'
        for _ in range(5):
            with self.assertRaises(AccountError):self.accounts.consume_code(phone,'login',wrong)
        with self.assertRaises(AccountError):self.accounts.consume_code(phone,'login',code)
        code=self.accounts.issue_code(phone,'login')
        with patch('account_store.time.time',return_value=time.time()+301):
            with self.assertRaises(AccountError):self.accounts.consume_code(phone,'login',code)
        self.next_sms();self.sms.fail=True
        self.assertEqual(self.client.post('/auth/sms/send',json={'phone':phone,'purpose':'login'}).status_code,503)
        with self.store.db() as db:self.assertIsNone(db.execute("SELECT 1 FROM sms_codes WHERE phone=? AND purpose='login'",(phone,)).fetchone())
        self.sms.enabled=False
        self.assertFalse(self.client.get('/auth/config').json()['sms_enabled'])
        self.assertEqual(self.client.post('/auth/sms/send',json={'phone':phone,'purpose':'login'}).status_code,503)

    def test_login_rate_limit_and_session_expiry(self):
        self.signup();token=self.client.cookies.get('resume_session')
        with patch('account_store.time.time',return_value=time.time()+8*24*3600):
            with self.assertRaises(AccountError):self.accounts.authenticate(token)
        for _ in range(10):self.client.post('/auth/login',json={'identifier':'tester','password':'wrong'})
        self.assertEqual(self.client.post('/auth/login',json={'identifier':'tester','password':'wrong'}).status_code,429)
        c=TestClient(self.app)
        self.assertEqual(c.post('/auth/login',json={'identifier':'tester','password':'Testing password 123'}).status_code,403)

    def test_cookie_business_tasks_persistence_and_account_switch(self):
        self.app.state.manager.core=FakeCore(self.store)
        account=self.signup().json()['account'];uid=account['user_id']
        self.client.headers['X-Resume-Account']=uid
        sid=self.client.post('/sessions').json()['session_id'];base=f'/sessions/{sid}'
        posted=self.client.post(base+'/messages',json={'message':'hello'})
        self.assertEqual(posted.status_code,202,posted.text)
        tid=posted.json()['task_id']
        for _ in range(100):
            task=self.client.get(base+'/tasks/'+tid).json()
            if task['status'] in {'completed','failed'}:break
            time.sleep(.01)
        self.assertEqual(task['status'],'completed',task)
        download=base+'/files/'+task['files'][0]['file_id']
        self.assertEqual(self.client.get(download).text,'test output')
        token=self.client.cookies.get('resume_session')
        restored=AccountStore(SessionStore(self.temp.name))
        self.assertEqual(restored.authenticate(token)['user_id'],uid)
        self.assertEqual(restored.login_password('tester','Testing password 123'),uid)
        # Another tab replaces the shared Cookie, while this tab still holds old state.
        other=self.accounts.create('another','Testing password 123')
        other_token,other_csrf=self.accounts.new_session(other)
        switched={'Cookie':'resume_session='+other_token,'X-CSRF-Token':other_csrf}
        mismatch=self.client.get('/materials',headers=switched)
        self.assertEqual(mismatch.status_code,409)
        self.assertTrue(mismatch.json()['account_changed'])
        before=len(self.store.list_sessions(other))
        self.assertEqual(self.client.post('/sessions',headers=switched).status_code,409)
        self.assertEqual(len(self.store.list_sessions(other)),before)
        switched['X-Resume-Account']=other
        self.assertEqual(self.client.get(download,headers=switched).status_code,404)
        self.assertEqual(self.client.get(base+'/messages',headers=switched).status_code,404)

    def test_password_change_cannot_overwrite_concurrent_reset(self):
        uid=self.signup().json()['account']['user_id']
        from account_store import password_hash
        def concurrent_reset(value):
            self.accounts.change_password(uid,'Concurrent reset password',verified=True)
            return password_hash(value)
        with patch('account_store.password_hash',side_effect=lambda value:password_hash(value) if value=='Concurrent reset password' else concurrent_reset(value)):
            with self.assertRaises(AccountError):
                self.accounts.change_password(uid,'Stale request password','Testing password 123')
        self.assertEqual(self.accounts.login_password('tester','Concurrent reset password'),uid)
