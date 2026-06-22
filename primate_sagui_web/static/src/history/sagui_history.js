/** @odoo-module **/
// DASHBOARD de Historial — nivel PREMIUM (craft visual, marca Sagui). Los datos vienen de
// primate.ai.action.dashboard_data (agregaciones server-side). Chart.js del bundle lazy del core
// (web.chartjs_lib). Charts con tooltips navy custom, paleta por modelo CONSISTENTE, doughnut con
// total al centro, barras horizontales (la más cara en naranja) con drill-down, KPIs con count-up
// y delta vs período previo, barra de presupuesto, skeletons en loading. Respeta reduced-motion.
import { Component, useState, useRef, onWillStart, onWillUnmount, useEffect } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { loadBundle } from "@web/core/assets";

// ---- paleta de marca (coincide con sagui.scss) ----
const C = {
    navy: "#0F2B33", teal: "#48B3A6", tealDeep: "#1f6f64", orange: "#FF8A00",
    surface: "#F4F6F6", ink: "#0F2B33", muted: "#5b6b6e", line: "#dfe5e5", gray: "#9aa7ab",
};
// Serie categórica con SIGNIFICADO: teal primario + tints/navy secundarios. Naranja se reserva
// para "destacar" (más caro / over-budget); gris para "Otros"/desconocido.
const CAT = ["#48B3A6", "#1f6f64", "#0F2B33", "#7FC9BF", "#356E78", "#9AD5CC", "#2E7D74"];

// ---- plugins Chart.js (inline) ----
// Valor formateado al final de cada barra horizontal.
function barValuePlugin(fmt) {
    return {
        id: "barValues",
        afterDatasetsDraw(chart) {
            const { ctx } = chart;
            const meta = chart.getDatasetMeta(0);
            if (!meta || meta.hidden) { return; }
            ctx.save();
            ctx.font = "600 11px 'Inter', system-ui, sans-serif";
            ctx.fillStyle = C.ink;
            ctx.textBaseline = "middle";
            ctx.textAlign = "left";
            meta.data.forEach((bar, i) => {
                const v = chart.data.datasets[0].data[i];
                if (v === undefined || v === null) { return; }
                ctx.fillText(fmt(v), bar.x + 8, bar.y);
            });
            ctx.restore();
        },
    };
}
// Total al centro de un doughnut.
function centerTextPlugin(label, value) {
    return {
        id: "centerText",
        afterDraw(chart) {
            const a = chart.chartArea;
            if (!a) { return; }
            const cx = (a.left + a.right) / 2, cy = (a.top + a.bottom) / 2;
            const { ctx } = chart;
            ctx.save();
            ctx.textAlign = "center"; ctx.textBaseline = "middle";
            ctx.fillStyle = C.ink; ctx.font = "700 19px 'Inter', system-ui, sans-serif";
            ctx.fillText(value, cx, cy - 3);
            ctx.fillStyle = C.muted; ctx.font = "500 11px 'Inter', system-ui, sans-serif";
            ctx.fillText(label, cx, cy + 16);
            ctx.restore();
        },
    };
}

