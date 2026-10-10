"""阿里云号码认证（Dypnsapi），不使用旧版 Dysmsapi 短信接口。"""
import importlib.util
import json
import os

from backend.account_store import AccountError


class SmsGateway:
    @property
    def enabled(self):
        return (os.getenv('RESUME_SMS_PROVIDER') == 'aliyun-pnvs'
                and all(os.getenv(k) for k in ('ALIBABA_CLOUD_ACCESS_KEY_ID', 'ALIBABA_CLOUD_ACCESS_KEY_SECRET',
                                              'RESUME_SMS_SIGN_NAME', 'RESUME_SMS_TEMPLATE_CODE'))
                and importlib.util.find_spec('alibabacloud_dypnsapi20170525') is not None)

    def client(self):
        if not self.enabled:
            raise AccountError('短信服务暂不可用，请使用密码登录', 503)
        from alibabacloud_dypnsapi20170525.client import Client
        from alibabacloud_tea_openapi.models import Config
        return Client(Config(access_key_id=os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'],
                             access_key_secret=os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'],
                             endpoint='dypnsapi.aliyuncs.com', protocol='https'))

    async def _request(self, operation, request):
        from alibabacloud_tea_util.models import RuntimeOptions
        try:
            client = self.client()
            response = await getattr(client, operation)(request, RuntimeOptions(
                connect_timeout=5000, read_timeout=10000, autoretry=False))
            result = response.body
            if result is None or result.code != 'OK' or result.success is not True:
                raise AccountError('短信服务请求失败，请稍后再试', 503)
            return result
        except AccountError:
            raise
        except Exception:
            # SDK diagnostics can include request details; do not expose or log them.
            raise AccountError('短信服务请求失败，请检查服务配置或稍后再试', 503) from None

    async def send(self, phone):
        from alibabacloud_dypnsapi20170525.models import SendSmsVerifyCodeRequest
        await self._request('send_sms_verify_code_with_options_async', SendSmsVerifyCodeRequest(
            phone_number=phone.removeprefix('+86'), country_code='86',
            sign_name=os.environ['RESUME_SMS_SIGN_NAME'], template_code=os.environ['RESUME_SMS_TEMPLATE_CODE'],
            template_param=json.dumps({'code':'##code##', 'min':'5'}),
            code_length=6, valid_time=300, interval=60, code_type=1,
            duplicate_policy=1, return_verify_code=False))

    async def verify(self, phone, code):
        from alibabacloud_dypnsapi20170525.models import CheckSmsVerifyCodeRequest
        result = await self._request('check_sms_verify_code_with_options_async', CheckSmsVerifyCodeRequest(
            phone_number=phone.removeprefix('+86'), country_code='86', verify_code=code))
        return result.model is not None and result.model.verify_result == 'PASS'
