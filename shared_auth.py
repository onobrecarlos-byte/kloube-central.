import base64,hashlib,hmac,json,os,secrets,time
from pathlib import Path
from http.cookies import SimpleCookie

ROOT=Path(__file__).resolve().parent
COOKIE='kloube_session'
TTL=12*60*60

def secret():
    configured=os.environ.get('KLOUBE_SESSION_SECRET','')
    if configured:return configured.encode()
    if os.environ.get('VERCEL'):raise RuntimeError('Configure KLOUBE_SESSION_SECRET para habilitar o login online.')
    path=ROOT/'.session-secret'
    if not path.exists():
        try:
            with path.open('x',encoding='ascii') as f:f.write(secrets.token_hex(32))
        except FileExistsError:pass
    return path.read_bytes().strip()

def user_version(email):
    from domain_monitor.server import store
    from domain_monitor.monitor_engine import ADMIN_EMAIL
    data = store.load()
    if email.casefold() in data.get('disabled_users', []): return None
    configured = os.environ.get('KLOUBE_ADMIN_PASSWORD', '')
    if configured and email.casefold() == ADMIN_EMAIL.casefold() and email.casefold() not in data.get('store_managed_users', []):
        return hmac.new(secret(), configured.encode(), hashlib.sha256).hexdigest()
    users=data.get('users',{})
    key=next((k for k in users if k.casefold()==email.casefold()),None)
    if key is None:return None
    return hmac.new(secret(),str(users[key]).encode(),hashlib.sha256).hexdigest()

def issue(email):
    version=user_version(email)
    if version is None:raise ValueError('Usuário não encontrado.')
    raw=json.dumps({'email':email,'exp':int(time.time())+TTL,'v':version},separators=(',',':')).encode()
    payload=base64.urlsafe_b64encode(raw).decode().rstrip('=')
    signature=hmac.new(secret(),payload.encode(),hashlib.sha256).hexdigest()
    return payload+'.'+signature

def validate(token):
    try:
        payload,signature=token.split('.')
        if not hmac.compare_digest(signature,hmac.new(secret(),payload.encode(),hashlib.sha256).hexdigest()):return None
        data=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
        if data['exp']<=time.time():return None
        version=user_version(data['email'])
        return data['email'] if version and hmac.compare_digest(data['v'],version) else None
    except (ValueError,KeyError,TypeError):return None

def current_user(handler):
    try:
        jar=SimpleCookie();jar.load(handler.headers.get('Cookie',''))
        return validate(jar[COOKIE].value) if COOKIE in jar else None
    except Exception:return None

