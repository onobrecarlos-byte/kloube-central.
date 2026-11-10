function displayDomain(value) { if (!value || value === "—") return value || ""; try { return "https://" + new URL(String(value).includes("://") ? value : "https://" + value).hostname.toLowerCase().replace(/^www\./, ""); } catch { return value; } }
const state = {
    selectedPreset: 'this_month',
    selectedMonth: '2026-11',
    periodFilter: 'month',
    startDate: '',
    endDate: '',
    currentView: 'summary',
    attentionFilter: null,
    activeDrawerSection: null,
    drawerSearch: '',
    storesSearch: '',
    activeStoresOnly: false,
    historySearch: '',
    historyType: 'all',
    recordsDateFilter: '',
    recordsSortAsc: false, // false = mais recente primeiro (maior para menor), true = mais antigo primeiro
    incidentsFilter: 'all',
    monthlySummary: null,
    records: [],
    deletedRecords: [],
    deletionAudit: [],
    storesCatalog: {},
    knownStores: [],
    whatsappConfig: { target_group_name: 'EQUIPE KLOUBE ECOM', allowed_phones: [] },
};

const SECTION_LABELS = {
    reactivated_stores: {eyebrow: 'REATIVAÇÕES', title: 'Reativadas', subtitle: 'Lojas existentes que voltaram ao ar no período selecionado.'},
    start_stores: {
        eyebrow: 'RESUMO DAS LOJAS',
        title: 'Lojas no início do mês',
        subtitle: 'Relação de lojas que iniciaram o período ativas.',
    },
    active_stores: {
        eyebrow: 'RESUMO DAS LOJAS',
        title: 'Lojas ativas no período',
        subtitle: 'Lojas que estão em operação ativa.',
    },
    published_stores: {
        eyebrow: 'LOJAS NOVAS',
        title: 'Lojas novas',
        subtitle: 'Novas lojas lançadas e publicadas.',
    },
    inactive_all: {
        eyebrow: 'LOJAS INATIVAS',
        title: 'Lojas inativas no período',
        subtitle: 'Lojas inativadas por DMCA, Google Ads ou queda de domínio.',
    },
    registered_all: {
        eyebrow: 'TOTAL DE LOJAS',
        title: 'Total de lojas registradas',
        subtitle: 'Soma das lojas do início com lojas publicadas.',
    },
    inactive_dmca: {
        eyebrow: 'MOTIVO DAS INATIVAÇÕES',
        title: 'Lojas com DMCA',
        subtitle: 'Lojas que caíram por direitos autorais na Shopify.',
    },
    inactive_ads: {
        eyebrow: 'MOTIVO DAS INATIVAÇÕES',
        title: 'Lojas inativas por Contas suspensas',
        subtitle: 'Lojas inativadas devido a bloqueios e suspensões no Ads.',
    },
    inactive_domain: {
        eyebrow: 'MOTIVO DAS INATIVAÇÕES',
        title: 'Lojas com domínio fora do ar',
        subtitle: 'Lojas que saíram do ar por falha ou queda de domínio.',
    },
    ads_accounts: {
        eyebrow: 'CONTAS GOOGLE ADS',
        title: 'Contas suspensas identificadas',
        subtitle: 'Todas as contas Google Ads com suspensão/bloqueio.',
    },
    ads_affected_stores: {
        eyebrow: 'CONTAS GOOGLE ADS',
        title: 'Lojas afetadas por suspensões',
        subtitle: 'Lojas vinculadas às contas suspensas.',
    },
};

function escapeHtml(str) {
    return String(str ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function showToast(title, message) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const el = document.createElement('div');
    el.className = 'toast';
    el.innerHTML = `
        <strong style="display:block;font-size:13px;color:#FFFFFF;margin-bottom:2px;">${escapeHtml(title)}</strong>
        <span style="font-size:12.5px;color:#94A3B8;">${escapeHtml(message)}</span>
    `;
    container.appendChild(el);
    setTimeout(() => el.remove(), 4000);
}

function formatPtDate(raw) {
    if (!raw) return '—';
    const m = String(raw).match(/^(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{2}):(\d{2}))?/);
    if (m) {
        return m[4] ? `${m[3]}/${m[2]}/${m[1]} às ${m[4]}:${m[5]}` : `${m[3]}/${m[2]}/${m[1]}`;
    }
    return String(raw);
}

/* ==========================================================================
   RENDERIZAÇÃO DO DASHBOARD PRINCIPAL (RESUMO DAS LOJAS)
   ========================================================================== */
function renderDashboard() {
    const s = state.monthlySummary;
    if (!s) return;

    // Métricas do resumo
    document.getElementById('kpi-start-stores').textContent = s.start_active_stores;
    document.getElementById('kpi-active-stores').textContent = s.end_active_stores;
    document.getElementById('label-active-stores').textContent = s.active_is_current ? 'Ativas hoje' : 'Ativas no fim';
    document.getElementById('kpi-inactive-stores').textContent = s.total_inactive;
    document.getElementById('kpi-total-stores').textContent = s.total_registered;
    document.getElementById('kpi-published-stores').textContent = s.published_stores_count;
    document.getElementById('kpi-reactivated-stores').textContent = s.reactivated_stores_count || 0;

    // 4 Cards de Atenção (DMCA, Contas suspensas, Domínio fora do ar, Lojas afetadas)
    document.getElementById('att-val-dmca').textContent = s.inactive_reasons.dmca;
    document.getElementById('att-val-accounts-suspended').textContent = s.ads_suspensions.total_accounts;
    document.getElementById('att-val-domain-down').textContent = s.inactive_reasons.domain_down;
    document.getElementById('att-val-affected-stores').textContent = s.ads_suspensions.affected_stores;

    // Atualiza estado ativo nos cards de atenção
    document.querySelectorAll('[data-filter-attention]').forEach(card => {
        const filterType = card.getAttribute('data-filter-attention');
        card.classList.toggle('active-filter', state.attentionFilter === filterType);
    });

    // Renderiza a Tabela de Incidentes
    renderIncidentsTable();
}

