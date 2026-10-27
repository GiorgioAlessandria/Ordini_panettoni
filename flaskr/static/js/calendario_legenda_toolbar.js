/* Sposta la legenda di produzione/scadenze dopo Today (Oggi) in FullCalendar.
 * Modulo separato: funziona anche se calendario.js usa una versione precedente.
 */
(function () {
    'use strict';

    function inizializzaLegendaAccantoOggi() {
        const shell = document.querySelector('.calendario-shell');
        const calendario = shell?.querySelector('#calendar');
        const legenda = shell?.querySelector('.calendario-legenda');
        if (!calendario || !legenda) return;

        function spostaLegenda() {
            const oggi = calendario.querySelector('.fc-header-toolbar .fc-today-button');
            if (!oggi) return;
            const gruppoSinistro = oggi.closest('.fc-toolbar-chunk');
            if (!gruppoSinistro) return;

            // L'operazione è idempotente: evita continui spostamenti e cicli
            // quando FullCalendar aggiorna l'intestazione a ogni cambio vista.
            if (legenda.parentElement !== gruppoSinistro || legenda.previousElementSibling !== oggi) {
                oggi.insertAdjacentElement('afterend', legenda);
            }
            legenda.classList.add('calendario-legenda--toolbar', 'calendario-legenda--vicino-oggi');
        }

        spostaLegenda();
        // La toolbar viene creata da FullCalendar e può essere ricreata al
        // cambio di vista: intercettiamo solo variazioni nella struttura DOM.
        const osservatore = new MutationObserver(spostaLegenda);
        osservatore.observe(calendario, { childList: true, subtree: true });
        window.addEventListener('pagehide', () => osservatore.disconnect(), { once: true });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', inizializzaLegendaAccantoOggi, { once: true });
    } else {
        inizializzaLegendaAccantoOggi();
    }
})();
