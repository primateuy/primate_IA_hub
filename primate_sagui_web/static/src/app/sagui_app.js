/** @odoo-module **/
// Client action de página completa: SHELL de Sagui (sidebar de marca + navegación interna Owl).
// [VERIFICADO v19] registry.category("actions").add(tag, Component) con standardActionServiceProps;
// navegación entre secciones por state (no recarga la acción); orm/action vía useService;
// nombre del usuario desde "@web/core/user". Íconos SVG inline (NUNCA emojis).
import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { SaguiConversations } from "@primate_sagui_web/conversations/sagui_conversations";
import { SaguiHistory } from "@primate_sagui_web/history/sagui_history";
import { SaguiAutomations } from "@primate_sagui_web/automations/sagui_automations";
import { SaguiRecipes } from "@primate_sagui_web/recipes/sagui_recipes";

// Íconos Heroicons (outline) como arrays de paths 'd' — el template los pinta con currentColor.
const ICONS = {
    inicio: ["m2.25 12 8.954-8.955c.44-.439 1.152-.439 1.591 0L21.75 12M4.5 9.75v10.125c0 " +
        ".621.504 1.125 1.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125h2.25c.621 0 " +
        "1.125.504 1.125 1.125V21h4.125c.621 0 1.125-.504 1.125-1.125V9.75M8.25 21h8.25"],
    conversaciones: ["M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.087.16 " +
        "2.185.283 3.293.369V21l4.184-4.183a1.14 1.14 0 0 1 .778-.332 48.294 48.294 0 0 0 " +
        "5.83-.498c1.585-.233 2.708-1.626 2.708-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 " +
        "48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z"],
    automatizaciones: ["m3.75 13.5 10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75Z"],
    recetas: ["M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 " +
        "6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 " +
        ".512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"],
    conectores: ["M14.25 6.087c0-.355.186-.676.401-.959.221-.29.349-.634.349-1.003 0-1.036-1.007-" +
        "1.875-2.25-1.875s-2.25.84-2.25 1.875c0 .369.128.713.349 1.003.215.283.401.604.401.959v0a.64.64 " +
        "0 0 1-.657.643 48.39 48.39 0 0 1-4.163-.3c.186 1.613.293 3.25.315 4.907a.656.656 0 0 1-.658.663v0c-" +
        ".355 0-.676-.186-.959-.401a1.647 1.647 0 0 0-1.003-.349c-1.036 0-1.875 1.007-1.875 2.25s.84 2.25 " +
        "1.875 2.25c.369 0 .713-.128 1.003-.349.283-.215.604-.401.959-.401v0c.31 0 .555.26.532.57a48.039 " +
        "48.039 0 0 1-.642 5.056c1.518.19 3.058.309 4.616.354a.64.64 0 0 0 .657-.643v0c0-.355-.186-.676-" +
        ".401-.959a1.647 1.647 0 0 1-.349-1.003c0-1.035 1.008-1.875 2.25-1.875 1.243 0 2.25.84 2.25 1.875 0 " +
        ".369-.128.713-.349 1.003-.215.283-.4.604-.4.959v0c0 .333.277.599.61.58a48.1 48.1 0 0 0 5.427-.63 " +
        "48.05 48.05 0 0 0 .582-4.717.532.532 0 0 0-.533-.57v0c-.355 0-.676.186-.959.401-.29.221-.634.349-" +
        "1.003.349-1.035 0-1.875-1.007-1.875-2.25s.84-2.25 1.875-2.25c.37 0 .713.128 1.003.349.283.215.604.401.96.401v0a.656.656 " +
        "0 0 0 .658-.663 48.422 48.422 0 0 0-.37-5.36c-1.886.342-3.81.574-5.766.689a.578.578 0 0 1-.61-.58v0Z"],
    historial: ["M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"],
    ajustes: ["M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.751-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.752.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z",
        "M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"],
};

const SECTIONS = [
    { key: "inicio", label: "Inicio", desc: "Inicio" },
    { key: "conversaciones", label: "Conversaciones", desc: "Chat con Sagui" },
    { key: "automatizaciones", label: "Automatizaciones", desc: "Flujos automáticos" },
    { key: "recetas", label: "Recetas", desc: "Tareas guardadas y reutilizables" },
    { key: "conectores", label: "Conectores", desc: "Conexión con la IA" },
    { key: "historial", label: "Historial", desc: "Uso y costos" },
    { key: "ajustes", label: "Ajustes", desc: "Configuración de Sagui" },
];

