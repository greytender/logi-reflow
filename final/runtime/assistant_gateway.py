"""Session-scoped credentials and real provider Chat Completions requests."""
import json
import time
import secrets
from http.cookies import SimpleCookie, CookieError
from threading import RLock
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

COOKIE='logireflow_models'
HOSTS={'deepseek':('api.deepseek.com',),'qwen':('dashscope.aliyuncs.com','aliyuncs.com'),
       'glm':('open.bigmodel.cn',),'kimi':('api.moonshot.cn','api.moonshot.ai')}

class ProviderError(ValueError): pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ProviderError('接口地址发生跳转，请检查 Base URL')

class ModelGateway:
    def __init__(self):
        self.sessions={}
        self.lock=RLock()

    @staticmethod
    def token(cookie):
        try:
            jar=SimpleCookie();jar.load(cookie or '')
            return jar[COOKIE].value if COOKIE in jar else None
        except CookieError:return None

    def _prune(self):
        now=time.monotonic()
        for token,entry in list(self.sessions.items()):
            if entry['expires']<now:del self.sessions[token]

    def config(self,cookie,provider):
        with self.lock:
            self._prune()
            value=self.sessions.get(self.token(cookie),{}).get('providers',{}).get(provider)
            return dict(value) if value else None

    def save(self,cookie,body):
        provider=body.get('provider')
        if provider not in HOSTS:raise ValueError('请选择模型服务')
        base=str(body.get('base_url','')).strip().rstrip('/')
        url=urlsplit(base)
        if (url.scheme!='https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.port not in (None,443)
                or not any(url.hostname==h or url.hostname.endswith('.'+h) for h in HOSTS[provider])):
            raise ValueError('请填写所选模型服务的 HTTPS 接口地址')
        model=str(body.get('model','')).strip()
        if not model or len(model)>200:raise ValueError('请填写模型名称')
        previous=self.config(cookie,provider)
        key=body.get('api_key')
        if not key and previous and previous['base_url']==base:key=previous['api_key']
        if not isinstance(key,str) or not 10<=len(key)<=4096 or not key.isascii() or any(ord(c)<33 or ord(c)>126 for c in key):
            raise ValueError('请填写有效的 API Key')
        with self.lock:
            self._prune()
            token=self.token(cookie)
            if token not in self.sessions:
                if len(self.sessions)>=32:raise ValueError('连接数量已满，请稍后重试')
                token=secrets.token_urlsafe(32)
                self.sessions[token]={'providers':{},'expires':time.monotonic()+86400}
            self.sessions[token]['providers'][provider]=dict(base_url=base,model=model,api_key=key)
            self.sessions[token]['expires']=time.monotonic()+86400
        return token

    def complete(self,cookie,provider,message,context):
        config=self.config(cookie,provider)
        if not config:raise ValueError('请先填写该模型的 API Key')
        prompt=('你是城配续航调度助手小航，用简洁中文直接回答用户的问题。以下 JSON 是当前计划的工具数据，'
                '只依据这些数据解释数量、费用、冷链与回收。不得编造签收、派单、节省或核验结论。'
                '不复述身份介绍、免责声明或接口状态。数据不足时明确指出所需字段。\n'+
                json.dumps(context,ensure_ascii=False))
        request=Request(config['base_url']+'/chat/completions',method='POST',
            data=json.dumps(dict(model=config['model'],messages=[dict(role='system',content=prompt),
                dict(role='user',content=message)],stream=False,max_tokens=1200),ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+config['api_key']})
        try:
            with build_opener(NoRedirect()).open(request,timeout=90) as response:
                raw=response.read(2_000_001)
            if len(raw)>2_000_000:raise ProviderError('模型响应过长，请缩短问题后重试')
            data=json.loads(raw)
            answer=data['choices'][0]['message']['content']
            if not isinstance(answer,str) or not answer.strip():raise ProviderError('模型没有返回内容，请检查模型名称')
            return dict(answer=answer.strip(),model=config['model'],usage=data.get('usage',{}))
        except HTTPError as error:
            if error.code in (401,403):raise ProviderError('API Key 校验失败，请检查密钥与权限') from None
            if error.code==429:raise ProviderError('请求额度或频率达到限制，请检查账户额度后重试') from None
            raise ProviderError(f'模型服务返回 {error.code}，请检查接口地址和模型名称') from None
        except (URLError,TimeoutError,OSError):raise ProviderError('模型请求失败，请检查网络后重试') from None
        except (KeyError,IndexError,TypeError,json.JSONDecodeError):raise ProviderError('模型响应格式异常，请检查接口配置') from None