export class SaguiHistory extends Component {
    static template = "primate_sagui_web.SaguiHistory";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        this.charts = {};
        this.modelColors = {};
        this._raf = null;
        this.refs = {
            action: useRef("chartAction"),
            model: useRef("chartModel"),
            time: useRef("chartTime"),
            user: useRef("chartUser"),
            cache: useRef("chartCache"),
        };
        this.state = useState({
            loading: true,
            error: "",
            range: "30",
            data: null,
            prog: 0,           // 0..1 progreso del count-up de KPIs
            drill: null,
            drillLoading: false,
            budgetEdit: false,
            budgetInput: "",
        });
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        // Render de charts DESPUÉS del repintado (canvas en el DOM), cuando cambian datos/loading.
        useEffect(
            () => { this.renderAll(); return () => this.destroyCharts(); },
            () => [this.state.data, this.state.loading],
        );
        onWillUnmount(() => { if (this._raf) { cancelAnimationFrame(this._raf); } this.destroyCharts(); });
    }

    // ---------- datos ----------
    _dateFrom() {
        if (this.state.range === "all") { return false; }
        const days = parseInt(this.state.range, 10);
        const d = new Date();
        d.setDate(d.getDate() - days);
        return d.toISOString().slice(0, 10) + " 00:00:00";
    }

    async load() {
        this.state.loading = true;
        this.state.error = "";
        try {
            this.state.data = await this.orm.call(
                "primate.ai.action", "dashboard_data", [this._dateFrom(), false]);
            this._buildColors();
            this.startCountUp();
        } catch (e) {
            this.state.error = (e && e.data && e.data.message) || (e && e.message) || "No pude cargar el dashboard.";
        } finally {
            this.state.loading = false;
        }
    }

    async setRange(key) {
        if (this.state.range === key) { return; }
        this.state.range = key;
        this.state.drill = null;
        await this.load();
    }

    // ---------- count-up (KPIs) ----------
    startCountUp() {
        if (this._raf) { cancelAnimationFrame(this._raf); this._raf = null; }
        if (this.reduceMotion) { this.state.prog = 1; return; }
        this.state.prog = 0;
        const t0 = performance.now();
        const dur = 700;
        const step = (t) => {
            const p = Math.min(1, (t - t0) / dur);
            this.state.prog = 1 - Math.pow(1 - p, 3);   // easeOutCubic
            if (p < 1) { this._raf = requestAnimationFrame(step); } else { this._raf = null; }
        };
        this._raf = requestAnimationFrame(step);
    }

    // ---------- color por modelo (CONSISTENTE entre charts) ----------
    _buildColors() {
        this.modelColors = {};
        const rows = (this.state.data && this.state.data.by_model) || [];
        rows.forEach((r, i) => {
            const m = (r.model || "").toLowerCase();
            this.modelColors[r.model] = (r.model === "—" || m.includes("otros")) ? C.gray : CAT[i % CAT.length];
        });
    }
    modelColor(m) { return this.modelColors[m] || C.gray; }

    // ---------- formato ----------
    money(v) { return "$" + (Number(v) || 0).toFixed(4); }
    money2(v) { return "$" + (Number(v) || 0).toFixed(2); }
    tokens(v) {
        v = Number(v) || 0;
        if (v >= 1e6) { return (v / 1e6).toFixed(2) + "M"; }
        if (v >= 1e3) { return (v / 1e3).toFixed(1) + "k"; }
        return String(v);
    }
    pct(v) { return (Number(v) || 0).toFixed(0) + "%"; }
    get hasData() {
        const d = this.state.data;
        return d && (d.by_action.length || d.by_model.length || d.by_day.length);
    }

    // delta % vs período previo (para ▲/▼). null si no hay base de comparación.
    delta(cur, prev) {
        if (prev === undefined || prev === null || !prev) { return null; }
        return ((cur - prev) / prev) * 100;
    }
    get costDelta() {
        const d = this.state.data;
        return d && d.prev ? this.delta(d.totals.cost, d.prev.cost) : null;
    }
    get costDeltaUp() { return this.costDelta > 0; }
    get costDeltaAbs() { return Math.abs(this.costDelta || 0).toFixed(0); }

    // KPIs con count-up aplicado (evita usar Math.* dentro del template Owl)
    get kCost() { return this.money2(this.state.data.totals.cost * this.state.prog); }
    get kIn() { return this.tokens(Math.round(this.state.data.totals.input_tokens * this.state.prog)); }
    get kOut() { return this.tokens(Math.round(this.state.data.totals.output_tokens * this.state.prog)); }
    get kSaving() { return this.money2(this.state.data.cache.saving * this.state.prog); }
    get kActions() { return Math.round(this.state.data.totals.actions * this.state.prog); }

    // ---------- legends HTML (cuadraditos + %) ----------
    get modelLegend() {
        const rows = (this.state.data && this.state.data.by_model) || [];
        const total = rows.reduce((s, r) => s + r.cost, 0) || 1;
        return rows.map((r) => ({
            label: r.model, color: this.modelColor(r.model),
            pct: ((r.cost / total) * 100).toFixed(0),
        }));
    }

    // ---------- presupuesto ----------
    get budgetPct() {
        const b = this.state.data && this.state.data.budget;
        if (!b || !b.limit) { return 0; }
        return Math.min(100, (b.spent / b.limit) * 100);
    }
    get budgetOver() {
        const b = this.state.data && this.state.data.budget;
        return b && b.limit && b.spent > b.limit;
    }
    openBudgetEdit() {
        const b = this.state.data && this.state.data.budget;
        this.state.budgetInput = b && b.limit ? String(b.limit) : "";
        this.state.budgetEdit = true;
    }
    async saveBudget() {
        const v = parseFloat(this.state.budgetInput) || 0;
        try {
            const r = await this.orm.call("primate.ai.action", "set_monthly_budget", [v]);
            if (r.ok && this.state.data) { this.state.data.budget = { ...this.state.data.budget, limit: r.limit }; }
            this.state.budgetEdit = false;
        } catch (e) {
            this.state.error = (e && e.data && e.data.message) || "No pude guardar el presupuesto.";
            this.state.budgetEdit = false;
        }
    }

    // ---------- Chart.js ----------
    destroyCharts() {
        for (const k of Object.keys(this.charts)) {
            try { this.charts[k].destroy(); } catch (e) { /* noop */ }
        }
        this.charts = {};
    }

    _tt(extra) {
        // tooltip navy custom (nada del gris default)
        return Object.assign({
            enabled: true, backgroundColor: C.navy, titleColor: "#ffffff", bodyColor: "#EAF2F1",
            borderColor: "rgba(72,179,166,0.45)", borderWidth: 1, padding: 12, cornerRadius: 10,
            displayColors: false, titleFont: { weight: "600", size: 12 }, bodyFont: { size: 12 },
            bodySpacing: 4, caretSize: 6,
        }, extra || {});
    }
    _anim(stagger) {
        if (this.reduceMotion) { return false; }
        const a = { duration: 700, easing: "easeOutQuart" };
        if (stagger) {
            a.delay = (ctx) => (ctx.type === "data" && ctx.mode === "default" ? ctx.dataIndex * 55 : 0);
        }
        return a;
    }

    renderAll() {
        this.destroyCharts();
        if (!window.Chart || !this.hasData || this.state.loading) { return; }
        this.renderActionChart();
        this.renderModelChart();
        this.renderTimeChart();
        this.renderUserChart();
        this.renderCacheChart();
    }

    // Principal: COSTO POR ACCIÓN (barras horizontales desc, la más cara en NARANJA, click=drill).
    renderActionChart() {
        const el = this.refs.action.el;
        const rows = (this.state.data.by_action || []).slice(0, 12);
        if (!el || !rows.length) { return; }
        const ell = (s) => (s && s.length > 28 ? s.slice(0, 27) + "…" : s);
        this.charts.action = new window.Chart(el, {
            type: "bar",
            data: {
                labels: rows.map((r) => ell(r.label || r.action_type_label)),
                datasets: [{
                    data: rows.map((r) => r.cost),
                    backgroundColor: rows.map((_r, i) => (i === 0 ? C.orange : C.teal)),
                    hoverBackgroundColor: rows.map((_r, i) => (i === 0 ? "#ff9e2e" : "#5ec4b7")),
                    borderRadius: 7, borderSkipped: false, maxBarThickness: 24,
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false, animation: this._anim(true),
                layout: { padding: { right: 56 } },
                onHover: (evt, els) => { el.style.cursor = els.length ? "pointer" : "default"; },
                onClick: (evt, els) => { if (els.length) { this.openDrill(rows[els[0].index]); } },
                scales: {
                    x: { ticks: { color: C.muted, callback: (v) => "$" + v }, grid: { color: C.line }, border: { display: false } },
                    y: { ticks: { color: C.ink, font: { size: 11 } }, grid: { display: false }, border: { display: false } },
                },
                plugins: {
                    legend: { display: false },
                    tooltip: this._tt({
                        callbacks: {
                            title: (items) => rows[items[0].dataIndex].label,
                            label: (ctx) => {
                                const r = rows[ctx.dataIndex];
                                return [` ${this.money(r.cost)}`,
                                    ` ${this.tokens(r.input_tokens)} in · ${this.tokens(r.output_tokens)} out`,
                                    ` ${r.calls} llamada(s) · ${r.user}`,
                                    " click para ver el detalle"];
                            },
                        },
                    }),
                },
            },
            plugins: [barValuePlugin((v) => this.money2(v))],
        });
    }

    renderModelChart() {
        const el = this.refs.model.el;
        const rows = this.state.data.by_model || [];
        if (!el || !rows.length) { return; }
        const total = rows.reduce((s, r) => s + r.cost, 0);
        this.charts.model = new window.Chart(el, {
            type: "doughnut",
            data: {
                labels: rows.map((r) => r.model),
                datasets: [{
                    data: rows.map((r) => r.cost),
                    backgroundColor: rows.map((r) => this.modelColor(r.model)),
                    borderColor: "#fff", borderWidth: 3, hoverOffset: 6,
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false, cutout: "68%",
                animation: this.reduceMotion ? false : { animateRotate: true, duration: 800 },
                plugins: {
                    legend: { display: false },
                    tooltip: this._tt({
                        callbacks: {
                            label: (ctx) => ` ${ctx.label}: ${this.money2(ctx.parsed)} (${((ctx.parsed / total) * 100).toFixed(0)}%)`,
                        },
                    }),
                },
            },
            plugins: [centerTextPlugin("total", this.money2(total))],
        });
    }

    renderTimeChart() {
        const el = this.refs.time.el;
        const rows = this.state.data.by_day || [];
        if (!el || !rows.length) { return; }
        const ctx = el.getContext("2d");
        const grad = ctx.createLinearGradient(0, 0, 0, el.height || 220);
        grad.addColorStop(0, "rgba(72,179,166,0.28)");
        grad.addColorStop(1, "rgba(72,179,166,0.02)");
        this.charts.time = new window.Chart(el, {
            type: "line",
            data: {
                labels: rows.map((r) => r.day.slice(5)),   // MM-DD
                datasets: [{
                    data: rows.map((r) => r.cost),
                    borderColor: C.teal, backgroundColor: grad, fill: true, tension: 0.35,
                    borderWidth: 2, pointRadius: 0, pointHoverRadius: 5,
                    pointHoverBackgroundColor: C.tealDeep, pointHoverBorderColor: "#fff", pointHoverBorderWidth: 2,
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false, animation: this._anim(false),
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: this._tt({
                        callbacks: { title: (i) => rows[i[0].dataIndex].day, label: (c) => ` ${this.money(c.parsed.y)}` },
                    }),
                },
                scales: {
                    x: { ticks: { color: C.muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 }, grid: { display: false }, border: { display: false } },
                    y: { ticks: { color: C.muted, callback: (v) => "$" + v }, grid: { color: C.line }, border: { display: false } },
                },
            },
        });
    }

    renderUserChart() {
        const el = this.refs.user.el;
        const rows = this.state.data.by_user || [];
        if (!el || !rows.length) { return; }
        this.charts.user = new window.Chart(el, {
            type: "bar",
            data: {
                labels: rows.map((r) => r.user),
                datasets: [{
                    data: rows.map((r) => r.cost),
                    backgroundColor: rows.map((_r, i) => CAT[i % CAT.length]),
                    hoverBackgroundColor: "#5ec4b7",
                    borderRadius: 7, borderSkipped: false, maxBarThickness: 46,
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false, animation: this._anim(true),
                plugins: {
                    legend: { display: false },
                    tooltip: this._tt({ callbacks: { label: (c) => ` ${this.money2(c.parsed.y)}` } }),
                },
                scales: {
                    x: { ticks: { color: C.ink, font: { size: 11 } }, grid: { display: false }, border: { display: false } },
                    y: { ticks: { color: C.muted, callback: (v) => "$" + v }, grid: { color: C.line }, border: { display: false } },
                },
            },
        });
    }

    renderCacheChart() {
        const el = this.refs.cache.el;
        const c = this.state.data.cache;
        if (!el || !c || (!c.saving && !c.cache_read_cost)) { return; }
        this.charts.cache = new window.Chart(el, {
            type: "bar",
            data: {
                labels: ["Caché"],
                datasets: [
                    { label: "Pagado (lectura)", data: [c.cache_read_cost], backgroundColor: C.muted, borderRadius: 6, maxBarThickness: 46 },
                    { label: "Ahorrado", data: [c.saving], backgroundColor: C.teal, borderRadius: 6, maxBarThickness: 46 },
                ],
            },
            options: {
                responsive: true, maintainAspectRatio: false, indexAxis: "y", animation: this._anim(false),
                plugins: {
                    legend: { position: "bottom", labels: { color: C.ink, boxWidth: 10, boxHeight: 10, font: { size: 11 } } },
                    tooltip: this._tt({ callbacks: { label: (c2) => ` ${c2.dataset.label}: ${this.money2(c2.parsed.x)}` } }),
                },
                scales: {
                    x: { stacked: true, ticks: { color: C.muted, callback: (v) => "$" + v }, grid: { color: C.line }, border: { display: false } },
                    y: { stacked: true, grid: { display: false }, ticks: { color: C.ink }, border: { display: false } },
                },
            },
        });
    }

    // ---------- drill-down (panel desde la derecha) ----------
    async openDrill(actionRow) {
        this.state.drillLoading = true;
        this.state.drill = { label: actionRow.label, action_type_label: actionRow.action_type_label, ops: [] };
        try {
            const r = await this.orm.call("primate.ai.action", "action_operations", [actionRow.id]);
            this.state.drill = r.ok ? r : { label: actionRow.label, error: r.error, ops: [] };
        } catch (e) {
            this.state.drill = { label: actionRow.label, error: (e && e.message) || "Error", ops: [] };
        } finally {
            this.state.drillLoading = false;
        }
    }
    closeDrill() { this.state.drill = null; }
    opColor(model) { return this.modelColor(model); }
}
