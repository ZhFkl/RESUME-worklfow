import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from argon2 import PasswordHasher
from starlette.testclient import TestClient
from backend.api import create_app
from backend.account_store import AccountError, AccountStore
from backend.session_store import SessionStore
from test_backend import FakeCore


class Email:
    enabled = True
    def __init__(self):
        self.sent = []
        self.fail = False
    async def send(self, address, code):
        if self.fail:
            raise AccountError('验证码邮件发送失败', 503)
        self.sent.append((address, code))


class DisabledSms:
    enabled = False


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.hasher=patch('backend.account_store.PH',PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1));self.hasher.start();self.addCleanup(self.hasher.stop)
        self.email=Email();self.app=create_app(self.temp.name,email_factory=lambda:self.email,sms_factory=DisabledSms)
        self.client=TestClient(self.app);self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
        self.client.headers['X-Resume-Client']='browser'
        self.accounts=self.app.state.accounts;self.store=self.app.state.store

    def signup(self,name='tester',legacy=None):
        address=name.lower()+'@example.com'
        response=self.client.post('/auth/email/send',json={'email':address})
        self.assertEqual(response.status_code,200,response.text)
        data={'username':name,'email':address,'code':self.email.sent[-1][1],'password':'Testing password 123','role':'admin'}
        if legacy:data['legacy_token']=legacy
        r=self.client.post('/auth/register',json=data);self.assertEqual(r.status_code,200,r.text)
        self.client.headers['X-CSRF-Token']=r.json()['csrf'];return r

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
        self.assertEqual(self.client.post('/auth/register',json={'username':'TESTER','password':'Testing password 123','email':'other@example.com','code':self.accounts.issue_email_code('other@example.com')}).status_code,409)
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

    def test_disabled_sms_and_existing_data_preserved(self):
        account=self.signup().json()['account'];uid=account['user_id']
        phone='+8613800138000'
        with self.store.db() as db:
            db.execute('UPDATE accounts SET phone=? WHERE user_id=?',(phone,uid))
        self.assertFalse(self.client.get('/auth/config').json()['sms_enabled'])
        for method,path in [('POST','/auth/sms/send'),('POST','/auth/sms/login'),
                            ('POST','/auth/password/reset'),('PUT','/auth/phone')]:
            response=self.client.request(method,path,json={'phone':phone,'code':'123456'})
            self.assertIn(response.status_code,(400,404,405,503),response.text)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT phone FROM accounts WHERE user_id=?',(uid,)).fetchone()[0],phone)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sms_codes').fetchone()[0],0)
        self.client.post('/auth/logout')
        for identifier in [phone,'13800138000']:
            result=self.client.post('/auth/login',json={'identifier':identifier,'password':'Testing password 123'})
            self.assertEqual(result.status_code,401)
        result=self.client.post('/auth/login',json={'identifier':'tester','password':'Testing password 123'})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['account']['user_id'],uid)

    def test_login_rate_limit_and_session_expiry(self):
        self.signup();token=self.client.cookies.get('resume_session')
        with patch('backend.account_store.time.time',return_value=time.time()+8*24*3600):
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
        from backend.account_store import password_hash
        def concurrent_reset(value):
            self.accounts.change_password(uid,'Concurrent reset password',verified=True)
            return password_hash(value)
        with patch('backend.account_store.password_hash',side_effect=lambda value:password_hash(value) if value=='Concurrent reset password' else concurrent_reset(value)):
            with self.assertRaises(AccountError):
                self.accounts.change_password(uid,'Stale request password','Testing password 123')
        self.assertEqual(self.accounts.login_password('tester','Concurrent reset password'),uid)

    def test_email_registration_is_required_and_login_accepts_both_identifiers(self):
        self.assertEqual(self.client.post('/auth/register',json={'username':'missing','password':'Testing password 123'}).status_code,400)
        self.assertEqual(self.client.post('/auth/register',json={'username':'missing','password':'Testing password 123','email':'missing@example.com','code':'123456'}).status_code,400)
        result=self.signup();uid=result.json()['account']['user_id']
        self.assertEqual(result.json()['account']['email'],'tester@example.com')
        self.client.post('/auth/logout')
        for identifier in ['TESTER',' TESTER@EXAMPLE.COM ']:
            response=self.client.post('/auth/login',json={'identifier':identifier,'password':'Testing password 123'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['account']['user_id'],uid)
        # Old accounts with no email are unaffected.
        old=self.accounts.create('olduser','Testing password 123')
        self.assertEqual(self.accounts.login_password('olduser','Testing password 123'),old)
        self.assertIsNone(self.accounts.account(old)['email'])

    def test_email_delivery_limits_failures_and_unconfigured_service(self):
        address='recipient@example.com'
        response=self.client.post('/auth/email/send',json={'email':address.upper()})
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('code',response.json())
        code=self.email.sent[-1][1]
        with self.store.db() as db:
            row=db.execute('SELECT * FROM email_codes WHERE email=?',(address,)).fetchone()
            self.assertNotEqual(row['code_hash'],code)
        self.assertEqual(self.client.post('/auth/email/send',json={'email':address}).status_code,429)
        self.email.fail=True
        self.assertEqual(self.client.post('/auth/email/send',json={'email':'fail@example.com'}).status_code,503)
        with self.store.db() as db:
            self.assertIsNone(db.execute('SELECT * FROM email_codes WHERE email=?',('fail@example.com',)).fetchone())
        self.email.enabled=False
        self.assertFalse(self.client.get('/auth/config').json()['registration_open'])
        self.assertEqual(self.client.post('/auth/email/send',json={'email':'off@example.com'}).status_code,503)
        self.assertEqual(len(self.email.sent),1)
        self.assertEqual(self.client.post('/auth/email/send',json={'email':'bad\r\nBcc:evil@example.com'}).status_code,400)

    def test_email_code_expiry_attempts_resend_and_atomic_consumption(self):
        address='code@example.com'
        code=self.accounts.issue_email_code(address)
        wrong='000000' if code!='000000' else '111111'
        for _ in range(5):
            with self.assertRaises(AccountError):
                self.accounts.create('codeuser','Testing password 123',email=address,email_code=wrong)
        with self.assertRaises(AccountError):
            self.accounts.create('codeuser','Testing password 123',email=address,email_code=code)
        with self.store.db() as db:
            self.assertGreaterEqual(db.execute('SELECT attempts FROM email_codes WHERE email=?',(address,)).fetchone()[0],5)
        code=self.accounts.issue_email_code(address)
        with patch('backend.account_store.time.time',return_value=time.time()+301):
            with self.assertRaises(AccountError):
                self.accounts.create('codeuser','Testing password 123',email=address,email_code=code)
        with patch('backend.account_store.secrets.randbelow',side_effect=[123456,654321]):
            old=self.accounts.issue_email_code(address)
            code=self.accounts.issue_email_code(address)
        with self.assertRaises(AccountError):
            self.accounts.create('codeuser','Testing password 123',email=address,email_code=old)
        # A conflicting username must not consume a valid email code.
        self.accounts.create('taken','Testing password 123')
        with self.assertRaises(AccountError):
            self.accounts.create('taken','Testing password 123',email=address,email_code=code)
        uid=self.accounts.create('codeuser','Testing password 123',email=address,email_code=code)
        self.assertEqual(self.accounts.account(uid)['email'],address)
        with self.assertRaises(AccountError):
            self.accounts.create('replay','Testing password 123',email=address,email_code=code)
        code=self.accounts.issue_email_code(address)
        with self.assertRaises(AccountError):
            self.accounts.create('duplicate','Testing password 123',email=address.upper(),email_code=code)

    def test_email_code_cannot_create_two_accounts_concurrently(self):
        from concurrent.futures import ThreadPoolExecutor
        address='parallel@example.com';code=self.accounts.issue_email_code(address)
        def create(name):
            try:return self.accounts.create(name,'Testing password 123',email=address,email_code=code)
            except AccountError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(create,['parallelone','paralleltwo']))
        self.assertEqual(sum(x is not None for x in results),1)

    def test_existing_database_migrates_without_losing_user_workspace(self):
        from backend.account_store import password_hash
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(directory);old=store.create_user();uid=old['user_id']
            (store.workspace(uid)/'项目经历库.md').write_text('Existing confirmed facts')
            with store.db() as db:
                db.execute('CREATE TABLE accounts (user_id TEXT PRIMARY KEY, username TEXT UNIQUE, phone TEXT UNIQUE, password_hash TEXT, display_name TEXT NOT NULL, role TEXT DEFAULT "user", enabled INTEGER DEFAULT 1, created_at TEXT, last_login TEXT)')
                db.execute('INSERT INTO accounts(user_id,username,password_hash,display_name) VALUES (?,?,?,?)',
                           (uid,'existing',password_hash('Testing password 123'),'existing'))
            migrated=AccountStore(store)
            self.assertEqual(migrated.login_password('existing','Testing password 123'),uid)
            self.assertIsNone(migrated.account(uid)['email'])
            self.assertEqual((store.workspace(uid)/'项目经历库.md').read_text(),'Existing confirmed facts')
            # Repeated startup must be safe.
            self.assertEqual(AccountStore(store).account(uid)['username'],'existing')
