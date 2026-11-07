"""Provider-independent automation API. Tokens never authorize legacy/admin routes."""
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs

EVENTS = ('store_published', 'store_reactivated', 'ads_suspended', 'store_inactive')
SCOPES = ('read', 'write')


def fingerprint(token):
    return hashlib.sha256(token.encode()).hexdigest()


def text(body, key, required=False, limit=2000):
    value = body.get(key, '')
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('Campo inválido: ' + key)
    value = value.strip()
    if required and not value:
        raise ValueError('Campo obrigatório: ' + key)
    return value


def public_token(item):
    return {k: item.get(k) for k in ('id', 'name', 'scopes', 'created_at', 'expires_at', 'revoked_at')}


def authenticate(data, token, needed):
    from shared_auth import user_version
    digest = fingerprint(token)
    item = next((x for x in data.get('integration_tokens', [])
                 if hmac.compare_digest(x.get('hash', ''), digest)), None)
    if not item or item.get('revoked_at') or item['expires_at'] <= datetime.now().timestamp():
        return None
    version = user_version(item['owner'])
    if not version or not hmac.compare_digest(item.get('user_version', ''), version):
        return None
    return item if needed in item.get('scopes', []) else None


def create_event(data, body, actor, token_id):
    import server
    kind = text(body, 'event_type', True)
    if kind not in EVENTS:
        raise ValueError('event_type inválido.')
    name = server.normalize_store_name(text(body, 'store_name', True, 200))
    if not name:
        raise ValueError('Nome da loja inválido.')
    category = text(body, 'inactive_category')
    if kind == 'store_inactive' and category not in ('dmca', 'ads_block', 'domain_down'):
        raise ValueError('Informe inactive_category: dmca, ads_block ou domain_down.')
    account = text(body, 'account_number', limit=100)
    if kind == 'ads_suspended' and not account:
        raise ValueError('Informe account_number para a conta suspensa.')
    also = body.get('also_mark_store_inactive', False)
    if not isinstance(also, bool):
        raise ValueError('also_mark_store_inactive deve ser booleano.')
    stamp = text(body, 'created_at') or server.now_iso()
    if len(stamp) == 10:
        stamp += ' 00:00:00'
    parsed = datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S')
    if parsed > datetime.now(server.BRT_TZ).replace(tzinfo=None) + timedelta(minutes=5):
        raise ValueError('A data do evento não pode estar no futuro.')
    event = dict(id='rec_' + uuid.uuid4().hex[:10], event_type=kind, store_name=name,
                 domain=text(body, 'domain', limit=500),
                 account_number=server.normalize_google_ads_account(account) if kind == 'ads_suspended' else '—',
                 inactive_category=category if kind == 'store_inactive' else '',
                 reason=text(body, 'reason'), created_at=stamp, month_key=stamp[:7],
                 source='integration', created_by=actor, integration_id=token_id)
    data.setdefault('records', []).insert(0, event)
    server.update_catalog_from_event(data, event)
    if kind == 'ads_suspended' and also:
        closure = dict(event, id='rec_' + uuid.uuid4().hex[:10], event_type='store_inactive', inactive_category='ads_block')
        data['records'].insert(0, closure)
        server.update_catalog_from_event(data, closure)
    return event


