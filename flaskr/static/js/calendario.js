// FullCalendar 7: vista settimanale oraria + vista a riquadri, mantenendo il riepilogo senza orari.
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
            `hsl(${tonalita} 76% 34%)`, // bordo saturo e leggibile
            `hsl(${tonalita} 83% 77%)`, // colore del riquadro nettamente visibile
        ];
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

    // Il filtro weekend resta nel markup statico, fuori dalla toolbar
    // ricostruita da FullCalendar quando si cambia vista o settimana.

    // Il weekend viene nascosto solo se non contiene fasi produttive.
    // Le scadenze cliente non obbligano a mostrare la colonna: chi consulta il
    // calendario puo' scegliere di visualizzarle tramite il comando dedicato.
    const mostraWeekendCheckbox = document.getElementById('calendar-show-weekends');
    const statoWeekend = document.getElementById('calendar-weekend-status');
    const periodiAnalizzati = new Map();
    let weekendConProduzione = new Set();
    let intervalloCorrente = null;
    let vistaCorrente = null;
    let ultimaRichiesta = 0;
    let giorniNascosti = [0, 6];

    function dataLocale(data) {
        const anno = data.getFullYear();
        const mese = String(data.getMonth() + 1).padStart(2, '0');
        const giorno = String(data.getDate()).padStart(2, '0');
        return `${anno}-${mese}-${giorno}`;
    }

    function dataEstesa(data, giorni) {
        const copia = new Date(data.getFullYear(), data.getMonth(), data.getDate(), 12);
        copia.setDate(copia.getDate() + giorni);
        return dataLocale(copia);
    }

    function weekendConEventi(eventi) {
        const giorni = new Set();
        for (const evento of eventi) {
            if (!evento || typeof evento.start !== 'string') continue;
            // Leggiamo la data locale della fase, senza conversione in UTC.
            const inizio = evento.start.slice(0, 10);
            if (!/^\d{4}-\d{2}-\d{2}$/.test(inizio)) continue;
            const giorno = new Date(`${inizio}T12:00:00`).getDay();
            if (giorno === 0 || giorno === 6) giorni.add(giorno);
        }
        return giorni;
    }

    function applicaVisibilitaWeekend() {
        // La vista giornaliera non ha colonne da comprimere: mantenere il giorno
        // scelto dall'utente, anche quando e' sabato o domenica.
        const mostraTutti = Boolean(mostraWeekendCheckbox?.checked);
        const nuovi = (vistaCorrente === 'dayGridDay' || mostraTutti)
            ? []
            : [0, 6].filter(giorno => !weekendConProduzione.has(giorno));
        if (nuovi.join(',') === giorniNascosti.join(',')) return;
        giorniNascosti = nuovi;
        calendario.setOption('hiddenDays', nuovi);
    }

    function aggiornaWeekendPerVista(view) {
        vistaCorrente = view.type;
        if (vistaCorrente === 'dayGridDay') {
            applicaVisibilitaWeekend();
            return;
        }
        // currentStart/currentEnd sono stabili anche quando hiddenDays cambia.
        // Nelle viste settimanali verifichiamo SOLO i weekend della settimana
        // corrente; altrimenti un piano nel weekend successivo mostrerebbe
        // erroneamente sabato e domenica anche nella settimana vuota.
        // Solo il mese include giorni di raccordo visibili di altri mesi.
        const calendarioMensile = view.type === 'dayGridMonth';
        const inizio = calendarioMensile
            ? dataEstesa(view.currentStart, -7) : dataLocale(view.currentStart);
        const fine = calendarioMensile
            ? dataEstesa(view.currentEnd, 7) : dataLocale(view.currentEnd);
        const chiave = `${inizio}|${fine}`;
        if (chiave === intervalloCorrente) {
            applicaVisibilitaWeekend();
            return;
        }
        intervalloCorrente = chiave;
        const richiesta = ++ultimaRichiesta;

        const memorizzati = periodiAnalizzati.get(chiave);
        if (memorizzati) {
            weekendConProduzione = memorizzati;
            if (statoWeekend) statoWeekend.textContent = '';
            applicaVisibilitaWeekend();
            return;
        }
        // Durante il caricamento non mostrare giorni che potrebbero essere vuoti.
        // Se la richiesta fallisce, invece, mostriamo entrambi i giorni per
        // evitare di occultare per errore una lavorazione pianificata.
        weekendConProduzione = new Set();
        if (statoWeekend) statoWeekend.textContent = 'Verifica weekend…';
        applicaVisibilitaWeekend();

        const url = new URL(pianificazioneUrl, window.location.href);
        url.searchParams.set('start', inizio);
        url.searchParams.set('end', fine);
        fetch(url.toString(), { credentials: 'same-origin' })
            .then(risposta => {
                if (!risposta.ok) throw new Error(`HTTP ${risposta.status}`);
                return risposta.json();
            })
            .then(eventi => {
                if (!Array.isArray(eventi)) throw new Error('Risposta calendario non valida');
                const presenti = weekendConEventi(eventi);
                periodiAnalizzati.set(chiave, presenti);
                if (richiesta !== ultimaRichiesta || chiave !== intervalloCorrente) return;
                weekendConProduzione = presenti;
                if (statoWeekend) statoWeekend.textContent = '';
                applicaVisibilitaWeekend();
            })
            .catch(errore => {
                console.error('Impossibile verificare le lavorazioni del weekend', errore);
                if (richiesta !== ultimaRichiesta || chiave !== intervalloCorrente) return;
                weekendConProduzione = new Set([0, 6]);
                if (statoWeekend) statoWeekend.textContent = 'Weekend visibili: verifica non disponibile';
                applicaVisibilitaWeekend();
            });
    }

    mostraWeekendCheckbox?.addEventListener('change', applicaVisibilitaWeekend);

    const calendario = new FullCalendar.Calendar(calendarEl, {
        themeSystem: 'classic',
        datesSet(info) {
            aggiornaWeekendPerVista(info.view);
        },
        initialView: 'timeGridWeek',
        hiddenDays: [0, 6],
        locale: 'it',
        firstDay: 1,
        timeZone: 'local',
        height: 'auto',
        expandRows: true,
        allDaySlot: true,
        eventDisplay: 'block',
        displayEventEnd: false,
        displayEventTime: false,
        dayMaxEvents: false,
        dayMaxEventRows: false,
        slotEventOverlap: false,
        slotMinTime: '07:00:00',
        slotMaxTime: '18:00:00',
        slotDuration: '00:30:00',
        slotLabelInterval: '01:00:00',
        nowIndicator: true,
        headerToolbar: {
            start: 'prev,next today',
            center: 'title',
            end: 'timeGridWeek,dayGridWeek,dayGridMonth,dayGridDay',
        },
        buttonText: {
            today: 'Oggi',
            timeGridWeek: 'Settimana (ore)',
            dayGridWeek: 'Riquadri',
            dayGridMonth: 'Mese',
            dayGridDay: 'Giorno',
        },
        views: {
            timeGridWeek: { dayHeaderFormat: { weekday: 'long', day: 'numeric', month: 'short' } },
            dayGridWeek: { dayHeaderFormat: { weekday: 'long', day: 'numeric', month: 'short' } },
        },
        eventSources: [
            { id: 'scadenze', url: scadenzeUrl },
            { id: 'pianificazione', url: pianificazioneUrl },
        ],
        eventClassNames(info) {
            const produzione = info.event.source?.id === 'pianificazione' ||
                (info.event.extendedProps.id_pianificazione != null &&
                 info.event.extendedProps.tipo_evento !== 'scadenze_giorno');
            return produzione
                ? ['evento-produzione', 'lavorazione-' + tipoLavorazione(info.event.extendedProps.lavorazione)]
                : ['evento-scadenza', propsStato(info.event.extendedProps)];
        },
        eventContent(info) {
            const props = info.event.extendedProps;
            const produzione = info.event.source?.id === 'pianificazione' ||
                (info.event.extendedProps.id_pianificazione != null &&
                 info.event.extendedProps.tipo_evento !== 'scadenze_giorno');
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
                    etichetta.style.setProperty('--fase-badge-fg', '#000000');
                }
            } else {
                addText(scheda, 'cal-event-badge scadenza-badge', 'Scadenze del giorno');
            }
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
            const produzione = info.event.source?.id === 'pianificazione' ||
                (info.event.extendedProps.id_pianificazione != null &&
                 info.event.extendedProps.tipo_evento !== 'scadenze_giorno');
            if (produzione) {
                const [bordo, sfondo] = infoPiano(p.id_pianificazione);
                // Applica il colore direttamente al riquadro FullCalendar.
                // Le proprieta' inline con !important prevalgono sui temi e
                // sulle regole che impostano background: ... !important.
                info.el.style.setProperty('--piano-accent', bordo);
                info.el.style.setProperty('--piano-bg', sfondo);
                info.el.style.setProperty('background', sfondo, 'important');
                info.el.style.setProperty('background-color', sfondo, 'important');
                info.el.style.setProperty('border', `2px solid ${bordo}`, 'important');
                info.el.style.setProperty('border-left', `6px solid ${bordo}`, 'important');
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
    document.body.addEventListener('pianificazioneSalvata', () => {
        periodiAnalizzati.clear();
        intervalloCorrente = null;
        calendario.refetchEvents();
        aggiornaWeekendPerVista(calendario.view);
    });
})();
