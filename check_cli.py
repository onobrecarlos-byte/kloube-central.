"""Scheduled worker for the integrated application, independent of browser sessions."""
import sys
import server  # Configures the report store and automatic incident bridge.
from domain_monitor import server as monitor

def main():
    if not server.notion_stores.enabled():
        server.monitor_bridge.register_publications(server.store.load())
    data=monitor.store.load()
    if data.get('settings',{}).get('monitoring_paused',False):
        print('Monitoramento pausado; nenhuma consulta executada.');return
    if server.notion_stores.enabled():
        targets = monitor._notion_targets(data.get("domains", []))
        print(f"Notion consultado: {len(targets)} domínios elegíveis para monitoramento.")
        if not targets:
            print("Nenhum domínio ativo para verificar.")
            return
        data = dict(data, domains=targets)
    if not monitor._should_refresh_domains(data):
        print('Intervalo ainda não venceu; nenhuma consulta executada.');return
    monitor.verify_all_domains()
    print('Verificação concluída; resultados e ocorrências salvos no armazenamento compartilhado.')

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Falha na execução compartilhada: '+str(error),file=sys.stderr)
        raise SystemExit(1)
