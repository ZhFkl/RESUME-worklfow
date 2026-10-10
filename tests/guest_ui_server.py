"""Disposable UI test server: real auth/storage, fake model; never uses production data."""
import sys,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'tests')]
import uvicorn
from backend.api import create_app
from test_backend import FakeCore
from starlette.responses import JSONResponse
from starlette.routing import Route
scratch=tempfile.TemporaryDirectory(prefix='resume-guest-ui-')
class TestEmail:
    enabled = True
    def __init__(self):self.sent = {}
    async def send(self, address, code):self.sent[address] = code
class TestSms:
    enabled = True
    def __init__(self):self.codes = {}
    async def send(self, phone):
        import secrets
        self.codes[phone] = f'{secrets.randbelow(1000000):06d}'
    async def verify(self, phone, code):return self.codes.get(phone) == code
sms = TestSms()
mail = TestEmail()
app=create_app(scratch.name,core_factory=FakeCore,email_factory=lambda:mail,sms_factory=lambda:sms)
# This mailbox exists only in this disposable loopback test server.
async def email_code(request):
    return JSONResponse({'code':mail.sent.get(request.query_params.get('email'))})
app.router.routes.insert(0,Route('/test/email-code',email_code))
async def sms_code(request):
    from backend.account_store import phone_number
    return JSONResponse({'code':sms.codes.get(phone_number(request.query_params.get('phone')))})
async def sms_reset(request):
    with app.state.store.db() as db:
        db.execute("DELETE FROM auth_limits WHERE key LIKE 'sms:minute:%'")
    return JSONResponse({'ok':True})
app.router.routes.insert(0,Route('/test/sms-code',sms_code))
app.router.routes.insert(0,Route('/test/sms-reset',sms_reset))
uvicorn.run(app,host='127.0.0.1',port=18800,log_level='warning')
