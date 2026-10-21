// Mantiene il testo non salvato durante gli aggiornamenti periodici delle tabelle HTMX.
// Non duplica salvataggi: i POST sono gestiti dalle route già protette per ruolo e CSRF.
(() => {
    const ids = new Set(["ordini_cliente_da_produrre", "ordini_cliente_in_produzione"]);
    const bozze = new Map();

    document.body.addEventListener("htmx:beforeSwap", event => {
        const area = event.detail.target;
        if (!area || !ids.has(area.id)) return;
        const testo = new Map();
        const attivo = document.activeElement;
        let focus = null;
        area.querySelectorAll("[data-pan-note-draft]").forEach(input => {
            const key = input.dataset.panNoteDraft;
            // Preserva le bozze di nuovi commenti e i testi in corso di modifica.
            // Per gli esistenti, conserva solo i campi realmente toccati dall'operatore.
            if (input.dataset.panNoteDirty === "1") testo.set(key, input.value);
            if (input === attivo) {
                focus = {key, start: input.selectionStart, end: input.selectionEnd};
            }
        });
        bozze.set(area.id, {testo, focus});
    });

    document.body.addEventListener("htmx:afterSwap", event => {
        const area = event.detail.target;
        if (!area || !ids.has(area.id)) return;
        const salvate = bozze.get(area.id);
        if (!salvate) return;
        area.querySelectorAll("[data-pan-note-draft]").forEach(input => {
            const key = input.dataset.panNoteDraft;
            if (salvate.testo.has(key)) {
                input.value = salvate.testo.get(key);
                input.dataset.panNoteDirty = "1";
            }
            if (salvate.focus?.key === key) {
                input.focus({preventScroll: true});
                if (salvate.focus.start !== null) {
                    input.setSelectionRange(salvate.focus.start, salvate.focus.end);
                }
            }
        });
        bozze.delete(area.id);
    });

    document.addEventListener("input", event => {
        if (event.target.matches?.("[data-pan-note-draft]")) {
            event.target.dataset.panNoteDirty = "1";
        }
    });

    document.body.addEventListener("htmx:responseError", event => {
        const cella = event.detail.elt?.closest?.(".pan-note-inline");
        const feedback = cella?.querySelector(".pan-note-feedback");
        if (feedback) feedback.textContent = "Salvataggio non riuscito. Verifica la sessione e riprova.";
    });
})();
