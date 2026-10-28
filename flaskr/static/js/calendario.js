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

    // Colore associato in modo stabile all'IdPianificazione.
    // L'angolo aureo evita il riutilizzo periodico della palette di 8 colori.
    // Il colore del piano identifica bordo e sfondo della scheda; quello della
    // singola lavorazione rimane distinto nell'etichetta della fase.
    function infoPiano(id) {
        const idNumerico = Number(id);
        const hue = Number.isSafeInteger(idNumerico) && idNumerico > 0
            ? (idNumerico * 137.50776405) % 360
            : 215;
        const tonalita = hue.toFixed(3);
        return [
            `hsl(${tonalita} 68% 35%)`, // bordo leggibile
            `hsl(${tonalita} 78% 95%)`, // sfondo chiaro
        ];
    }

    function testoContrastoEsadecimale(colore) {
        // La luminosità relativa WCAG consente una scritta leggibile anche
        // quando l'utente sceglie giallo, bianco o colori molto chiari.
        const canali = [1, 3, 5].map(indice => {
            const valore = parseInt(colore.slice(indice, indice + 2), 16) / 255;
            return valore <= 0.04045 ? valore / 12.92 : ((valore + 0.055) / 1.055) ** 2.4;
        });
        const luminanza = 0.2126 * canali[0] + 0.7152 * canali[1] + 0.0722 * canali[2];
        return luminanza > 0.179 ? '#16202e' : '#ffffff';
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

    function propsStato(props) {
        return props.stato_giorno === 'eseguito'
            ? 'scadenza-giorno-completata'
            : 'scadenza-giorno-in-corso';
    }

    function allineaLegendaAlTitolo() {
        // FullCalendar genera il titolo della settimana/mese dinamicamente.
        // Collocare la legenda accanto al titolo evita una fascia dedicata.
        const legenda = calendarEl.closest('.calendario-shell')?.querySelector('.calendario-legenda');
        const titolo = calendarEl.querySelector('.fc-header-toolbar .fc-toolbar-title');
        const zonaTitolo = titolo?.closest('.fc-toolbar-chunk');
        if (!legenda || !zonaTitolo) return;
        legenda.classList.add('calendario-legenda--toolbar');
        zonaTitolo.appendChild(legenda);
    }

    const calendario = new FullCalendar.Calendar(calendarEl, {
        themeSystem: 'classic',
        datesSet() { allineaLegendaAlTitolo(); },
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
                : ['evento-scadenza', propsStato(info.event.extendedProps)];
        },
        eventContent(info) {
            const props = info.event.extendedProps;
            const produzione = info.event.source?.id === 'pianificazione';
            const scheda = document.createElement('div');
            scheda.className = 'cal-event-content';

            if (produzione) {
                const nomeFase = props.numero_fase == null
                    ? (props.lavorazione || 'Lavorazione')
                    : `Fase ${props.numero_fase} · ${props.lavorazione || 'Lavorazione'}`;
                const etichetta = addText(scheda, 'cal-event-badge',
                    `Piano #${props.id_pianificazione ?? '?'} · ${nomeFase}`);
                etichetta.dataset.lavorazione = tipoLavorazione(props.lavorazione);
                if (typeof props.colore_fase === 'string' && /^#[0-9a-f]{6}$/i.test(props.colore_fase)) {
                    etichetta.dataset.coloreFase = 'personalizzato';
                    etichetta.style.setProperty('--fase-badge-bg', props.colore_fase);
                    etichetta.style.setProperty('--fase-badge-fg', testoContrastoEsadecimale(props.colore_fase));
                }
            } else {
                addText(scheda, 'cal-event-badge scadenza-badge', 'Scadenze del giorno');
            }
            if (info.timeText && produzione) addText(scheda, 'cal-event-time', info.timeText);
            addText(scheda, 'cal-event-title',
                produzione ? (props.descrizione_articolo || info.event.title || 'Articolo')
                           : (info.event.title || 'Scadenze cliente'));

            if (produzione) {
                addText(scheda, 'cal-event-summary',
                    `${props.numero_ordini ?? 0} ordini · ${props.quantita_assegnata ?? 'N/D'} pz assegnati`);
                if (props.dati_incompleti) addText(scheda, 'cal-event-warning', 'Quantità da verificare');
            } else {
                addText(scheda, 'cal-event-summary',
                    `${props.numero_completati ?? 0} eseguiti · ${props.numero_da_eseguire ?? 0} da eseguire`);
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
                    `Da stock: ${p.quantita_da_stock ?? 0} pz`,
                    `Cotte: ${p.numero_cotte ?? 'N/D'}`,
                    `Peso: ${p.peso_totale_kg ?? 'N/D'} kg`,
                );
                if (p.impastatrici?.length) righe.push(`Impastatrici: ${p.impastatrici.join(', ')}`);
            } else {
                righe.push(
                    `Ordini del giorno: ${p.numero_ordini ?? 0}`,
                    `Eseguiti: ${p.numero_completati ?? 0}`,
                    `Da eseguire: ${p.numero_da_eseguire ?? 0}`,
                    `Quantità ordinata: ${p.quantita_ordinata ?? 0} pz`,
                    'Clicca per vedere la tabella con i commenti'
                );
            }
            info.el.title = righe.join('\n');
        },
        eventClick(info) {
            const url = info.event.extendedProps.detailUrl;
            if (url && window.apriRiepilogoCalendario) {
                info.jsEvent.preventDefault();
                window.apriRiepilogoCalendario(url);
            }
        },
        eventSourceFailure(error) { console.error('Errore caricamento eventi calendario', error); },
    });

    calendarEl.dataset.calendarInitialized = '1';
    calendario.render();
    allineaLegendaAlTitolo();
    document.body.addEventListener('pianificazioneSalvata', () => calendario.refetchEvents());
})();
