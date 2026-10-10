import asyncio
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from argon2 import PasswordHasher
from starlette.testclient import TestClient
from backend.api import create_app
from backend.account_store import AccountError
from backend.session_store import SessionStore
from backend.sms_auth import SmsAuth
from backend.sms_gateway import SmsGateway


class FakeSms:
    enabled = True
    def __init__(self):self.sent=[];self.checked=[];self.fail=False
    async def send(self,phone):
        if self.fail:raise AccountError('模拟发送失败',503)
        self.sent.append(phone)
    async def verify(self,phone,code):
        self.checked.append((phone,code))
        return code=='123456'


class SmsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch('backend.account_store.PH',PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1));p.start();self.addCleanup(p.stop)
        self.sms=FakeSms();self.app=create_app(self.temp.name,sms_factory=lambda:self.sms)
        self.client=TestClient(self.app);self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
        self.client.headers['X-Resume-Client']='browser'
        self.store=self.app.state.store

    def send(self,purpose,phone='13800138000'):
        with self.store.db() as db:db.execute("DELETE FROM auth_limits WHERE key LIKE 'sms:minute:%'")
        result=self.client.post('/auth/sms/send',json={'phone':phone,'purpose':purpose})
        self.assertEqual(result.status_code,200,result.text);self.assertNotIn('code',result.json())
        return result.json()['challenge']

    def signup(self,phone='13800138000'):
        token=self.send('register',phone)
        result=self.client.post('/auth/sms/login',json={'mode':'register','phone':phone,'challenge':token,'code':'123456','username':'smsuser','password':'Test password 123'})
        self.assertEqual(result.status_code,200,result.text);self.client.headers['X-CSRF-Token']=result.json()['csrf']
        return result.json()['account']['user_id']

    def test_register_login_replay_and_disabled_account(self):
        uid=self.signup();self.assertEqual(self.app.state.accounts.account(uid)['phone'],'+8613800138000')
        self.client.post('/auth/logout')
        token=self.send('login');data={'phone':'13800138000','challenge':token,'code':'123456'}
        result=self.client.post('/auth/sms/login',json=data)
        self.assertEqual(result.status_code,200,result.text);self.assertEqual(result.json()['account']['user_id'],uid)
        self.assertEqual(self.client.post('/auth/sms/login',json=data).status_code,400)
        with self.store.db() as db:db.execute('UPDATE accounts SET enabled=0 WHERE user_id=?',(uid,))
        data['challenge']=self.send('login')
        self.assertEqual(self.client.post('/auth/sms/login',json=data).status_code,403)

    def test_wrong_purpose_phone_expiry_attempts_and_autoregistration(self):
        token=self.send('register');data={'phone':'13800138000','challenge':token,'code':'123456'}
        self.assertEqual(self.client.post('/auth/sms/login',json=data).status_code,400)
        self.assertEqual(self.sms.checked,[])
        token=self.send('login');data['challenge']=token;data['phone']='13900139000'
        self.assertEqual(self.client.post('/auth/sms/login',json=data).status_code,400)
        data['phone']='13800138000';data['code']='000000'
        for _ in range(5):self.assertEqual(self.client.post('/auth/sms/login',json=data).status_code,400)
        data['code']='123456';self.assertEqual(self.client.post('/auth/sms/login',json=data).status_code,400)
        self.assertEqual(len(self.sms.checked),5)
        token=self.send('login');data['challenge']=token
        with self.store.db() as db:db.execute('UPDATE sms_attempts SET expires=?',(time.time()-1,))
        self.assertEqual(self.client.post('/auth/sms/login',json=data).status_code,400)
        self.assertIsNone(self.app.state.accounts.by_phone('+8613800138000'))
        data['challenge']=self.send('login')
        result=self.client.post('/auth/sms/login',json=data)
        self.assertEqual(result.status_code,200,result.text)
        account=result.json()['account']
        self.assertFalse(account['has_password'])
        self.assertIsNone(account['username'])
        data['challenge']=self.send('login')
        again=self.client.post('/auth/sms/login',json=data)
        self.assertEqual(again.status_code,200,again.text)
        self.assertEqual(again.json()['account']['user_id'],account['user_id'])

    def test_binding_requires_session_csrf_and_preserves_existing_user(self):
        self.assertEqual(self.client.post('/auth/sms/send',json={'phone':'13800138000','purpose':'bind'}).status_code,401)
        uid=self.app.state.accounts.create('existing','Test password 123')
        result=self.client.post('/auth/login',json={'identifier':'existing','password':'Test password 123'})
        self.assertEqual(self.client.post('/auth/sms/send',json={'phone':'13800138000','purpose':'bind'}).status_code,403)
        self.client.headers['X-CSRF-Token']=result.json()['csrf']
        token=self.send('bind');data={'phone':'13800138000','code':'123456','challenge':token}
        self.assertEqual(self.client.post('/auth/sms/login',json=data).status_code,400)
        result=self.client.put('/auth/phone',json=data);self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['account']['user_id'],uid)
        self.client.post('/auth/logout');data['challenge']=self.send('login')
        result=self.client.post('/auth/sms/login',json=data);self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['account']['user_id'],uid)

    def test_send_failure_disabled_service_rate_limits_and_origin(self):
        self.assertEqual(self.client.post('/auth/sms/send',headers={'Origin':'https://other.example'},json={'phone':'13800138000','purpose':'login'}).status_code,403)
        self.send('login')
        self.assertEqual(self.client.post('/auth/sms/send',json={'phone':'13800138000','purpose':'login'}).status_code,429)
        self.sms.fail=True
        self.assertEqual(self.client.post('/auth/sms/send',json={'phone':'13900139000','purpose':'login'}).status_code,503)
        with self.store.db() as db:self.assertIsNone(db.execute('SELECT * FROM sms_attempts WHERE phone=?',('+8613900139000',)).fetchone())
        self.sms.enabled=False
        self.assertFalse(self.client.get('/auth/config').json()['sms_enabled'])
        self.assertEqual(self.client.post('/auth/sms/send',json={'phone':'13700137000','purpose':'login'}).status_code,503)


class SmsConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_one_verification_can_consume_challenge(self):
        with tempfile.TemporaryDirectory() as root:
            sms=FakeSms();auth=SmsAuth(SessionStore(root),sms)
            token=await auth.send('+8613800138000','login')
            async def verify():
                try:await auth.verify('+8613800138000','login',token,'123456');return True
                except AccountError:return False
            self.assertEqual(sum(await asyncio.gather(verify(),verify())),1)
            self.assertEqual(len(sms.checked),1)


class SmsGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_models_and_pass_required(self):
        config={'RESUME_SMS_PROVIDER':'aliyun-pnvs','ALIBABA_CLOUD_ACCESS_KEY_ID':'fake-id',
                'ALIBABA_CLOUD_ACCESS_KEY_SECRET':'fake-secret','RESUME_SMS_SIGN_NAME':'test','RESUME_SMS_TEMPLATE_CODE':'100001'}
        with patch.dict(os.environ,config,clear=True):
            gateway=SmsGateway();self.assertTrue(gateway.enabled)
            client=SimpleNamespace(send_sms_verify_code_with_options_async=AsyncMock(return_value=SimpleNamespace(body=SimpleNamespace(code='OK',success=True))),
                                   check_sms_verify_code_with_options_async=AsyncMock(return_value=SimpleNamespace(body=SimpleNamespace(code='OK',success=True,model=SimpleNamespace(verify_result='UNKNOWN')))))
            with patch.object(gateway,'client',return_value=client):
                await gateway.send('+8613800138000')
                req=client.send_sms_verify_code_with_options_async.call_args.args[0]
                self.assertEqual(req.phone_number,'13800138000');self.assertEqual(req.valid_time,300)
                self.assertEqual(req.code_length,6);self.assertFalse(req.return_verify_code)
                self.assertFalse(await gateway.verify('+8613800138000','123456'))
                client.check_sms_verify_code_with_options_async.return_value.body.model.verify_result='PASS'
                self.assertTrue(await gateway.verify('+8613800138000','123456'))
                client.send_sms_verify_code_with_options_async.side_effect=Exception('private-key-detail')
                with self.assertRaises(AccountError) as caught:await gateway.send('+8613800138000')
                self.assertNotIn('private-key-detail',str(caught.exception))