def dispatch(handler, method):
    path = urlparse(handler.path).path
    if not (path.startswith('/api/v1/') or path in ('/api/integrations', '/api/integrations/revoke')):
        return False
    import server
    from shared_auth import current_user, send, user_version
    def reply(payload, status=200):
        send(handler, payload, status)
        return True
    try:
        if method == 'POST' and int(handler.headers.get('Content-Length', '0')) > 65536:
            return reply({'ok': False, 'error': 'Corpo da requisição muito grande.'}, 413)
        origin = handler.headers.get('Origin')
        if path.startswith('/api/integrations') and method == 'POST' and origin and urlparse(origin).netloc != handler.headers.get('Host'):
            return reply({'ok': False, 'error': 'Origem não permitida.'}, 403)
        with server.monitor_bridge.lock:
            data = server.store.load()
            if path.startswith('/api/integrations'):
                owner = current_user(handler)
                if not owner:
                    return reply({'ok': False, 'error': 'Faça login.'}, 401)
                items = data.setdefault('integration_tokens', [])
                if method == 'GET' and path == '/api/integrations':
                    return reply({'ok': True, 'tokens': [public_token(x) for x in items if x['owner'] == owner]})
                if method != 'POST':
                    return reply({'ok': False, 'error': 'Método não permitido.'}, 405)
                body = handler._read_json()
                if not isinstance(body, dict):
                    raise ValueError('Envie um objeto JSON.')
                if path.endswith('/revoke'):
                    item = next((x for x in items if x['owner'] == owner and x['id'] == body.get('id')), None)
                    if not item:
                        return reply({'ok': False, 'error': 'Conexão não encontrada.'}, 404)
                    item['revoked_at'] = server.now_iso()
                    server.store.save(data)
                    return reply({'ok': True})
                name = text(body, 'name', True, 100)
                scopes = body.get('scopes', ['read'])
                if not isinstance(scopes, list) or not scopes or any(x not in SCOPES for x in scopes):
                    raise ValueError('Permissões inválidas.')
                days = body.get('expires_in_days', 90)
                if type(days) is not int or not 1 <= days <= 365:
                    raise ValueError('Validade deve ser entre 1 e 365 dias.')
                token = 'klb_' + secrets.token_urlsafe(32)
                item = dict(id=uuid.uuid4().hex, name=name, owner=owner, hash=fingerprint(token),
                            scopes=list(set(scopes)), created_at=server.now_iso(),
                            expires_at=(datetime.now() + timedelta(days=days)).timestamp(),
                            user_version=user_version(owner))
                items.append(item)
                server.store.save(data)
                return reply({'ok': True, 'token': token, 'connection': public_token(item)}, 201)

            allowed = {('/api/v1/stores', 'GET'): 'read', ('/api/v1/stores/update', 'POST'): 'write', ('/api/v1/reports', 'GET'): 'read',
                       ('/api/v1/records', 'GET'): 'read', ('/api/v1/records', 'POST'): 'write',
                       ('/api/v1/openapi.json', 'GET'): 'read'}
            scope = allowed.get((path, method))
            if not scope:
                return reply({'ok': False, 'error': 'Rota ou método não permitido.'}, 404)
            authorization = handler.headers.get('Authorization', '')
            item = authenticate(data, authorization[7:] if authorization.startswith('Bearer ') else '', scope)
            if not item:
                return reply({'ok': False, 'error': 'Token inválido, expirado ou sem permissão.'}, 401)
            if path.endswith('/openapi.json'):
                return reply(openapi())
            if path == '/api/v1/stores/update':
                body = handler._read_json()
                if not isinstance(body, dict):raise ValueError('Envie um objeto JSON.')
                name = text(body, 'store_name', True, 200)
                catalog = data.get('stores_catalog', {})
                matched = next((k for k in catalog if k.casefold() == name.casefold()), None)
                if matched is None:return reply({'ok':False, 'error':'Loja não encontrada.'},404)
                status = text(body, 'status', True)
                if status not in ('Ativa','Inativa'):raise ValueError('Status inválido.')
                from domain_monitor.monitor_engine import normalize_domain_input
                host, url = normalize_domain_input(text(body,'domain',True,500))
                from domain_monitor.server import store as monitor_store
                monitoring = monitor_store.load()
                candidates = [d for d in monitoring.get('domains',[]) if d.get('name','').casefold()==matched.casefold()]
                domain = next((d for d in candidates if server.monitor_bridge.host(d.get('domain',''))==server.monitor_bridge.host(host)),None)
                domain = domain or next((d for d in candidates if d.get('operational_active') is not False and d.get('status')!='draft'),None)
                domain = domain or next(iter(candidates),None)
                if domain is None:
                    return reply({'ok':False,'error':'Domínio da loja não encontrado no monitoramento.'},409)
                duplicate = next((d for d in monitoring.get('domains',[]) if d is not domain and server.monitor_bridge.host(d.get('domain',''))==server.monitor_bridge.host(host)),None)
                if duplicate is not None and duplicate.get('operational_active') is not False and duplicate.get('status')!='draft':
                    return reply({'ok':False,'error':'Domínio já associado a outra loja ativa.'},409)
                if duplicate is not None:
                    domain.update(operational_active=False,status='draft',reason='Endereço corrigido pela integração.')
                    domain = duplicate
                stamp = server.now_iso()
                domain.update(name=matched,domain=host,url=url,operational_active=status=='Ativa',
                              status='unknown' if status=='Ativa' else 'draft',checked_at='',http_code=None,final_url='',
                              status_label='Aguardando verificação' if status=='Ativa' else 'Loja inativa',
                              reason='Domínio atualizado pela integração.')
                # Commit monitoring first. A repeat safely completes the catalog if the second save fails.
                monitor_store.save(monitoring)
                data = server.store.load()
                entry = data['stores_catalog'][matched]
                entry.update(current_domain=host,status=status,status_verified_at=stamp,status_source='integration')
                data.setdefault('store_edit_audit',[]).append({'store_name':matched,'domain':host,'status':status,'at':stamp,'by':item['owner'],'integration_id':item['id']})
                server.store.save(data)
                return reply({'ok':True,'store':entry})
            if method == 'POST':
                body = handler._read_json()
                if not isinstance(body, dict):
                    raise ValueError('Envie um objeto JSON.')
                key = handler.headers.get('Idempotency-Key', '')
                if not key or len(key) > 200:
                    raise ValueError('Envie Idempotency-Key único por operação (até 200 caracteres).')
                digest = fingerprint(json.dumps(body, sort_keys=True, ensure_ascii=False))
                previous = next((x for x in data.get('integration_requests', [])
                                 if x['token_id'] == item['id'] and x['key'] == key), None)
                if previous:
                    if previous['body_hash'] != digest:
                        return reply({'ok': False, 'error': 'Chave já usada com outro conteúdo.'}, 409)
                    monitoring = server.monitor_bridge.sync_publications(data) if previous['record']['event_type'] == 'store_published' else None
                    return reply({'ok': True, 'record': previous['record'], 'replayed': True, 'monitor_sync': monitoring})
                event = create_event(data, body, item['owner'], item['id'])
                data.setdefault('integration_requests', []).append(dict(
                    token_id=item['id'], key=key, body_hash=digest, record=event))
                server.store.save(data)
                monitoring = server.monitor_bridge.sync_publications(data) if event['event_type'] == 'store_published' else None
                return reply({'ok': True, 'record': event, 'monitor_sync': monitoring}, 201)
            query = {k: v[0] for k, v in parse_qs(urlparse(handler.path).query).items()}
            if path.endswith('/stores'):
                return reply({'ok': True, 'stores': list(data.get('stores_catalog', {}).values())})
            records = data.get('records', [])
            for param in ('start_date', 'end_date'):
                if query.get(param):
                    datetime.strptime(query[param], '%Y-%m-%d')
            if query.get('start_date') and query.get('end_date') and query['start_date'] > query['end_date']:
                raise ValueError('Data inicial deve ser anterior à final.')
            selected = [r for r in records
                        if (not query.get('store_name') or server._name_words(server.normalize_store_name(r.get('store_name', ''))) == server._name_words(server.normalize_store_name(query['store_name'])))
                        and (not query.get('event_type') or r.get('event_type') == query['event_type'])
                        and (not query.get('start_date') or r.get('created_at', '')[:10] >= query['start_date'])
                        and (not query.get('end_date') or r.get('created_at', '')[:10] <= query['end_date'])]
            offset = int(query.get('offset', '0'))
            limit = int(query.get('limit', '100'))
            if offset < 0 or not 1 <= limit <= 500:
                raise ValueError('Paginação inválida: limit de 1 a 500; offset >= 0.')
            result = dict(ok=True, total=len(selected), records=selected[offset:offset + limit],
                          next_offset=offset + limit if offset + limit < len(selected) else None)
            if path.endswith('/reports'):
                result['counts'] = {event: sum(r.get('event_type') == event for r in selected) for event in EVENTS}
                result['generated_at'] = server.now_iso()
                result['filters'] = query
            return reply(result)
    except (ValueError, TypeError, json.JSONDecodeError):
        return reply({'ok': False, 'error': 'Dados inválidos. Confira campos, datas, permissões e Idempotency-Key.'}, 400)
    except (server.SharedStorageConflict, server.StorageConflict):
        return reply({'ok': False, 'error': 'Atualização simultânea. Repita com a mesma Idempotency-Key.'}, 409)
    except (server.SharedStorageError, server.StorageUnavailable):
        return reply({'ok': False, 'error': 'Armazenamento indisponível.'}, 503)


