function initDatePicker() {
    const container = document.getElementById('calendar-filter-container');
    const trigger = document.getElementById('btn-calendar-trigger');
    const menu = document.getElementById('calendar-menu');
    const label = document.getElementById('calendar-trigger-label');
    const today = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/Sao_Paulo' }));
    today.setHours(0, 0, 0, 0);
    const iso = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    const date = s => new Date(`${s}T12:00:00`);
    const shift = (d, n) => { const r = new Date(d); r.setDate(r.getDate()+n); return r; };
    const presets = [['exact_day','Dia exato'],['custom','Personalizar'],['today','Hoje'],['yesterday','Ontem'],['this_week','Esta semana (dom. até hoje)'],['last7','Últimos 7 dias'],['prev_week','Semana passada (dom. a sáb.)'],['last14','Últimos 14 dias'],['this_month','Este mês'],['last30','Últimos 30 dias'],['prev_month','Último mês'],['all','Todo o período']];
    let start = state.startDate || iso(new Date(today.getFullYear(),today.getMonth(),1));
    let end = state.endDate || iso(today);
    let month = new Date(date(end).getFullYear(),date(end).getMonth(),1);
    let selectingEnd = false;
    let selected = state.selectedPreset;
    menu.innerHTML = `<div class="range-layout"><div class="range-shortcuts">${presets.map(([id,text]) => `<button type="button" class="calendar-preset-btn" data-range="${id}">${text}</button>`).join('')}<div class="range-relative"><label><input id="range-days" type="number" min="1" max="3650" value="30" aria-label="Quantidade de dias"> dias até</label><button type="button" data-relative="0">hoje</button><button type="button" data-relative="1">ontem</button></div></div><div class="range-calendar"><div class="range-inputs"><label>Data de início<input type="date" id="range-start" value="${start}"></label><span>–</span><label>Data de término<input type="date" id="range-end" value="${end}"></label></div><div class="range-month-nav"><strong id="range-month-label"></strong><button type="button" id="range-prev" aria-label="Mês anterior">‹</button><button type="button" id="range-next" aria-label="Próximo mês">›</button></div><div class="range-weekdays">${['D','S','T','Q','Q','S','S'].map(d=>`<span>${d}</span>`).join('')}</div><div id="range-grid" class="range-grid"></div><p id="range-hint" aria-live="polite">Selecione a data de início e a de término.</p><div class="range-footer"><button type="button" id="range-cancel" class="btn btn-secondary">Cancelar</button><button type="button" id="range-apply" class="btn btn-primary">Aplicar</button></div></div></div>`;
    const startInput = document.getElementById('range-start');
    const endInput = document.getElementById('range-end');
    function render() {
        startInput.value = start; endInput.value = end;
        endInput.disabled = selected === 'exact_day';
        document.getElementById('range-hint').textContent = selected === 'exact_day' ? 'Clique em um dia para filtrar essa data.' : 'Selecione a data de início e a de término.';
        document.getElementById('range-month-label').textContent = month.toLocaleDateString('pt-BR',{month:'long',year:'numeric'});
        menu.querySelectorAll('[data-range]').forEach(b=>b.classList.toggle('active',b.dataset.range===selected));
        let html = '';
        const first = shift(month,-month.getDay());
        for(let i=0;i<42;i++) {
            const d=shift(first,i), key=iso(d);
            const classes=['range-day',d.getMonth()!==month.getMonth()?'outside':'',key===start||key===end?'endpoint':'',key>start&&key<end?'between':'',key===iso(today)?'today':''].filter(Boolean).join(' ');
            html+=`<button type="button" class="${classes}" data-date="${key}" aria-label="${d.toLocaleDateString('pt-BR')}" aria-pressed="${key>=start&&key<=end}">${d.getDate()}</button>`;
        }
        document.getElementById('range-grid').innerHTML=html;
    }
    function close(){menu.style.display='none';container.classList.remove('open');trigger.setAttribute('aria-expanded','false');}
    trigger.setAttribute('aria-expanded','false');
    trigger.addEventListener('click',()=>{
        if(menu.style.display!=='none'){close();return;}
        start=state.startDate||start;end=state.endDate||end;selected=state.selectedPreset;
        month=new Date(date(end).getFullYear(),date(end).getMonth(),1);
        render();menu.style.display='block';container.classList.add('open');trigger.setAttribute('aria-expanded','true');
    });
    document.addEventListener('click',e=>{if(!container.contains(e.target))close();});
    document.addEventListener('keydown',e=>{if(e.key==='Escape')close();});
    document.getElementById('range-prev').onclick=()=>{month.setMonth(month.getMonth()-1);render();};
    document.getElementById('range-next').onclick=()=>{month.setMonth(month.getMonth()+1);render();};
    document.getElementById('range-cancel').onclick=close;
    menu.addEventListener('click',e=>{
        const day=e.target.closest('[data-date]');
        if(day && selected==='exact_day'){start=end=day.dataset.date;render();document.getElementById('range-apply').click();return;}
        if(day){selected='custom';if(!selectingEnd){start=day.dataset.date;end=start;selectingEnd=true;}else{end=day.dataset.date;if(end<start)[start,end]=[end,start];selectingEnd=false;}render();return;}
        const preset=e.target.closest('[data-range]');
        const relative=e.target.closest('[data-relative]');
        if(!preset&&!relative)return;
        selected=preset?preset.dataset.range:'custom';
        if(selected==='exact_day'){end=start;selectingEnd=false;render();return;}
        if(selected==='custom'&&!relative){render();startInput.focus();return;}
        let a=new Date(today),b=new Date(today);
        const weekday=today.getDay();
        if(relative){const n=Number(document.getElementById('range-days').value);if(!Number.isInteger(n)||n<1||n>3650)return; b=shift(today,-Number(relative.dataset.relative));a=shift(b,1-n);}
        else if(selected==='yesterday')a=b=shift(today,-1);
        else if(selected==='this_week')a=shift(today,-weekday);
        else if(selected==='prev_week'){a=shift(today,-weekday-7);b=shift(today,-weekday-1);}
        else if(/^last\d+$/.test(selected))a=shift(today,1-Number(selected.slice(4)));
        else if(selected==='this_month')a=new Date(today.getFullYear(),today.getMonth(),1);
        else if(selected==='prev_month'){a=new Date(today.getFullYear(),today.getMonth()-1,1);b=new Date(today.getFullYear(),today.getMonth(),0);}
        else if(selected==='all'){a=new Date(2000,0,1);b=new Date(2099,11,31);}
        start=iso(a);end=iso(b);month=new Date(b.getFullYear(),b.getMonth(),1);selectingEnd=false;render();
    });
    [startInput,endInput].forEach(input=>input.addEventListener('change',()=>{start=startInput.value;end=selected==='exact_day'?start:endInput.value;if(selected!=='exact_day')selected='custom';if(start){month=new Date(date(start).getFullYear(),date(start).getMonth(),1);}render();}));
    document.getElementById('range-apply').onclick=()=>{
        if(!start||!end||start>end){document.getElementById('range-hint').textContent='Informe um período válido: início antes do término.';return;}
        state.selectedPreset=selected;state.periodFilter='custom';state.startDate=start;state.endDate=end;state.selectedMonth=start.slice(0,7);
        label.textContent=presets.find(p=>p[0]===selected)?.[1]||`${date(start).toLocaleDateString('pt-BR')} – ${date(end).toLocaleDateString('pt-BR')}`;
        if(selected==='exact_day')label.textContent=date(start).toLocaleDateString('pt-BR');
        if(selected==='custom')label.textContent=`${date(start).toLocaleDateString('pt-BR')} – ${date(end).toLocaleDateString('pt-BR')}`;
        close();fetchState();
    };
    render();
}
