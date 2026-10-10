import os
import smtplib
import unittest
from unittest.mock import patch, MagicMock
from backend.email_gateway import EmailGateway
from backend.account_store import AccountError, email_address


class EmailGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_tls_delivery(self):
        config={'RESUME_SMTP_HOST':'smtp.example.com','RESUME_SMTP_USERNAME':'sender',
                'RESUME_SMTP_PASSWORD':'secret','RESUME_EMAIL_FROM':'sender@example.com'}
        for security,constructor in [('ssl','SMTP_SSL'),('starttls','SMTP')]:
            with self.subTest(security=security), patch.dict(os.environ,dict(config,RESUME_SMTP_SECURITY=security),clear=True), patch('backend.email_gateway.smtplib.'+constructor) as factory:
                smtp=factory.return_value.__enter__.return_value
                smtp.send_message.return_value={}
                gateway=EmailGateway();self.assertTrue(gateway.enabled)
                await gateway.send('receiver@example.net','123456')
                smtp.login.assert_called_once_with('sender','secret')
                message=smtp.send_message.call_args.args[0]
                self.assertEqual(message['To'],'receiver@example.net')
                self.assertEqual(message['From'],'sender@example.com')
                self.assertIn('123456',message.get_content())
                if security=='starttls':smtp.starttls.assert_called_once()

    async def test_unconfigured_or_failed_delivery_never_succeeds(self):
        with patch.dict(os.environ,{},clear=True):
            gateway=EmailGateway();self.assertFalse(gateway.enabled)
            with self.assertRaises(AccountError):await gateway.send('receiver@example.net','123456')
        config={'RESUME_SMTP_HOST':'smtp.example.com','RESUME_SMTP_USERNAME':'sender',
                'RESUME_SMTP_PASSWORD':'secret','RESUME_EMAIL_FROM':'sender@example.com'}
        with patch.dict(os.environ,config,clear=True), patch('backend.email_gateway.smtplib.SMTP_SSL',side_effect=smtplib.SMTPException('private diagnostic')):
            with self.assertRaises(AccountError) as caught:
                await EmailGateway().send('receiver@example.net','123456')
            self.assertNotIn('private diagnostic',str(caught.exception))

    def test_addresses_are_not_limited_to_a_provider(self):
        for address in ['person@qq.com','person@163.com','person@gmail.com','person@outlook.com','person+tag@company.example']:
            self.assertEqual(email_address(address.upper()),address)
        for address in ['a\r\nBcc:b@example.com','a@@example.com','.a@example.com','a..b@example.com','a@-example.com']:
            with self.assertRaises(AccountError):email_address(address)
