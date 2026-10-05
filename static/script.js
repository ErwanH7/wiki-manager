// WikiMasters Collection Manager - frontend

const RARITY_ORDER = { C: 1, PC: 2, R: 3, SR: 4, UR: 5, L: 6 };
const RARITY_LABELS = { C: "Commun", PC: "Peu Commun", R: "Rare", SR: "Super Rare", UR: "Ultra Rare", L: "Légende" };
const RARITY_COLORS = { C: "#9aa3b5", PC: "#3ecf8e", R: "#4ea8ff", SR: "#b36bff", UR: "#ff5c7a", L: "#ffb547" };
const CATEGORY_COLORS = ["#7c5cff", "#3ecf8e", "#4ea8ff", "#ffb547", "#ff5c7a", "#b36bff", "#2dd4bf",
    "#f472b6", "#a3e635", "#fb923c", "#60a5fa", "#e879f9", "#9aa3b5"];

let allCards = [];
let bestSellers = [];
let appStatus = {};
let charts = {};
let currentPage = 1;
let sortOrder = 1;
const loaded = {};

const $ = (id) => document.getElementById(id);

// ---------- Utilitaires ----------

function escapeHtml(str) {
    return String(str ?? "").replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function formatPrice(value) {
    return Number(value || 0).toLocaleString("fr-FR", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function priceCell(card, key = "avg_price") {
    if (card.price_source === "category" || card.price_source === "estimate_sales") {
        return `<span class="muted" title="${escapeHtml(card.price_note || "Estimation")}">≈ ${formatPrice(card[key])}</span>`;
    }
    if (card.price_source === "sales_other") {
        return `<span title="Moyenne des ventes de cette carte dans une autre rareté">${formatPrice(card[key])}*</span>`;
    }
    if (!card.price_source && !card[key]) return '<span class="muted">–</span>';
    return formatPrice(card[key]);
}

// Le site ne fournit que la moyenne des ventes (compte non Pro) : nombre inconnu -> "–"
function salesCountCell(card) {
    if (card.nb_sales) return card.nb_sales;
    return '<span class="muted" title="Nombre de ventes non fourni par le site (seule la moyenne est disponible)">–</span>';
}

function demandCell(card) {
    if (card.demand) return demandBar(card.demand);
    return '<span class="muted" title="Aucune enchère en cours avec une offre pour cette carte">–</span>';
}

function rarityBadge(code) {
    return `<span class="rarity rarity-${code}">${RARITY_LABELS[code] || code}</span>`;
}

function trendIcon(trend) {
    if (trend === "up") return '<span class="trend-up">📈</span>';
    if (trend === "down") return '<span class="trend-down">📉</span>';
    return '<span class="trend-stable">➖</span>';
}

function demandBar(demand) {
    const max = Math.max(1, ...allCards.map((c) => c.demand || 0));
    const pct = Math.round(((demand || 0) / max) * 100);
    return `<span class="demand-bar"><span style="width:${pct}%"></span></span>${demand || 0}`;
}

function getSettings() {
    const defaults = appStatus.settings || { min_sell_price: 0, min_buyers: 0 };
    try {
        return { ...defaults, ...JSON.parse(localStorage.getItem("wm-settings") || "{}") };
    } catch {
        return defaults;
    }
}

const PROGRESS_LABELS = {
    collection: "Chargement de la collection",
    market: "Lecture du marché",
    sales: "Prix de vente des cartes",
    sales_retry: "Nouvel essai (cartes refusées)",
    prices: "Calcul des prix",
};

function showProgress(progress) {
    const p = progress || {};
    const label = PROGRESS_LABELS[p.step] || "Chargement";
    const pct = p.total ? Math.round((p.done / p.total) * 100) : null;
    $("loader-text").textContent = p.total
        ? `${label} : ${p.done} / ${p.total}`
        : `${label}${p.done ? ` (${p.done})` : ""}…`;
    $("loader-bar").style.width = pct === null ? "100%" : `${pct}%`;
    $("loader-bar").classList.toggle("indeterminate", pct === null);
}

async function api(path, options = {}) {
    // 202 = chargement en arrière-plan : on attend en affichant la progression
    for (;;) {
        const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
        const data = await res.json().catch(() => ({}));
        if (res.status === 202 && data.loading) {
            $("loader").classList.remove("hidden");
            showProgress(data.progress);
            await new Promise((r) => setTimeout(r, 1000));
            path = path.replace(/[?&]refresh=1/, "");
            continue;
        }
        return handleResponse(res, data);
    }
}

function handleResponse(res, data) {
    if (!res.ok) {
        const err = new Error(data.error || `Erreur ${res.status}`);
        err.status = res.status;
        throw err;
    }
    return data;
}

function showError(message) {
    const el = $("global-error");
    el.textContent = message;
    el.classList.toggle("hidden", !message);
}

function handleError(err) {
    console.error("Erreur:", err);
    if (err.status === 401) {
        setConnected(false);
        showError("Session expirée ou authentification échouée. Reconnecte-toi.");
    } else if (err.status === 503) {
        showError("Le serveur WikiMasters semble surchargé (503). Réessaie plus tard.");
    } else {
        showError(err.message);
    }
}

// ---------- Authentification ----------

async function checkStatus() {
    appStatus = await api("/api/status");
    $("demo-badge").classList.toggle("hidden", !appStatus.demo);
    $("login-fields").classList.toggle("hidden", appStatus.demo || appStatus.has_credentials);
    $("login-hint").textContent = appStatus.demo
        ? "Mode démo : une collection fictive va être chargée."
        : appStatus.has_credentials
            ? "Utilise les identifiants de ton fichier .env."
            : "Aucun identifiant dans .env : saisis-les ci-dessous.";
    setConnected(appStatus.authenticated);
    renderInfo();
    if (appStatus.authenticated) loadAll();
}

function setConnected(connected) {
    $("status-dot").classList.toggle("on", connected);
    $("status-text").textContent = connected
        ? (appStatus.email || "Connecté")
        : "Non connecté";
    $("login-section").classList.toggle("hidden", connected);
    $("refresh-btn").classList.toggle("hidden", !connected);
    $("logout-btn").classList.toggle("hidden", !connected);
    document.querySelectorAll(".tab-panel, .tabs").forEach((el) => {
        el.style.visibility = connected ? "visible" : "hidden";
    });
}

async function login() {
    const btn = $("login-btn");
    btn.disabled = true;
    $("login-error").classList.add("hidden");
    try {
        await api("/api/login", {
            method: "POST",
            body: JSON.stringify({ email: $("login-email").value, password: $("login-password").value }),
        });
        $("login-password").value = "";
        appStatus = await api("/api/status");
        setConnected(true);
        await loadAll();
    } catch (err) {
        $("login-error").textContent = err.message;
        $("login-error").classList.remove("hidden");
    } finally {
        btn.disabled = false;
    }
}

async function logout() {
    await api("/api/logout", { method: "POST" });
    allCards = [];
    Object.keys(loaded).forEach((k) => delete loaded[k]);
    appStatus = await api("/api/status");
    setConnected(false);
}

// ---------- Chargement ----------

async function loadAll(refresh = false) {
    $("loader").classList.remove("hidden");
    showProgress(null);
    showError("");
    try {
        const data = await api(`/api/collection${refresh ? "?refresh=1" : ""}`);
        allCards = data.cards;
        Object.keys(loaded).forEach((k) => delete loaded[k]);
        populateCategoryFilter();
        await loadTab(currentTab());
        startPriceWatch();
    } catch (err) {
        handleError(err);
    } finally {
        $("loader").classList.add("hidden");
    }
}

// ---------- Prix récupérés en arrière-plan ----------

let priceWatch = null;
let pricesVersion = null;

function startPriceWatch() {
    if (priceWatch) return;
    priceWatch = setInterval(checkPriceUpdate, 4000);
    checkPriceUpdate();
}

async function checkPriceUpdate() {
    let status;
    try {
        status = await api("/api/status");
    } catch {
        return;
    }
    const update = status.price_update;
    const banner = $("price-banner");
    if (status.warning) showError(status.warning);
    if (update && update.running) {
        const count = update.total ? ` : ${update.done} / ${update.total} cartes` : "";
        banner.textContent = `⏳ Récupération des prix réels en arrière-plan${count}. ` +
            "Tu peux utiliser l'app, l'affichage se met à jour tout seul.";
        banner.classList.remove("hidden");
    } else if (update && update.error) {
        banner.textContent = `⚠ Mise à jour des prix interrompue : ${update.error}`;
        banner.classList.remove("hidden");
    } else {
        banner.classList.add("hidden");
    }
    if (pricesVersion !== null && status.prices_version !== pricesVersion) {
        await refreshDataSilently();
    }
    pricesVersion = status.prices_version;
    if (!update || !update.running) {
        clearInterval(priceWatch);
        priceWatch = null;
    }
}

async function refreshDataSilently() {
    try {
        const data = await api("/api/collection");
        allCards = data.cards;
        Object.keys(loaded).forEach((k) => delete loaded[k]);
        populateCategoryFilter();
        await loadTab(currentTab());
    } catch (err) {
        console.error("Erreur:", err);
    }
}

function currentTab() {
    return document.querySelector(".tab.active").dataset.tab;
}

async function loadTab(tab) {
    if (!allCards.length && tab !== "settings") return;
    try {
        if (tab === "dashboard" && !loaded.dashboard) await loadDashboard();
        if (tab === "sell") await loadBestSellers();
        if (tab === "categories" && !loaded.categories) await loadCategories();
        if (tab === "collection") loadCollection();
        if (tab === "settings") loadSettings();
    } catch (err) {
        handleError(err);
    }
}

function switchTab(tab) {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${tab}`));
    loadTab(tab);
}

// ---------- Dashboard ----------

async function loadDashboard() {
    const stats = await api("/api/stats");
    $("stat-total").textContent = stats.total_cards.toLocaleString("fr-FR");
    $("stat-unique").textContent = stats.unique_cards.toLocaleString("fr-FR");
    $("stat-value").textContent = formatPrice(stats.total_value);
    $("stat-avg").textContent = formatPrice(stats.avg_card_value);
    $("loaded-at").textContent = stats.loaded_at ? `Données chargées le ${stats.loaded_at}` : "";

    const mv = stats.most_valuable;
    $("most-valuable").innerHTML = mv
        ? `<div class="mv">
               <div class="mv-title">${escapeHtml(mv.title)}</div>
               ${rarityBadge(mv.rarity)}
               <span>Prix moyen : <b>${formatPrice(mv.avg_price)}</b></span>
               <span class="muted">${mv.nb_sales} ventes · demande ${mv.demand}</span>
               <button class="btn btn-secondary btn-small" data-detail="${escapeHtml(mv.id)}">Détails</button>
           </div>`
        : "Aucune carte.";

    drawRarityChart(stats.by_rarity);
    drawCategoryChart(stats.by_category);
    loaded.dashboard = true;
}

function drawRarityChart(data) {
    const codes = Object.keys(data);
    charts.rarity?.destroy();
    charts.rarity = new Chart($("rarity-chart"), {
        type: "doughnut",
        data: {
            labels: codes.map((c) => data[c].label),
            datasets: [{ data: codes.map((c) => data[c].count), backgroundColor: codes.map((c) => RARITY_COLORS[c]), borderWidth: 0 }],
        },
        options: chartOptions({ legend: true }),
    });
}

function drawCategoryChart(data) {
    const names = Object.keys(data);
    charts.category?.destroy();
    charts.category = new Chart($("category-chart"), {
        type: "bar",
        data: {
            labels: names,
            datasets: [{
                label: "Cartes",
                data: names.map((n) => data[n].count),
                backgroundColor: names.map((_, i) => CATEGORY_COLORS[i % CATEGORY_COLORS.length]),
                borderRadius: 4,
            }],
        },
        options: chartOptions({ scales: true }),
    });
}

function chartOptions({ legend = false, scales = false } = {}) {
    const grid = { color: "#2a2f3d" };
    const ticks = { color: "#8b91a3" };
    return {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: legend, position: "right", labels: { color: "#e6e8ef" } } },
        scales: scales ? { x: { grid, ticks }, y: { grid, ticks, beginAtZero: true } } : {},
    };
}

// ---------- À vendre ----------

async function loadBestSellers() {
    const settings = getSettings();
    const minInput = $("sell-min-price");
    if (minInput.value === "" && settings.min_sell_price) minInput.value = settings.min_sell_price;
    const params = new URLSearchParams({ min_buyers: settings.min_buyers || 0 });
    if (minInput.value) params.set("min_price", minInput.value);
    if ($("sell-max-price").value) params.set("max_price", $("sell-max-price").value);
    if ($("sell-rarity").value) params.set("rarity", $("sell-rarity").value);
    const data = await api(`/api/best-sellers?${params}`);
    bestSellers = data.cards;
    displayBestSellers(bestSellers);
}

function filterBestSellers() {
    clearTimeout(filterBestSellers.timer);
    filterBestSellers.timer = setTimeout(() => loadBestSellers().catch(handleError), 250);
}

function displayBestSellers(cards) {
    $("sell-empty").classList.toggle("hidden", cards.length > 0);
    $("sell-body").innerHTML = cards.map((card, i) => `
        <tr>
            <td class="muted">${i + 1}</td>
            <td class="title-cell">${card.starred ? "⭐ " : ""}${escapeHtml(card.title)}</td>
            <td>${rarityBadge(card.rarity)}</td>
            <td class="num">${card.quantity}</td>
            <td class="num">${priceCell(card)}</td>
            <td class="num">${priceCell(card, "last_price")}</td>
            <td>${demandCell(card)}</td>
            <td>${trendIcon(card.trend)}</td>
            <td class="num"><b>${formatPrice(card.profit_score)}</b></td>
            <td><button class="btn btn-secondary btn-small" data-detail="${escapeHtml(card.id)}">Détails</button></td>
        </tr>`).join("");
}

// ---------- Catégories ----------

async function loadCategories() {
    const data = await api("/api/categories");
    $("categories-grid").innerHTML = data.categories.map((cat) => `
        <div class="card category-card">
            <h3><span>${escapeHtml(cat.name)}</span><span>${cat.count}</span></h3>
            <div class="cat-meta">${cat.unique} cartes uniques · valeur ${formatPrice(cat.total_value)}</div>
            <ul>
                ${cat.top_cards.map((c) => `
                    <li data-detail="${escapeHtml(c.id)}">
                        <span>${escapeHtml(c.title)} ${rarityBadge(c.rarity)}</span>
                        <span class="num">${formatPrice(c.avg_price)}</span>
                    </li>`).join("")}
            </ul>
        </div>`).join("");
    loaded.categories = true;
}

// ---------- Collection ----------

function populateCategoryFilter() {
    const select = $("collection-category");
    const current = select.value;
    const categories = [...new Set(allCards.map((c) => c.category))].sort();
    select.innerHTML = '<option value="">Toutes catégories</option>' +
        categories.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
    select.value = categories.includes(current) ? current : "";
}

function filterCollection() {
    const query = $("collection-search").value.trim().toLowerCase()
        .normalize("NFD").replace(/[̀-ͯ]/g, "");
    const rarity = $("collection-rarity").value;
    const category = $("collection-category").value;
    const sortKey = $("collection-sort").value;

    const filtered = allCards.filter((c) => {
        const title = c.title.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
        return (!query || title.includes(query))
            && (!rarity || c.rarity === rarity)
            && (!category || c.category === category);
    });

    filtered.sort((a, b) => {
        let va = a[sortKey], vb = b[sortKey];
        if (sortKey === "rarity") { va = RARITY_ORDER[va]; vb = RARITY_ORDER[vb]; }
        if (typeof va === "string") return va.localeCompare(vb, "fr") * sortOrder;
        return ((va || 0) - (vb || 0)) * sortOrder;
    });
    return filtered;
}

function loadCollection() {
    const cards = filterCollection();
    const pageSize = parseInt($("page-size").value, 10);
    const pages = Math.max(1, Math.ceil(cards.length / pageSize));
    currentPage = Math.min(currentPage, pages);
    const pageCards = cards.slice((currentPage - 1) * pageSize, currentPage * pageSize);

    $("collection-empty").classList.toggle("hidden", cards.length > 0);
    $("page-info").textContent = `Page ${currentPage} / ${pages} · ${cards.length} cartes`;
    $("page-prev").disabled = currentPage <= 1;
    $("page-next").disabled = currentPage >= pages;

    $("collection-body").innerHTML = pageCards.map((card) => `
        <tr>
            <td class="title-cell">${card.starred ? "⭐ " : ""}${escapeHtml(card.title)}${card.wiki_category
                ? `<div class="muted small">${escapeHtml(card.wiki_category)}</div>` : ""}</td>
            <td>${rarityBadge(card.rarity)}</td>
            <td>${escapeHtml(card.category)}</td>
            <td class="num">${card.quantity}</td>
            <td class="num">${priceCell(card)}</td>
            <td class="num">${salesCountCell(card)}</td>
            <td>${demandCell(card)}</td>
            <td class="num">${formatPrice(card.total_value)}</td>
            <td><button class="btn btn-secondary btn-small" data-detail="${escapeHtml(card.id)}">Détails</button></td>
        </tr>`).join("");
}

// ---------- Paramètres ----------

function loadSettings() {
    const s = getSettings();
    $("setting-min-price").value = s.min_sell_price;
    $("setting-min-buyers").value = s.min_buyers;
}

function saveSettings() {
    const settings = {
        min_sell_price: parseFloat($("setting-min-price").value) || 0,
        min_buyers: parseInt($("setting-min-buyers").value, 10) || 0,
    };
    try {
        localStorage.setItem("wm-settings", JSON.stringify(settings));
    } catch (err) {
        console.error("Erreur:", err);
    }
    $("sell-min-price").value = settings.min_sell_price || "";
    $("settings-saved").classList.remove("hidden");
    setTimeout(() => $("settings-saved").classList.add("hidden"), 2000);
}

function renderInfo() {
    $("app-info").innerHTML = `
        <li>Version : 1.0</li>
        <li>Mode : ${appStatus.demo ? "Démo (données fictives)" : "WikiMasters"}</li>
        <li>Cartes en mémoire : ${appStatus.cards_loaded || allCards.length}</li>
        ${appStatus.cache ? `<li>Cache des prix : ${appStatus.cache.sales_cached} cartes,
            marché ${appStatus.cache.market_cached} enchères${appStatus.cache.market_age_min !== null
                ? ` (il y a ${appStatus.cache.market_age_min} min)` : ""}</li>` : ""}
        <li>Score de profit : (prix moyen × demande × multiplicateur rareté) / 100</li>
        <li>Multiplicateurs : C 1x · PC 2x · R 3x · SR 4x · UR 5x · L 6x</li>`;
}

// ---------- Modal détails ----------

let detailCardId = null;

async function showDebug() {
    const out = $("debug-output");
    out.textContent = "Diagnostic en cours…";
    out.classList.remove("hidden");
    try {
        const data = await api(`/api/card/${encodeURIComponent(detailCardId)}/debug`);
        out.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
        out.textContent = `Erreur : ${err.message}`;
    }
}

async function showDetails(cardId) {
    detailCardId = cardId;
    $("debug-output").classList.add("hidden");
    try {
        const card = await api(`/api/card/${encodeURIComponent(cardId)}`);
        $("modal-title").textContent = card.title;
        $("modal-meta").innerHTML = [
            ["Rareté", rarityBadge(card.rarity)],
            ["Catégorie", escapeHtml(card.category)],
            ["Quantité", card.quantity],
            ["Prix moyen", priceCell(card)],
            ["Source du prix", card.price_source === "sales" ? "Moyenne des ventes réelles" : card.price_source === "card" ? "Enchère en cours la plus basse" : card.price_source === "sales_other" ? "Ventes réelles (autre rareté)" : card.price_source === "estimate_sales" ? "≈ Estimation (ventes de cartes similaires)" : card.price_source === "category" ? "≈ Estimation (prix demandés, peu fiable)" : "Aucune donnée"],
            ["Médiane", formatPrice(card.median_price)],
            [card.price_source?.startsWith("sales") ? "Min / Max enchères" : "Min / Max", `${formatPrice(card.min_price)} / ${formatPrice(card.max_price)}`],
            ["Dernier prix", formatPrice(card.last_price)],
            ["Enchères / ventes listées", salesCountCell(card)],
            ["Demande (offres en cours)", card.demand || '<span class="muted">–</span>'],
            ["Tendance", trendIcon(card.trend)],
            ["Score", `<b>${formatPrice(card.profit_score)}</b>`],
            ...(card.price_note ? [["Note", `<span class="${card.price_note.includes("échouée") ? "error" : "muted"}">${escapeHtml(card.price_note)}</span>`]] : []),
            ...(card.wiki_category ? [["Catégorie Wiki", escapeHtml(card.wiki_category)]] : []),
            ...(card.atk || card.def ? [["ATK / DEF", `${card.atk} / ${card.def}`]] : []),
            ...(card.q_score ? [["Q-score", card.q_score]] : []),
            ...(card.pageviews ? [["Vues Wikipédia", Number(card.pageviews).toLocaleString("fr-FR")]] : []),
            ...(card.wikipedia_url ? [["Wikipédia", `<a href="${escapeHtml(card.wikipedia_url)}" target="_blank" rel="noopener">Ouvrir ↗</a>`]] : []),
        ].map(([label, value]) => `<div><small>${label}</small>${value}</div>`).join("");

        const history = card.sales_history || [];
        $("history-empty").classList.toggle("hidden", history.length > 0);
        $("history-empty").textContent = appStatus.has_sales
            ? "Pas encore d'historique de prix pour cette carte."
            : "Historique des prix non disponible : l'endpoint des ventes WikiMasters n'est pas encore configuré.";
        $("history-chart").parentElement.classList.toggle("hidden", history.length === 0);
        charts.history?.destroy();
        if (history.length) {
            charts.history = new Chart($("history-chart"), {
                type: "line",
                data: {
                    labels: history.map((s) => (s.date || "").slice(0, 10)),
                    datasets: [{
                        label: "Prix des enchères",
                        data: history.map((s) => s.price),
                        borderColor: "#7c5cff",
                        backgroundColor: "rgba(124,92,255,.15)",
                        fill: true,
                        tension: 0.3,
                    }],
                },
                options: chartOptions({ scales: true }),
            });
        }

        $("modal-related").innerHTML = '<span class="muted">Chargement…</span>';
        $("modal").classList.remove("hidden");

        const related = await api(`/api/related/${encodeURIComponent(card.title)}`);
        $("modal-related").innerHTML = related.cards.length
            ? related.cards.map((c) => `<span class="related-chip" data-detail="${escapeHtml(c.id)}">
                    ${escapeHtml(c.title)} · ${formatPrice(c.avg_price)}</span>`).join("")
            : '<span class="muted">Aucune carte liée.</span>';
    } catch (err) {
        handleError(err);
    }
}

function closeModal() {
    $("modal").classList.add("hidden");
}

// ---------- Événements ----------

document.addEventListener("DOMContentLoaded", () => {
    $("tabs").addEventListener("click", (e) => {
        const tab = e.target.closest(".tab");
        if (tab) switchTab(tab.dataset.tab);
    });

    $("login-btn").addEventListener("click", login);
    $("login-password").addEventListener("keydown", (e) => { if (e.key === "Enter") login(); });
    $("logout-btn").addEventListener("click", logout);
    $("refresh-btn").addEventListener("click", () => loadAll(true));

    ["sell-rarity", "sell-min-price", "sell-max-price"].forEach((id) =>
        $(id).addEventListener("input", filterBestSellers));

    const resetAndRender = () => { currentPage = 1; loadCollection(); };
    ["collection-search", "collection-rarity", "collection-category", "collection-sort", "page-size"]
        .forEach((id) => $(id).addEventListener("input", resetAndRender));
    $("collection-order").addEventListener("click", () => {
        sortOrder *= -1;
        $("collection-order").textContent = sortOrder === 1 ? "↑" : "↓";
        loadCollection();
    });
    $("page-prev").addEventListener("click", () => { currentPage--; loadCollection(); });
    $("page-next").addEventListener("click", () => { currentPage++; loadCollection(); });

    $("save-settings").addEventListener("click", saveSettings);
    $("clear-cache").addEventListener("click", async () => {
        await api("/api/cache/clear", { method: "POST" });
        appStatus = await api("/api/status");
        renderInfo();
        loadAll(true);
    });

    // Boutons "Détails" (délégation)
    document.body.addEventListener("click", (e) => {
        const target = e.target.closest("[data-detail]");
        if (target) showDetails(target.dataset.detail);
    });
    $("modal-close").addEventListener("click", closeModal);
    $("debug-btn").addEventListener("click", showDebug);
    $("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

    checkStatus().catch(handleError);
});