function renderIncidentsTable() {
    const s = state.monthlySummary;
    if (!s) return;

    const allIncidents = s.attention_incidents || [];
    let filtered = allIncidents;

    if (state.attentionFilter === 'dmca') {
        filtered = allIncidents.filter(x => x.incident_category === 'dmca');
    } else if (state.attentionFilter === 'domain_down') {
        filtered = allIncidents.filter(x => x.incident_category === 'domain_down');
    } else if (state.attentionFilter === 'ads_suspended') {
        filtered = allIncidents.filter(x => x.incident_category === 'ads_suspended' || x.incident_category === 'ads_block');
    } else if (state.attentionFilter === 'affected_stores') {
        filtered = allIncidents.filter(x => x.incident_category === 'ads_suspended' || x.incident_category === 'ads_block');
    }

    const tbody = document.getElementById('incidents-tbody');
    const emptyBox = document.getElementById('incidents-empty');

    if (!filtered || filtered.length === 0) {
        tbody.innerHTML = '';
        emptyBox.style.display = 'block';
        return;
    }

    emptyBox.style.display = 'none';
    tbody.innerHTML = filtered.map(item => {
        let iconHtml = '<img src="/assets/icon-google-ads.png" class="incident-row-icon" alt="Google Ads" />';
        if (item.icon === 'shopify_red') {
            iconHtml = '<img src="/assets/icon-shopify-red.png" class="incident-row-icon" alt="DMCA" />';
        } else if (item.icon === 'shopify_green') {
            iconHtml = '<img src="/assets/icon-shopify-green.png" class="incident-row-icon" alt="Shopify" />';
        } else if (item.icon === 'domain') {
            iconHtml = `
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#94A3B8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="10"></circle>
                    <line x1="2" y1="12" x2="22" y2="12"></line>
                    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
                </svg>
            `;
        }

        const badgeClass = item.status_type === 'danger' ? 'badge-danger' : (item.status_type === 'success' ? 'badge-success' : 'badge-warning');

        return `
            <tr>
                <td>${iconHtml}</td>
                <td>
                    <strong class="store-name-bold store-clickable-link btn-inspect-store" data-store-name="${escapeHtml(item.store_name)}" title="Clique para ver os bloqueios e datas de ${escapeHtml(item.store_name)}">
                        ${escapeHtml(item.store_name)}
                    </strong>
                </td>
                <td><span class="incident-type-text">${escapeHtml(item.tipo)}</span></td>
                <td><span class="badge-pill ${badgeClass}">${escapeHtml(item.status)}</span></td>
                <td><span class="date-text-muted">${escapeHtml(item.registered_at)}</span></td>
                <td style="text-align:right;">
                    ${item.id && !item.id.startsWith('oct_') ? `<button type="button" class="icon-btn-del" onclick="deleteRecord('${escapeHtml(item.id)}')" title="Excluir">&#10005;</button>` : ''}
                </td>
            </tr>
        `;
    }).join('');
}

/* ==========================================================================
   MODAL DE DETALHES DA LOJA (BLOQUEIOS, DATAS E HISTÓRICO COMPLETO)
   ========================================================================== */
async function openStoreDetail(storeName) {
    if (!storeName) return;
    try {
        const res = await fetch(`/api/store-detail?name=${encodeURIComponent(storeName)}`);
        const data = await res.json();
        if (!data.ok || !data.store) {
            alert('Não foi possível carregar os detalhes desta loja.');
            return;
        }

        const st = data.store;
        document.getElementById('store-domains-disclosure').open = false;
        document.getElementById('store-accounts-disclosure').open = false;
        document.getElementById('modal-store-name').textContent = st.name;
        const adsInfo = st.current_ads_account ? ` • Conta Ads Atual: ${st.current_ads_account}` : '';
        document.getElementById('modal-store-domain').textContent = (st.current_domain ? `Domínio principal: ${st.current_domain}` : 'Sem domínio principal') + adsInfo;

        // Seletor de status operacional no modal
        const statusSelect = document.getElementById('modal-store-status-select');
        if (statusSelect) {
            statusSelect.value = String(st.status || '').trim().toLowerCase() === 'ativa' ? 'Ativa' : 'Inativa';
            statusSelect.onchange = async () => {
                await changeStoreStatus(st.name, statusSelect.value);
            };
        }

        // 1. Número de domínios
        const domainsNumEl = document.getElementById('store-stat-domains-num');
        const domainsDetailEl = document.getElementById('store-stat-domains-detail');
        const domList = [...new Set((st.domains_list || (st.current_domain ? [st.current_domain] : [])).map(d => String(d).trim()).filter(Boolean))];
        if (domainsNumEl) domainsNumEl.textContent = domList.length;
        if (domainsDetailEl) {
            if (domList.length === 0) {
                domainsDetailEl.textContent = 'Nenhum domínio cadastrado';
            } else {
                domainsDetailEl.innerHTML = domList.map(d => `<span class="tag-mono" style="margin-right:4px;">${escapeHtml(d)}</span>`).join('');
            }
        }

        // 2. DMCA
        const dmcaNumEl = document.getElementById('store-stat-dmca-num');
        const dmcaDatesEl = document.getElementById('store-stat-dmca-dates');
        if (dmcaNumEl) dmcaNumEl.textContent = st.total_dmca || 0;
        if (dmcaDatesEl) {
            if (st.dmca_dates && st.dmca_dates.length > 0) {
                dmcaDatesEl.innerHTML = `Datas: <strong>${escapeHtml(st.dmca_dates.join(', '))}</strong>`;
            } else {
                dmcaDatesEl.textContent = 'Nenhuma ocorrência registrada';
            }
        }

        // 3. Em quantas contas e quais foram publicadas
        const accountsNumEl = document.getElementById('store-stat-accounts-num');
        const accountsDetailEl = document.getElementById('store-stat-accounts-detail');
        const accList = [...new Set((st.accounts_list || (st.current_ads_account ? [st.current_ads_account] : []))
            .map(acc => String(acc).trim())
            .filter(acc => /^(?:Google\s*Ads\s*#?\s*\d{1,4}|\d{3}[- ]?\d{3}[- ]?\d{4}|\d{1,4})$/i.test(acc)))];
        if (accountsNumEl) accountsNumEl.textContent = Math.max(accList.length, st.total_accounts_count || 0);
        if (accountsDetailEl) {
            if (accList.length === 0) {
                accountsDetailEl.textContent = 'Nenhuma conta registrada';
            } else {
                accountsDetailEl.innerHTML = accList.map(acc => {
                    const isCurrent = acc === st.current_ads_account;
                    return `<span class="tag-mono" style="margin-right:4px;${isCurrent ? 'color:#10B981;font-weight:600;' : ''}">${escapeHtml(acc)}${isCurrent ? ' (Atual)' : ''}</span>`;
                }).join('');
            }
        }

        if (accountsDetailEl && st.current_account_unidentified) {
            if (accList.length === 0) accountsDetailEl.textContent = '';
            const note = document.createElement('span');
            note.className = 'tag-mono';
            note.style.color = '#10B981';
            note.textContent = 'Em uso · número não informado';
            accountsDetailEl.appendChild(note);
        }

        // Registros e ocorrências
        const timelineEl = document.getElementById('modal-store-timeline');
        const counterEl = document.getElementById('modal-records-counter');
        const incidents = st.all_incidents || [];
        if (counterEl) counterEl.textContent = `${incidents.length} registro(s)`;

        if (incidents.length === 0) {
            timelineEl.innerHTML = `<div class="table-empty-box"><p>Nenhum bloqueio, DMCA ou registro encontrado para esta loja.</p></div>`;
        } else {
            timelineEl.innerHTML = incidents.map(ev => {
                let badgeClass = 'badge-warning';
                if (ev.type === 'DMCA') badgeClass = 'badge-danger';
                else if (ev.type === 'Domínio fora do ar') badgeClass = 'badge-warning';

                const accText = ev.account && ev.account !== '—' ? `<span class="tag-mono">${escapeHtml(ev.account)}</span>` : '';

                return `
                    <div class="timeline-event-card">
                        <div class="timeline-event-main">
                            <div style="display:flex;align-items:center;gap:8px;">
                                <span class="badge-pill ${badgeClass}" style="font-size:11.5px;padding:2px 8px;">${escapeHtml(ev.type)}</span>
                                ${accText}
                            </div>
                            <span class="timeline-event-reason">${escapeHtml(ev.reason)}</span>
                        </div>
                        <div class="timeline-event-meta">
                            <span class="timeline-event-date">${escapeHtml(ev.date)}</span>
                        </div>
                    </div>
                `;
            }).join('');
        }

        document.getElementById('store-detail-modal').style.display = 'flex';
    } catch (e) {
        console.error(e);
        alert('Erro ao carregar detalhes da loja.');
    }
}

/* ==========================================================================
   RENDERIZAÇÃO DAS VIEWS SECUNDÁRIAS
   ========================================================================== */
function renderStoresView() {
    const s = state.monthlySummary;
    if (!s) return;

    const startStores = s.details ? s.details.start_stores || [] : [];
    const activeStores = s.details ? s.details.active_stores || [] : [];
    const inactiveStores = s.details ? s.details.inactive_all || [] : [];
    const inactiveNames = new Set(inactiveStores.map(x => (x.store_name || '').toLowerCase()));

    const list = [];
    const seen = new Set();

    activeStores.forEach(st => {
        const key = (st.store_name || '').toLowerCase();
        if (!seen.has(key)) {
            seen.add(key);
            list.push({
                name: st.store_name,
                domain: st.domain || '',
                status: 'ativa',
                swaps: st.domain_swaps_count || 0,
            });
        }
    });

    if (!state.activeStoresOnly) startStores.forEach(st => {
        const key = (st.store_name || '').toLowerCase();
        if (!seen.has(key)) {
            seen.add(key);
            list.push({
                name: st.store_name,
                domain: st.domain || '',
                status: s.active_is_current ? 'inativa' : (inactiveNames.has(key) ? 'inativa' : 'ativa'),
                swaps: st.domain_swaps_count || 0,
            });
        }
    });

    if (!state.activeStoresOnly && s.active_is_current) {
        Object.entries(state.storesCatalog).forEach(([name, entry]) => {
            const actualName = entry.name || name;
            const key = actualName.toLowerCase();
            const status = String(entry.status || '').trim().toLowerCase() === 'ativa' ? 'ativa' : 'inativa';
            const existing = list.find(item => item.name.toLowerCase() === key);
            if (existing) existing.status = status;
            else list.push({name: actualName, domain: entry.current_domain || '', status, swaps: entry.domain_swaps_count || 0});
        });
    }

    const q = state.storesSearch.toLowerCase();
    const filtered = list.filter(it => {
        if (!q) return true;
        return it.name.toLowerCase().includes(q) || it.domain.toLowerCase().includes(q);
    });

    const tbody = document.getElementById('stores-tbody');
    if (!tbody) return;

    document.getElementById('stores-catalog-subtitle').textContent = state.activeStoresOnly
        ? (s.active_is_current ? 'Lojas ativas hoje' : 'Lojas ativas no fim do período selecionado')
        : 'Todas as lojas sob acompanhamento, domínios vinculados e trocas de domínio realizadas.';
    document.getElementById('btn-all-stores').style.display = state.activeStoresOnly ? '' : 'none';

    tbody.innerHTML = filtered.map(st => {
        const isAtiva = st.status === 'ativa';
        const statusBadge = `
            <select class="status-select-pill ${isAtiva ? 'status-pill-active' : 'status-pill-inactive'}" 
                    data-store="${escapeHtml(st.name)}" 
                    onchange="changeStoreStatus(this.getAttribute('data-store'), this.value)"
                    title="Clique para alternar o status operacional">
                <option value="Ativa" ${isAtiva ? 'selected' : ''}>● Ativa</option>
                <option value="Inativa" ${!isAtiva ? 'selected' : ''}>● Inativa</option>
            </select>
        `;

        return `
            <tr>
                <td>
                    <strong class="store-name-bold store-clickable-link btn-inspect-store" data-store-name="${escapeHtml(st.name)}" title="Clique para ver o histórico desta loja">
                        ${escapeHtml(st.name)}
                    </strong>
                </td>
                <td>${st.domain ? `<span class="tag-mono">${escapeHtml(displayDomain(st.domain))}</span>` : '—'}</td>
                <td>${statusBadge}</td>
                <td>${st.swaps > 0 ? `${st.swaps} troca(s)` : '0'}</td>
                <td>
                    <button type="button" class="btn btn-secondary btn-sm btn-inspect-store" style="padding:4px 10px;font-size:12px;" data-store-name="${escapeHtml(st.name)}">Ver histórico</button>
                </td>
            </tr>
        `;
    }).join('');
}

function renderIncidentsView() {
    const s = state.monthlySummary;
    if (!s) return;

    let incidents = s.attention_incidents || [];
    if (state.incidentsFilter !== 'all') {
        incidents = incidents.filter(x => ['ads_suspended', 'ads_block'].includes(state.incidentsFilter) ? ['ads_suspended', 'ads_block'].includes(x.incident_category) : x.incident_category === state.incidentsFilter);
    }

    const tbody = document.getElementById('incidents-full-tbody');
    if (!tbody) return;

    tbody.innerHTML = incidents.map(item => {
        let iconHtml = '<img src="/assets/icon-google-ads.png" class="incident-row-icon" alt="Google Ads" />';
        if (item.icon === 'shopify_red') {
            iconHtml = '<img src="/assets/icon-shopify-red.png" class="incident-row-icon" alt="DMCA" />';
        }

        return `
            <tr>
                <td>${iconHtml}</td>
                <td>
                    <strong class="store-name-bold store-clickable-link btn-inspect-store" data-store-name="${escapeHtml(item.store_name)}" title="Ver detalhes da loja">
                        ${escapeHtml(item.store_name)}
                    </strong>
                </td>
                <td>${item.account_number && item.account_number !== '—' ? `<span class="tag-mono">${escapeHtml(item.account_number)}</span>` : '—'}</td>
                <td style="color:#CBD5E1;">${escapeHtml(item.incident_category === 'dmca' ? 'DMCA' : (['ads_block', 'ads_suspended'].includes(item.incident_category) ? 'Suspensão do Google' : item.tipo))}</td>
                <td><span class="date-text-muted">${escapeHtml(item.registered_at)}</span></td>
                <td>
                    <button type="button" class="btn btn-secondary btn-sm btn-inspect-store" style="padding:3px 8px;font-size:11.5px;" data-store-name="${escapeHtml(item.store_name)}">Ver histórico</button>
                    ${item.id && !item.id.startsWith('oct_') ? `<button type="button" class="icon-btn-del" onclick="deleteRecord('${escapeHtml(item.id)}')">Excluir</button>` : ''}
                </td>
            </tr>
        `;
    }).join('');
}

function renderRecordsView() {
    const q = state.historySearch.toLowerCase();
    const dateFilter = state.recordsDateFilter;

    let filtered = state.records.filter(r => {
        const eventType = r.display_event_type || r.event_type;
        const kind = state.historyType;
        if (kind === 'dmca' && r.inactive_category !== 'dmca') return false;
        if (kind === 'ads' && eventType !== 'ads_suspended' && r.inactive_category !== 'ads_block') return false;
        if (kind === 'domain_down' && r.inactive_category !== 'domain_down') return false;
        if (!['all', 'dmca', 'ads', 'domain_down'].includes(kind) && eventType !== kind) return false;
        if (dateFilter && !(r.created_at || '').startsWith(dateFilter)) {
            return false;
        }
        if (!q) return true;
        return (
            (r.store_name || '').toLowerCase().includes(q) ||
            (r.account_number || '').toLowerCase().includes(q) ||
            (r.reason || '').toLowerCase().includes(q) ||
            (r.domain || '').toLowerCase().includes(q)
        );
    });

    // Ordenação por data (maior para menor ou menor para maior)
    filtered.sort((a, b) => {
        const da = a.created_at || '';
        const db = b.created_at || '';
        return state.recordsSortAsc ? da.localeCompare(db) : db.localeCompare(da);
    });

    const tbody = document.getElementById('records-full-tbody');
    if (!tbody) return;

    if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="table-empty-box">Nenhum lançamento encontrado para os filtros selecionados.</td></tr>`;
        return;
    }

    tbody.innerHTML = filtered.map(r => {
        const displayedType = r.display_event_type || r.event_type;
        let typeBadge = '<span class="badge-pill badge-warning">Conta suspensa</span>';
        if (displayedType === 'store_reactivated') {
            typeBadge = '<span class="badge-pill badge-success">Loja reativada</span>';
        } else if (displayedType === 'store_published') {
            typeBadge = '<span class="badge-pill badge-success">Loja nova</span>';
        } else if (displayedType === 'store_inactive') {
            typeBadge = `<span class="badge-pill badge-danger">${r.inactive_category === 'dmca' ? 'DMCA' : r.inactive_category === 'ads_block' ? 'Suspensão de Google Ads' : r.inactive_category === 'domain_down' ? 'Domínio fora do ar' : 'Loja inativa'}</span>`;
        } else if (displayedType === 'domain_swapped') {
            typeBadge = '<span class="badge-pill badge-warning">Troca de domínio</span>';
        }

        return `
            <tr>
                <td>${typeBadge}</td>
                <td>
                    <strong class="store-name-bold store-clickable-link btn-inspect-store" data-store-name="${escapeHtml(r.store_name)}" title="Ver detalhes da loja">
                        ${escapeHtml(r.store_name)}
                    </strong>
                </td>
                <td>${r.domain ? `<span class="tag-mono">${escapeHtml(displayDomain(r.domain))}</span>` : '—'}</td>
                <td>${r.account_number && r.account_number !== '—' ? `<span class="tag-mono">${escapeHtml(r.account_number)}</span>` : '—'}</td>
                <td style="color:#CBD5E1;">${escapeHtml(r.reason)}</td>
                <td><span class="date-text-muted">${escapeHtml(formatPtDate(r.created_at))}</span></td>
                <td style="text-align:right;white-space:nowrap;">
                    <button type="button" class="btn btn-secondary btn-sm btn-edit-record" data-id="${escapeHtml(r.id)}" style="padding:2px 8px;font-size:11.5px;margin-right:6px;">Editar</button>
                    <button type="button" class="icon-btn-del" onclick="deleteRecord('${escapeHtml(r.id)}')">Excluir</button>
                </td>
            </tr>
        `;
    }).join('');
}

/* ==========================================================================
   DRAWER / INSPECTOR LATERAL
   ========================================================================== */
function openDrawer(sectionKey) {
    state.activeDrawerSection = sectionKey;
    const meta = SECTION_LABELS[sectionKey] || { eyebrow: 'DETALHES', title: 'Detalhes', subtitle: '' };

    document.getElementById('drawer-eyebrow').textContent = meta.eyebrow;
    document.getElementById('drawer-title').textContent = meta.title;
    document.getElementById('drawer-subtitle').textContent = meta.subtitle;
    document.getElementById('drawer-search').value = '';
    state.drawerSearch = '';

    renderDrawerItems();

    document.getElementById('inspector-overlay').style.display = 'block';
    document.getElementById('inspector-drawer').style.display = 'flex';
}

function closeDrawer() {
    document.getElementById('inspector-overlay').style.display = 'none';
    document.getElementById('inspector-drawer').style.display = 'none';
    state.activeDrawerSection = null;
}

function renderDrawerItems() {
    const s = state.monthlySummary;
    if (!s || !s.details || !state.activeDrawerSection) return;

    const rawItems = s.details[state.activeDrawerSection] || [];
    const q = state.drawerSearch.toLowerCase();
    const items = rawItems.filter(it => {
        if (!q) return true;
        return (
            (it.store_name || '').toLowerCase().includes(q) ||
            (it.account_number || '').toLowerCase().includes(q) ||
            (it.reason || '').toLowerCase().includes(q) ||
            (it.detail || '').toLowerCase().includes(q) ||
            (it.domain || '').toLowerCase().includes(q)
        );
    });

    document.getElementById('drawer-counter').textContent = `${items.length} ${items.length === 1 ? 'item' : 'itens'}`;
    const listEl = document.getElementById('drawer-items-list');

    if (items.length === 0) {
        listEl.innerHTML = `<div class="table-empty-box"><p>Nenhum item registrado neste indicador.</p></div>`;
        return;
    }

    listEl.innerHTML = items.map((it, idx) => {
        const title = it.store_name || `Item ${idx + 1}`;
        let sub = it.detail || it.reason || '';
        if (it.domain && !sub.includes(it.domain)) {
            sub = `${it.domain} · ${sub}`;
        }
        if (it.created_at) {
            sub = `${sub} (${formatPtDate(it.created_at)})`;
        }

        let rightBadge = '';
        if (it.account_number && it.account_number !== '—') {
            rightBadge = `<span class="tag-mono">${escapeHtml(it.account_number)}</span>`;
        } else if (it.count) {
            rightBadge = `<span class="tag-mono">${it.count} contas</span>`;
        }

        let delBtn = '';
        if (it.id && !it.id.startsWith('oct_')) {
            delBtn = `<button type="button" class="icon-btn-del" onclick="deleteRecord('${escapeHtml(it.id)}')" title="Excluir">&#10005;</button>`;
        }

        return `
            <div class="drawer-item">
                <div>
                    <span class="drawer-item-title store-clickable-link btn-inspect-store" data-store-name="${escapeHtml(title)}" title="Clique para ver o histórico de ${escapeHtml(title)}">${idx + 1}. ${escapeHtml(title)}</span>
                    <span class="drawer-item-sub">${escapeHtml(sub)}</span>
                </div>
                <div style="display:flex;align-items:center;gap:8px;">
                    ${rightBadge}
                    <button type="button" class="btn btn-secondary btn-sm btn-inspect-store" style="padding:3px 8px;font-size:11px;" data-store-name="${escapeHtml(title)}">Ver histórico</button>
                    ${delBtn}
                </div>
            </div>
        `;
    }).join('');
}

/* ==========================================================================
   SINCRONIZAÇÃO E API
   ========================================================================== */
async function fetchState() {
    try {
        const params = new URLSearchParams();
        params.set('month', state.selectedMonth);
        params.set('period', state.periodFilter);
        if (state.periodFilter === 'custom') {
            if (state.startDate) params.set('start_date', state.startDate);
            if (state.endDate) params.set('end_date', state.endDate);
        }

        const url = `/api/state?${params.toString()}`;
        const res = await fetch(url, { cache: 'no-store' });
        if (res.status === 401) { location.replace('/login'); return; }
        const data = await res.json();
        if (data.ok) {
            state.monthlySummary = data.monthly_summary;
            state.records = data.records || [];
            state.deletedRecords = data.deleted_records || [];
            state.canPurgeTrash = data.can_purge_trash === true;
            state.deletionAudit = data.deletion_audit || [];
            renderTrash();
            state.storesCatalog = data.stores_catalog || {};
            state.knownStores = data.known_stores || [];
            state.whatsappConfig = data.whatsapp_config || {};

            renderAll();
            populateDatalist();
        }
    } catch (err) {
        console.error('Erro ao sincronizar com servidor:', err);
    }
}

function renderAll() {
    renderDashboard();
    renderStoresView();
    renderIncidentsView();
    renderRecordsView();
    if (state.activeDrawerSection) {
        renderDrawerItems();
    }
}

function populateDatalist() {
    const datalist = document.getElementById('known-stores-datalist');
    if (!datalist) return;
    const names = new Set();
    state.knownStores.forEach(s => s.name && names.add(s.name));
    state.records.forEach(r => r.store_name && names.add(r.store_name));
    datalist.innerHTML = Array.from(names).sort().map(nm => `<option value="${escapeHtml(nm)}">`).join('');
}

function switchView(viewName, { activeStoresOnly = false } = {}) {
    state.currentView = viewName;
    state.activeStoresOnly = activeStoresOnly;
    document.getElementById('btn-open-add-modal').style.display = viewName === 'settings' ? 'none' : '';
    if (viewName === 'settings') loadMasterUsers();
    const dateControl = document.getElementById('calendar-filter-container');
    if (dateControl) dateControl.style.display = viewName === 'summary' ? '' : 'none';
    if (viewName !== 'summary') {
        document.getElementById('calendar-menu').style.display = 'none';
        dateControl.classList.remove('open');
    }
    const viewTitles = {summary:'Resumo das lojas',stores:'Lojas',incidents:'Incidentes',records:'Registros',settings:'Configurações',trash:'Lixeira'};
    document.getElementById('page-main-title').textContent = viewTitles[viewName] || 'Kloube';
    document.querySelectorAll('.sidebar-nav .nav-item').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-view') === viewName);
    });

    document.querySelectorAll('.view-panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === `view-${viewName}`);
    });

    if (viewName === 'stores') renderStoresView();
    if (viewName === 'incidents') renderIncidentsView();
    if (viewName === 'records') renderRecordsView();
}

