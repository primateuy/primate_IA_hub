/** @odoo-module **/
// Fase 2: botón "Consultar a Sagui" en el control panel de TODOS los formularios.
// Verificado contra v19: se inyecta en el slot "control-panel-additional-actions"
// del template web.FormView (junto al CogMenu) parcheando FormController.components,
// y abre Discuss enfocando el DM con Sagui vía mail.action_discuss.
import { Component } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { FormController } from "@web/views/form/form_controller";

export class SaguiConsultButton extends Component {
    static template = "primate_sagui.SaguiConsultButton";
    static props = {
        resModel: String,
        resId: { type: Number },
    };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
    }

    async onClick() {
        // El método corre como el usuario actual -> respeta permisos (sin sudo).
        const action = await this.orm.call(
            "primate.sagui.assistant",
            "action_consultar_registro",
            [this.props.resModel, this.props.resId]
        );
        await this.action.doAction(action);
    }
}

// Registramos el componente en el FormController para que el template inyectado
// (ver sagui_consult_button.xml) pueda referenciarlo en cualquier formulario.
FormController.components = {
    ...FormController.components,
    SaguiConsultButton,
};
