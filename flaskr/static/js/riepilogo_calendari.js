(function () {
    'use strict';
    async function apriRiepilogoCalendario(url) {
        const modal = document.getElementById('modal-riepilogo-calendario');
        const contenuto = document.getElementById('contenuto-riepilogo-calendario');
        if (!url || !modal || !contenuto) return;
        contenuto.textContent = 'Caricamento riepilogo...';
        try {
            if (!window.htmx || !window.bootstrap) throw new Error('HTMX o Bootstrap non disponibili');
            // htmx.ajax restituisce una Promise in HTMX 1.9+/2.x
            await window.htmx.ajax('GET', url, { target: '#contenuto-riepilogo-calendario', swap: 'innerHTML' });
            window.bootstrap.Modal.getOrCreateInstance(modal).show();
        } catch (error) {
            console.error('Errore caricamento riepilogo', error);
            contenuto.textContent = 'Non è stato possibile caricare il riepilogo.';
            window.bootstrap?.Modal?.getOrCreateInstance(modal)?.show();
        }
    }
    window.apriRiepilogoCalendario = apriRiepilogoCalendario;
    function apriDaEvento(event) {
        if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
        const elemento = event.target.closest?.('[data-riepilogo-url]');
        if (!elemento) return;
        // Evita che lo stesso evento venga gestito due volte da controlli annidati.
        if (event.type === 'keydown' && event.target !== elemento) return;
        event.preventDefault();
        apriRiepilogoCalendario(elemento.dataset.riepilogoUrl);
    }
    document.addEventListener('click', apriDaEvento);
    document.addEventListener('keydown', apriDaEvento);
})();