async function deleteRecord(id) {
    if (!id || !confirm('Mover este lançamento para a lixeira? Você poderá restaurá-lo depois.')) return;
    try {
        const res = await fetch('/api/records/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id }),
        });
        const data = await res.json();
        if (data.ok) {
            showToast('Movido para a lixeira', 'O registro pode ser restaurado na Lixeira.');
            await fetchState();
        } else {
            alert(data.error || 'Erro ao excluir');
        }
    } catch (e) {
        alert('Falha na comunicação com o servidor.');
    }
}

function quickAddRecordForStore(storeName) {
    const modal = document.getElementById('add-modal');
    document.getElementById('input-store').value = storeName;
    modal.style.display = 'flex';
}

/* ==========================================================================
   CONFIGURAÇÃO DE EVENTOS DOM
   ========================================================================== */
document.addEventListener('DOMContentLoaded', () => {
    // 1. Navegação lateral
    document.querySelectorAll('.sidebar-nav button.nav-item[data-view]').forEach(btn => {
        btn.addEventListener('click', () => {
            const view = btn.getAttribute('data-view');
            switchView(view);
        });
    });

    initDatePicker();

    // 3. Clique nos cartões de métricas (abre o painel de detalhes)
    document.querySelectorAll('[data-inspect]').forEach(item => {
        item.addEventListener('click', () => {
            const section = item.getAttribute('data-inspect');
            openDrawer(section);
        });
    });

    document.getElementById('btn-active-stores').addEventListener('click', () => {
        state.storesSearch = '';
        document.getElementById('stores-search-input').value = '';
        switchView('stores', { activeStoresOnly: true });
    });
    document.getElementById('btn-all-stores').addEventListener('click', () => switchView('stores'));

    // 4. Clique nos 4 Cards de Atenção (Filtra a tabela de incidentes)
    document.querySelectorAll('[data-filter-attention]').forEach(card => {
        card.addEventListener('click', () => {
            const filterType = card.getAttribute('data-filter-attention');
            if (state.attentionFilter === filterType) {
                state.attentionFilter = null; // desmarca
            } else {
                state.attentionFilter = filterType;
            }
            renderDashboard();
        });
    });

    // 5. Drawer controles
    document.getElementById('btn-close-drawer').addEventListener('click', closeDrawer);
    document.getElementById('inspector-overlay').addEventListener('click', closeDrawer);
    document.getElementById('drawer-search').addEventListener('input', (e) => {
        state.drawerSearch = e.target.value;
        renderDrawerItems();
    });

    // 6. Modal Detalhes da Loja Controles
    const storeDetailModal = document.getElementById('store-detail-modal');
    document.getElementById('btn-close-store-detail').addEventListener('click', () => {
        storeDetailModal.style.display = 'none';
    });
    document.getElementById('btn-close-store-footer').addEventListener('click', () => {
        storeDetailModal.style.display = 'none';
    });
    storeDetailModal.addEventListener('click', (e) => {
        if (e.target === storeDetailModal) {
            storeDetailModal.style.display = 'none';
        }
    });

    // 7. Ordenação e Filtro por Data na Tela de Registros
    const btnToggleSort = document.getElementById('btn-toggle-sort-order');
    const thSortDate = document.getElementById('th-sort-date');
    const sortIcon = document.getElementById('sort-date-icon');

    function toggleDateSort() {
        state.recordsSortAsc = !state.recordsSortAsc;
        if (btnToggleSort) {
            btnToggleSort.innerHTML = state.recordsSortAsc ? '<span>Data: Mais antigo ⬆</span>' : '<span>Data: Mais recente ⬇</span>';
        }
        if (sortIcon) {
            sortIcon.textContent = state.recordsSortAsc ? '⬆' : '⬇';
        }
        renderRecordsView();
    }

    if (btnToggleSort) btnToggleSort.addEventListener('click', toggleDateSort);
    if (thSortDate) thSortDate.addEventListener('click', toggleDateSort);

    document.getElementById('history-type-filter').addEventListener('change', e => { state.historyType = e.target.value; renderRecordsView(); });
    const histDateFilter = document.getElementById('history-date-filter');
    if (histDateFilter) {
        histDateFilter.addEventListener('input', (e) => {
            state.recordsDateFilter = e.target.value;
            renderRecordsView();
        });
    }

    const histSearch = document.getElementById('history-search-input');
    if (histSearch) {
        histSearch.addEventListener('input', (e) => {
            state.historySearch = e.target.value;
            renderRecordsView();
        });
    }

    // 8. Modais Adicionar e Importar
    const addModal = document.getElementById('add-modal');
    document.getElementById('btn-open-add-modal').addEventListener('click', () => {
        addModal.style.display = 'flex';
    });
    document.getElementById('btn-new-store').addEventListener('click', () => {
        document.getElementById('add-record-form').reset();
        const publication = addModal.querySelector('input[name="event_type"][value="store_published"]');
        publication.checked = true;
        publication.dispatchEvent(new Event('change', { bubbles: true }));
        addModal.style.display = 'flex';
        document.getElementById('input-store').focus();
    });
    document.getElementById('btn-close-add').addEventListener('click', () => {
        addModal.style.display = 'none';
    });
    document.getElementById('btn-cancel-add').addEventListener('click', () => {
        addModal.style.display = 'none';
    });

    const pasteModal = document.getElementById('paste-modal');
    const openPasteBtn = document.getElementById('btn-open-paste-modal');
    if (openPasteBtn) {
        openPasteBtn.addEventListener('click', () => pasteModal.style.display = 'flex');
    }
    const openPasteBtn2 = document.getElementById('btn-open-paste-modal-2');
    if (openPasteBtn2) {
        openPasteBtn2.addEventListener('click', () => pasteModal.style.display = 'flex');
    }
    document.getElementById('btn-close-paste').addEventListener('click', () => {
        pasteModal.style.display = 'none';
    });
    document.getElementById('btn-cancel-paste').addEventListener('click', () => {
        pasteModal.style.display = 'none';
    });

    // 9. Formulário de Novo Registro
    const typeRadios = document.querySelectorAll('input[name="event_type"]');
    typeRadios.forEach(radio => {
        radio.addEventListener('change', () => {
            document.querySelectorAll('.type-radio-card').forEach(c => c.classList.remove('active'));
            radio.closest('.type-radio-card').classList.add('active');

            const val = radio.value;
            const inactBox = document.getElementById('field-inactive-cat-box');
            const alsoRow = document.getElementById('row-also-inactive');
            if (val === 'store_inactive') {
                inactBox.style.display = 'block';
                alsoRow.style.display = 'none';
            } else if (val === 'ads_suspended') {
                inactBox.style.display = 'none';
                alsoRow.style.display = 'flex';
            } else {
                inactBox.style.display = 'none';
                alsoRow.style.display = 'none';
            }
        });
    });

    document.querySelectorAll('.chip-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.getElementById('input-reason').value = btn.getAttribute('data-reason');
        });
    });

    document.getElementById('add-record-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const eventType = document.querySelector('input[name="event_type"]:checked').value;
        const storeName = document.getElementById('input-store').value.trim();
        const domain = document.getElementById('input-domain').value.trim();
        const account = document.getElementById('input-account').value.trim();
        const inactiveCat = document.getElementById('input-inactive-cat').value;
        const reason = document.getElementById('input-reason').value.trim();
        const alsoInactive = document.getElementById('chk-also-inactive').checked;
        const dateVal = document.getElementById('input-date').value;

        if (!storeName) {
            alert('Por favor informe o nome da loja.');
            return;
        }

        const payload = {
            event_type: eventType,
            store_name: storeName,
            domain: domain,
            account_number: account,
            inactive_category: eventType === 'store_inactive' ? inactiveCat : '',
            reason: reason || (eventType === 'ads_suspended' ? 'Conta suspensa' : (eventType === 'store_reactivated' ? 'Loja reativada' : (eventType === 'store_published' ? 'Loja nova publicada' : 'Inativa'))),
            also_mark_store_inactive: alsoInactive,
            month_key: state.selectedMonth,
            created_at: dateVal ? `${dateVal} 12:00:00` : '',
        };

        try {
            const res = await fetch('/api/records', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (data.ok) {
                const monitor = data.monitor_sync;
                showToast('Sucesso', monitor && !monitor.ok ? monitor.message : monitor && monitor.missing_domain ? 'Registro salvo. Informe um domínio válido para incluir a loja no monitoramento.' : eventType === 'store_published' ? 'Loja salva e domínio incluído no monitoramento.' : 'Registro gravado com sucesso.');
                addModal.style.display = 'none';
                document.getElementById('add-record-form').reset();
                await fetchState();
            } else {
                alert(data.error || 'Erro ao salvar registro.');
            }
        } catch (err) {
            alert('Erro de comunicação com o servidor.');
        }
    });

    // 10. Formulário de Importação do WhatsApp
    document.getElementById('paste-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const text = document.getElementById('paste-textarea').value.trim();
        if (!text) {
            alert('Cole o texto das mensagens.');
            return;
        }

        try {
            const res = await fetch('/api/whatsapp/import-paste', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ raw_text: text }),
            });
            const data = await res.json();
            if (data.ok) {
                showToast('Importado', `${data.imported_count || 0} novo(s) registro(s). ${data.duplicates_count || 0} repetido(s) ignorado(s).`);
                pasteModal.style.display = 'none';
                document.getElementById('paste-textarea').value = '';
                await fetchState();
            } else {
                alert(data.error || 'Erro ao processar mensagens.');
            }
        } catch (err) {
            alert('Falha na requisição.');
        }
    });

    // Busca nas lojas
    const storesSearch = document.getElementById('stores-search-input');
    if (storesSearch) {
        storesSearch.addEventListener('input', (e) => {
            state.storesSearch = e.target.value;
            renderStoresView();
        });
    }

    // Filtros de incidentes
    document.querySelectorAll('.inc-filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.inc-filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            state.incidentsFilter = btn.getAttribute('data-inc-cat');
            renderIncidentsView();
        });
    });

    // Conexão Nuvem / Upstash Redis
    async function checkSyncStatus() {
        try {
            const res = await fetch(`/api/sync-status?t=${Date.now()}`, { cache: 'no-store' });
            if (!res.ok) return;
            const data = await res.json();
            const badge = document.getElementById('sidebar-sync-badge');
            const text = document.getElementById('sidebar-sync-text');
            const cloudBadge = document.getElementById('badge-cloud-status');

            if (data.connected) {
                if (badge) badge.className = 'sidebar-status-box';
                if (text) text.textContent = 'Nuvem ativa (' + data.provider + ')';
                if (cloudBadge) {
                    cloudBadge.className = 'badge-pill badge-success';
                    cloudBadge.textContent = '● Conectado (Multi-usuário ativo)';
                }
            } else if (data.is_vercel) {
                if (text) text.textContent = 'Configuração compartilhada pendente';
                if (cloudBadge) {
                    cloudBadge.className = 'badge-pill badge-warning';
                    cloudBadge.textContent = 'Configuração da sincronização pendente';
                }
            } else {
                if (text) text.textContent = 'Armazenamento local';
                if (cloudBadge) {
                    cloudBadge.className = 'badge-pill badge-muted';
                    cloudBadge.textContent = '● Local (Sem nuvem)';
                }
            }
        } catch (e) {
            // silent
        }
    }

    const btnSaveCloud = document.getElementById('btn-save-cloud-config');
    if (btnSaveCloud) {
        btnSaveCloud.addEventListener('click', async () => {
            const url = (document.getElementById('cfg-cloud-url').value || '').trim();
            const token = (document.getElementById('cfg-cloud-token').value || '').trim();
            const resultSpan = document.getElementById('cloud-test-result');

            if (!url || !token) {
                resultSpan.style.color = '#EF4444';
                resultSpan.textContent = 'Preencha a URL e o Token do Upstash Redis.';
                return;
            }

            resultSpan.style.color = '#94A3B8';
            resultSpan.textContent = 'Testando conexão...';
            try {
                const res = await fetch('/api/cloud-config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url, token })
                });
                const data = await res.json();
                if (data.ok) {
                    resultSpan.style.color = '#10B981';
                    resultSpan.textContent = 'Conectado com sucesso! Alterações agora sincronizam em tempo real.';
                    await checkSyncStatus();
                    await fetchState();
                } else {
                    resultSpan.style.color = '#EF4444';
                    resultSpan.textContent = data.error || 'Erro ao conectar.';
                }
            } catch (err) {
                resultSpan.style.color = '#EF4444';
                resultSpan.textContent = 'Erro ao enviar dados para o servidor.';
            }
        });
    }

    // Alterar status operacional da loja (Ativa / Inativa)
    async function changeStoreStatus(storeName, newStatus) {
        if (!storeName || !newStatus) return;
        try {
            const res = await fetch('/api/store-status', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ store_name: storeName, status: newStatus })
            });
            const data = await res.json();
            if (data.ok) {
                showToast('Status atualizado', `${storeName} agora está marcada como ${newStatus}.`);
                await fetchState();
            } else {
                alert(data.error || 'Erro ao alterar status operacional.');
            }
        } catch (e) {
            alert('Erro ao comunicar com o servidor.');
        }
    }
    window.changeStoreStatus = changeStoreStatus;

    // Modal de Edição de Registro
    function openEditRecordModal(recId) {
        const rec = state.records.find(r => r.id === recId);
        if (!rec) return;

        document.getElementById('edit-record-id').value = rec.id || '';
        document.getElementById('edit-event-type').value = rec.event_type || 'ads_suspended';
        document.getElementById('edit-store').value = rec.store_name || '';
        document.getElementById('edit-domain').value = rec.domain || '';
        document.getElementById('edit-account').value = (rec.account_number && rec.account_number !== '—') ? rec.account_number : '';
        document.getElementById('edit-date').value = rec.created_at || '';
        document.getElementById('edit-reason').value = rec.reason || '';

        const inactBox = document.getElementById('edit-inactive-cat-box');
        if (rec.event_type === 'store_inactive') {
            inactBox.style.display = 'block';
            document.getElementById('edit-inactive-cat').value = rec.inactive_category || 'ads_block';
        } else {
            inactBox.style.display = 'none';
        }

        document.getElementById('edit-record-modal').style.display = 'flex';
    }

    const editEvTypeEl = document.getElementById('edit-event-type');
    if (editEvTypeEl) {
        editEvTypeEl.addEventListener('change', (e) => {
            const inactBox = document.getElementById('edit-inactive-cat-box');
            if (e.target.value === 'store_inactive') {
                inactBox.style.display = 'block';
            } else {
                inactBox.style.display = 'none';
            }
        });
    }

    const closeEdit = () => {
        const modal = document.getElementById('edit-record-modal');
        if (modal) modal.style.display = 'none';
    };
    const btnCloseEditModal = document.getElementById('btn-close-edit-modal');
    if (btnCloseEditModal) btnCloseEditModal.addEventListener('click', closeEdit);
    const btnCancelEditModal = document.getElementById('btn-cancel-edit-modal');
    if (btnCancelEditModal) btnCancelEditModal.addEventListener('click', closeEdit);

    const editForm = document.getElementById('edit-record-form');
    if (editForm) {
        editForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const id = document.getElementById('edit-record-id').value;
            const event_type = document.getElementById('edit-event-type').value;
            const inactive_category = document.getElementById('edit-inactive-cat').value;
            const store_name = document.getElementById('edit-store').value.trim();
            const domain = document.getElementById('edit-domain').value.trim();
            const account_number = document.getElementById('edit-account').value.trim();
            const created_at = document.getElementById('edit-date').value.trim();
            const reason = document.getElementById('edit-reason').value.trim();

            if (!store_name) {
                alert('Informe o nome da loja.');
                return;
            }

            try {
                const res = await fetch('/api/records/update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        id,
                        event_type,
                        inactive_category: event_type === 'store_inactive' ? inactive_category : '',
                        store_name,
                        domain,
                        account_number,
                        created_at,
                        reason
                    })
                });
                const data = await res.json();
                if (data.ok) {
                    closeEdit();
                    showToast('Atualizado', 'Registro salvo com sucesso!');
                    await fetchState();
                } else {
                    alert(data.error || 'Erro ao atualizar registro.');
                }
            } catch (err) {
                alert('Erro de conexão com o servidor.');
            }
        });
    }

    // Delegação global de cliques para lojas e edição
    document.addEventListener('click', (e) => {
        const inspectBtn = e.target.closest('.btn-inspect-store');
        if (inspectBtn) {
            e.preventDefault();
            const sName = inspectBtn.getAttribute('data-store-name');
            if (sName) {
                openStoreDetail(sName);
            }
            return;
        }

        const editBtn = e.target.closest('.btn-edit-record');
        if (editBtn) {
            e.preventDefault();
            const rId = editBtn.getAttribute('data-id');
            if (rId) {
                openEditRecordModal(rId);
            }
            return;
        }
    });

    const requestedView = new URLSearchParams(location.search).get('view');
    if (['summary','stores','incidents','records','settings','trash'].includes(requestedView)) switchView(requestedView);

    // Carregamento inicial e sincronização em tempo real
    fetchState();
    checkSyncStatus();
    setInterval(() => {
        fetchState();
        checkSyncStatus();
    }, 15000);

    window.addEventListener('focus', () => {
        fetchState();
        checkSyncStatus();
    });
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            fetchState();
            checkSyncStatus();
        }
    });
});

