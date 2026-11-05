"""Password-recovery emails only. Monitoring alerts remain disabled."""
import hashlib,hmac,os,re,secrets,time,smtplib,ssl
from email.message import EmailMessage
from shared_auth import secret

GENERIC='Se o e-mail estiver cadastrado, você receberá um código de recuperação.'

def smtp_config(settings):
    password=os.environ.get('KLOUBE_RESET_SMTP_PASS') or os.environ.get('KLOUBE_SMTP_PASS') or settings.get('smtp_pass','')
    user=os.environ.get('KLOUBE_RESET_SMTP_USER') or settings.get('smtp_user') or 'growthnobre@gmail.com'
    if not password:raise RuntimeError('O envio de recuperação ainda não foi configurado. Falta a senha de aplicativo do remetente.')
    return user,password

def send_code(email,code,settings):
    user,password=smtp_config(settings)
    message=EmailMessage()
    message['From']=user;message['To']=email;message['Subject']='Kloube — recuperação de senha'
    message.set_content(f'Seu código para recuperar a senha do Kloube é: {code}\n\nVálido por 15 minutos e para um único uso. Se você não solicitou, ignore esta mensagem.')
    host=settings.get('smtp_host') or 'smtp.gmail.com';port=int(settings.get('smtp_port') or 587)
    context=ssl.create_default_context()
    if port==465:
        with smtplib.SMTP_SSL(host,port,timeout=15,context=context) as server:
            server.login(user,password);server.send_message(message)
    else:
        with smtplib.SMTP(host,port,timeout=15) as server:
            server.ehlo();server.starttls(context=context);server.ehlo();server.login(user,password);server.send_message(message)

def digest(value):return hmac.new(secret(),value.encode(),hashlib.sha256).hexdigest()

def credential_version(email,data):
    from domain_monitor.monitor_engine import ADMIN_EMAIL
    password=data.get('users',{}).get(email,'')
    configured=os.environ.get('KLOUBE_ADMIN_PASSWORD','')
    if configured and email.casefold()==ADMIN_EMAIL.casefold() and email not in data.get('store_managed_users',[]):password=configured
    return digest(str(password))

def request_code(email,store):
    email=str(email or '').strip().casefold()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):return False,'Informe um e-mail válido.',400
    data=store.load();settings=data.get('settings',{})
    smtp_config(settings)  # Explicit configuration failure; never pretend a code was sent.
    matched=next((name for name in data.get('users',{}) if name.casefold()==email),None)
    if not matched or email in data.get('disabled_users',[]):return True,GENERIC,200
    requests=data.setdefault('password_recovery',{})
    previous=requests.get(email,{})
    now=time.time()
    if now-previous.get('requested_at',0)<60:return False,'Aguarde 60 segundos antes de solicitar outro código.',429
    recent=[stamp for stamp in previous.get('request_times',[]) if now-stamp<3600]
    if len(recent)>=5:return False,'Limite de solicitações atingido. Tente novamente mais tarde.',429
    code=f'{secrets.randbelow(1000000):06d}'
    request_id=secrets.token_hex(12)
    requests[email]={'id':request_id,'digest':digest(email+':'+code),'expires_at':now+900,'requested_at':now,'attempts':0,'request_times':recent+[now],'version':credential_version(matched,data)}
    store.save(data)
    try:send_code(matched,code,settings)
    except Exception:
        fresh=store.load()
        challenge=fresh.get('password_recovery',{}).get(email,{})
        if challenge.get('id')==request_id:
            challenge.pop('digest',None);challenge['expires_at']=0;store.save(fresh)
        return False,'Não foi possível enviar o código. Confira as credenciais do remetente.',503
    return True,GENERIC,200

def reset_password(email,code,password,store):
    from domain_monitor.monitor_engine import hash_account_password,now_iso
    email=str(email or '').strip().casefold();code=str(code or '').strip();password=str(password or '')
    if len(password)<6:return False,'A senha deve ter pelo menos 6 caracteres.',400
    data=store.load()
    matched=next((name for name in data.get('users',{}) if name.casefold()==email),None)
    challenge=data.get('password_recovery',{}).get(email,{})
    if not matched or email in data.get('disabled_users',[]) or challenge.get('expires_at',0)<=time.time() or challenge.get('attempts',0)>=5 or not challenge.get('digest'):
        return False,'Código inválido ou expirado. Solicite um novo código.',400
    if challenge.get('version')!=credential_version(matched,data):return False,'Código inválido ou expirado. Solicite um novo código.',400
    if not re.fullmatch(r'\d{6}',code) or not hmac.compare_digest(challenge['digest'],digest(email+':'+code)):
        challenge['attempts']=challenge.get('attempts',0)+1;store.save(data)
        return False,'Código inválido ou expirado.',400
    data['users'][matched]=hash_account_password(password)
    if email not in data.setdefault('store_managed_users',[]):data['store_managed_users'].append(email)
    challenge.pop('digest',None);challenge['expires_at']=0
    data.setdefault('user_audit',[]).insert(0,{'action':'Senha redefinida por código','email':matched,'by':matched,'at':now_iso()})
    store.save(data)
    return True,'Senha alterada. Entre com a nova senha.',200
