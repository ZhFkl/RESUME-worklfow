"""基于 HttpOnly Cookie 的账号 API；跨站检查及会话 CSRF 校验。"""
import asyncio
import hmac
import json
import os

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from account_store import AccountError, SESSION_SECONDS, digest, phone_number, password_hash

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
            public={'/auth/register','/auth/login','/auth/sms/send','/auth/sms/login','/auth/password/reset'}
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
    return JSONResponse({'registration_open':True,'sms_enabled':request.app.state.sms.enabled,'phone_region':'+86'})


async def register(request):
    data=await body(request);throttle(request,'register')
    if not data.get('username') or data.get('password') is None:
        raise AccountError('请填写用户名和密码')
    uid=await work(request,request.app.state.accounts.create,data['username'],data['password'],None,data.get('legacy_token'))
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


async def send_code(request):
    data=await body(request);purpose=data.get('purpose');phone=phone_number(data.get('phone'))
    if purpose not in {'register','login','reset','bind'}:raise AccountError('验证码用途无效')
    if purpose=='bind':current(request)
    if not request.app.state.sms.enabled:raise AccountError('短信服务尚未配置，请使用用户名和密码登录',503)
    accounts=request.app.state.accounts;throttle(request,'sms')
    # Limits span every purpose, so changing login/reset cannot bypass a send limit.
    accounts.limit('sms:minute:'+digest(phone),1,60)
    accounts.limit('sms:hour:'+digest(phone),5,3600)
    accounts.limit('sms:global:hour',int(os.getenv('RESUME_SMS_HOURLY_LIMIT','100')),3600)
    code=accounts.issue_code(phone,purpose)
    try:
        await asyncio.wait_for(request.app.state.sms.send(phone,code),timeout=20)
    except asyncio.TimeoutError as exc:
        accounts.discard_code(phone,purpose,code)
        raise AccountError('短信发送超时，请稍后重试',503) from exc
    except BaseException:
        accounts.discard_code(phone,purpose,code)
        raise
    return JSONResponse({'sent':True,'retry_after':60,'expires_in':300})


async def sms_login(request):
    data=await body(request);phone=phone_number(data.get('phone'));mode=data.get('mode','login')
    if mode not in {'login','register'}:raise AccountError('操作无效')
    throttle(request,'login',phone);accounts=request.app.state.accounts
    if mode=='register' and data.get('password') is not None:
        # Validate the password before consuming the code.
        await work(request,password_hash,data['password'])
    accounts.consume_code(phone,mode,data.get('code'))
    account=accounts.by_phone(phone)
    if mode=='register':
        if account:raise AccountError('手机号已注册，请使用手机号登录',409)
        uid=await work(request,accounts.create,data.get('username'),data.get('password'),phone,data.get('legacy_token'))
    else:
        if not account or not account['enabled']:raise AccountError('手机号未注册或账号不可用',401)
        uid=account['user_id']
    return login_response(request,uid)


async def reset_password(request):
    data=await body(request);phone=phone_number(data.get('phone'));throttle(request,'reset',phone)
    await work(request,password_hash,data.get('password'))
    accounts=request.app.state.accounts;accounts.consume_code(phone,'reset',data.get('code'))
    account=accounts.by_phone(phone)
    if not account or not account['enabled']:raise AccountError('账号不可用',400)
    await work(request,accounts.change_password,account['user_id'],data.get('password'),verified=True)
    # Do not silently log in after a reset; require an explicit new login.
    response=JSONResponse({'ok':True,'message':'密码已重设，请重新登录'});response.delete_cookie(COOKIE,path='/')
    return response


async def change_password(request):
    account=current(request);data=await body(request);throttle(request,'password',account['user_id'])
    await work(request,request.app.state.accounts.change_password,account['user_id'],data.get('password'),data.get('old_password'))
    return login_response(request,account['user_id'])


async def bind_phone(request):
    account=current(request);data=await body(request);phone=phone_number(data.get('phone'));throttle(request,'bind',account['user_id'])
    if account['phone']:raise AccountError('账号已绑定手机号')
    request.app.state.accounts.consume_code(phone,'bind',data.get('code'))
    request.app.state.accounts.bind_phone(account['user_id'],phone)
    return JSONResponse({'account':request.app.state.accounts.public(request.app.state.accounts.account(account['user_id']))})


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
    Route('/auth/login',login,methods=['POST']),Route('/auth/logout',logout,methods=['POST']),
    Route('/auth/sms/send',send_code,methods=['POST']),Route('/auth/sms/login',sms_login,methods=['POST']),
    Route('/auth/password/reset',reset_password,methods=['POST']),Route('/auth/password',change_password,methods=['PUT']),
    Route('/auth/phone',bind_phone,methods=['PUT']),Route('/admin/users',users),
    Route('/admin/users/{uid}',update_user,methods=['PATCH']),Route('/admin/users/{uid}/password',admin_password,methods=['PUT'])]
