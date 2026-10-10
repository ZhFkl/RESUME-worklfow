"""基于 HttpOnly Cookie 的账号 API；跨站检查及会话 CSRF 校验。"""
import hmac
import json
import os

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from backend.account_store import AccountError, SESSION_SECONDS, digest, email_address, phone_number, username, password_hash

COOKIE='resume_session'


def current(request):
    return request.app.state.accounts.authenticate(request.cookies.get(COOKIE,''))


class AccountProtection(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        expected_user = request.headers.get('x-resume-account')
        if expected_user:
            try:
                if current(request)['user_id'] != expected_user:
                    return JSONResponse({'error':'账号已切换，请刷新页面','account_changed':True},409)
            except AccountError as exc:
                return JSONResponse({'error':str(exc)},exc.status)
        if request.method not in {'GET','HEAD','OPTIONS'}:
            if request.app.state.allow_legacy_users and request.headers.get('authorization','').startswith('Bearer '):
                return await call_next(request)
            if request.app.state.allow_legacy_users and request.url.path=='/users':
                return await call_next(request)
            origin=request.headers.get('origin')
            expected=os.getenv('RESUME_PUBLIC_ORIGIN','').rstrip('/') or str(request.base_url).rstrip('/')
            if origin and origin != expected:
                return JSONResponse({'error':'请求来源不匹配'},403)
            public={'/auth/register','/auth/login','/auth/email/send','/auth/sms/send','/auth/sms/login'}
            if request.url.path not in public:
                try:
                    account=current(request)
                    if not hmac.compare_digest(account['csrf'],request.headers.get('x-csrf-token','')):
                        return JSONResponse({'error':'页面登录状态已变化，请刷新后重试'},403)
                except AccountError as exc:
                    return JSONResponse({'error':str(exc)},exc.status)
            if request.headers.get('x-resume-client')!='browser':
                return JSONResponse({'error':'缺少客户端请求标识'},403)
        response=await call_next(request)
        if request.url.path.startswith(('/auth/','/admin/')):
            response.headers['Cache-Control']='no-store'
        return response


async def body(request):
    raw=await request.body()
    if len(raw)>16384:raise AccountError('请求内容过长',413)
    try:data=json.loads(raw)
    except (ValueError,UnicodeDecodeError):raise AccountError('请求必须为 JSON')
    if not isinstance(data,dict):raise AccountError('请求必须为 JSON 对象')
    return data


async def work(request,fn,*args,**kwargs):
    async with request.app.state.auth_workers:
        return await run_in_threadpool(fn,*args,**kwargs)


def throttle(request,action,identifier=None):
    accounts=request.app.state.accounts
    ip=request.client.host if request.client else 'unknown'
    accounts.limit(action+':ip:'+digest(ip),30 if action=='login' else 15,900)
    if identifier is not None:
        accounts.limit(action+':id:'+digest(str(identifier).strip().lower()[:100]),10,900)


def login_response(request,uid):
    accounts=request.app.state.accounts
    token,csrf=accounts.new_session(uid)
    result=JSONResponse({'account':accounts.public(accounts.account(uid)),'csrf':csrf})
    secure=os.getenv('RESUME_COOKIE_SECURE','').lower() in {'1','true'} or request.url.scheme=='https'
    result.set_cookie(COOKIE,token,max_age=SESSION_SECONDS,httponly=True,secure=secure,samesite='lax',path='/')
    return result


async def config(request):
    return JSONResponse({'registration_open':request.app.state.email.enabled or request.app.state.sms.enabled,
                         'email_enabled':request.app.state.email.enabled, 'sms_enabled':request.app.state.sms.enabled})


async def send_email_code(request):
    data = await body(request)
    address = email_address(data.get('email'))
    if not request.app.state.email.enabled:
        raise AccountError('邮箱注册服务尚未配置，请稍后再试；已有账号可正常登录', 503)
    throttle(request, 'email-send')
    accounts = request.app.state.accounts
    accounts.limit('email:minute:'+digest(address), 1, 60)
    accounts.limit('email:hour:'+digest(address), 5, 3600)
    accounts.limit('email:global:hour', 100, 3600)
    code = accounts.issue_email_code(address)
    try:
        async with request.app.state.auth_workers:
            await request.app.state.email.send(address, code)
    except BaseException:
        accounts.discard_email_code(address, code)
        raise
    return JSONResponse({'sent':True, 'retry_after':60, 'expires_in':300})


def require_sms(request):
    if not request.app.state.sms.enabled:
        raise AccountError('短信服务暂不可用，请使用密码登录', 503)


async def send_sms_code(request):
    data = await body(request)
    phone = phone_number(data.get('phone'))
    purpose = data.get('purpose')
    if purpose not in {'login','register','bind'}:
        raise AccountError('短信验证码用途无效')
    owner = ''
    if purpose == 'bind':
        account = current(request)
        if not hmac.compare_digest(account['csrf'],request.headers.get('x-csrf-token','')):
            raise AccountError('请刷新页面后重试',403)
        if account['phone']:
            raise AccountError('账号已绑定手机号')
        owner = account['user_id']
    require_sms(request)
    throttle(request,'sms-send')
    accounts=request.app.state.accounts
    accounts.limit('sms:minute:'+digest(phone),1,60)
    accounts.limit('sms:hour:'+digest(phone),5,3600)
    accounts.limit('sms:global:hour',100,3600)
    challenge=await request.app.state.sms_auth.send(phone,purpose,owner)
    return JSONResponse({'sent':True,'challenge':challenge,'retry_after':60,'expires_in':300})


async def sms_login(request):
    require_sms(request)
    data=await body(request);phone=phone_number(data.get('phone'))
    mode=data.get('mode','login')
    if mode not in {'login','register'}:
        raise AccountError('操作无效')
    throttle(request,'sms-login',phone)
    accounts=request.app.state.accounts
    if mode == 'register':
        username(data.get('username'))
        await work(request,password_hash,data.get('password'))
    await request.app.state.sms_auth.verify(phone,mode,data.get('challenge'),data.get('code'))
    account=accounts.by_phone(phone)
    if mode == 'register':
        if account:
            raise AccountError('手机号已注册或绑定，请使用短信登录',409)
        uid=await work(request,accounts.create,data['username'],data['password'],phone,data.get('legacy_token'))
    else:
        if not account:
            uid=await work(request,accounts.create,None,None,phone,data.get('legacy_token'))
        else:
            if not account['enabled']:
                raise AccountError('账号不可用',403)
            uid=account['user_id']
    return login_response(request,uid)


async def bind_phone(request):
    require_sms(request)
    account=current(request);data=await body(request)
    if account['phone']:
        raise AccountError('账号已绑定手机号')
    phone=phone_number(data.get('phone'));throttle(request,'sms-bind',account['user_id'])
    await request.app.state.sms_auth.verify(phone,'bind',data.get('challenge'),data.get('code'),account['user_id'])
    request.app.state.accounts.bind_phone(account['user_id'],phone)
    return JSONResponse({'account':request.app.state.accounts.public(request.app.state.accounts.account(account['user_id']))})


async def register(request):
    data=await body(request);throttle(request,'register')
    if not data.get('username') or data.get('password') is None:
        raise AccountError('请填写用户名和密码')
    address = email_address(data.get('email'))
    uid=await work(request,request.app.state.accounts.create,data['username'],data['password'],None,data.get('legacy_token'),
                   email=address,email_code=data.get('code'))
    return login_response(request,uid)


async def login(request):
    data=await body(request);throttle(request,'login',data.get('identifier'))
    uid=await work(request,request.app.state.accounts.login_password,data.get('identifier'),data.get('password'))
    return login_response(request,uid)


async def me(request):
    account=current(request)
    return JSONResponse({'account':request.app.state.accounts.public(account),'csrf':account['csrf']})


async def logout(request):
    request.app.state.accounts.logout(request.cookies.get(COOKIE,''))
    response=JSONResponse({'ok':True});response.delete_cookie(COOKIE,path='/')
    return response


async def change_password(request):
    account=current(request);data=await body(request);throttle(request,'password',account['user_id'])
    await work(request,request.app.state.accounts.change_password,account['user_id'],data.get('password'),data.get('old_password'))
    return login_response(request,account['user_id'])


def admin(request):
    account=current(request)
    if account['role']!='admin':raise AccountError('需要管理员权限',403)
    return account


async def users(request):
    admin(request)
    return JSONResponse({'users':request.app.state.accounts.list_users()})


async def update_user(request):
    actor=admin(request);data=await body(request);uid=request.path_params['uid']
    request.app.state.accounts.set_enabled(actor['user_id'],uid,data.get('enabled'))
    return JSONResponse({'ok':True})


async def admin_password(request):
    admin(request);data=await body(request);uid=request.path_params['uid']
    await work(request,request.app.state.accounts.change_password,uid,data.get('password'),verified=True)
    return JSONResponse({'ok':True})


async def error(request,exc):
    return JSONResponse({'error':str(exc)},exc.status,headers={'Cache-Control':'no-store'})


routes=[Route('/auth/config',config),Route('/auth/me',me),Route('/auth/register',register,methods=['POST']),
    Route('/auth/email/send',send_email_code,methods=['POST']),
    Route('/auth/sms/send',send_sms_code,methods=['POST']),Route('/auth/sms/login',sms_login,methods=['POST']),
    Route('/auth/phone',bind_phone,methods=['PUT']),
    Route('/auth/login',login,methods=['POST']),Route('/auth/logout',logout,methods=['POST']),
    Route('/auth/password',change_password,methods=['PUT']),Route('/admin/users',users),
    Route('/admin/users/{uid}',update_user,methods=['PATCH']),Route('/admin/users/{uid}/password',admin_password,methods=['PUT'])]
