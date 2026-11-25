/* QopenCRM — interacción: diálogos, tablero arrastrable, filtros y atajos. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const modal = $('#modal'), modalBody = $('#modal-body');

  /* tema claro/oscuro, recordado en el navegador */
  document.addEventListener('click', e => {
    if (!e.target.closest('[data-theme-toggle]')) return;
    const root = document.documentElement;
    const next = root.dataset.theme === 'light' ? 'dark' : 'light';
    root.dataset.theme = next;
    try { localStorage.setItem('theme', next); } catch (_) {}
  });

  /* diálogos: cualquier elemento con data-modal="/url" abre su formulario */
  let modalSubmitted = false;
  function openModal(url) {
    modalSubmitted = false;
    htmx.ajax('GET', url, { target: '#modal-body', swap: 'innerHTML' }).then(() => {
      modal.classList.toggle('wide', !!$('[data-wide]', modalBody));
      if (!modal.open) modal.showModal();
      const first = $('input:not([type=hidden]):not([type=checkbox]),select,textarea', modalBody);
      if (first && !matchMedia('(max-width:820px)').matches) first.focus();
    });
  }
  window.openModal = openModal;
  document.addEventListener('click', e => {
    const opener = e.target.closest('[data-modal]');
    if (opener) { e.preventDefault(); e.stopPropagation(); openModal(opener.dataset.modal); return; }
    if (e.target.closest('[data-close]')) modal.close();
    if (e.target === modal) modal.close();
    const row = e.target.closest('tr[data-href]');
    if (row && !e.target.closest('a,button,input,form')) location.href = row.dataset.href;
  });
  document.body.addEventListener('openModal', e => openModal(e.detail.value));
  modal.addEventListener('submit', () => { modalSubmitted = true; });
  /* si se cancela «perder» tras arrastrar, la tarjeta vuelve a su columna */
  modal.addEventListener('close', () => { modalBody.innerHTML = ''; if (!modalSubmitted && $('#board')) reloadBoard(); });

  /* errores del servidor a la vista, no en silencio */
  document.body.addEventListener('htmx:responseError', e => toast(e.detail.xhr.status === 403 ? 'No tienes permiso para hacer eso.' : (e.detail.xhr.responseText || 'Algo ha fallado. Inténtalo de nuevo.').slice(0, 160), true));
  document.body.addEventListener('htmx:sendError', () => toast('Sin conexión con el servidor.', true));
  function toast(text, error) {
    const el = document.createElement('div');
    el.className = 'toast' + (error ? ' error' : '');
    el.textContent = text;
    $('#toasts').append(el);
    setTimeout(() => el.remove(), 5000);
  }
  $$('#toasts .toast').forEach(el => setTimeout(() => el.remove(), 6000));

  /* tablero: arrastrar entre etapas; ganar o perder soltando en la franja inferior */
  function reloadBoard() {
    return htmx.ajax('GET', location.pathname + location.search, { target: '#board', swap: 'outerHTML' });
  }
  function move(card, stage, position) {
    const data = new FormData();
    data.append('stage', stage); data.append('position', position); data.append('from', 'board');
    return fetch(card.dataset.move, { method: 'POST', body: data, headers: { 'X-CSRFToken': csrf(), 'HX-Request': 'true' } })
      .then(r => {
        if (!r.ok) { toast(r.status === 403 ? 'No puedes mover esta oportunidad.' : 'No se ha podido mover.', true); return reloadBoard(); }
        const trigger = r.headers.get('HX-Trigger');
        if (trigger && trigger.includes('openModal')) openModal(JSON.parse(trigger).openModal);
        else reloadBoard();
      });
  }
  const csrf = () => JSON.parse(document.body.getAttribute('hx-headers'))['X-CSRFToken'];
  function initBoard(root = document) {
    $$('[data-stage]', root).forEach(list => {
      if (list._sortable) return;
      list._sortable = new Sortable(list, {
        group: 'deals', animation: 160, draggable: '.deal', delay: 120, delayOnTouchOnly: true,
        ghostClass: 'sortable-ghost', dragClass: 'sortable-drag',
        onStart: () => document.body.classList.add('dragging'),
        onEnd: ev => {
          document.body.classList.remove('dragging');
          if (ev.from === ev.to && ev.oldIndex === ev.newIndex) return;
          move(ev.item, ev.to.dataset.stage, ev.newIndex);
        },
      });
    });
  }
  document.body.addEventListener('htmx:afterSwap', e => initBoard(e.target.parentElement || document));
  initBoard();

  /* línea de oportunidad: el producto propone tipo de cobro, unidad y precio; una línea libre los elige a mano */
  function syncLine() {
    const billing = $('#id_billing_model'), period = $('#id_period');
    const wrap = period && period.closest('.field');
    if (billing && wrap) wrap.hidden = billing.value !== 'monthly';
  }
  document.addEventListener('change', e => {
    if (e.target.id === 'id_billing_model') { syncLine(); return; }
    if (e.target.id !== 'id_product') return;
    const data = $('#line-prices');
    if (!data) return;
    const info = JSON.parse(data.textContent)[e.target.value];
    const hint = $('#line-hint');
    if (!info) { if (hint) hint.textContent = 'Línea libre: elige el tipo de cobro y descríbela.'; return; }
    const period = $('#id_period'), price = $('#id_unit_price'), billing = $('#id_billing_model'), unit = $('#id_unit');
    const annual = period && period.value === 'annual' && info.annual;
    if (price) price.value = (annual ? info.annual : info.price) || '';
    if (billing) billing.value = info.model;
    if (unit) unit.value = info.unit;
    if (hint) hint.textContent = info.billing + ' · por ' + info.unit + (info.price ? '' : ' · sin tarifa: indica el precio o déjalo a consultar');
    syncLine();
  });
  document.body.addEventListener('htmx:afterSwap', syncLine);

  /* IA: rellena los campos vacíos del formulario a partir de un texto pegado */
  document.addEventListener('click', e => {
    const button = e.target.closest('[data-ai-fill]');
    if (!button) return;
    const status = $('#ai-status'), data = new FormData();
    data.append('text', $('#ai-text').value);
    button.disabled = true; status.textContent = 'Leyendo el texto…';
    fetch(button.dataset.aiFill, { method: 'POST', body: data, headers: { 'X-CSRFToken': csrf() } })
      .then(r => r.json().then(body => ({ ok: r.ok, body })))
      .then(({ ok, body }) => {
        if (!ok) { status.textContent = body.error || 'No se ha podido leer el texto.'; return; }
        let filled = 0;
        Object.entries(body.fields).forEach(([name, value]) => {
          const input = $('#id_' + name, modalBody);
          if (input && !input.value) { input.value = value; filled++; }
        });
        status.textContent = filled ? filled + ' campos rellenados. Revísalos antes de guardar.' : 'No he encontrado datos nuevos en el texto.';
      })
      .catch(() => { status.textContent = 'Sin conexión con el servidor.'; })
      .finally(() => { button.disabled = false; });
  });

  /* Ctrl/⌘+Enter envía la nota */
  document.addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && e.target.matches('.composer textarea')) e.target.form.requestSubmit();
  });
  document.body.addEventListener('htmx:afterRequest', e => {
    if (e.target.matches && e.target.matches('.composer') && e.detail.successful) { e.target.reset(); $('.timeline-empty')?.remove(); }
  });

  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
})();

