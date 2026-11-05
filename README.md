# Kloube Central — instalação em repositório novo

## 1. GitHub
Crie um repositório PRIVADO chamado kloube-central, na conta onobrecarlos-byte.
Suba TODO O CONTEÚDO desta pasta na RAIZ do repositório, preservando as pastas.
O server.py da raiz é diferente de domain_monitor/server.py: não mova nem misture os dois.
O pacote é completo: data.json e blocks_data.json já incluem os dados atuais. Nunca troque esses arquivos por cópias antigas depois que a equipe começar a usar o novo app.

## 2. Token de armazenamento
No GitHub, configure um token com acesso ao novo repositório privado e permissão Contents: Read and write. O token usado no projeto antigo só serve se também tiver acesso a kloube-central.
Não coloque esse token no código nem no GitHub. Ele será uma variável do Vercel.

## 3. Vercel
Importe o repositório novo kloube-central.
Framework Preset: Python. Root Directory: ./ (raiz).
Build Command vem do pacote: python validate_release.py. Não personalizar Output Directory ou Install Command.
Entrada explícita: server:app, definida no pyproject.toml. Python 3.12, definido em .python-version.

Antes de clicar Deploy, configure estas quatro variáveis:
KLOUBE_GITHUB_REPO = onobrecarlos-byte/kloube-central
KLOUBE_GITHUB_BRANCH = main
KLOUBE_GITHUB_TOKEN = token com acesso ao NOVO repositório
KLOUBE_SESSION_SECRET = segredo longo e estável da nova sessão

Um arquivo kloube-central-VERCEL.env foi preparado SEPARADAMENTE da pasta de upload. Ele já contém o segredo de sessão e os nomes das variáveis. Falta preencher o token. Importe-o SOMENTE nas variáveis do Vercel; NÃO envie esse arquivo para o GitHub.

Não copiar variáveis antigas de Redis/Upstash: este projeto novo usa o GitHub compartilhado para os dois módulos. Os usuários e as senhas existentes estão em data.json (protegidas por hash). Não é necessário criar contas de novo.

## 4. Conferência inicial
Depois do deploy, abra https://SEU-ENDERECO/health.
A resposta deve mostrar release kloube-central-20261105 e ready: true. Isso confere a presença da configuração; não substitui o teste real de permissão do token.
Entre com seu usuário e senha atuais, abra resumo, registros, lixeira, configurações e monitoramento.
Use duas sessões para confirmar uma alteração compartilhada.
Na aba Actions do GitHub, confira os workflows de validação e monitoramento. O monitor agendado respeita pausa e intervalo. Execuções do GitHub podem atrasar; não há garantia de pontualidade exata.

## E-mail de recuperação (opcional)
O app pode abrir e funcionar sem SMTP. Alertas do monitor permanecem bloqueados.
Para recuperação de senha: KLOUBE_RESET_SMTP_USER=growthnobre@gmail.com e KLOUBE_RESET_SMTP_PASS=senha de aplicativo dessa conta. A conta Gmail precisa permitir senhas de aplicativo. Não use a senha comum no código e não envie credenciais no chat.

## Testes e limites
O pacote é validado com o empacotador @vercel/python 30.1.0 do Vercel CLI 65.11.0 e Python 3.12. O build verifica arquivos ausentes e servidor trocado antes de publicar. A função gerada é testada localmente com o runtime oficial no Windows, com adaptação de teste apenas para fork inexistente nessa plataforma.
O novo repositório e suas variáveis do Vercel ainda não foram criados nem acessados por este pacote. A validação online final depende desses passos.
