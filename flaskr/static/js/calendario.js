// FullCalendar 7: raggruppamento verificato delle fasi su colonne e righe per blocco.
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

    // Nella vista oraria, gli eventi che occupano lo stesso identico intervallo
    // diventano un unico contenitore, visualizzato come griglia di schede.
    // L'endpoint Flask e gli eventi delle altre viste non vengono modificati.
    function raggruppaFasiPerBlocco(eventi) {
        if (!Array.isArray(eventi)) return [];
        const gruppi = new Map();
        const altri = [];
        for (const evento of eventi) {
            if (evento.allDay || typeof evento.start !== 'string' ||
                    typeof evento.end !== 'string') {
                altri.push(evento);
                continue;
            }
            const chiave = `${evento.start}|${evento.end}`;
            if (!gruppi.has(chiave)) gruppi.set(chiave, []);
            gruppi.get(chiave).push(evento);
        }
        for (const [chiave, fasi] of gruppi) {
            if (fasi.length === 1) {
                altri.push(fasi[0]);
                continue;
            }
            // Ordine deterministico: le schede restano sempre nella stessa posizione.
            fasi.sort((a, b) => {
                const aa = a.extendedProps || {};
                const bb = b.extendedProps || {};
                return Number(aa.id_pianificazione || 0) - Number(bb.id_pianificazione || 0) ||
                    Number(aa.numero_fase || 0) - Number(bb.numero_fase || 0) ||
                    String(a.id).localeCompare(String(b.id));
            });
            const prima = fasi[0];
            altri.push({
                id: `gruppo-${chiave}`,
                title: `${fasi.length} fasi pianificate`,
                start: prima.start,
                end: prima.end,
                allDay: false,
                extendedProps: { tipo_evento: 'gruppo_fasi', fasi },
            });
        }
        return altri;
    }

    function dettagliFase(evento) {
        const p = evento.extendedProps || {};
        const righe = [
            `Pianificazione: ${p.id_pianificazione ?? '?'}`,
            `Fase: ${p.numero_fase ?? '?'} · ${p.lavorazione || 'Lavorazione'}`,
            `Articolo: ${p.descrizione_articolo || evento.title || 'N/D'}`,
            `Ordini: ${(p.ordini || []).join(', ')}`,
            `Quantità assegnata: ${p.quantita_assegnata ?? 'N/D'}`,
        ];
        if (p.impastatrici?.length) righe.push(`Impastatrici: ${p.impastatrici.join(', ')}`);
        return righe.join('\n');
    }

    function creaSchedaFase(evento) {
        const p = evento.extendedProps || {};
        const nomeFase = p.numero_fase == null
            ? (p.lavorazione || 'Lavorazione')
            : `Fase ${p.numero_fase} · ${p.lavorazione || 'Lavorazione'}`;
        const scheda = document.createElement('button');
        scheda.type = 'button';
        scheda.className = 'cal-blocco-scheda';
        const [bordo, sfondo] = infoPiano(p.id_pianificazione);
        scheda.style.setProperty('--piano-accent', bordo);
        scheda.style.setProperty('--piano-bg', sfondo);
        // La scheda raggruppata e' un <button>, non un evento FullCalendar:
        // colora direttamente l'intera superficie come i vecchi riquadri.
        // L'inline !important evita che reset Bootstrap/temi la rendano bianca.
        scheda.style.setProperty('background', sfondo, 'important');
        scheda.style.setProperty('background-color', sfondo, 'important');
        scheda.style.setProperty('border', `2px solid ${bordo}`, 'important');
        scheda.style.setProperty('border-left', `5px solid ${bordo}`, 'important');
        scheda.title = dettagliFase(evento);
        scheda.setAttribute('aria-label', dettagliFase(evento));
        const etichetta = addText(scheda, 'cal-event-badge',
            `Piano #${p.id_pianificazione ?? '?'} · ${nomeFase}`);
        etichetta.dataset.lavorazione = tipoLavorazione(p.lavorazione);
        if (typeof p.colore_fase === 'string' && /^#[0-9a-f]{6}$/i.test(p.colore_fase)) {
            etichetta.dataset.coloreFase = 'personalizzato';
            etichetta.style.setProperty('--fase-badge-bg', p.colore_fase);
            etichetta.style.setProperty('--fase-badge-fg', '#000000');
        }
        addText(scheda, 'cal-event-title', p.descrizione_articolo || evento.title || 'Articolo');
        addText(scheda, 'cal-event-summary',
            `${p.numero_ordini ?? 0} ordini · ${p.quantita_assegnata ?? 'N/D'} pz`);
        if (p.dati_incompleti) addText(scheda, 'cal-event-warning', 'Quantità da verificare');
        scheda.addEventListener('click', (click) => {
            click.preventDefault();
            click.stopPropagation();
            if (p.detailUrl && window.apriRiepilogoCalendario) {
                window.apriRiepilogoCalendario(p.detailUrl);
            }
        });
        return scheda;
    }

    function propsStato(props) {
        return props.stato_giorno === 'eseguito'
            ? 'scadenza-giorno-completata'
            : 'scadenza-giorno-in-corso';
    }

    // I comandi sono nel template, fuori dal DOM ricreato da FullCalendar.
    // Aggiornare titolo e stato dei pulsanti a ogni cambio vista/periodo.
    const titoloIntestazione = document.getElementById('calendar-current-title');
    const pulsantiVista = Array.from(document.querySelectorAll('[data-calendar-view]'));

    function aggiornaIntestazione(vista) {
        if (titoloIntestazione) titoloIntestazione.textContent = vista.title || 'Calendario';
        for (const pulsante of pulsantiVista) {
            const attivo = pulsante.dataset.calendarView === vista.type;
            pulsante.classList.toggle('is-active', attivo);
            pulsante.setAttribute('aria-pressed', String(attivo));
        }
    }


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
            aggiornaIntestazione(info.view);
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
        lazyFetching: false, // Ricalcola raggruppamento al cambio della vista.
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
        // Navigazione e scelta viste sono nella barra HTML permanente.
        headerToolbar: false,
        views: {
            timeGridWeek: { dayHeaderFormat: { weekday: 'long', day: 'numeric', month: 'short' } },
            dayGridWeek: { dayHeaderFormat: { weekday: 'long', day: 'numeric', month: 'short' } },
        },
        eventSources: [
            { id: 'scadenze', url: scadenzeUrl },
            {
                id: 'pianificazione',
                url: pianificazioneUrl,
                // FullCalendar: nella singola event source la callback è `success`,
                // NON `eventSourceSuccess` (che è un'opzione globale).
                // Questo è il punto che impediva il precedente raggruppamento.
                success(eventi) {
                    return calendario.view?.type === 'timeGridWeek'
                        ? raggruppaFasiPerBlocco(eventi)
                        : eventi;
                },
            },
        ],
        eventClassNames(info) {
            const gruppo = info.event.extendedProps.tipo_evento === 'gruppo_fasi';
            if (gruppo) return ['evento-blocco-piani'];
            const produzione = info.event.source?.id === 'pianificazione' ||
                (info.event.extendedProps.id_pianificazione != null &&
                 info.event.extendedProps.tipo_evento !== 'scadenze_giorno');
            return produzione
                ? ['evento-produzione', 'lavorazione-' + tipoLavorazione(info.event.extendedProps.lavorazione)]
                : ['evento-scadenza', propsStato(info.event.extendedProps)];
        },
        eventContent(info) {
            const props = info.event.extendedProps;
            if (props.tipo_evento === 'gruppo_fasi') {
                const griglia = document.createElement('div');
                griglia.className = 'cal-blocco-griglia';
                griglia.setAttribute('aria-label', `${props.fasi?.length ?? 0} fasi nello stesso blocco`);
                for (const fase of props.fasi || []) griglia.appendChild(creaSchedaFase(fase));
                return { domNodes: [griglia] };
            }
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
            if (p.tipo_evento === 'gruppo_fasi') return;
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

    // Collegare i comandi una sola volta: FullCalendar non li ricrea.
    document.getElementById('calendar-prev')?.addEventListener('click', () => calendario.prev());
    document.getElementById('calendar-next')?.addEventListener('click', () => calendario.next());
    document.getElementById('calendar-today')?.addEventListener('click', () => calendario.today());
    for (const pulsante of pulsantiVista) {
        pulsante.addEventListener('click', () => {
            const nomeVista = pulsante.dataset.calendarView;
            if (nomeVista && calendario.view.type !== nomeVista) calendario.changeView(nomeVista);
        });
    }

    calendarEl.dataset.calendarInitialized = '1';
    calendario.render();
    document.body.addEventListener('pianificazioneSalvata', () => {
        periodiAnalizzati.clear();
        intervalloCorrente = null;
        calendario.refetchEvents();
        aggiornaWeekendPerVista(calendario.view);
    });
})();
