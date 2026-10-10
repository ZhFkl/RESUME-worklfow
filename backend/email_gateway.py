"""Send registration codes over authenticated TLS SMTP; never expose codes in logs."""
import os
import smtplib
import ssl
from email.message import EmailMessage

from starlette.concurrency import run_in_threadpool

from backend.account_store import AccountError, email_address


class EmailGateway:
    def __init__(self):
        self.host = os.getenv('RESUME_SMTP_HOST', '').strip()
        self.username = os.getenv('RESUME_SMTP_USERNAME', '').strip()
        self.password = os.getenv('RESUME_SMTP_PASSWORD', '')
        self.sender = os.getenv('RESUME_EMAIL_FROM', '').strip()
        self.security = os.getenv('RESUME_SMTP_SECURITY', 'ssl').strip().lower()
        try:
            self.port = int(os.getenv('RESUME_SMTP_PORT') or ('465' if self.security == 'ssl' else '587'))
            self.sender = email_address(self.sender)
            self.enabled = bool(self.host and self.username and self.password
                                and self.security in {'ssl', 'starttls'} and 1 <= self.port <= 65535)
        except (ValueError, AccountError):
            self.enabled = False

    async def send(self, address, code):
        if not self.enabled:
            raise AccountError('邮箱注册服务尚未配置，请稍后再试；已有账号可正常登录', 503)
        await run_in_threadpool(self._send, email_address(address), code)

    def _send(self, address, code):
        message = EmailMessage()
        message['From'] = self.sender
        message['To'] = address
        message['Subject'] = 'Resume Workflow 注册验证码'
        message.set_content(f'你的 Resume Workflow 注册验证码是：{code}\n\n'
                            '验证码 5 分钟内有效，请勿向他人透露。\n'
                            '如果你没有申请注册，请忽略这封邮件。')
        context = ssl.create_default_context()
        try:
            if self.security == 'ssl':
                connection = smtplib.SMTP_SSL(self.host, self.port, timeout=10, context=context)
            else:
                connection = smtplib.SMTP(self.host, self.port, timeout=10)
            with connection as smtp:
                if self.security == 'starttls':
                    smtp.starttls(context=context)
                smtp.login(self.username, self.password)
                if smtp.send_message(message):
                    raise AccountError('验证码邮件发送失败，请稍后再试', 503)
        except AccountError:
            raise
        except Exception:
            raise AccountError('验证码邮件发送失败，请稍后再试', 503) from None
