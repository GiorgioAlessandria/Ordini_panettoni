/* Gestione ordini: filtri e ordinamento senza paginazione.
 * Gli ordini visibili sono tutti presenti nella tabella: si scorre verticalmente.
 * Non modifica il caricamento HTMX, la selezione né le operazioni di produzione.
 */
(() => {
    'use strict';
    const workspace = document.querySelector('.ordini-workspace');
    if (!workspace) return;

    const contenitori = ['ordini_cliente_da_produrre', 'ordini_cliente_in_produzione'];
    const stato = new Map();
    const collator = new Intl.Collator('it', { sensitivity: 'base', numeric: true });

    const statoPer = (id) => {
        if (!stato.has(id)) stato.set(id, {
            filtri: {}, ordine: null, ascendente: true,
            scrollTop: 0, scrollLeft: 0, pianificazioniAperte: new Set()
        });
        return stato.get(id);
    };

    function trovaContenitore(elemento) {
        return elemento && elemento.closest('#ordini_cliente_da_produrre, #ordini_cliente_in_produzione');
    }

    function normalizza(testo) {
        return String(testo ?? '').trim().toLocaleLowerCase('it');
    }

    function filtraRiga(riga, filtri) {
        const d = riga.dataset;
        const testo = normalizza(d.filterText || riga.textContent);
        const ricerca = normalizza(filtri.ricerca);
        if (ricerca && !testo.includes(ricerca)) return false;

        const confronti = {
            prezzatura: d.filterPrezzatura,
            glassa: d.filterGlassa,
            priorita: d.filterPriorita,
            parziale: d.filterParziale,
            pianificazione: d.filterPianificazione
        };
        for (const [nome, valoreRiga] of Object.entries(confronti)) {
            if (filtri[nome] && normalizza(valoreRiga) !== normalizza(filtri[nome])) return false;
        }
        const consegna = d.filterConsegna || '';
        if (filtri.dal && consegna < filtri.dal) return false;
        if (filtri.al && consegna > filtri.al) return false;
        return true;
    }

    function valoreOrdinamento(riga, chiave, tipo) {
        const cella = Array.from(riga.cells).find(td => td.dataset.col === chiave);
        const testo = cella ? (cella.dataset.sortValue || cella.textContent || '') : '';
        if (tipo === 'number') {
            const numero = Number(String(testo).replace(/[^\d,.-]/g, '').replace(',', '.'));
            return Number.isFinite(numero) ? numero : Number.POSITIVE_INFINITY;
        }
        return String(testo).trim();
    }

    function aggiornaTabella(contenitore, { ripristinaScroll = false } = {}) {
        if (!contenitore) return;
        const form = contenitore.querySelector('.ordini-form');
        if (!form) return;
        const statoCorrente = statoPer(contenitore.id);
        const filtri = statoCorrente.filtri;

        form.querySelectorAll('[data-filter]').forEach(campo => {
            const nome = campo.dataset.filter;
            if (Object.prototype.hasOwnProperty.call(filtri, nome) && campo.value !== filtri[nome]) {
                campo.value = filtri[nome];
            }
        });

        const corpo = form.querySelector('.ordini-table tbody');
        if (!corpo) return;
        const righe = Array.from(corpo.querySelectorAll('tr[data-record]'));
        if (statoCorrente.ordine) {
            const { chiave, tipo } = statoCorrente.ordine;
            righe.sort((a, b) => {
                const va = valoreOrdinamento(a, chiave, tipo);
                const vb = valoreOrdinamento(b, chiave, tipo);
                const cmp = tipo === 'number' ? va - vb : collator.compare(va, vb);
                return statoCorrente.ascendente ? cmp : -cmp;
            });
            const frammento = document.createDocumentFragment();
            righe.forEach(r => frammento.appendChild(r));
            corpo.appendChild(frammento);
        }
        let visibili = 0;
        for (const riga of righe) {
            const visibile = filtraRiga(riga, filtri);
            riga.hidden = !visibile;
            riga.style.display = visibile ? '' : 'none';
            if (visibile) visibili++;
        }
        const vuota = corpo.querySelector('tr[data-no-data]');
        if (vuota) vuota.hidden = righe.length > 0 && visibili > 0;
        const contatore = form.querySelector('[data-contatore-righe]');
        if (contatore) contatore.textContent = `${visibili} ${visibili === 1 ? 'ordine' : 'ordini'}`;

        // In caso di residui di una versione precedente, rimuove la paginazione;
        // tutte le righe filtrate sono visibili tramite scorrimento.
        form.querySelectorAll('.ordini-paginazione').forEach(el => el.remove());
        // HTMX sostituisce completamente il tbody: riapre i dettagli
        // delle pianificazioni identificate dal loro ID, non dalla posizione.
        if (ripristinaScroll) {
            form.querySelectorAll('tr[data-record][data-id-pianificazione]').forEach(riga => {
                const idPiano = riga.dataset.idPianificazione;
                const dettagli = riga.querySelector('details.piano-dettagli');
                if (idPiano && dettagli) {
                    dettagli.open = statoCorrente.pianificazioniAperte.has(idPiano);
                }
            });
        }
        const area = form.querySelector('.ordini-table-scroll');
        if (area && ripristinaScroll) {
            area.scrollTop = statoCorrente.scrollTop;
            area.scrollLeft = statoCorrente.scrollLeft;
        }
    }

    workspace.addEventListener('input', ev => {
        const campo = ev.target.closest('[data-filter]');
        const contenitore = trovaContenitore(campo);
        if (!contenitore) return;
        statoPer(contenitore.id).filtri[campo.dataset.filter] = campo.value;
        aggiornaTabella(contenitore);
    });
    workspace.addEventListener('change', ev => {
        const campo = ev.target.closest('[data-filter]');
        const contenitore = trovaContenitore(campo);
        if (!contenitore) return;
        statoPer(contenitore.id).filtri[campo.dataset.filter] = campo.value;
        aggiornaTabella(contenitore);
    });
    workspace.addEventListener('click', ev => {
        const reset = ev.target.closest('[data-reset-filters]');
        const bottoneOrdina = ev.target.closest('[data-sort-key]');
        if (reset) {
            const contenitore = trovaContenitore(reset);
            if (!contenitore) return;
            const statoCorrente = statoPer(contenitore.id);
            statoCorrente.filtri = {};
            contenitore.querySelectorAll('[data-filter]').forEach(c => { c.value = ''; });
            aggiornaTabella(contenitore);
            return;
        }
        if (bottoneOrdina) {
            const contenitore = trovaContenitore(bottoneOrdina);
            if (!contenitore) return;
            const s = statoPer(contenitore.id);
            const chiave = bottoneOrdina.dataset.sortKey;
            const tipo = bottoneOrdina.dataset.sortType || 'text';
            s.ascendente = s.ordine && s.ordine.chiave === chiave ? !s.ascendente : true;
            s.ordine = { chiave, tipo };
            contenitore.querySelectorAll('[data-sort-column]').forEach(th => {
                th.setAttribute('aria-sort', th.dataset.sortColumn === chiave ? (s.ascendente ? 'ascending' : 'descending') : 'none');
            });
            aggiornaTabella(contenitore);
        }
    });

    document.body.addEventListener('htmx:beforeSwap', ev => {
        const contenitore = ev.detail.target;
        if (!contenitore || !contenitori.includes(contenitore.id)) return;
        const s = statoPer(contenitore.id);
        const area = contenitore.querySelector('.ordini-table-scroll');
        if (area) {
            s.scrollTop = area.scrollTop;
            s.scrollLeft = area.scrollLeft;
        }
        // Snapshot dei dettagli espansi *prima* dello swap periodico.
        s.pianificazioniAperte = new Set(
            Array.from(contenitore.querySelectorAll('tr[data-record][data-id-pianificazione]'))
                .filter(riga => riga.querySelector('details.piano-dettagli')?.open)
                .map(riga => riga.dataset.idPianificazione)
                .filter(Boolean)
        );
    });
    document.body.addEventListener('htmx:afterSwap', ev => {
        const contenitore = ev.detail.target;
        if (!contenitore || !contenitori.includes(contenitore.id)) return;
        aggiornaTabella(contenitore, { ripristinaScroll: true });
    });
    contenitori.forEach(id => aggiornaTabella(document.getElementById(id)));
})();
