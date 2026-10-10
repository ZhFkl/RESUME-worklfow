"""账号、可撤销登录会话与邮箱验证码；沿用原用户 ID 和工作目录。"""
import hashlib
import hmac
import os
import re
import secrets
import shutil
import sqlite3
import time
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from backend.session_store import BASE_DIR, EMPTY_KB, now

PH = PasswordHasher()
DUMMY_HASH = PH.hash(secrets.token_urlsafe(24))
SESSION_SECONDS = 7 * 24 * 3600


class AccountError(Exception):
    def __init__(self, message, status=400):
        self.status = status
        super().__init__(message)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def username(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{2,31}', value):
        raise AccountError('用户名需为 3～32 位，以字母开头，可含数字、点、下划线和短横线')
    return value.lower()


def email_address(value):
    if not isinstance(value, str):
        raise AccountError('请输入有效的邮箱地址')
    value = value.strip().lower()
    # Use an ASCII mailbox for SMTP compatibility; verification proves ownership.
    if len(value) > 254 or value.count('@') != 1:
        raise AccountError('请输入有效的邮箱地址')
    local, domain = value.split('@')
    labels = domain.split('.')
    if (not 1 <= len(local) <= 64 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
            or local.startswith('.') or local.endswith('.') or '..' in local
            or len(labels) < 2 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', x) for x in labels)):
        raise AccountError('请输入有效的邮箱地址')
    return value


def phone_number(value):
    if not isinstance(value, str):
        raise AccountError('请输入中国大陆手机号')
    value = re.sub(r'[\s-]', '', value)
    if value.startswith('+86'):
        value = value[3:]
    if not re.fullmatch(r'1[3-9]\d{9}', value):
        raise AccountError('请输入有效的中国大陆手机号（+86）')
    return '+86' + value


def password_hash(value):
    if not isinstance(value, str) or not 6 <= len(value) <= 128:
        raise AccountError('密码长度需为 6～128 个字符')
    return PH.hash(value)


def password_ok(encoded, value):
    if not isinstance(value, str) or len(value) > 128:
        return False
    try:
        return PH.verify(encoded or DUMMY_HASH, value) and bool(encoded)
    except (VerificationError, InvalidHashError):
        return False


class AccountStore:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS accounts (
                    user_id TEXT PRIMARY KEY REFERENCES users(id), username TEXT UNIQUE,
                    phone TEXT UNIQUE, password_hash TEXT, display_name TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user', enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, last_login TEXT);
                CREATE TABLE IF NOT EXISTS login_sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES accounts(user_id),
                    csrf TEXT NOT NULL, expires_at REAL NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS auth_limits (key TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sms_codes (
                    phone TEXT NOT NULL, purpose TEXT NOT NULL, code_hash TEXT NOT NULL,
                    expires_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(phone,purpose));
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(accounts)')}
            if 'email' not in columns:
                db.execute('ALTER TABLE accounts ADD COLUMN email TEXT')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS accounts_email ON accounts(email COLLATE NOCASE) WHERE email IS NOT NULL')
            db.execute('CREATE TABLE IF NOT EXISTS email_codes (email TEXT PRIMARY KEY, code_hash TEXT NOT NULL, expires_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)')
        # Server-local key prevents offline enumeration of the six-digit code hashes.
        path = store.root / 'auth-secret'
        try:
            with open(path, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as file:
                file.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        self.secret = path.read_text().strip().encode()

    def limit(self, key, maximum, window):
        clock = time.time()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM auth_limits WHERE until<?', (clock,))
            row = db.execute('SELECT count FROM auth_limits WHERE key=?', (key,)).fetchone()
            if row and row['count'] >= maximum:
                raise AccountError('操作过于频繁，请稍后再试', 429)
            db.execute('INSERT INTO auth_limits VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1', (key, clock + window))

    def code_hash(self, phone, purpose, code):
        return hmac.new(self.secret, (phone+'|'+purpose+'|'+code).encode(), hashlib.sha256).hexdigest()

    def issue_code(self, phone, purpose):
        code = f'{secrets.randbelow(1000000):06d}'
        with self.store.db() as db:
            db.execute('INSERT INTO sms_codes VALUES (?,?,?,?,0) ON CONFLICT(phone,purpose) DO UPDATE SET code_hash=excluded.code_hash,expires_at=excluded.expires_at,attempts=0',
                       (phone, purpose, self.code_hash(phone, purpose, code), time.time()+300))
        return code

    def discard_code(self, phone, purpose, code):
        with self.store.db() as db:
            db.execute('DELETE FROM sms_codes WHERE phone=? AND purpose=? AND code_hash=?', (phone, purpose, self.code_hash(phone,purpose,code)))

    def consume_code(self, phone, purpose, code):
        valid = False
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM sms_codes WHERE phone=? AND purpose=?', (phone,purpose)).fetchone()
            if row and row['expires_at'] > time.time() and row['attempts'] < 5:
                valid = isinstance(code,str) and len(code)==6 and hmac.compare_digest(row['code_hash'],self.code_hash(phone,purpose,code))
                if valid:
                    db.execute('DELETE FROM sms_codes WHERE phone=? AND purpose=?', (phone,purpose))
                else:
                    db.execute('UPDATE sms_codes SET attempts=attempts+1 WHERE phone=? AND purpose=?', (phone,purpose))
        if not valid:
            raise AccountError('验证码错误、已过期或已使用')

    def issue_email_code(self, address):
        address = email_address(address)
        code = f'{secrets.randbelow(1000000):06d}'
        with self.store.db() as db:
            db.execute('DELETE FROM email_codes WHERE expires_at<?', (time.time(),))
            db.execute('INSERT INTO email_codes VALUES (?,?,?,0) ON CONFLICT(email) DO UPDATE SET code_hash=excluded.code_hash,expires_at=excluded.expires_at,attempts=0',
                       (address, self.code_hash(address, 'email-register', code), time.time()+300))
        return code

    def discard_email_code(self, address, code):
        with self.store.db() as db:
            db.execute('DELETE FROM email_codes WHERE email=? AND code_hash=?',
                       (address, self.code_hash(address, 'email-register', code)))

    def _consume_email_code(self, db, address, code):
        row = db.execute('SELECT * FROM email_codes WHERE email=?', (address,)).fetchone()
        if (row and row['expires_at'] > time.time() and row['attempts'] < 5
                and isinstance(code, str) and re.fullmatch(r'[0-9]{6}', code)
                and hmac.compare_digest(row['code_hash'], self.code_hash(address, 'email-register', code))):
            db.execute('DELETE FROM email_codes WHERE email=?', (address,))
            return
        if row:
            db.execute('UPDATE email_codes SET attempts=attempts+1 WHERE email=?', (address,))
        # Persist failed attempts before raising; successful consumption remains in
        # the account-creation transaction and rolls back if creation fails.
        db.commit()
        raise AccountError('邮箱验证码错误、已过期或已使用')

    def public(self, row):
        return {key:row[key] for key in ('user_id','username','phone','email','display_name','role','enabled','created_at','last_login')} | {'has_password':bool(row['password_hash'])}

    def account(self, uid):
        with self.store.db() as db:
            row = db.execute('SELECT * FROM accounts WHERE user_id=?',(uid,)).fetchone()
        if row is None:
            raise AccountError('账号不存在',404)
        return dict(row)

    def create(self, name=None, password=None, phone=None, legacy_token=None, *, email=None, email_code=None):
        email = email_address(email) if email is not None else None
        name = username(name) if name else None
        encoded = password_hash(password) if password is not None else None
        if not name and not phone:
            raise AccountError('需要用户名或已验证手机号')
        uid, fresh = uuid4().hex, False
        try:
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                if email is not None:
                    self._consume_email_code(db, email, email_code)
                if legacy_token:
                    if not isinstance(legacy_token,str) or len(legacy_token)>200:
                        raise AccountError('旧工作空间凭据无效')
                    old = db.execute('SELECT id FROM users WHERE token_hash=?',(digest(legacy_token),)).fetchone()
                    if not old or db.execute('SELECT 1 FROM accounts WHERE user_id=?',(old['id'],)).fetchone():
                        raise AccountError('旧工作空间凭据无效或已绑定账号')
                    uid = old['id']
                else:
                    fresh = True
                    root = self.store.root/'users'/uid/'workspace'
                    root.mkdir(parents=True)
                    (root/'项目经历库.md').write_text(EMPTY_KB,encoding='utf-8')
                    shutil.copytree(BASE_DIR/'模板',root/'模板')
                    db.execute('INSERT INTO users VALUES (?,?,?)',(uid,digest(secrets.token_urlsafe(32)),now()))
                db.execute('INSERT INTO accounts(user_id,username,phone,password_hash,display_name,created_at,email) VALUES (?,?,?,?,?,?,?)',
                           (uid,name,phone,encoded,name or '用户'+phone[-4:],now(),email))
                if legacy_token:
                    db.execute('UPDATE users SET token_hash=? WHERE id=?',(digest(secrets.token_urlsafe(32)),uid))
        except BaseException as exc:
            if fresh:
                shutil.rmtree(self.store.root/'users'/uid,ignore_errors=True)
            if isinstance(exc,sqlite3.IntegrityError):
                raise AccountError('用户名或邮箱已被使用',409) from exc
            raise
        return uid

    def login_password(self, identifier, password):
        if not isinstance(identifier,str) or len(identifier)>254:
            raise AccountError('用户名、邮箱或密码错误',401)
        key = identifier.strip().lower()
        with self.store.db() as db:
            row = db.execute('SELECT * FROM accounts WHERE username=? OR email=? COLLATE NOCASE',(key,key)).fetchone()
        if not password_ok(row['password_hash'] if row else None,password) or not row or not row['enabled']:
            raise AccountError('用户名、邮箱或密码错误，或账号不可用',401)
        return row['user_id']

    def by_phone(self, phone):
        with self.store.db() as db:
            row = db.execute('SELECT * FROM accounts WHERE phone=?',(phone,)).fetchone()
        return dict(row) if row else None

    def new_session(self, uid):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT enabled FROM accounts WHERE user_id=?',(uid,)).fetchone()
            if not row or not row['enabled']:
                raise AccountError('账号不可用',403)
            db.execute('DELETE FROM login_sessions WHERE expires_at<?',(time.time(),))
            db.execute('INSERT INTO login_sessions VALUES (?,?,?,?,?)',(digest(token),uid,csrf,time.time()+SESSION_SECONDS,now()))
            db.execute('UPDATE accounts SET last_login=? WHERE user_id=?',(now(),uid))
        return token,csrf

    def authenticate(self, token):
        if not token or len(token)>200:
            raise AccountError('请先登录',401)
        with self.store.db() as db:
            row=db.execute('SELECT a.*,s.csrf FROM login_sessions s JOIN accounts a ON a.user_id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND a.enabled=1',(digest(token),time.time())).fetchone()
        if not row:
            raise AccountError('登录已过期，请重新登录',401)
        return dict(row)

    def logout(self, token):
        with self.store.db() as db:
            db.execute('DELETE FROM login_sessions WHERE token_hash=?',(digest(token),))

    def change_password(self, uid, new_password, old_password=None, verified=False):
        row=self.account(uid)
        if not verified and not password_ok(row['password_hash'],old_password):
            raise AccountError('当前密码不正确；忘记密码请联系管理员',403)
        encoded=password_hash(new_password)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if verified:
                db.execute('UPDATE accounts SET password_hash=? WHERE user_id=?',(encoded,uid))
            else:
                changed=db.execute('UPDATE accounts SET password_hash=? WHERE user_id=? AND password_hash=?',
                                   (encoded,uid,row['password_hash']))
                if changed.rowcount != 1:
                    raise AccountError('密码已变化，请重新登录后再试',409)
            db.execute('DELETE FROM login_sessions WHERE user_id=?',(uid,))

    def bind_phone(self, uid, phone):
        try:
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT phone FROM accounts WHERE user_id=?',(uid,)).fetchone()
                if row['phone']:
                    raise AccountError('已绑定手机号，暂不支持直接更换号码')
                db.execute('UPDATE accounts SET phone=? WHERE user_id=?',(phone,uid))
        except sqlite3.IntegrityError as exc:
            raise AccountError('该手机号已绑定其他账号',409) from exc

    def list_users(self):
        with self.store.db() as db:
            return [self.public(row) for row in db.execute('SELECT * FROM accounts ORDER BY created_at DESC')]

    def set_enabled(self, actor, uid, enabled):
        if type(enabled) is not bool:
            raise AccountError('enabled 必须是布尔值')
        if actor==uid and not enabled:
            raise AccountError('不能停用自己的管理员账号')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            target=db.execute('SELECT * FROM accounts WHERE user_id=?',(uid,)).fetchone()
            if not target:raise AccountError('账号不存在',404)
            if target['role']=='admin' and not enabled:
                if db.execute("SELECT count(*) FROM accounts WHERE role='admin' AND enabled=1 AND user_id<>?",(uid,)).fetchone()[0]==0:
                    raise AccountError('至少保留一个启用的管理员')
            db.execute('UPDATE accounts SET enabled=? WHERE user_id=?',(int(enabled),uid))
            if not enabled:db.execute('DELETE FROM login_sessions WHERE user_id=?',(uid,))
