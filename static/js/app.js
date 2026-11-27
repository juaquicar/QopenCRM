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
      document.dispatchEvent(new CustomEvent('modal:open', { detail: { url } }));
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
  const phone = matchMedia('(max-width:820px)');
  function initBoard(root = document) {
    if (phone.matches) return;
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

  /* IA: rellena los campos vacíos del formulario a partir de un texto pegado o de una búsqueda en internet */
  document.addEventListener('click', e => {
    const button = e.target.closest('[data-ai-fill], [data-ai-web]');
    if (!button) return;
    const web = 'aiWeb' in button.dataset, status = $('#ai-status'), form = button.closest('form');
    const data = web ? new FormData(form) : new FormData();
    data.set('text', $('#ai-text').value);
    const buttons = $$('[data-ai-fill], [data-ai-web]', form);
    buttons.forEach(b => { b.disabled = true; });
    status.textContent = web ? 'Buscando en internet… puede tardar medio minuto.' : 'Leyendo el texto…';
    fetch(web ? button.dataset.aiWeb : button.dataset.aiFill, { method: 'POST', body: data, headers: { 'X-CSRFToken': csrf() } })
      .then(r => r.json().then(body => ({ ok: r.ok, body })))
      .then(({ ok, body }) => {
        if (!ok) { status.textContent = body.error || (web ? 'No se ha podido buscar.' : 'No se ha podido leer el texto.'); return; }
        let filled = 0;
        Object.entries(body.fields).forEach(([name, value]) => {
          const input = $('#id_' + name, modalBody);
          if (input && !input.value) { input.value = value; filled++; }
        });
        status.textContent = filled ? filled + ' campos rellenados. Revísalos antes de guardar.' : (web ? 'No he encontrado datos nuevos fiables.' : 'No he encontrado datos nuevos en el texto.');
        if (web && filled && body.source) {
          const link = document.createElement('a');
          link.className = 'link'; link.target = '_blank'; link.rel = 'noopener'; link.href = body.source;
          link.textContent = body.source.replace(/^https?:\/\/(www\.)?/, '').split('/')[0];
          status.append(' Fuente: ', link, '.');
        }
      })
      .catch(() => { status.textContent = 'Sin conexión con el servidor.'; })
      .finally(() => { buttons.forEach(b => { b.disabled = false; }); });
  });

  /* Copiar un enlace (reuniones de Teams) */
  document.addEventListener('click', e => {
    const button = e.target.closest('[data-copy]');
    if (!button || !navigator.clipboard) return;
    navigator.clipboard.writeText(button.dataset.copy).then(() => {
      const label = button.textContent;
      button.textContent = 'Copiado';
      setTimeout(() => { button.textContent = label; }, 1600);
    }).catch(() => {});
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
  const parts = combo => ({ hidden: combo.querySelector(':scope > input[type=hidden]'), input: combo.querySelector('.combo-input'), list: combo.querySelector('.combo-list') });
  const form = combo => combo.closest('form');
  const createField = combo => combo.dataset.create === 'new_company' ? form(combo).querySelector('[name=new_company]') : null;
  const close = combo => { const { list, input } = parts(combo); list.hidden = true; input.setAttribute('aria-expanded', 'false'); };
  /* al ocultar los datos de la empresa o el contacto nuevo vuelven a su valor inicial (país, tipo, base legal…) */
  const reset = i => { if (i.tagName === 'SELECT') { const d = [...i.options].findIndex(o => o.defaultSelected); i.selectedIndex = Math.max(d, 0); } else i.value = i.defaultValue; };
  function newContact(combo, show, name) {
    form(combo).querySelectorAll('[data-new-contact]').forEach(el => {
      el.hidden = !show;
      if (!show) el.querySelectorAll('input,select,textarea').forEach(reset);
    });
    if (show) { const first = form(combo).querySelector('[name=new_contact_first_name]'); const [head, ...rest] = name.trim().split(/\s+/); first.value = head || ''; form(combo).querySelector('[name=new_contact_last_name]').value = rest.join(' '); first.focus(); }
  }
  /* datos que solo tienen sentido si la empresa es nueva (dónde se la conoció) */
  function newCompany(combo, show) {
    if (combo.dataset.create !== 'new_company') return;
    form(combo).querySelectorAll('[data-new-company]').forEach(el => {
      el.hidden = !show;
      if (!show) el.querySelectorAll('input,select,textarea').forEach(reset);
    });
  }
  /* varias empresas: cada elegida es una etiqueta con su ✕ y su campo oculto */
  function pickMany(combo, item) {
    const { input } = parts(combo), chips = combo.querySelector('.chips');
    if (!chips.querySelector(`input[value="${item.id}"]`)) {
      const chip = document.createElement('span'); chip.className = 'chip-pick'; chip.textContent = item.label;
      const hidden = document.createElement('input'); hidden.type = 'hidden'; hidden.name = combo.dataset.multi; hidden.value = item.id;
      const x = document.createElement('button'); x.type = 'button'; x.setAttribute('aria-label', 'Quitar'); x.textContent = '✕';
      chip.append(hidden, x); chips.append(chip);
    }
    input.value = ''; close(combo);
  }
  function pick(combo, item) {
    if (combo.dataset.multi) { pickMany(combo, item); return; }
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
      if (combo.dataset.multi) {
        const main = form(combo).querySelector(':scope [data-combo] > input[type=hidden][name=company]');
        const taken = [...combo.querySelectorAll('.chips input')].map(i => i.value).concat(main && main.value ? [main.value] : []);
        results = results.filter(r => !taken.includes(String(r.id)));
      }
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
  document.addEventListener('click', e => {
    const x = e.target.closest && e.target.closest('.chip-pick button');
    if (x) { e.preventDefault(); x.parentElement.remove(); }
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

/* Menciones: al escribir «@» en un texto con data-mentions se sugieren personas del equipo.
   La lista flota sobre la página (position:fixed) para que no la recorten el cuadro de nota ni el diálogo. */
(() => {
  let timer = null, seq = 0, open = null;
  const at = ta => { const m = /(^|[^\w.@])@([\w.\-]*)$/.exec(ta.value.slice(0, ta.selectionStart)); return m ? { start: ta.selectionStart - m[2].length - 1, q: m[2] } : null; };
  function list(ta) {
    if (!ta._mentionList) {
      const ul = document.createElement('ul'); ul.className = 'mention-list'; ul.setAttribute('role', 'listbox'); ul.hidden = true;
      ul._ta = ta; ta.after(ul); ta._mentionList = ul; /* junto al textarea: dentro del diálogo si lo hay (capa superior) */
    }
    return ta._mentionList;
  }
  function place(ta) {
    const ul = ta._mentionList, r = ta.getBoundingClientRect(), width = Math.min(300, r.width - 16);
    ul.style.width = width + 'px'; ul.style.left = Math.max(8, Math.min(r.left + 8, innerWidth - width - 8)) + 'px';
    const below = innerHeight - r.bottom, h = Math.min(ul.scrollHeight, 240);
    if (below < h + 32 && r.top > below) { ul.style.top = ''; ul.style.bottom = (innerHeight - r.top + 4) + 'px'; }
    else { ul.style.bottom = ''; ul.style.top = (r.bottom + 4) + 'px'; }
  }
  const close = ta => { if (ta && ta._mentionList) ta._mentionList.hidden = true; if (open === ta) open = null; };
  function insert(ta, item) {
    const hit = at(ta); if (!hit) return;
    const before = ta.value.slice(0, hit.start), after = ta.value.slice(ta.selectionStart);
    ta.value = `${before}@${item.handle} ${after}`;
    const pos = before.length + item.handle.length + 2; ta.setSelectionRange(pos, pos); ta.focus(); close(ta);
  }
  const initials = name => name.replace(/@.*/, '').split(/[\s.]+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase();
  function suggest(ta) {
    const hit = at(ta); if (!hit) { close(ta); return; }
    const mine = ++seq;
    fetch(`${ta.dataset.mentions}?${new URLSearchParams({ q: hit.q })}`, { headers: { 'X-Requested-With': 'fetch' } }).then(r => r.json()).then(({ results }) => {
      if (mine !== seq) return;
      const ul = list(ta); ul.innerHTML = '';
      if (!results.length) { close(ta); return; }
      const hint = document.createElement('li'); hint.className = 'hint'; hint.textContent = 'Mencionar a… (Enter o Tab para elegir)'; ul.append(hint);
      results.forEach((item, i) => {
        const li = document.createElement('li'); li.setAttribute('role', 'option'); li.setAttribute('aria-selected', i === 0);
        const av = document.createElement('span'); av.className = 'avatar'; av.textContent = initials(item.label);
        const who = document.createElement('span'); who.className = 'who';
        const b = document.createElement('b'); b.textContent = item.label;
        const small = document.createElement('small'); small.textContent = '@' + item.handle;
        who.append(b, small); li.append(av, who); li._mention = item; ul.append(li);
      });
      ul.hidden = false; open = ta; place(ta);
    }).catch(() => {});
  }
  document.addEventListener('input', e => {
    if (!e.target.matches || !e.target.matches('textarea[data-mentions]')) return;
    clearTimeout(timer); timer = setTimeout(() => suggest(e.target), 120);
  });
  document.addEventListener('keydown', e => {
    const ta = e.target; if (!ta.matches || !ta.matches('textarea[data-mentions]') || !ta._mentionList || ta._mentionList.hidden) return;
    const items = [...ta._mentionList.querySelectorAll('li[role=option]')]; let i = items.findIndex(li => li.getAttribute('aria-selected') === 'true');
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); i = (i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      items.forEach((li, j) => li.setAttribute('aria-selected', i === j)); items[i].scrollIntoView({ block: 'nearest' });
    } else if ((e.key === 'Enter' || e.key === 'Tab') && !e.ctrlKey && !e.metaKey && items[i]) { e.preventDefault(); e.stopPropagation(); insert(ta, items[i]._mention); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(ta); }
  }, true);
  document.addEventListener('mousedown', e => {
    const li = e.target.closest && e.target.closest('.mention-list li[role=option]');
    if (li) { e.preventDefault(); insert(li.parentElement._ta, li._mention); return; }
    if (open && !(e.target.closest && e.target.closest('.mention-list')) && e.target !== open) close(open);
  });
  /* al desplazar o redimensionar, la lista sigue al texto */
  addEventListener('scroll', () => { if (open) place(open); }, true);
  addEventListener('resize', () => { if (open) place(open); });
  document.addEventListener('focusout', e => { if (e.target === open) setTimeout(() => { if (open && document.activeElement !== open) close(open); }, 150); });
})();

/* Móvil: lo que hace que la web se use como una app (hojas inferiores, abanico de alta, pestañas, etapas deslizables). */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const phone = matchMedia('(max-width:820px)');
  const buzz = () => { try { navigator.vibrate && navigator.vibrate(8); } catch (_) {} };
  const store = { get: k => { try { return sessionStorage.getItem(k); } catch (_) { return null; } }, set: (k, v) => { try { sessionStorage.setItem(k, v); } catch (_) {} } };

  /* hojas inferiores: se abren con data-sheet, se cierran tocando fuera, con «atrás» o arrastrándolas hacia abajo */
  document.addEventListener('click', e => {
    const opener = e.target.closest('[data-sheet]');
    if (opener) { const sheet = $(opener.dataset.sheet); if (sheet && !sheet.open) sheet.showModal(); return; }
    if (e.target.matches && e.target.matches('dialog.sheet')) {  /* tocar fuera (el fondo) la cierra; su propio margen, no */
      const r = e.target.getBoundingClientRect();
      if (e.clientY < r.top || e.clientY > r.bottom || e.clientX < r.left || e.clientX > r.right) e.target.close();
    }
  });
  let drag = null;
  document.addEventListener('touchstart', e => {
    const grip = e.target.closest('[data-grip], dialog.modal header');
    const sheet = grip && !e.target.closest('button,a,input') && grip.closest('dialog');
    if (sheet && phone.matches) drag = { sheet, y: e.touches[0].clientY, dy: 0 };
  }, { passive: true });
  document.addEventListener('touchmove', e => {
    if (!drag) return;
    drag.dy = Math.max(0, e.touches[0].clientY - drag.y);
    drag.sheet.style.transform = `translateY(${drag.dy}px)`; drag.sheet.style.transition = 'none';
  }, { passive: true });
  document.addEventListener('touchend', () => {
    if (!drag) return;
    const { sheet, dy } = drag; drag = null;
    sheet.style.transition = 'transform .2s'; sheet.style.transform = dy > 110 ? 'translateY(100%)' : '';
    setTimeout(() => { if (dy > 110) sheet.close(); sheet.style.transform = sheet.style.transition = ''; }, dy > 110 ? 180 : 210);
  });

  /* abanico de alta del botón flotante */
  let watcher = null;
  function dial(open) {
    const el = $('#dial'); if (!el) return;
    el.classList.toggle('open', open); document.body.classList.toggle('dial-open', open);
    $('[data-dial]', el).setAttribute('aria-expanded', open);
    if (watcher) { watcher.destroy(); watcher = null; }
    if (open && 'CloseWatcher' in window) { watcher = new CloseWatcher(); watcher.onclose = () => dial(false); }  /* el «atrás» de Android lo cierra */
  }
  document.addEventListener('click', e => {
    const el = $('#dial'); if (!el) return;
    if (e.target.closest('[data-dial]')) { buzz(); dial(!el.classList.contains('open')); }
    else if (el.classList.contains('open')) dial(false);
  }, true);

  /* pestañas de una ficha: cada una enseña sus bloques (data-pane) y se recuerda al volver */
  function showTab(name) {
    $$('[data-tabs] [data-tab]').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
    $$('[data-pane]').forEach(p => p.classList.toggle('on', p.dataset.pane === name));
  }
  const tabKey = 'tab:' + location.pathname;
  if ($('[data-tabs]')) { const saved = store.get(tabKey); if (saved && $(`[data-tab="${saved}"]`)) showTab(saved); }
  document.addEventListener('click', e => {
    const tab = e.target.closest('[data-tabs] [data-tab]');
    if (tab) { showTab(tab.dataset.tab); store.set(tabKey, tab.dataset.tab); scrollTo({ top: 0 }); }
    const toggle = e.target.closest('[data-filters]');
    if (toggle) { const open = toggle.closest('.filters').classList.toggle('open'); toggle.setAttribute('aria-expanded', open); }
    if (e.target.closest('.tick,.stage-tabs button,.tabbar a,.tabbar button')) buzz();
  });
  /* la nota recién registrada se ve aunque se guarde desde otra pestaña */
  document.body.addEventListener('htmx:afterRequest', e => { if (e.target.matches && e.target.matches('.composer') && e.detail.successful) buzz(); });

  /* «atrás» de la barra: vuelve a donde se estaba (con sus filtros y su posición), no al listado desde cero */
  document.addEventListener('click', e => {
    const back = e.target.closest('[data-back]');
    if (!back || history.length < 2) return;
    let same = false; try { same = new URL(document.referrer).origin === location.origin; } catch (_) {}
    if (same) { e.preventDefault(); history.back(); }
  });

  /* leads por etapas: deslizar cambia de etapa y la pestaña activa la sigue */
  const stageKey = 'stage:' + location.search;
  function pager() {
    const board = $('.board'), tabs = $$('.stage-tabs button');
    if (!board || !tabs.length || !phone.matches) return;
    const cols = $$('.col', board);
    const go = (i, smooth) => cols[i] && board.scrollTo({ left: cols[i].offsetLeft - cols[0].offsetLeft, behavior: smooth ? 'smooth' : 'instant' });
    const mark = i => tabs.forEach((t, j) => { if (t.classList.toggle('on', i === j) && i === j) t.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' }); });
    let now = Math.min(+store.get(stageKey) || 0, cols.length - 1), ticking = false;
    if (now) { go(now, false); mark(now); }
    board.addEventListener('scroll', () => {
      if (ticking) return; ticking = true;
      requestAnimationFrame(() => {
        ticking = false;
        const step = cols.length > 1 ? cols[1].offsetLeft - cols[0].offsetLeft : board.clientWidth;
        const i = Math.max(0, Math.min(cols.length - 1, Math.round(board.scrollLeft / step)));
        if (i !== now) { now = i; mark(i); store.set(stageKey, i); }
      });
    }, { passive: true });
    tabs.forEach((t, i) => t.addEventListener('click', () => go(i, true)));
  }
  pager();
  document.body.addEventListener('htmx:afterSwap', e => { if (e.target.id === 'board' || (e.detail.target && e.detail.target.id === 'board')) pager(); });
  /* en una ficha, la etapa actual queda a la vista en la tira de etapas */
  const stepOn = $('.steps .on'); if (stepOn && phone.matches) stepOn.scrollIntoView({ inline: 'center', block: 'nearest' });

  /* accesos directos del icono de la app y enlaces «…?nuevo=contact»: abren el alta directamente */
  const params = new URLSearchParams(location.search), fresh = params.get('nuevo');
  if (fresh && /^(opportunity|contact|company|task)$/.test(fresh)) {
    params.delete('nuevo');
    history.replaceState(null, '', location.pathname + (params.size ? '?' + params : ''));
    window.openModal(`/m/${fresh}/`);
  }

  /* texto compartido desde otra app: entra en la ficha nueva, listo para que la IA la rellene (o en sus notas) */
  let shared = '';
  document.addEventListener('click', e => { if (e.target.closest('[data-share]')) shared = ($('#share-text') || {}).value || ''; }, true);
  document.addEventListener('modal:open', () => {
    const body = $('#modal-body');
    if (shared.trim()) {
      const ai = $('#ai-text', body), notes = $('#id_notes, #id_description', body);
      if (ai) { ai.value = shared; ai.closest('details').open = true; }
      else if (notes && !notes.value) notes.value = shared;
      shared = '';
    }
    /* agenda del móvil (Contact Picker): elegir una persona rellena nombre, correos y teléfonos */
    const pick = $('[data-pick-contact]', body);
    if (pick && navigator.contacts && navigator.contacts.select) pick.hidden = false;
  });
  document.addEventListener('click', async e => {
    const pick = e.target.closest('[data-pick-contact]');
    if (!pick) return;
    let found = [];
    try { found = await navigator.contacts.select(['name', 'email', 'tel'], { multiple: false }); } catch (_) { return; }
    if (!found.length) return;
    const person = found[0], form = pick.closest('form'), set = (name, value) => { const el = form.querySelector(`[name=${name}]`); if (el && value) el.value = value; };
    const [first, ...rest] = ((person.name || [])[0] || '').trim().split(/\s+/);
    const emails = [...new Set((person.email || []).map(v => v.trim().toLowerCase()).filter(Boolean))];
    const phones = [...new Set((person.tel || []).map(v => v.replace(/\s+/g, ' ').trim()).filter(Boolean))];
    set('first_name', first); set('last_name', rest.join(' '));
    set('email', emails[0]); set('extra_emails', emails.slice(1).join('\n'));
    set('mobile', phones[0]); set('extra_phones', phones.slice(1).join('\n'));
    buzz();
  });
})();