def send(handler,payload,status=200,token=None,clear=False):
    raw=json.dumps(payload,ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header('Content-Type','application/json; charset=utf-8')
    handler.send_header('Content-Length',str(len(raw)))
    handler.send_header('Cache-Control','no-store')
    if token is not None or clear:
        secure='; Secure' if os.environ.get('VERCEL') or handler.headers.get('X-Forwarded-Proto')=='https' else ''
        handler.send_header('Set-Cookie',f'{COOKIE}={token or ""}; Path=/; HttpOnly; SameSite=Lax; Max-Age={0 if clear else TTL}{secure}')
    handler.end_headers();handler.wfile.write(raw)

def gate(handler,method):
    from integrations_api import dispatch
    if dispatch(handler, method):return True
    from urllib.parse import urlparse
    path=urlparse(handler.path).path
    if path == '/health' and method == 'GET':
        from shared_github import enabled
        import platform
        checks = {
            'github_configured': enabled(),
            'session_secret_configured': bool(os.environ.get('KLOUBE_SESSION_SECRET')),
            'report_data_present': (ROOT/'blocks_data.json').is_file(),
            'monitor_data_present': (ROOT/'data.json').is_file(),
        }
        missing = [name for name, value in checks.items() if not value]
        send(handler, {'ok': True, 'release': 'kloube-central-20261105', 'python': platform.python_version(), 'ready': not missing, 'checks': checks, 'missing': missing})
        return True
    user=current_user(handler)
    from domain_monitor.monitor_engine import MASTER_EMAILS, now_iso
    master = bool(user and user.casefold() in MASTER_EMAILS)
    if path == '/api/users' and method == 'GET':
        if not user:send(handler, {'ok':False,'error':'Faça login.'},401);return True
        if not master:send(handler, {'ok':False,'error':'Acesso restrito aos administradores master.'},403);return True
        from domain_monitor.server import store
        data=store.load()
        users=[{'email':email,'master':email.casefold() in MASTER_EMAILS} for email in data.get('users',{})]
        send(handler, {'ok':True,'users':users,'audit':data.get('user_audit',[]),'current_user':user});return True
    if path == '/api/users/delete' and method == 'POST':
        if not user:send(handler, {'ok':False,'error':'Faça login.'},401);return True
        if not master:send(handler, {'ok':False,'error':'Apenas administradores master podem excluir usuários.'},403);return True
        body=handler._read_json()
        target=str(body.get('email','')).strip().casefold()
        from domain_monitor.server import store
        data=store.load();users=data.get('users',{})
        matched=next((email for email in users if email.casefold()==target),None)
        if matched is None:send(handler, {'ok':False,'error':'Usuário não encontrado.'},404);return True
        remaining=[email for email in users if email.casefold() in MASTER_EMAILS and email.casefold()!=target]
        if target in MASTER_EMAILS and not remaining:
            send(handler, {'ok':False,'error':'Mantenha pelo menos um administrador master com acesso.'},409);return True
        del users[matched]
        if target not in data.setdefault('disabled_users',[]):data['disabled_users'].append(target)
        data.setdefault('user_audit',[]).insert(0, {'action':'Usuário excluído','email':matched,'by':user,'at':now_iso()})
        store.save(data)
        send(handler, {'ok':True,'self_deleted':target==user.casefold()},clear=target==user.casefold());return True
    if path in ('/api/session','/monitoramento/api/session'):
        send(handler,{'ok':bool(user),'user':{'email':user} if user else None,'can_manage_users':master},200 if user else 401);return True
    if path in ('/api/logout','/monitoramento/api/logout') and method=='POST':
        send(handler,{'ok':True},clear=True);return True
    if path in ('/api/login','/monitoramento/api/auth/login') and method=='POST':
        try:
            body=handler._read_json()
            from domain_monitor.server import store
            ok,result=store.verify_login(body.get('email',''),body.get('password',''))
            if not ok:send(handler,{'ok':False,'error':result},401)
            else:send(handler,{'ok':True,'user':{'email':result}},token=issue(result))
        except RuntimeError as exc:send(handler,{'ok':False,'error':str(exc)},503)
        except (ValueError,TypeError):send(handler,{'ok':False,'error':'Dados de acesso inválidos.'},400)
        return True
    if path in ('/api/auth/send-reset-code','/monitoramento/api/auth/send-reset-code','/api/auth/reset-password','/monitoramento/api/auth/reset-password') and method=='POST':
        try:
            from password_recovery import request_code,reset_password
            from domain_monitor.server import store
            body=handler._read_json()
            if path.endswith('/send-reset-code'):
                ok,message,status=request_code(body.get('email',''),store)
            else:
                ok,message,status=reset_password(body.get('email',''),body.get('code',''),body.get('new_password',''),store)
            send(handler,{'ok':ok,'message':message,'error':None if ok else message},status)
        except RuntimeError as error:send(handler,{'ok':False,'error':str(error)},503)
        except (ValueError,TypeError):send(handler,{'ok':False,'error':'Dados de recuperação inválidos.'},400)
        return True
    if path in ('/login','/login.html') and method=='GET':
        if user:
            handler.send_response(302);handler.send_header('Content-Length','0');handler.send_header('Location','/');handler.end_headers()
        else:
            raw=(ROOT/'public/login.html').read_bytes()
            handler.send_response(200);handler.send_header('Content-Type','text/html; charset=utf-8');handler.send_header('Content-Length',str(len(raw)));handler.send_header('Cache-Control','no-store');handler.end_headers();handler.wfile.write(raw)
        return True
    public=path in ('/login.js','/styles.css','/favicon.svg') or (path.startswith('/monitoramento/') and not path.startswith('/monitoramento/api/') and path.endswith(('.css','.js','.png','.svg','.jpg','.woff2')))
    if not user and not public:
        if path.startswith('/api/') or path.startswith('/monitoramento/api/'):
            send(handler,{'ok':False,'error':'Faça login para continuar.'},401)
        else:
            handler.send_response(302);handler.send_header('Content-Length','0');handler.send_header('Location','/login');handler.send_header('Cache-Control','no-store');handler.end_headers()
        return True
    if path.endswith('/api/auth/reset-password') and user:
        # The legacy handler may only bypass the reset code for the authenticated user.
        old_read=handler._read_json
        def read():
            body=old_read()
            body['logged_in_user']=user if body.get('email','').casefold()==user.casefold() else ''
            return body
        handler._read_json=read
    if path.endswith('/api/auth/create-user') and user:
        old_read=handler._read_json
        def read():
            body=old_read();body['requester_email']=user;return body
        handler._read_json=read
    return False