document.getElementById('btn-app-logout').addEventListener('click', async () => {
    await fetch('/api/logout', {method:'POST'});
    localStorage.removeItem('kloube_auth_user_v1');
    location.replace('/login');
});

function renderTrash() {
    const body = document.getElementById('trash-records-body');
    const audit = document.getElementById('trash-audit-body');
    if (!body || !audit) return;
    body.innerHTML = state.deletedRecords.length ? state.deletedRecords.map(record => `<tr><td>${escapeHtml(record.store_name)}</td><td>${escapeHtml(record.reason || record.event_type)}</td><td>${escapeHtml(formatPtDate(record.created_at))}</td><td>${escapeHtml(formatPtDate(record._deleted_at))}</td><td>${escapeHtml(record._deleted_by)}</td><td><button type="button" class="btn btn-secondary btn-sm" data-restore-id="${escapeHtml(record._trash_id)}">Restaurar</button>${state.canPurgeTrash ? `<button type="button" class="btn btn-secondary btn-sm" style="color:#f87171;margin-left:8px" data-purge-id="${escapeHtml(record._trash_id)}">Excluir definitivamente</button>` : ''}</td></tr>`).join('') : '<tr><td colspan="6" class="table-empty-box">Lixeira vazia.</td></tr>';
    audit.innerHTML = state.deletionAudit.length ? state.deletionAudit.map(entry => `<tr><td>${escapeHtml(entry.action)}</td><td>${escapeHtml(entry.store_name)}</td><td>${escapeHtml(formatPtDate(entry.at))}</td><td>${escapeHtml(entry.by)}</td></tr>`).join('') : '<tr><td colspan="4" class="table-empty-box">Nenhuma exclusão registrada.</td></tr>';
}
document.getElementById('trash-records-body').addEventListener('click', async event => {
    const button = event.target.closest('[data-restore-id], [data-purge-id]');
    if (!button) return;
    const purge = !!button.dataset.purgeId;
    if (purge && (!state.canPurgeTrash || !confirm('Excluir definitivamente este registro da lixeira? Essa ação não pode ser desfeita pelo app.'))) return;
    button.disabled = true;
    try {
        const response = await fetch(purge ? '/api/records/purge' : '/api/records/restore', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({trash_id:purge ? button.dataset.purgeId : button.dataset.restoreId, confirm:purge})});
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || 'Não foi possível restaurar.');
        showToast(purge ? 'Excluído definitivamente' : 'Restaurado', purge ? 'Registro removido da lixeira. A ação ficou no histórico.' : 'Registro devolvido ao histórico.');
        await fetchState();
    } catch(error) { alert(error.message); }
    finally {button.disabled = false;}
});

