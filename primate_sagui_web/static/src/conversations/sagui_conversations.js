/** @odoo-module **/
// Sección "Conversaciones": dos paneles. Izquierda = lista de MIS conversaciones (crear/alternar/
// borrar); derecha = el chat con streaming de la seleccionada. Persistencia en sagui.conversation /
// sagui.message (aisladas por record rule). [VERIFICADO v19] orm.call a métodos @api.model;
// ConfirmationDialog vía el dialog service; remonte del chat por t-key al alternar.
import { Component, useState, onWillStart, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { SaguiChat } from "@primate_sagui_web/chat/sagui_chat";

export class SaguiConversations extends Component {
    static template = "primate_sagui_web.SaguiConversations";
    static components = { SaguiChat };
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.state = useState({ list: [], currentId: null });
        // Bandera de vida: onActivity puede dispararse desde el chat cuando ya navegaste fuera de
        // Conversaciones (componente destruido) -> guardamos el set de estado tras el await.
        this._alive = true;
        onWillUnmount(() => { this._alive = false; });
        onWillStart(async () => {
            await this.loadList();
            if (this._alive && this.state.list.length) {
                this.state.currentId = this.state.list[0].id;
            }
        });
    }

    async loadList() {
        const list = await this.orm.call("sagui.conversation", "list_conversations", []);
        if (this._alive) { this.state.list = list; }
    }

    async newConversation() {
        const c = await this.orm.call("sagui.conversation", "create_conversation", []);
        await this.loadList();
        if (this._alive) { this.state.currentId = c.id; }
    }

    selectConversation(id) {
        this.state.currentId = id;
    }

    removeConversation(ev, id, name) {
        ev.stopPropagation();   // no seleccionar al borrar
        this.dialog.add(ConfirmationDialog, {
            title: "Borrar conversación",
            body: `¿Seguro que querés borrar «${name}»? No se puede deshacer.`,
            confirmLabel: "Borrar",
            confirm: async () => {
                await this.orm.call("sagui.conversation", "delete_conversation", [id]);
                if (!this._alive) { return; }
                if (this.state.currentId === id) { this.state.currentId = null; }
                await this.loadList();
                if (this._alive && !this.state.currentId && this.state.list.length) {
                    this.state.currentId = this.state.list[0].id;
                }
            },
            cancel: () => {},
        });
    }

    // Tras cada turno del chat: refresca la lista (título automático + orden por última actividad).
    onActivity() {
        if (this._alive) { this.loadList(); }
    }

    fmtDate(d) {
        return d ? String(d).slice(0, 16).replace("T", " ") : "";
    }
}