/* Editor de presupuestos: líneas editables y totales al vuelo */
(() => {
  const editor = document.getElementById('quote-editor');
  if (!editor) return;
  const rows = document.getElementById('quote-rows');
  const template = document.getElementById('quote-row-template');
  const catalog = JSON.parse(document.getElementById('quote-catalog').textContent);
  /* admite «1.234,50» y «1234.5» */
  const number = text => {
    let t = String(text || '').replace(/[€\s]/g, '');
    if (!t) return null;
    if (t.includes(',')) t = t.replace(/\./g, '').replace(',', '.');
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  };
  const money = n => n.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €';
  function recalc() {
    let base = 0;
    rows.querySelectorAll('[data-quote-row]').forEach(row => {
      const field = name => row.querySelector(`[name=${name}]`);
      field('line_period').hidden = field('line_billing_model').value !== 'monthly';
      const price = number(field('line_unit_price').value), cell = row.querySelector('[data-quote-amount]');
      if (price === null) { cell.textContent = 'A consultar'; return; }
      const amount = Math.round((number(field('line_quantity').value) ?? 1) * price * (100 - (number(field('line_discount').value) ?? 0))) / 100;
      cell.textContent = money(amount); base += amount;
    });
    const tax = Math.round(base * (number(document.getElementById('q-tax').value) ?? 0)) / 100;
    document.getElementById('q-base').textContent = money(base);
    document.getElementById('q-taxamount').textContent = money(tax);
    document.getElementById('q-total').textContent = money(base + tax);
  }
  function addRow(values) {
    const row = template.content.firstElementChild.cloneNode(true);
    Object.entries(values || {}).forEach(([name, value]) => { const el = row.querySelector(`[name=${name}]`); if (el && value != null) el.value = value; });
    rows.append(row); recalc();
    if (!values) row.querySelector('textarea').focus();
  }
  editor.addEventListener('input', recalc);
  editor.addEventListener('change', recalc);
  editor.addEventListener('click', e => {
    if (e.target.closest('[data-quote-add]')) addRow();
    const remove = e.target.closest('[data-quote-remove]');
    if (remove) { remove.closest('[data-quote-row]').remove(); recalc(); }
  });
  document.getElementById('q-catalog').addEventListener('change', e => {
    const p = catalog.find(item => String(item.id) === e.target.value);
    if (p) addRow({ line_code: p.code, line_unit: p.unit, line_description: p.name, line_billing_model: p.billing_model, line_period: 'monthly', line_quantity: '1', line_unit_price: p.price ?? '', line_discount: '0' });
    e.target.value = '';
  });
  recalc();
})();