export class SaguiApp extends Component {
    static template = "primate_sagui_web.SaguiApp";
    static components = { SaguiConversations, SaguiHistory, SaguiAutomations, SaguiRecipes };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.userName = (user && user.name) || "";
        this.sections = SECTIONS;
        this.icons = ICONS;
        this.state = useState({
            section: "inicio",
            history: [],
            historyLoaded: false,
            historyError: "",
            // Config embebida en Ajustes (la API key es WRITE-ONLY: nunca se trae el valor real).
            settings: { model: "", model_fast: "", write_whitelist: "", api_key_set: false, can_edit: false },
            settingsLoaded: false,
            settingsSaving: false,
            settingsMsg: "",
            newApiKey: "",
            // Conectores MCP (sistemas externos): lista + borradores de credencial por conector.
            connectors: [],
            connectorsLoaded: false,
            connIsManager: false,
            connDrafts: {},   // { [connId]: { username, api_key, msg, busy, error } }
        });
    }

    get currentLabel() {
        return (this.sections.find((s) => s.key === this.state.section) || {}).label || "";
    }

    setSection(key) {
        this.state.section = key;
        // Historial: lo maneja el componente SaguiHistory (se autocarga al montarse).
        if ((key === "ajustes" || key === "conectores") && !this.state.settingsLoaded) {
            this.loadSettings();
        }
        if (key === "conectores" && !this.state.connectorsLoaded) {
            this.loadConnectors();
        }
    }

    // ---- Conectores MCP (sistemas externos): cada usuario pega SU credencial y la prueba ----
    async loadConnectors() {
        try {
            const r = await this.orm.call("sagui.connector", "web_connectors", []);
            this.state.connectors = r.connectors || [];
            this.state.connIsManager = !!r.is_manager;
            for (const c of this.state.connectors) {
                this.state.connDrafts[c.id] = { username: c.username || "", api_key: "", msg: "", busy: false, error: false };
            }
        } catch (e) {
            this.state.settingsMsg = this._errMsg(e, "No pude leer los conectores.");
        } finally {
            this.state.connectorsLoaded = true;
        }
    }

    // Agregar conector GitHub (manager): scaffolding con defaults de Docker (read-only).
    async addGithub() {
        try {
            await this.orm.call("sagui.connector", "web_add_github", ["GitHub"]);
            this.state.connectorsLoaded = false;
            await this.loadConnectors();
        } catch (e) {
            this.state.settingsMsg = this._errMsg(e, "No pude agregar GitHub.");
        }
    }

    // Habilitar/deshabilitar escrituras externas de un conector (manager).
    async toggleWrites(conn) {
        try {
            const r = await this.orm.call("sagui.connector", "web_set_writes", [conn.id, !conn.allow_writes]);
            conn.allow_writes = r.allow_writes;
            const d = this.draft(conn.id);
            d.msg = "Cambiá tu PAT y reconectá para refrescar las herramientas."; d.error = false;
        } catch (e) {
            const d = this.draft(conn.id);
            d.error = true; d.msg = this._errMsg(e, "No pude cambiar las escrituras.");
        }
    }

    draft(id) {
        if (!this.state.connDrafts[id]) {
            this.state.connDrafts[id] = { username: "", api_key: "", msg: "", busy: false, error: false };
        }
        return this.state.connDrafts[id];
    }

    async saveConnector(conn) {
        const d = this.draft(conn.id);
        if (d.busy) { return; }
        d.busy = true; d.msg = ""; d.error = false;
        try {
            const r = await this.orm.call("sagui.connector", "web_save_credential",
                [conn.id, d.username, d.api_key, true]);
            conn.api_key_set = !!r.api_key_set;
            conn.username = r.username || "";
            if (r.tools) { conn.tools = r.tools; }
            d.api_key = "";
            d.error = !r.ok;
            d.msg = r.ok
                ? (r.count !== undefined ? ("Conectado ✓ — " + r.count + " herramientas disponibles") : "Guardado ✓")
                : (r.error || "No pude conectar.");
        } catch (e) {
            d.error = true; d.msg = this._errMsg(e, "No pude guardar la credencial.");
        } finally {
            d.busy = false;
        }
    }

    async deleteConnector(conn) {
        const d = this.draft(conn.id);
        d.busy = true; d.msg = ""; d.error = false;
        try {
            await this.orm.call("sagui.connector", "web_delete_credential", [conn.id]);
            conn.api_key_set = false; conn.username = "";
            d.username = ""; d.api_key = ""; d.msg = "Credencial eliminada.";
        } catch (e) {
            d.error = true; d.msg = this._errMsg(e, "No pude eliminar la credencial.");
        } finally {
            d.busy = false;
        }
    }

    // ---- Ajustes / Conectores: config leída/escrita vía el conector (no abre la pantalla de Odoo) ----
    async loadSettings() {
        try {
            const s = await this.orm.call("primate.ai.connector", "get_sagui_settings", []);
            Object.assign(this.state.settings, s);
        } catch (e) {
            this.state.settingsMsg = this._errMsg(e, "No pude leer la configuración.");
        } finally {
            this.state.settingsLoaded = true;
        }
    }

    async saveSettings() {
        if (this.state.settingsSaving) { return; }
        this.state.settingsSaving = true;
        this.state.settingsMsg = "";
        try {
            const s = await this.orm.call("primate.ai.connector", "set_sagui_settings", [{
                model: this.state.settings.model,
                model_fast: this.state.settings.model_fast,
                write_whitelist: this.state.settings.write_whitelist,
                api_key: this.state.newApiKey,
            }]);
            Object.assign(this.state.settings, s);
            this.state.newApiKey = "";
            this.state.settingsMsg = "Guardado ✓";
        } catch (e) {
            this.state.settingsMsg = this._errMsg(e, "No pude guardar la configuración.");
        } finally {
            this.state.settingsSaving = false;
        }
    }

    _errMsg(e, fallback) {
        return (e && e.data && e.data.message) || (e && e.message) || fallback;
    }

    // Historial: lee el log de uso (primate.ai.usage.log; el usuario tiene permiso de lectura).
    async loadHistory() {
        try {
            this.state.history = await this.orm.searchRead(
                "primate.ai.usage.log",
                [],
                ["create_date", "model_name", "input_tokens", "output_tokens", "cost", "user_id"],
                { limit: 100, order: "id desc" },
            );
        } catch (e) {
            this.state.historyError = (e && e.message) || "No pude leer el historial.";
        } finally {
            this.state.historyLoaded = true;
        }
    }

    fmtCost(c) { return "$" + (Number(c) || 0).toFixed(4); }
    fmtDate(d) { return d ? String(d).slice(0, 16).replace("T", " ") : ""; }
    fmtUser(u) { return Array.isArray(u) ? u[1] : (u || ""); }

    // Conectores / Ajustes: abren la configuración general (sección Primate IA) en el cliente web.
    openSettings() {
        this.action.doAction("base_setup.action_general_configuration");
    }
}

registry.category("actions").add("primate_sagui_app", SaguiApp);