def openapi():
    params = [{'name': k, 'in': 'query', 'schema': {'type': 'string'}}
              for k in ('store_name', 'event_type', 'start_date', 'end_date', 'limit', 'offset')]
    paths = {}
    for name in ('stores', 'records', 'reports'):
        paths['/api/v1/' + name] = {'get': {'operationId': 'get_' + name,
            'summary': 'Consultar ' + name, 'parameters': params if name != 'stores' else [],
            'responses': {'200': {'description': 'Dados consultados; reports inclui contagens e registros paginados.'}}}}
    paths['/api/v1/records']['post'] = {'operationId': 'create_record', 'summary': 'Registrar loja nova, reativação, suspensão de conta ou inativação (DMCA).',
        'parameters': [{'name': 'Idempotency-Key', 'in': 'header', 'required': True, 'schema': {'type': 'string'}}],
        'requestBody': {'required': True, 'content': {'application/json': {'schema': {'type': 'object',
            'required': ['event_type', 'store_name'], 'properties': {
                'event_type': {'type': 'string', 'enum': list(EVENTS)},
                **{k: {'type': 'string'} for k in ('store_name', 'domain', 'account_number', 'reason', 'created_at')},
                'inactive_category': {'type': 'string', 'enum': ['dmca', 'ads_block', 'domain_down']},
                'also_mark_store_inactive': {'type': 'boolean', 'default': False}}}}}},
        'responses': {'201': {'description': 'Registro criado.'}, '200': {'description': 'Operação já processada.'}}}
    return {'openapi': '3.0.3', 'info': {'title': 'Kloube Automation API', 'version': '1.0.0'},
            'security': [{'bearerAuth': []}], 'components': {'securitySchemes': {
                'bearerAuth': {'type': 'http', 'scheme': 'bearer'}}}, 'paths': paths}
