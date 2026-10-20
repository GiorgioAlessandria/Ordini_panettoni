// Vista singola su tablet/mobile; entrambe le tabelle restano nel DOM per HTMX.
(() => {
    document.addEventListener("click", (event) => {
        const pulsante = event.target.closest("[data-panel-switch]");
        if (!pulsante) return;
        const workspace = pulsante.closest(".ordini-workspace");
        if (!workspace) return;
        workspace.dataset.pannelloAttivo = pulsante.dataset.panelSwitch;
        workspace.querySelectorAll("[data-panel-switch]").forEach((p) => {
            const attivo = p === pulsante;
            p.setAttribute("aria-pressed", String(attivo));
            p.classList.toggle("btn-primary", attivo);
            p.classList.toggle("btn-outline-primary", !attivo);
        });
        window.dispatchEvent(new Event("ordini:layout-change"));
    });
})();
