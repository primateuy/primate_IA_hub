/** @odoo-module **/
// Botón en el systray que abre/cierra el panel deslizante con el chat (compacto).
// [VERIFICADO v19] registry.category("systray").add(name, {Component}, {sequence})
// (mismo patrón que addons/mail .../call/common/call_menu.js).
import { Component, useState, useExternalListener, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { SaguiChat } from "@primate_sagui_web/chat/sagui_chat";

export class SaguiSystray extends Component {
    static template = "primate_sagui_web.SaguiSystray";
    static components = { SaguiChat };
    static props = {};

    setup() {
        this.state = useState({ open: false });
        this.root = useRef("root");
        // Cerrar el panel al clickear fuera (UX de popover).
        useExternalListener(window, "click", (ev) => {
            if (this.state.open && this.root.el && !this.root.el.contains(ev.target)) {
                this.state.open = false;
            }
        });
    }

    toggle() { this.state.open = !this.state.open; }
}

registry.category("systray").add(
    "primate_sagui.systray",
    { Component: SaguiSystray },
    { sequence: 10 }
);
