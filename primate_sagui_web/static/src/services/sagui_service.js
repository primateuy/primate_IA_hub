/** @odoo-module **/
// Servicio: mantiene el historial y consume el stream SSE de /sagui/ask.
// [VERIFICADO v19] registry.category("services").add(name, {dependencies, start}).
import { registry } from "@web/core/registry";

export const saguiService = {
    dependencies: [],
    start() {
        const history = []; // [{role, content}] que se manda al backend

        async function ask(message, handlers, conversationId = null) {
            // handlers: { onText(delta), onTool(name, phase), onDone(usage), onError(msg) }
            // conversationId: si viene, el backend persiste y carga el historial de la conversación
            // (modo Conversaciones); si es null, modo EFÍMERO (systray) con history del cliente.
            // NUNCA lanza: cualquier fallo se reporta por onError (no escapa como rechazo sin manejar).
            let full = "";
            try {
                const body = conversationId
                    ? { message, conversation_id: conversationId }
                    : { message, history: [...history] };
                const resp = await fetch("/sagui/ask", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                });
                if (!resp.ok || !resp.body) {
                    handlers.onError?.("No se pudo conectar con Sagui");
                    return;
                }
                // Modo efímero: registramos el turno del usuario (en persistido lo hace el backend).
                if (!conversationId) {
                    history.push({ role: "user", content: message });
                }

                const reader = resp.body.getReader();
                const decoder = new TextDecoder();
                let buf = "";
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) { break; }
                    buf += decoder.decode(value, { stream: true });
                    // Los eventos SSE se separan por línea en blanco.
                    const parts = buf.split("\n\n");
                    buf = parts.pop();
                    for (const part of parts) {
                        const line = part.replace(/^data:\s*/, "");
                        if (!line) { continue; }
                        let ev;
                        try { ev = JSON.parse(line); } catch (e) { continue; }
                        if (ev.type === "text") { full += ev.text; handlers.onText?.(ev.text); }
                        else if (ev.type === "tool") { handlers.onTool?.(ev.name, ev.phase); }
                        else if (ev.type === "error") { handlers.onError?.(ev.message); }
                        else if (ev.type === "done") { handlers.onDone?.(ev.usage); }
                    }
                }
            } catch (e) {
                handlers.onError?.((e && e.message) || "Se interrumpió la conexión con Sagui");
            } finally {
                // Solo en efímero acumulamos el historial local (en persistido manda el backend).
                if (!conversationId && full) { history.push({ role: "assistant", content: full }); }
            }
        }

        function reset() { history.length = 0; }

        return { ask, reset, history };
    },
};

registry.category("services").add("sagui", saguiService);
