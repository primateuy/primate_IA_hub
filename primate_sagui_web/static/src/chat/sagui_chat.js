/** @odoo-module **/
// Componente de chat reutilizable (systray compacto y app full-page comparten este componente).
// [VERIFICADO v19] OWL 2: setup() + useState/useRef + onMounted/onPatched; servicios con
// useService; render seguro con htmlEscape+markup de @odoo/owl.
import { Component, useState, useRef, onMounted, onPatched, onWillStart, onWillUnmount } from "@odoo/owl";
import { markup, htmlEscape } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class SaguiChat extends Component {
    static template = "primate_sagui_web.SaguiChat";
    static props = {
        compact: { type: Boolean, optional: true },
        // Si viene, el chat es PERSISTIDO (sección Conversaciones): carga sus mensajes y persiste.
        // Si no, es EFÍMERO (panel del systray). El padre remonta con t-key al alternar conversación.
        conversationId: { optional: true },
        onActivity: { type: Function, optional: true },
    };

    setup() {
        this.sagui = useService("sagui");
        this.orm = useService("orm");
        this.scroller = useRef("scroller");
        this.input = useRef("input");
        this.state = useState({
            messages: [],      // {role:'user'|'assistant', text, tools:[{name,phase}]}
            input: "",
            busy: false,
        });
        // Conversación persistida: cargar sus mensajes (la record rule asegura que sean del usuario).
        onWillStart(async () => {
            if (this.props.conversationId) {
                try {
                    const msgs = await this.orm.call(
                        "sagui.conversation", "get_conversation_messages", [this.props.conversationId]);
                    this.state.messages = (msgs || []).map((m) => ({ role: m.role, text: m.body, tools: [] }));
                } catch {
                    /* conversación nueva o sin acceso: arranca vacía */
                }
            }
        });
        // Autoscroll: nos "pegamos" al fondo salvo que el usuario haya scrolleado hacia arriba.
        this._stick = true;
        // Bandera de vida: el stream SSE puede seguir vivo tras desmontar el componente (cambio de
        // sección / de conversación). Sin esto, los callbacks mutan estado de un componente destruido
        // -> "Component is destroyed". Guardamos cada handler con _alive.
        this._alive = true;
        onWillUnmount(() => { this._alive = false; });
        onMounted(() => { this._scrollToBottom(); this.input.el?.focus(); });
        onPatched(() => { if (this._stick) { this._scrollToBottom(); } });
    }

    get canSend() { return this.state.input.trim() && !this.state.busy; }

    async send() {
        if (!this.canSend) { return; }
        const text = this.state.input.trim();
        this.state.input = "";
        this.state.messages.push({ role: "user", text });
        // Mensaje del assistant que se va completando con el stream. OJO (OWL reactivity):
        // hay que mutar la REFERENCIA REACTIVA que devuelve el array, no el objeto crudo,
        // si no el re-render no se entera de los deltas.
        this.state.messages.push({ role: "assistant", text: "", tools: [] });
        const reply = this.state.messages[this.state.messages.length - 1];
        this.state.busy = true;
        this._stick = true;

        try {
            await this.sagui.ask(text, {
                onText: (delta) => { if (this._alive) { reply.text += delta; } },
                onTool: (name, phase) => {
                    if (!this._alive) { return; }
                    // Si la tool ya estaba 'start', la marcamos 'done' en vez de duplicar el chip.
                    const existing = reply.tools.find((t) => t.name === name && t.phase === "start");
                    if (phase === "done" && existing) { existing.phase = "done"; }
                    else { reply.tools.push({ name, phase }); }
                },
                onError: (msg) => { if (this._alive) { reply.text += (reply.text ? "\n\n" : "") + "⚠️ " + msg; } },
                onDone: () => {
                    if (!this._alive) { return; }
                    this.state.busy = false;
                    // Avisar al padre (refresca lista: título auto + orden por última actividad).
                    this.props.onActivity?.();
                },
            }, this.props.conversationId);
        } finally {
            // Pase lo que pase, liberamos el input (solo si el componente sigue vivo).
            if (this._alive) {
                this.state.busy = false;
                this.input.el?.focus();
            }
        }
    }

    onKeydown(ev) {
        // Enter envía; Shift+Enter inserta salto de línea.
        if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); this.send(); }
    }

    onScroll() {
        // Si el usuario se acerca al fondo, reactivamos el "pegado"; si sube, lo soltamos.
        const el = this.scroller.el;
        if (!el) { return; }
        const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
        this._stick = distance < 40;
    }

    _scrollToBottom() {
        const el = this.scroller.el;
        if (el) { el.scrollTop = el.scrollHeight; }
    }

    /**
     * Render markdown básico de Claude a HTML SEGURO (espejo de _markdown_to_html en Python):
     * primero se escapa TODO (anti-XSS) y recién después se inyectan nuestras propias etiquetas
     * conocidas (negrita, itálica, código, saltos de línea). Devuelve markup -> se usa con t-out.
     */
    renderMarkdown(text) {
        if (!text) { return markup(""); }
        let safe = String(htmlEscape(text)); // a partir de acá trabajamos sobre HTML seguro
        safe = safe.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
        safe = safe.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
        safe = safe.replace(/`([^`]+)`/g, "<code>$1</code>");
        safe = safe.replace(/\n/g, "<br/>");
        return markup(safe);
    }
}
