"""短信发送适配器。未配置时明确拒绝，不回显验证码，不模拟发送成功。"""
import importlib.util
import json
import os

from account_store import AccountError


class SmsGateway:
    @property
    def enabled(self):
        return (os.getenv('RESUME_SMS_PROVIDER') == 'aliyun'
                and all(os.getenv(key) for key in ('ALIBABA_CLOUD_ACCESS_KEY_ID','ALIBABA_CLOUD_ACCESS_KEY_SECRET','RESUME_SMS_SIGN_NAME','RESUME_SMS_TEMPLATE_CODE'))
                and importlib.util.find_spec('alibabacloud_dysmsapi20170525') is not None)

    async def send(self, phone, code):
        if not self.enabled:
            raise AccountError('短信服务尚未配置，请使用用户名和密码登录',503)
        from alibabacloud_dysmsapi20170525.client import Client
        from alibabacloud_dysmsapi20170525.models import SendSmsRequest
        from alibabacloud_tea_openapi.models import Config
        from alibabacloud_tea_util.models import RuntimeOptions
        config=Config(access_key_id=os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'],access_key_secret=os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'])
        config.endpoint='dysmsapi.aliyuncs.com';config.protocol='https'
        request=SendSmsRequest(phone_numbers=phone.removeprefix('+86'),sign_name=os.environ['RESUME_SMS_SIGN_NAME'],
            template_code=os.environ['RESUME_SMS_TEMPLATE_CODE'],template_param=json.dumps({'code':code}))
        try:
            result=await Client(config).send_sms_with_options_async(request,RuntimeOptions(connect_timeout=5000,read_timeout=10000,autoretry=False))
            if result.body.code!='OK':
                raise AccountError('短信发送未成功，请稍后重试或联系管理员',503)
        except AccountError:
            raise
        except Exception as exc:
            raise AccountError('短信发送未成功，请稍后重试或联系管理员',503) from exc
