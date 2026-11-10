"""Scheduled worker for the integrated application, independent of browser sessions."""
import sys
import server  # Configures the report store and automatic incident bridge.
from domain_monitor import server as monitor

def main():
    server.monitor_bridge.register_publications(server.store.load())
    data=monitor.store.load()
    if data.get('settings',{}).get('monitoring_paused',False):
        print('Monitoramento pausado; nenhuma consulta executada.');return
    if not monitor._should_refresh_domains(data):
        print('Intervalo ainda não venceu; nenhuma consulta executada.');return
    monitor.verify_all_domains()
    print('Verificação concluída; resultados e ocorrências salvos no armazenamento compartilhado.')

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Falha na execução compartilhada: '+str(error),file=sys.stderr)
        raise SystemExit(1)
