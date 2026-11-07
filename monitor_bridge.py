"""Register identified monitoring incidents in the shared report, without email."""
import threading,uuid,re
from urllib.parse import urlparse

lock=threading.RLock()
_store=None
_normalize=None

def configure(store,normalize):
    global _store,_normalize
    _store,_normalize=store,normalize

def key(name):
    import unicodedata
    value=unicodedata.normalize('NFKD',_normalize(name or ''))
    return ''.join(c for c in value.casefold() if c.isalnum() and not unicodedata.combining(c))

def host(value):
    value=(value or '').strip().lower()
    parsed=urlparse(value if '://' in value else '//'+value)
    return (parsed.hostname or '').removeprefix('www.')

def identify(domain,data):
    name=key(domain.get('name',''))
    name={'krakotaunderwear':'krakatoa','vivacuerpomexico':'vivacuerpo','longwellco':'longwell'}.get(name,name)
    hostname=host(domain.get('domain',''))
    candidates=set()
    active_candidates=set()
    for catalog_name,entry in data.get('stores_catalog',{}).items():
        names={key(catalog_name),key(entry.get('name',''))}
        domains={host(entry.get('current_domain',''))}
        domains.update(host(d.get('domain','')) for d in entry.get('domain_history',[]))
        # Match a complete canonical name or an explicitly catalogued domain, never a guessed substring.
        if (name and name in names) or (hostname and hostname in domains):
            candidates.add(_normalize(catalog_name))
            if str(entry.get('status', '')).casefold() == 'ativa':
                active_candidates.add(_normalize(catalog_name))
    if len(active_candidates)==1:return next(iter(active_candidates))
    return next(iter(candidates)) if len(candidates)==1 else ''

def category(domain):
    if domain.get('status')!='offline':return ''
    reason=str(domain.get('reason',''))
    if str(domain.get('http_code',''))=='423' or re.search(r'\bHTTP\s*423\b',reason,re.I):return 'dmca'
    if re.search(r'\b(DNS|NXDOMAIN)\b',reason,re.I):return 'domain_down'
    return ''

def duplicate_record(records,event):
    name=key(event.get('store_name',''))
    stamp=event.get('created_at','')
    kind=event.get('event_type')
    for record in records:
        if key(record.get('store_name',''))!=name:continue
        oldkind=record.get('event_type')
        publication=kind in ('store_published','store_reactivated') and oldkind in ('store_published','store_reactivated')
        if not publication and oldkind!=kind:continue
        if record.get('created_at','')[:10]!=stamp[:10]:continue
        if record.get('monitor_closed_at') and stamp>record['monitor_closed_at']:continue
        if kind=='store_inactive' and record.get('inactive_category')!=event.get('inactive_category'):continue
        if kind=='ads_suspended':
            a=re.sub(r'\D','',str(record.get('account_number','')))
            b=re.sub(r'\D','',str(event.get('account_number','')))
            if not a or a!=b:continue
        return record
    return None

def sync(domains):
    if _store is None:return
    with lock:
        data=_store.load()
        started=data.get('monitor_registration_started_at','')
        if not started:return
        states=data.setdefault('monitor_registration_state',{})
        changed=False
        for domain in domains:
            stamp=domain.get('checked_at','')
            if not stamp or stamp<started or domain.get('status') in ('draft','checking','unknown'):continue
            domain_id=domain.get('id') or host(domain.get('domain',''))
            previous=states.get(domain_id,{})
            if stamp<=previous.get('checked_at',''):continue
            name=identify(domain,data)
            if not name:continue
            cat=category(domain)
            previous_cat=previous.get('category','')
            record_id=previous.get('record_id','')
            if cat and cat!=previous_cat:
                event={'id':'rec_'+uuid.uuid4().hex[:10],'event_type':'store_inactive','store_name':name,
                       'domain':host(domain.get('domain','')),'account_number':'—','inactive_category':cat,
                       'reason':'DMCA' if cat=='dmca' else 'Domínio fora do ar','created_at':stamp,
                       'month_key':stamp[:7],'source':'domain_monitor','monitor_domain_id':domain_id,
                       'monitor_diagnostic':domain.get('reason','')}
                existing=duplicate_record(data.setdefault('records',[]),event)
                if existing:record_id=existing['id']
                else:
                    data['records'].insert(0,event);record_id=event['id']
                for catalog_name,entry in data.get('stores_catalog',{}).items():
                    if key(catalog_name)==key(name) and stamp>=entry.get('status_verified_at',''):
                        entry['status']='Inativa';entry['status_verified_at']=stamp;entry['status_source']='domain_monitor'
            elif not cat and previous_cat and domain.get('status')=='online':
                for record in data.get('records',[]):
                    if record.get('id')==record_id and record.get('source')=='domain_monitor':record['monitor_closed_at']=stamp
                record_id=''
            # Preserve an unresolved episode during transient SSL/HTTP errors.
            if not cat and domain.get('status')!='online':cat=previous_cat
            states[domain_id]={'checked_at':stamp,'category':cat,'record_id':record_id,'store_name':name}
            changed=True
        if changed:_store.save(data)

