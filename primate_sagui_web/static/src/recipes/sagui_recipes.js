/** @odoo-module **/
// Sección "Recetas": tareas guardadas, parametrizadas y reutilizables. Comparten core con
// Automatizaciones (mismo mode/scope/run-loop, en el backend). On-demand: elegir receta -> form de
// params -> Simular (dry-run) -> Ejecutar. Escrituras fuera de scope = confirmación interactiva
// (pending.write). Puente "Programar" crea una automatización a partir de la receta.
import { Component, useState, onWillStart, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

const MODE = {
    readonly: { label: "Solo lectura", cls: "o-ro" },
    propose: { label: "Confirmar", cls: "o-prop" },
    auto: { label: "Auto (scope)", cls: "o-auto" },
};
const EMPTY_FORM = {
    name: "", description: "", category: "", tags: "", instruction: "",
    mode: "readonly", allowed_models: "", op_create: true, op_write: true,
    field_whitelist: "", record_domain: "", max_records_per_run: 10, allow_unlink: false,
    output_type: "chat", shared: false, params: [],
};
const EMPTY_PARAM = { name: "", label: "", ptype: "text", required: false, default: "", selection_options: "", relation: "" };

export class SaguiRecipes extends Component {
    static template = "primate_sagui_web.SaguiRecipes";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this._alive = true;
        this.state = useState({
            list: [], categories: [], loading: true, error: "",
            search: "", filterCat: "", onlyFav: false,
            editing: null, form: { ...EMPTY_FORM }, saving: false,
            running: null, runForm: {}, runResult: null, runLoading: false, runMode: "",
            scheduling: null, schedForm: { schedule_type: "daily", time: "08:00", weekday: "0" },
        });
        onWillUnmount(() => { this._alive = false; });
        onWillStart(async () => { await Promise.all([this.load(), this.loadCats()]); });
    }

    _err(e, fb) { return (e && e.data && e.data.message) || (e && e.message) || fb; }
    modeOf(m) { return MODE[m] || null; }

    async load() {
        this.state.loading = true;
        try {
            const list = await this.orm.call("sagui.recipe", "web_list", []);
            if (this._alive) { this.state.list = list; }
        } catch (e) {
            if (this._alive) { this.state.error = this._err(e, "No pude cargar las recetas."); }
        } finally {
            if (this._alive) { this.state.loading = false; }
        }
    }
    async loadCats() {
        try { const c = await this.orm.call("sagui.recipe", "web_categories", []); if (this._alive) { this.state.categories = c; } }
        catch (e) { /* noop */ }
    }

    get filtered() {
        const q = this.state.search.trim().toLowerCase();
        return this.state.list.filter((r) => {
            if (this.state.onlyFav && !r.favorite) { return false; }
            if (this.state.filterCat && r.category !== this.state.filterCat) { return false; }
            if (q && !(`${r.name} ${r.description} ${r.tags}`.toLowerCase().includes(q))) { return false; }
            return true;
        });
    }
    tagList(r) { return (r.tags || "").split(",").map((t) => t.trim()).filter(Boolean); }

    // ---- editor ----
    openNew() { this.state.form = JSON.parse(JSON.stringify(EMPTY_FORM)); this.state.editing = "new"; }
    async openEdit(r) {
        try {
            const d = await this.orm.call("sagui.recipe", "web_get", [r.id]);
            const ops = (d.allowed_operations || "").split(",").map((x) => x.trim());
            this.state.form = {
                name: d.name, description: d.description, category: d.category, tags: d.tags,
                instruction: d.instruction, mode: d.mode, allowed_models: d.allowed_models,
                op_create: ops.includes("create"), op_write: ops.includes("write"),
                field_whitelist: d.field_whitelist, record_domain: d.record_domain,
                max_records_per_run: d.max_records_per_run, allow_unlink: d.allow_unlink,
                output_type: d.output_type, shared: d.shared,
                params: d.params.map((p) => ({ ...EMPTY_PARAM, ...p })),
            };
            this.state.editing = r.id;
        } catch (e) {
            this.state.error = this._err(e, "No pude abrir la receta.");
        }
    }
    cancelEdit() { this.state.editing = null; }
    addParam() { this.state.form.params.push({ ...EMPTY_PARAM }); }
    removeParam(i) { this.state.form.params.splice(i, 1); }
    get formValid() { return this.state.form.name.trim() && this.state.form.instruction.trim(); }

    async save() {
        if (!this.formValid || this.state.saving) { return; }
        this.state.saving = true;
        const f = this.state.form;
        const ops = [];
        if (f.op_create) { ops.push("create"); }
        if (f.op_write) { ops.push("write"); }
        const vals = {
            name: f.name.trim(), description: f.description, category: f.category.trim(), tags: f.tags,
            instruction: f.instruction, mode: f.mode, allowed_models: f.allowed_models.trim(),
            allowed_operations: ops.join(","), field_whitelist: f.field_whitelist.trim(),
            record_domain: f.record_domain.trim(), max_records_per_run: parseInt(f.max_records_per_run, 10) || 0,
            allow_unlink: f.allow_unlink, output_type: f.output_type, shared: f.shared,
            params: f.params.filter((p) => p.name.trim()).map((p) => ({
                name: p.name.trim(), label: p.label, ptype: p.ptype, required: p.required,
                default: p.default, selection_options: p.selection_options, relation: p.relation,
            })),
        };
        try {
            const id = this.state.editing === "new" ? null : this.state.editing;
            await this.orm.call("sagui.recipe", "web_save", [vals, id]);
            await Promise.all([this.load(), this.loadCats()]);
            if (this._alive) { this.state.editing = null; }
        } catch (e) {
            if (this._alive) { this.state.error = this._err(e, "No pude guardar la receta."); }
        } finally {
            if (this._alive) { this.state.saving = false; }
        }
    }

    remove(r) {
        this.dialog.add(ConfirmationDialog, {
            title: "Borrar receta", body: `¿Seguro que querés borrar «${r.name}»?`, confirmLabel: "Borrar",
            confirm: async () => { await this.orm.call("sagui.recipe", "web_delete", [r.id]); await this.load(); },
            cancel: () => {},
        });
    }
    async toggleFav(r) {
        try { await this.orm.call("sagui.recipe", "web_toggle_favorite", [r.id]); await this.load(); }
        catch (e) { /* noop */ }
    }

    // ---- runner ----
    async openRun(r) {
        try {
            const d = await this.orm.call("sagui.recipe", "web_get", [r.id]);
            const rf = {};
            d.params.forEach((p) => { rf[p.name] = p.default || ""; });
            this.state.running = d;
            this.state.runForm = rf;
            this.state.runResult = null;
        } catch (e) {
            this.state.error = this._err(e, "No pude abrir la receta.");
        }
    }
    closeRun() { this.state.running = null; this.state.runResult = null; }
    get runValid() {
        return (this.state.running.params || []).every((p) => !p.required || String(this.state.runForm[p.name] || "").trim());
    }
    selOptions(p) { return (p.selection_options || "").split(",").map((x) => x.trim()).filter(Boolean); }

    async _runCall(method, label) {
        if (this.state.runLoading) { return; }
        this.state.runLoading = true; this.state.runMode = label; this.state.runResult = null;
        try {
            const r = await this.orm.call("sagui.recipe", method, [this.state.running.id, this.state.runForm]);
            if (!this._alive) { return; }
            // marcá cada pending con su estado local para los botones
            (r.pendings || []).forEach((p) => { p.state = "pending"; });
            this.state.runResult = { ...r, mode: label };
        } catch (e) {
            if (this._alive) { this.state.runResult = { status: "error", output: this._err(e, "Falló."), pendings: [], mode: label }; }
        } finally {
            if (this._alive) { this.state.runLoading = false; }
        }
    }
    simulate() { return this._runCall("web_dry_run", "sim"); }
    execute() { return this._runCall("web_run", "run"); }

    async confirmPending(p) {
        try {
            const r = await this.orm.call("sagui.recipe", "web_confirm_pending", [p.token]);
            p.state = r.ok ? "done" : "error"; p.info = r.ok ? r.info : r.error;
        } catch (e) { p.state = "error"; p.info = this._err(e, "Error"); }
    }
    async cancelPending(p) {
        try { await this.orm.call("sagui.recipe", "web_cancel_pending", [p.token]); p.state = "cancelled"; }
        catch (e) { /* noop */ }
    }

    // ---- puente: programar ----
    openSchedule(r) {
        this.state.scheduling = this.state.running || r;
        this.state.schedForm = { schedule_type: "daily", time: "08:00", weekday: "0" };
    }
    closeSchedule() { this.state.scheduling = null; }
    timeToFloat(s) { const [h, m] = String(s || "0:0").split(":").map((x) => parseInt(x, 10) || 0); return h + m / 60; }
    async doSchedule() {
        const s = this.state.schedForm;
        const params = this.state.running && this.state.running.id === this.state.scheduling.id ? this.state.runForm : {};
        try {
            const r = await this.orm.call("sagui.recipe", "web_schedule", [
                this.state.scheduling.id, params,
                { schedule_type: s.schedule_type, time_of_day: this.timeToFloat(s.time), weekday: s.weekday },
            ]);
            this.state.scheduling = null;
            this.state.error = "";
            this.state.runResult = { status: "scheduled", output: `Programada como automatización «${r.name}». Vela en Automatizaciones.`, pendings: [], mode: "sched" };
        } catch (e) {
            this.state.error = this._err(e, "No pude programar.");
            this.state.scheduling = null;
        }
    }
}
