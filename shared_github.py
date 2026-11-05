"""Shared GitHub persistence with optimistic concurrency; never overwrite a newer revision."""
import base64,json,os,threading,urllib.request,urllib.error,urllib.parse

class StorageUnavailable(RuntimeError):pass
class StorageConflict(StorageUnavailable):pass

def config():
    token=os.environ.get('KLOUBE_GITHUB_TOKEN') or os.environ.get('GITHUB_TOKEN') or ''
    repo=os.environ.get('KLOUBE_GITHUB_REPO') or os.environ.get('GITHUB_REPO') or ''
    if not repo:
        owner=os.environ.get('VERCEL_GIT_REPO_OWNER','');slug=os.environ.get('VERCEL_GIT_REPO_SLUG','')
        if owner and slug:repo=owner+'/'+slug
    branch=os.environ.get('KLOUBE_GITHUB_BRANCH') or os.environ.get('VERCEL_GIT_COMMIT_REF') or os.environ.get('GITHUB_REF_NAME') or 'main'
    return token,repo,branch

def enabled():
    token,repo,_=config();return bool(token and repo)

class GitHubFile:
    def __init__(self,path):self.path=path;self.snapshot=threading.local()
    def _request(self,method,body=None):
        token,repo,branch=config()
        if not token or not repo:raise StorageUnavailable('Configure o acesso ao armazenamento compartilhado do GitHub.')
        url=f'https://api.github.com/repos/{repo}/contents/{urllib.parse.quote(self.path)}'
        if method=='GET':url+='?ref='+urllib.parse.quote(branch,safe='')
        request=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','Content-Type':'application/json','User-Agent':'Kloube-Integrated/1.0','Cache-Control':'no-cache'})
        try:
            with urllib.request.urlopen(request,timeout=12) as response:return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in (409,422):raise StorageConflict('Outra pessoa atualizou estes dados. Atualize a página e tente novamente.') from error
            raise StorageUnavailable('Não foi possível acessar os dados compartilhados no GitHub.') from error
        except (OSError,ValueError) as error:raise StorageUnavailable('Falha de comunicação com o armazenamento compartilhado.') from error
    def load(self):
        payload=self._request('GET')
        try:data=json.loads(base64.b64decode(payload['content']).decode('utf-8-sig'))
        except (ValueError,KeyError,TypeError) as error:raise StorageUnavailable('Arquivo compartilhado inválido.') from error
        if not isinstance(data,dict):raise StorageUnavailable('Arquivo compartilhado inválido.')
        self.snapshot.sha=payload['sha'];return data
    def save(self,data):
        sha=getattr(self.snapshot,'sha',None)
        if not sha:raise StorageConflict('Atualize os dados antes de salvar.')
        _,_,branch=config()
        payload=self._request('PUT',{'message':f'chore(sync): update {self.path} [skip ci]','branch':branch,'sha':sha,'content':base64.b64encode(json.dumps(data,ensure_ascii=False,indent=2).encode()).decode()})
        self.snapshot.sha=payload['content']['sha']
