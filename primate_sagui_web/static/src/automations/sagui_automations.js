/** @odoo-module **/
// Sección "Automatizaciones".
//   FASE 1: digests/alertas read-only.
//   FASE 2: acciones que escriben, seguras — modo (readonly/propose/auto) + scope + INBOX de
//   aprobación + dry-run + kill switch. La arquitectura ES el modelo de seguridad: las escrituras
//   se interceptan en el backend (auto solo dentro de scope; resto a la bandeja). Acá solo UI.
import { Component, useState, onWillStart, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

const STATUS = {
    ok: { label: "Reportó", cls: "o-ok" }, nothing: { label: "Sin novedad", cls: "o-muted" },
    error: { label: "Error", cls: "o-err" }, paused: { label: "Pausada", cls: "o-warn" },
};
const MODE = {
    readonly: { label: "Solo lectura", cls: "o-ro" },
    propose: { label: "Proponer", cls: "o-prop" },
    auto: { label: "Auto (scope)", cls: "o-auto" },
};
const EMPTY = {
    name: "", instruction: "", schedule_type: "daily", time: "08:00", weekday: "0",
    delivery_discuss: true, delivery_inapp: true,
    mode: "readonly", allowed_models: "", op_create: true, op_write: true,
    field_whitelist: "", record_domain: "", max_records_per_run: 10, allow_unlink: false,
};

export class SaguiAutomations extends Component {
    static template = "primate_sagui_web.SaguiAutomations";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this._alive = true;
        this.state = useState({
            tab: "list",                 // "list" | "inbox"
            list: [], inbox: [], loading: true, inboxLoading: false, error: "",
            editing: null, form: { ...EMPTY }, saving: false,
            running: null, result: null,
            sim: null, simLoading: false,
            killed: false, isManager: false,
        });
        onWillUnmount(() => { this._alive = false; });
        onWillStart(async () => {
            await Promise.all([this.load(), this.loadKill()]);
            await this.loadInbox();
        });
    }

    _err(e, fb) { return (e && e.data && e.data.message) || (e && e.message) || fb; }

    async load() {
        this.state.loading = true;
        try {
            const list = await this.orm.call("sagui.automation", "web_list", []);
            if (this._alive) { this.state.list = list; }
        } catch (e) {
            if (this._alive) { this.state.error = this._err(e, "No pude cargar las automatizaciones."); }
        } finally {
            if (this._alive) { this.state.loading = false; }
        }
    }

    async loadInbox() {
        this.state.inboxLoading = true;
        try {
            const inbox = await this.orm.call("sagui.automation.proposal", "web_inbox", []);
            if (this._alive) { this.state.inbox = inbox; }
        } catch (e) {
            if (this._alive) { this.state.error = this._err(e, "No pude cargar la bandeja."); }
        } finally {
            if (this._alive) { this.state.inboxLoading = false; }
        }
    }

    async loadKill() {
        try {
            const k = await this.orm.call("sagui.automation", "web_kill_state", []);
            if (this._alive) { this.state.killed = k.paused; this.state.isManager = k.is_manager; }
        } catch (e) { /* noop */ }
    }

    get inboxCount() { return this.state.inbox.reduce((n, g) => n + g.items.length, 0); }

    setTab(t) { this.state.tab = t; if (t === "inbox") { this.loadInbox(); } }

    // ---- formato ----
    floatToTime(f) {
        const h = Math.floor(f || 0), m = Math.round(((f || 0) - Math.floor(f || 0)) * 60);
        return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0");
    }
    timeToFloat(s) { const [h, m] = String(s || "0:0").split(":").map((x) => parseInt(x, 10) || 0); return h + m / 60; }
    statusOf(s) { return STATUS[s] || null; }
    modeOf(m) { return MODE[m] || null; }
    fmtDate(d) { return d ? String(d).slice(0, 16).replace("T", " ") : "—"; }
    scheduleText(a) {
        if (a.schedule_type === "hourly") { return "Cada hora"; }
        const t = this.floatToTime(a.time_of_day);
        if (a.schedule_type === "daily") { return `Diaria · ${t}`; }
        const days = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];
        return `Semanal · ${days[parseInt(a.weekday, 10) || 0]} ${t}`;
    }
    // antes->después legible para un ítem del inbox
    diffLines(item) {
        const out = [];
        const before = {};
        (item.before || []).forEach((r) => { before[r.id] = r; });
        if (item.operation === "create") {
            for (const [k, v] of Object.entries(item.values || {})) { out.push({ field: k, from: "—", to: String(v) }); }
        } else if (item.operation === "unlink") {
            (item.before || []).forEach((r) => out.push({ field: "eliminar", from: r.display_name || ("#" + r.id), to: "—" }));
        } else {
            for (const [k, v] of Object.entries(item.values || {})) {
                const froms = (item.before || []).map((r) => String(r[k])).join(", ") || "—";
                out.push({ field: k, from: froms, to: String(v) });
            }
        }
        return out;
    }

    // ---- editor ----
    openNew() { this.state.form = { ...EMPTY }; this.state.editing = "new"; }
    openEdit(a) {
        const ops = (a.allowed_operations || "").split(",").map((x) => x.trim());
        this.state.form = {
            name: a.name, instruction: a.instruction, schedule_type: a.schedule_type,
            time: this.floatToTime(a.time_of_day), weekday: a.weekday || "0",
            delivery_discuss: a.delivery_discuss, delivery_inapp: a.delivery_inapp,
            mode: a.mode || "readonly", allowed_models: a.allowed_models || "",
            op_create: ops.includes("create"), op_write: ops.includes("write"),
            field_whitelist: a.field_whitelist || "", record_domain: a.record_domain || "",
            max_records_per_run: a.max_records_per_run || 10, allow_unlink: a.allow_unlink,
        };
        this.state.editing = a.id;
    }
    cancelEdit() { this.state.editing = null; }
    get formValid() { return this.state.form.name.trim() && this.state.form.instruction.trim(); }

    _formVals() {
        const f = this.state.form;
        const ops = [];
        if (f.op_create) { ops.push("create"); }
        if (f.op_write) { ops.push("write"); }
        return {
            name: f.name.trim(), instruction: f.instruction.trim(), schedule_type: f.schedule_type,
            time_of_day: this.timeToFloat(f.time), weekday: f.weekday,
            delivery_discuss: f.delivery_discuss, delivery_inapp: f.delivery_inapp,
            mode: f.mode, allowed_models: f.allowed_models.trim(), allowed_operations: ops.join(","),
            field_whitelist: f.field_whitelist.trim(), record_domain: f.record_domain.trim(),
            max_records_per_run: parseInt(f.max_records_per_run, 10) || 0, allow_unlink: f.allow_unlink,
        };
    }

    async save() {
        if (!this.formValid || this.state.saving) { return; }
        this.state.saving = true;
        try {
            const id = this.state.editing === "new" ? null : this.state.editing;
            await this.orm.call("sagui.automation", "web_save", [this._formVals(), id]);
            await this.load();
            if (this._alive) { this.state.editing = null; }
        } catch (e) {
            if (this._alive) { this.state.error = this._err(e, "No pude guardar."); }
        } finally {
            if (this._alive) { this.state.saving = false; }
        }
    }

    async toggle(a) {
        try { await this.orm.call("sagui.automation", "web_toggle", [a.id, !a.active]); await this.load(); }
        catch (e) { if (this._alive) { this.state.error = this._err(e, "No pude cambiar el estado."); } }
    }

    remove(a) {
        this.dialog.add(ConfirmationDialog, {
            title: "Borrar automatización", body: `¿Seguro que querés borrar «${a.name}»?`,
            confirmLabel: "Borrar",
            confirm: async () => { await this.orm.call("sagui.automation", "web_delete", [a.id]); await this.load(); },
            cancel: () => {},
        });
    }

    // ---- Probar ahora / Simular (usan TUS tokens; son acciones explícitas) ----
    async runNow(a) {
        if (this.state.running) { return; }
        this.state.running = a.id; this.state.result = null;
        try {
            const r = await this.orm.call("sagui.automation", "web_run_now", [a.id]);
            if (!this._alive) { return; }
            this.state.result = { name: a.name, status: r.status, output: r.output, applied: r.applied || [], proposed: r.proposed || [] };
            await Promise.all([this.load(), this.loadInbox()]);
        } catch (e) {
            if (this._alive) { this.state.result = { name: a.name, status: "error", output: this._err(e, "Falló la prueba."), applied: [], proposed: [] }; }
        } finally {
            if (this._alive) { this.state.running = null; }
        }
    }
    dismissResult() { this.state.result = null; }

    async dryRun(a) {
        if (this.state.simLoading) { return; }
        this.state.simLoading = true; this.state.sim = null;
        try {
            const r = await this.orm.call("sagui.automation", "web_dry_run", [a.id]);
            if (this._alive) { this.state.sim = { name: a.name, status: r.status, output: r.output, simulated: r.simulated || [] }; }
        } catch (e) {
            if (this._alive) { this.state.sim = { name: a.name, status: "error", output: this._err(e, "Falló la simulación."), simulated: [] }; }
        } finally {
            if (this._alive) { this.state.simLoading = false; }
        }
    }
    dismissSim() { this.state.sim = null; }

    // ---- inbox: aprobar / rechazar ----
    async approve(ids) {
        try { await this.orm.call("sagui.automation.proposal", "web_approve", [ids]); await Promise.all([this.loadInbox(), this.load()]); }
        catch (e) { if (this._alive) { this.state.error = this._err(e, "No pude aprobar."); } }
    }
    async reject(ids) {
        try { await this.orm.call("sagui.automation.proposal", "web_reject", [ids]); await Promise.all([this.loadInbox(), this.load()]); }
        catch (e) { if (this._alive) { this.state.error = this._err(e, "No pude rechazar."); } }
    }
    approveGroup(g) { this.approve(g.items.map((i) => i.id)); }
    rejectGroup(g) { this.reject(g.items.map((i) => i.id)); }

    // ---- kill switch global (manager) ----
    async toggleKill() {
        const next = !this.state.killed;
        try {
            await this.orm.call("sagui.automation", "web_kill_all", [next]);
            if (this._alive) { this.state.killed = next; }
        } catch (e) {
            if (this._alive) { this.state.error = this._err(e, "No pude cambiar el kill switch."); }
        }
    }
}
