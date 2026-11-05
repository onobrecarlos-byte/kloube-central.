"""Run at build time so missing or misplaced app files cannot be deployed silently."""
from pathlib import Path
import ast,json
root=Path(__file__).resolve().parent
required=('server.py','vercel_wsgi.py','shared_auth.py','shared_github.py','password_recovery.py','monitor_bridge.py','domain_monitor/__init__.py','domain_monitor/server.py','domain_monitor/monitor_engine.py','domain_monitor/public/index.html','domain_monitor/public/app.js','domain_monitor/public/styles.css','public/index.html','public/app.js','public/login.html','public/login.js','blocks_data.json','data.json')
missing=[name for name in required if not (root/name).is_file()]
if missing:raise SystemExit('PACOTE INCOMPLETO: faltam '+', '.join(missing)+'. Preserve as pastas ao subir os arquivos.')
tree=ast.parse((root/'server.py').read_text(encoding='utf-8-sig'))
if not any(isinstance(node,ast.ClassDef) and node.name=='BlocksHandler' for node in tree.body):
    raise SystemExit('SERVER.PY TROCADO: o arquivo da raiz deve ser o servidor integrado, não domain_monitor/server.py.')
if any(isinstance(node,ast.ImportFrom) and node.level for node in tree.body):
    raise SystemExit('SERVER.PY TROCADO: imports relativos não pertencem ao servidor integrado da raiz.')
for name in required:
    if name.endswith('.py'):ast.parse((root/name).read_text(encoding='utf-8-sig'))
for name,key in [('data.json','domains'),('blocks_data.json','records')]:
    data=json.loads((root/name).read_text(encoding='utf-8-sig'))
    if key not in data:raise SystemExit('ARQUIVO DE DADOS INCORRETO: '+name)
import server
from domain_monitor import server as monitoring
if not callable(server.app) or not hasattr(monitoring,'KloubeHandler'):raise SystemExit('Entrada de aplicação inválida.')
print('VALIDAÇÃO OK: servidor integrado, monitor, login, assets, imports e arquivos de dados presentes.')
