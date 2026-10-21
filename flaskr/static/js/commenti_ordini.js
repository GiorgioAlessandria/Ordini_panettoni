// Sezione commenti sempre presente: selezione dell'ordine senza navigare altrove.
// Usa endpoint HTML/HTMX protetti dagli stessi ruoli delle note vendite.
(() => {
    const script = document.currentScript;
    const pathTemplate = script?.dataset.urlTemplate;
    const container = document.getElementById("pan-commenti-panel");
    const target = document.getElementById("pan-commenti-contenuto");
    const label = document.getElementById("pan-commenti-ordine-label");
    const picker = document.getElementById("pan-commenti-picker");
    const select = document.getElementById("pan-commenti-ordine-scelta");
    if (!pathTemplate || !container || !target || !label || !picker || !select) return;

    let currentOrder = "";

    function ordersOf(row) {
        const raw = row?.dataset.commentiOrdini || "";
        return raw.split(",").map(s => s.trim()).filter(s => /^\d+:\d+$/.test(s));
    }
    function urlFor(order) {
        const [documento, riga] = order.split(":");
        const position = pathTemplate.indexOf('/0/0/note/pannello');
        if (position === -1) return null;
        return pathTemplate.slice(0, position) + `/${documento}/${riga}/note/pannello` +
            pathTemplate.slice(position + '/0/0/note/pannello'.length);
    }
    function show(order, force = false) {
        if (!order || (!force && order === currentOrder)) return;
        const url = urlFor(order);
        if (!url || !window.htmx) return;
        currentOrder = order;
        label.textContent = `Ordine ${order.replace(":", "-")}`;
        target.innerHTML = '<p class="text-secondary small mb-0">Caricamento commenti...</p>';
        // I valori provengono esclusivamente da IdDocumento / IdRigaDoc numerici.
        window.htmx.ajax("GET", url, {target: "#pan-commenti-contenuto", swap: "innerHTML"});
        select.value = order;
    }
    function selectRow(row) {
        const orders = ordersOf(row);
        if (!orders.length) return;
        select.replaceChildren();
        for (const order of orders) {
            const option = document.createElement("option");
            option.value = order;
            option.textContent = `Ordine ${order.replace(":", "-")}`;
            select.append(option);
        }
        picker.hidden = orders.length <= 1;
        document.querySelectorAll(".ordini-table tr[data-record]").forEach((r) => {
            r.classList.toggle("pan-commenti-row-selected", r === row);
        });
        show(orders.includes(currentOrder) ? currentOrder : orders[0]);
    }
    function findRowForOrder(order) {
        return [...document.querySelectorAll(".ordini-table tr[data-record]")]
            .find(r => ordersOf(r).includes(order));
    }
    function syncAfterTables() {
        const existing = currentOrder ? findRowForOrder(currentOrder) : null;
        if (existing) {
            existing.classList.add("pan-commenti-row-selected");
            return;
        }
        const firstVisible = [...document.querySelectorAll(".ordini-table tr[data-record]")]
            .find(r => r.offsetParent !== null);
        if (firstVisible) selectRow(firstVisible);
        else if (currentOrder) {
            currentOrder = "";
            select.replaceChildren();
            picker.hidden = true;
            label.textContent = "Nessun ordine disponibile";
            target.textContent = "Nessun ordine da visualizzare.";
        }
    }
    document.addEventListener("click", (event) => {
        const button = event.target.closest("[data-commenti-seleziona]");
        if (!button) return;
        event.preventDefault();
        event.stopPropagation();
        const row = button.closest("tr[data-record]");
        if (row) selectRow(row);
    }, true);
    select.addEventListener("change", () => show(select.value));
    document.body.addEventListener("htmx:afterSwap", event => {
        const id = event.detail.target?.id;
        if (id === "ordini_cliente_da_produrre" || id === "ordini_cliente_in_produzione") {
            syncAfterTables();
        }
    });
    document.body.addEventListener("htmx:responseError", event => {
        if (event.detail.target?.id === "pan-commenti-contenuto") {
            target.textContent = "Impossibile caricare o salvare il commento. Verifica la sessione e riprova.";
        }
    });
})();