/* Desplegable con buscador (empresa, contacto): busca al escribir y permite crear ahí mismo */
(() => {
  let timer = null, seq = 0;
  const parts = combo => ({ hidden: combo.querySelector('input[type=hidden]'), input: combo.querySelector('.combo-input'), list: combo.querySelector('.combo-list') });
  const form = combo => combo.closest('form');
  const createField = combo => combo.dataset.create === 'new_company' ? form(combo).querySelector('[name=new_company]') : null;
  const close = combo => { const { list, input } = parts(combo); list.hidden = true; input.setAttribute('aria-expanded', 'false'); };
  function newContact(combo, show, name) {
    form(combo).querySelectorAll('[data-new-contact]').forEach(el => {
      el.hidden = !show;
      if (!show) el.querySelectorAll('input').forEach(i => { i.value = ''; });
    });
    if (show) { const first = form(combo).querySelector('[name=new_contact_first_name]'); const [head, ...rest] = name.trim().split(/\s+/); first.value = head || ''; form(combo).querySelector('[name=new_contact_last_name]').value = rest.join(' '); first.focus(); }
  }
  /* datos que solo tienen sentido si la empresa es nueva (dónde se la conoció) */
  function newCompany(combo, show) {
    if (combo.dataset.create !== 'new_company') return;
    form(combo).querySelectorAll('[data-new-company]').forEach(el => {
      el.hidden = !show;
      if (!show) el.querySelectorAll('select').forEach(i => { i.value = ''; });
    });
  }
  function pick(combo, item) {
    const { hidden, input } = parts(combo), field = createField(combo);
    combo.classList.toggle('is-new', !!item.create);
    if (item.create) {
      hidden.value = '';
      if (combo.dataset.create === 'new_contact') { input.value = ''; input.placeholder = 'Contacto nuevo: rellena sus datos abajo'; newContact(combo, true, item.text); }
      else { field.value = item.text; input.value = item.text + ' (nueva)'; newCompany(combo, true); }
    } else {
      hidden.value = item.id; input.value = item.label;
      if (field) field.value = '';
      newCompany(combo, false);
      if (combo.dataset.create === 'new_contact') newContact(combo, false);
      /* un contacto trae su empresa: se rellena si aún no hay ninguna elegida */
      if (item.company) {
        const other = form(combo).querySelector('[data-combo][data-create=new_company]');
        if (other && !parts(other).hidden.value && !createField(other).value) pick(other, item.company);
      }
    }
    close(combo);
    hidden.dispatchEvent(new Event('change', { bubbles: true }));
  }
  function search(combo) {
    const { input, list } = parts(combo), q = input.value.trim(), mine = ++seq;
    const params = new URLSearchParams({ q });
    if (combo.dataset.depends) { const dep = form(combo).querySelector(`input[type=hidden][name=${combo.dataset.depends}]`); if (dep && dep.value) params.set('company', dep.value); }
    fetch(`${combo.dataset.url}?${params}`, { headers: { 'X-Requested-With': 'fetch' } }).then(r => r.json()).then(({ results }) => {
      if (mine !== seq) return;
      list.innerHTML = '';
      results.forEach(item => {
        const li = document.createElement('li'); li.setAttribute('role', 'option'); li.textContent = item.label;
        if (item.sub) { const small = document.createElement('small'); small.textContent = item.sub; li.append(small); }
        li._item = item; list.append(li);
      });
      const exact = results.some(item => item.label.toLowerCase() === q.toLowerCase());
      if (combo.dataset.create && q && !exact) {
        const li = document.createElement('li'); li.className = 'create'; li.setAttribute('role', 'option');
        li.textContent = combo.dataset.create === 'new_contact' ? `＋ Crear contacto «${q}»` : `＋ Crear empresa «${q}»`;
        li._item = { create: true, text: q }; list.append(li);
      }
      if (!list.children.length) { const li = document.createElement('li'); li.className = 'none'; li.textContent = q ? 'Sin resultados.' : 'Escribe para buscar.'; list.append(li); }
      list.hidden = false; input.setAttribute('aria-expanded', 'true');
    }).catch(() => {});
  }
  /* si el diálogo vuelve con un error, la empresa nueva sigue escrita en su buscador */
  document.addEventListener('htmx:afterSwap', e => {
    e.target.querySelectorAll('[data-combo][data-create=new_company]').forEach(combo => {
      const field = createField(combo), { hidden, input } = parts(combo);
      if (field && field.value && !hidden.value) { input.value = field.value + ' (nueva)'; combo.classList.add('is-new'); }
    });
  });
  document.addEventListener('input', e => {
    const combo = e.target.closest && e.target.closest('[data-combo]');
    if (!combo || !e.target.matches('.combo-input')) return;
    /* al teclear deja de valer lo elegido antes: hay que volver a elegir o crear */
    parts(combo).hidden.value = ''; combo.classList.remove('is-new');
    const field = createField(combo); if (field) field.value = '';
    newCompany(combo, false);
    clearTimeout(timer); timer = setTimeout(() => search(combo), 160);
  });
  document.addEventListener('focusin', e => {
    if (e.target.matches && e.target.matches('.combo-input')) { e.target.select(); search(e.target.closest('[data-combo]')); }
  });
  document.addEventListener('mousedown', e => {
    const li = e.target.closest && e.target.closest('.combo-list li');
    if (li && li._item) { e.preventDefault(); pick(li.closest('[data-combo]'), li._item); return; }
    document.querySelectorAll('[data-combo]').forEach(combo => { if (!combo.contains(e.target)) close(combo); });
  });
  document.addEventListener('keydown', e => {
    if (!e.target.matches || !e.target.matches('.combo-input')) return;
    const combo = e.target.closest('[data-combo]'), { list } = parts(combo);
    const items = [...list.querySelectorAll('li')].filter(li => li._item);
    let at = items.findIndex(li => li.getAttribute('aria-selected') === 'true');
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); if (list.hidden) { search(combo); return; }
      at = (at + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      items.forEach((li, i) => li.setAttribute('aria-selected', i === at)); items[at] && items[at].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter' && !list.hidden) {
      e.preventDefault(); const chosen = items[at] || (items.length === 1 ? items[0] : null); if (chosen) pick(combo, chosen._item);
    } else if (e.key === 'Escape' && !list.hidden) { e.stopPropagation(); e.preventDefault(); close(combo); }
    else if (e.key === 'Tab') close(combo);
  });
})();
