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
    for catalog_name,entry in data.get('stores_catalog',{}).items():
        names={key(catalog_name),key(entry.get('name',''))}
        domains={host(entry.get('current_domain',''))}
        domains.update(host(d.get('domain','')) for d in entry.get('domain_history',[]))
        # Match a complete canonical name or an explicitly catalogued domain, never a guessed substring.
        if (name and name in names) or (hostname and hostname in domains):
            candidates.add(_normalize(catalog_name))
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
