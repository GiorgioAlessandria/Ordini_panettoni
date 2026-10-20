// FullCalendar 7: vista settimanale con eventi concorrenti affiancati.
(function () {
    'use strict';
    const calendarEl = document.getElementById('calendar');
    if (!calendarEl || calendarEl.dataset.calendarInitialized === '1') return;
    if (!window.FullCalendar?.Calendar) {
        console.error('FullCalendar non caricato: verificare fullcalendar.global.js in base.j2');
        return;
    }

    const scadenzeUrl = calendarEl.dataset.eventsScadenzeUrl;
    const pianificazioneUrl = calendarEl.dataset.eventsPianificazioneUrl;
    if (!scadenzeUrl || !pianificazioneUrl) {
        console.error('Mancano data-events-scadenze-url e/o data-events-pianificazione-url');
        return;
    }

    const coloriPiani = [
        ['#1d4ed8', '#eff6ff'], ['#0f766e', '#f0fdfa'],
        ['#7e22ce', '#faf5ff'], ['#be185d', '#fdf2f8'],
        ['#0369a1', '#f0f9ff'], ['#4d7c0f', '#f7fee7'],
        ['#4338ca', '#eef2ff'], ['#b45309', '#fffbeb'],
    ];

    function infoPiano(id) {
        const idNumerico = Number.parseInt(id, 10);
        const indice = Number.isFinite(idNumerico) ? Math.abs(idNumerico - 1) % coloriPiani.length : 0;
        return coloriPiani[indice];
    }

    function tipoLavorazione(nome) {
        const testo = String(nome || '').toLocaleLowerCase('it');
        if (testo.includes('impast')) return 'impasto';
        if (testo.includes('cott') || testo.includes('forno')) return 'cottura';
        if (testo.includes('lievit')) return 'lievitazione';
        return 'altro';
    }

    function addText(parent, className, value) {
        const nodo = document.createElement('div');
        nodo.className = className;
        nodo.textContent = String(value);
        parent.appendChild(nodo);
        return nodo;
    }

    const calendario = new FullCalendar.Calendar(calendarEl, {
        themeSystem: 'classic',
        initialView: 'timeGridWeek',
        locale: 'it',
        firstDay: 1,
        timeZone: 'local',
        height: 'auto',
        expandRows: true,
        allDaySlot: true,
        eventDisplay: 'block',
        displayEventEnd: true,
        dayMaxEvents: false,
        dayMaxEventRows: false,
        slotEventOverlap: false,
        slotMinTime: '06:00:00',
        slotMaxTime: '18:00:00',
        slotDuration: '00:30:00',
        slotLabelInterval: '01:00:00',
        nowIndicator: true,
        headerToolbar: {
            start: 'prev,next today',
            center: 'title',
            end: 'timeGridWeek,dayGridWeek,dayGridMonth,timeGridDay',
        },
        buttonText: {
            today: 'Oggi',
            timeGridWeek: 'Settimana',
            dayGridWeek: 'Riquadri',
            dayGridMonth: 'Mese',
            timeGridDay: 'Giorno',
        },
        views: {
            timeGridWeek: { dayHeaderFormat: { weekday: 'long', day: 'numeric', month: 'short' } },
            dayGridWeek: { dayHeaderFormat: { weekday: 'long', day: 'numeric', month: 'short' } },
        },
        eventTimeFormat: { hour: '2-digit', minute: '2-digit', hour12: false },
        eventSources: [
            { id: 'scadenze', url: scadenzeUrl },
            { id: 'pianificazione', url: pianificazioneUrl },
        ],
        eventClassNames(info) {
            const produzione = info.event.source?.id === 'pianificazione';
            return produzione
                ? ['evento-produzione', 'lavorazione-' + tipoLavorazione(info.event.extendedProps.lavorazione)]
                : ['evento-scadenza'];
        },
        eventContent(info) {
            const props = info.event.extendedProps;
            const produzione = info.event.source?.id === 'pianificazione';
            const scheda = document.createElement('div');
            scheda.className = 'cal-event-content';

            if (produzione) {
                const etichetta = addText(scheda, 'cal-event-badge',
                    `Piano #${props.id_pianificazione ?? '?'} · ${props.lavorazione || 'Lavorazione'}`);
                etichetta.dataset.lavorazione = tipoLavorazione(props.lavorazione);
            } else {
                addText(scheda, 'cal-event-badge scadenza-badge', 'Scadenza cliente');
            }
            if (info.timeText && produzione) addText(scheda, 'cal-event-time', info.timeText);
            addText(scheda, 'cal-event-title', props.descrizione_articolo || info.event.title || 'Articolo');

            if (produzione) {
                addText(scheda, 'cal-event-summary',
                    `${props.numero_ordini ?? 0} ordini · ${props.quantita_assegnata ?? 'N/D'} pz assegnati`);
                if (props.dati_incompleti) addText(scheda, 'cal-event-warning', 'Quantità da verificare');
            } else {
                if (props.cliente) addText(scheda, 'cal-event-summary', props.cliente);
                if (props.quantita_residua != null) addText(scheda, 'cal-event-summary',
                    `Da consegnare: ${props.quantita_residua} pz`);
            }
            return { domNodes: [scheda] };
        },
        eventDidMount(info) {
            const p = info.event.extendedProps;
            const produzione = info.event.source?.id === 'pianificazione';
            if (produzione) {
                const [bordo, sfondo] = infoPiano(p.id_pianificazione);
                info.el.style.setProperty('--piano-accent', bordo);
                info.el.style.setProperty('--piano-bg', sfondo);
            }
            const righe = [info.event.title];
            if (produzione) {
                righe.push(
                    `Pianificazione: ${p.id_pianificazione}`,
                    `Lavorazione: ${p.lavorazione}`,
                    `Ordini: ${(p.ordini || []).join(', ')}`,
                    `Quantità assegnata: ${p.quantita_assegnata ?? 'N/D'}`,
                    `Produzione: ${p.panettoni_previsti ?? 'N/D'}`,
                    `Cotte: ${p.numero_cotte ?? 'N/D'}`,
                    `Peso: ${p.peso_totale_kg ?? 'N/D'} kg`,
                );
                if (p.impastatrici?.length) righe.push(`Impastatrici: ${p.impastatrici.join(', ')}`);
            } else {
                righe.push(`Cliente: ${p.cliente || 'N/D'}`, `Da consegnare: ${p.quantita_residua ?? 'N/D'} pz`);
            }
            info.el.title = righe.join('\n');
        },
        eventClick(info) {
            const url = info.event.extendedProps.detailUrl;
            if (url && window.htmx) {
                info.jsEvent.preventDefault();
                window.htmx.ajax('GET', url, '#htmx-global');
            }
        },
        eventSourceFailure(error) { console.error('Errore caricamento eventi calendario', error); },
    });

    calendarEl.dataset.calendarInitialized = '1';
    calendario.render();
    document.body.addEventListener('pianificazioneSalvata', () => calendario.refetchEvents());
})();