async function loadMasterUsers() {
    const card=document.getElementById('master-users-card');
    try {
        const sessionResponse=await fetch('/api/session',{cache:'no-store'});
        const session=await sessionResponse.json();
        card.style.display=session.can_manage_users?'':'none';
        if(!session.can_manage_users)return;
        const response=await fetch('/api/users',{cache:'no-store'});
        const data=await response.json();
        if(!data.ok)throw new Error(data.error||'Não foi possível carregar usuários.');
        document.getElementById('master-users-body').innerHTML=data.users.map(user=>`<tr><td>${escapeHtml(user.email)}${user.email===data.current_user?' · Você':''}</td><td>${user.master?'Administrador master':'Usuário'}</td><td><button type="button" class="btn btn-secondary btn-sm" data-delete-user="${escapeHtml(user.email)}">Excluir usuário</button></td></tr>`).join('');
        document.getElementById('master-users-audit').innerHTML=data.audit.length?data.audit.map(item=>`<p>${escapeHtml(item.action)} · ${escapeHtml(item.email)} · ${escapeHtml(item.by)} · ${escapeHtml(formatPtDate(item.at))}</p>`).join(''):'Nenhuma ação registrada.';
    }catch(error){document.getElementById('master-users-message').textContent=error.message;}
}
document.getElementById('master-create-user-form').addEventListener('submit',async event=>{
    event.preventDefault();
    const button=document.getElementById('master-create-user-btn'),message=document.getElementById('master-users-message');
    button.disabled=true;
    try{
        const response=await fetch('/monitoramento/api/auth/create-user',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({new_email:document.getElementById('master-new-email').value.trim(),new_password:document.getElementById('master-new-password').value})});
        const data=await response.json();if(!data.ok)throw new Error(data.error||'Não foi possível criar a conta.');
        event.target.reset();message.textContent='Conta criada. Nenhum e-mail foi enviado.';await loadMasterUsers();
    }catch(error){message.textContent=error.message;}finally{button.disabled=false;}
});
document.getElementById('master-users-body').addEventListener('click',async event=>{
    const button=event.target.closest('[data-delete-user]');if(!button)return;
    const email=button.dataset.deleteUser;
    if(!confirm(`Excluir o acesso de ${email}? As sessões desse usuário serão invalidadas.`))return;
    button.disabled=true;
    try{
        const response=await fetch('/api/users/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});
        const data=await response.json();if(!data.ok)throw new Error(data.error||'Não foi possível excluir.');
        if(data.self_deleted){localStorage.removeItem('kloube_auth_user_v1');location.replace('/login');return;}
        document.getElementById('master-users-message').textContent='Acesso excluído.';await loadMasterUsers();
    }catch(error){document.getElementById('master-users-message').textContent=error.message;button.disabled=false;}
});
