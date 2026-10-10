"""Local SMS attempts bind provider verification to one browser request and purpose."""
import re
import secrets
import time

from backend.account_store import AccountError, digest


class SmsAuth:
    def __init__(self, store, gateway):
        self.store, self.gateway = store, gateway
        with store.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS sms_attempts (
                phone TEXT PRIMARY KEY, token_hash TEXT NOT NULL, purpose TEXT NOT NULL,
                owner TEXT NOT NULL, expires REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL)''')

    async def send(self, phone, purpose, owner=''):
        token = secrets.token_urlsafe(32)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM sms_attempts WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO sms_attempts VALUES (?,?,?,?,?,0,?) ON CONFLICT(phone) DO UPDATE SET '
                       'token_hash=excluded.token_hash,purpose=excluded.purpose,owner=excluded.owner,'
                       'expires=excluded.expires,attempts=0,state=excluded.state',
                       (phone, digest(token), purpose, owner, time.time()+300, 'sending'))
        try:
            await self.gateway.send(phone)
            with self.store.db() as db:
                db.execute("UPDATE sms_attempts SET state='ready' WHERE phone=? AND token_hash=?",
                           (phone, digest(token)))
        except BaseException:
            with self.store.db() as db:
                db.execute('DELETE FROM sms_attempts WHERE phone=? AND token_hash=?', (phone, digest(token)))
            raise
        return token

    async def verify(self, phone, purpose, token, code, owner=''):
        if not isinstance(token, str) or len(token)>100 or not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}',code):
            raise AccountError('请获取并填写 6 位短信验证码')
        hashed = digest(token)
        with self.store.db() as db:
            claimed = db.execute("UPDATE sms_attempts SET state='checking',attempts=attempts+1 "
                "WHERE phone=? AND token_hash=? AND purpose=? AND owner=? AND expires>? AND attempts<5 AND state='ready'",
                (phone,hashed,purpose,owner,time.time()))
            if claimed.rowcount != 1:
                raise AccountError('短信验证码已过期、已使用或尝试过多，请重新获取')
        try:
            passed = await self.gateway.verify(phone, code)
        except BaseException:
            with self.store.db() as db:
                db.execute("UPDATE sms_attempts SET state='ready' WHERE phone=? AND token_hash=? AND state='checking'",(phone,hashed))
            raise
        with self.store.db() as db:
            updated=db.execute("UPDATE sms_attempts SET state=? WHERE phone=? AND token_hash=? AND state='checking' AND expires>?",
                              ('used' if passed else 'ready',phone,hashed,time.time()))
        if not passed or updated.rowcount != 1:
            raise AccountError('短信验证码错误或已过期')
