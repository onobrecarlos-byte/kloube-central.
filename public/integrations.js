(() => {
    const el = id => document.getElementById(id);
    el('integration-url').textContent = location.origin + '/api/v1';
    const message = text => { el('integration-message').textContent = text; };
    async function request(path, body) {
        const response = await fetch(path, body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {cache:'no-store'});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Não foi possível concluir.');
        return data;
    }
    async function load() {
        try {
            const data = await request('/api/integrations');
            const list = el('integration-list');
            list.replaceChildren();
            if (!data.tokens.length) list.textContent = 'Nenhuma conexão criada.';
            data.tokens.forEach(token => {
                const row = document.createElement('p');
                const expired = token.expires_at * 1000 <= Date.now();
                row.textContent = `${token.name} · ${token.scopes.includes('write') ? 'Consulta e escrita' : 'Consulta'} · ${token.revoked_at ? 'Revogado' : expired ? 'Expirado' : 'Válido até ' + new Date(token.expires_at * 1000).toLocaleDateString('pt-BR')} `;
                if (!token.revoked_at && !expired) {
                    const button = document.createElement('button');
                    button.type = 'button'; button.className = 'btn btn-secondary'; button.textContent = 'Revogar';
                    button.onclick = async () => {
                        button.disabled = true;
                        try { await request('/api/integrations/revoke', {id:token.id}); await load(); message('Conexão revogada.'); }
                        catch(error) { message(error.message); button.disabled = false; }
                    };
                    row.append(button);
                }
                list.append(row);
            });
        } catch(error) { message(error.message); }
    }
    el('integration-form').addEventListener('submit', async event => {
        event.preventDefault();
        const button = event.submitter; button.disabled = true;
        try {
            const data = await request('/api/integrations', {name:el('integration-name').value.trim(), scopes:el('integration-scope').value === 'write' ? ['read','write'] : ['read'], expires_in_days:Number(el('integration-days').value)});
            el('integration-token').value = data.token; el('integration-secret').hidden = false;
            message('Conexão criada. Copie o token antes de sair desta tela.'); await load();
        } catch(error) { message(error.message); }
        finally { button.disabled = false; }
    });
    el('integration-copy').onclick = async () => {
        try { await navigator.clipboard.writeText(el('integration-token').value); message('Token copiado.'); }
        catch { el('integration-token').select(); message('Selecione e copie o token manualmente.'); }
    };
    el('integration-hide').onclick = () => { el('integration-token').value = ''; el('integration-secret').hidden = true; };
    new MutationObserver(() => {
        if (!el('view-settings').classList.contains('active')) {
            el('integration-token').value = ''; el('integration-secret').hidden = true;
        } else load();
    }).observe(el('view-settings'), {attributes:true, attributeFilter:['class']});
    load();
})();