def register_publications(data):
    """Register published stores; safely retry after a cross-file storage failure."""
    from domain_monitor.server import store as domain_store
    from domain_monitor.monitor_engine import normalize_domain_input, now_iso
    with lock:
        monitoring = domain_store.load()
        seen = set(monitoring.get('publication_registrations', []))
        domains = monitoring.setdefault('domains', [])
        changed = False
        added = 0
        skipped = 0
        for event in reversed(data.get('records', [])):
            if event.get('event_type') != 'store_published':continue
            if (event.get('month_key') or event.get('created_at', '')[:7]) < '2026-11':continue
            event_id = event.get('id')
            if not event_id or event_id in seen:continue
            raw = event.get('domain', '')
            if not raw or raw == '—':
                name = key(event.get('store_name', ''))
                entry = next((v for k,v in data.get('stores_catalog', {}).items() if key(k) == name), {})
                raw = entry.get('current_domain', '')
            try:
                clean_host, clean_url = normalize_domain_input(raw)
            except ValueError:
                skipped += 1
                continue
            if not any(host(d.get('domain', '')) == host(clean_host) for d in domains):
                domains.insert(0, {
                    'id': 'dom_' + uuid.uuid4().hex[:10], 'domain': clean_host,
                    'url': clean_url, 'name': _normalize(event.get('store_name', '')) if _normalize else event.get('store_name', clean_host),
                    'expected_redirect': '', 'status': 'unknown', 'status_label': 'Aguardando verificação',
                    'reason': 'Cadastrado automaticamente com a publicação da loja.',
                    'http_code': None, 'final_url': '', 'ip_address': '', 'response_time_ms': 0,
                    'checked_at': '', 'created_at': now_iso(), 'source': 'store_publication',
                })
                added += 1
            seen.add(event_id)
            changed = True
        if changed:
            monitoring['publication_registrations'] = sorted(seen)
            domain_store.save(monitoring)
        return {'ok': True, 'added': added, 'missing_domain': skipped}


def sync_publications(data):
    # The store record has already been committed. A monitor failure must be
    # reported separately so callers do not repeat the store registration.
    try:
        return register_publications(data)
    except (OSError, RuntimeError):
        return {'ok': False, 'pending': True,
                'message': 'Loja salva. O domínio será sincronizado ao abrir o monitoramento.'}


def enrich_accounts(domains):
    if _store is None:return domains
    data=_store.load()
    result=[]
    def account(raw):
        raw=str(raw or '').strip()
        sequence=re.fullmatch(r'(?:Googl[ea]\s*Ads\s*#?\s*)?(\d{1,4})',raw,re.I)
        if sequence:return 'Google Ads '+str(int(sequence.group(1)))
        digits=re.sub(r'\D','',raw)
        if len(digits)==10:return digits[:3]+'-'+digits[3:6]+'-'+digits[6:]
        return ''
    for domain in domains:
        item=dict(domain)
        name=identify(domain,data)
        entries=[v for k,v in data.get('stores_catalog',{}).items() if name and key(k)==key(name)]
        values=[]
        current=''
        for entry in entries:
            current=account(entry.get('current_ads_account','')) or current
            values.append(current)
            values.extend(account(v.get('account','')) for v in entry.get('suspensions',[]))
        if name:
            values.extend(account(r.get('account_number','')) for r in data.get('records',[]) if key(r.get('store_name',''))==key(name))
        item['ads_accounts']=list(dict.fromkeys(v for v in values if v))
        item['current_ads_account']=current
        active_names=data.get('operational_snapshot',{}).get('active_stores',[])
        item['ads_in_use_unidentified']=bool(name and not current and any(key(n)==key(name) for n in active_names))
        result.append(item)
    return result
