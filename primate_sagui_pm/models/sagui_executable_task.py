# -*- coding: utf-8 -*-
# La tool `auditar_proyectos` y su enganche con el gate de escritura que YA existe.
#
# La instrucción de la receta no le pide al modelo que recorra la base: le pide que llame a esta
# tool. El recorrido es determinístico (el motor); el modelo agrupa, prioriza y redacta. Así una
# auditoría no depende de que el loop de tools alcance a paginar 400 tareas antes del tope de
# iteraciones, ni de que el modelo no se saltee una.
#
# Cada propuesta entra por `_intercept_write`, que es el gate compartido: hereda el modo
# (propose/auto), el scope, la auditoría en sagui.task.action.log y el dry-run. No hay un segundo
# camino de escritura, que es justamente lo que CLAUDE.md prohíbe inventar.
import logging

from odoo import api, models, _

_logger = logging.getLogger(__name__)

AUDIT_SPEC = {
    "name": "auditar_proyectos",
    "description": (
        "Audita proyectos, tareas y actividades con el playbook del Gestor de Proyectos y "
        "registra cada hallazgo. Devuelve el detalle agrupado por regla y por proyecto. Las "
        "propuestas quedan a la espera de aprobación; NO se aplica nada. Llamala UNA sola vez "
        "por corrida: ya recorre todo el alcance."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "alcance": {
                "type": "string", "enum": ["todo", "area", "proyecto"],
                "description": "Qué auditar. 'area' necesita area_code; 'proyecto', project_id.",
            },
            "area_code": {
                "type": "string",
                "description": "Código del área, ej. 'technical'. Sólo con alcance='area'.",
            },
            "area_id": {
                "type": "integer",
                "description": "ID del área, si te lo dieron como número en vez de código. "
                               "Alcanza con uno de los dos.",
            },
            "project_id": {
                "type": "integer",
                "description": "ID del proyecto. Sólo con alcance='proyecto'.",
            },
            "dias": {
                "type": "integer",
                "description": "N días sin movimiento. Vacío = el umbral del dashboard.",
            },
        },
        "required": ["alcance"],
    },
}


class SaguiExecutableTask(models.AbstractModel):
    _inherit = "sagui.executable.task"

    # ------------------------------------------------------------------ tools
    def _task_specs(self, env, write_mode):
        specs = super()._task_specs(env, write_mode)
        # Sólo en modo escritura: en readonly la tool no podría proponer nada, y ofrecerla sería
        # invitar al modelo a un callejón sin salida.
        if write_mode:
            specs.append(dict(AUDIT_SPEC))
        return specs

    def _tool_runner(self, env, assistant, name, args, actor, ctx):
        if name == "auditar_proyectos":
            return self._pm_auditar(env, args or {}, ctx)
        return super()._tool_runner(env, assistant, name, args, actor, ctx)

    # ------------------------------------------------------------------ marca de procedencia
    def _pm_marcar_propuesta(self, env, ctx, marca):
        """Estampa los campos pm_* en la propuesta recién creada, para la aprobación por bloque.

        Cada subclase sabe qué guardó en ctx["review"]: la receta un token, la automatización un
        id. Por eso es un hook y no una adivinanza sobre el tipo del último elemento.
        """
        return False

    # ------------------------------------------------------------------ auditoría
    def _pm_auditar(self, env, args, ctx):
        self.ensure_one()
        engine = env["primate.sagui.pm.engine"]
        try:
            contexto, hallazgos = engine.auditar(
                alcance=args.get("alcance") or "todo",
                area_code=args.get("area_code"),
                area_id=args.get("area_id"),
                project_id=args.get("project_id"),
                dias=args.get("dias"),
                desde=ctx.get("pm_desde"),
            )
        except ValueError as e:
            return _("No pude auditar: %s") % e

        origen = self._task_action_type()
        run = env["sagui.pm.run"].create({
            "origen": origen if origen in ("receta", "automatizacion") else "receta",
            "recipe_id": self.id if self._name == "sagui.recipe" else False,
            "alcance": contexto["alcance"],
            "area_id": contexto["area"].id if contexto["area"] else False,
            "project_id": contexto["proyecto"].id if contexto["proyecto"] else False,
            "dias": contexto["dias"],
            "desde": contexto.get("desde") or False,
        })
        ctx["pm_run"] = run

        for hallazgo in hallazgos:
            self._pm_registrar(env, run, hallazgo, ctx)

        texto = self._pm_informe(run, contexto)
        run.informe = texto
        return texto

    def _pm_registrar(self, env, run, hallazgo, ctx):
        """Crea el hallazgo y, si es una PROPUESTA, la manda al gate."""
        finding = env["sagui.pm.finding"].create({
            "run_id": run.id,
            "regla": hallazgo["regla"],
            "bloque": hallazgo["bloque"],
            "titulo": hallazgo["titulo"],
            "riesgo": hallazgo["riesgo"],
            "tipo": hallazgo["tipo"],
            "model_name": hallazgo["modelo"],
            "res_id": hallazgo["res_id"],
            "res_name": hallazgo["res_name"],
            "project_id": hallazgo["project_id"],
            "area_id": hallazgo["area_id"],
            "resumen": hallazgo["resumen"],
            "detalle": hallazgo["detalle"],
        })
        if hallazgo["tipo"] != "propuesta":
            return finding

        antes = len(ctx.get("review") or [])
        self._intercept_write(
            env, hallazgo["operacion"],
            {"model": hallazgo["modelo"], "ids": [hallazgo["res_id"]],
             "values": hallazgo["valores"]},
            ctx)
        if len(ctx.get("review") or []) > antes:
            propuesta = self._pm_marcar_propuesta(env, ctx, {
                "pm_run_id": run.id,
                "pm_rule_key": hallazgo["regla"],
                "pm_project_id": hallazgo["project_id"],
                "pm_area_id": hallazgo["area_id"],
            })
            if propuesta and propuesta._name == "primate.sagui.pending.write":
                finding.pending_write_id = propuesta.id
        return finding

    # ------------------------------------------------------------------ informe
    def _pm_informe(self, run, contexto):
        """Informe agrupado por regla y por proyecto, en texto plano para el chat.

        Lo arma el código y no el modelo: son conteos y nombres de registros, y un conteo
        redactado por un modelo es un conteo que hay que ir a verificar.
        """
        if contexto["alcance"] == "area":
            donde = _("área %s") % contexto["area"].display_name
        elif contexto["alcance"] == "proyecto":
            donde = _("proyecto «%s»") % contexto["proyecto"].display_name
        else:
            donde = _("todos los proyectos")
        lineas = [_("AUDITORÍA — %(w)s · %(p)s proyectos · sin movimiento = %(d)s días") % {
            "w": donde, "p": len(contexto["proyectos"]), "d": contexto["dias"]}]
        if not run.finding_ids:
            lineas.append(_("Sin hallazgos."))
            return "\n".join(lineas)

        lineas.append(_("%(t)s hallazgos: %(a)s propuestas (a aprobar), %(b)s preguntas, "
                        "%(c)s notas.") % {
            "t": run.finding_count, "a": run.propuesta_count,
            "b": run.pregunta_count, "c": run.nota_count})

        for regla, hallazgos in self._pm_agrupar(run.finding_ids, "regla").items():
            primero = hallazgos[0]
            lineas.append("")
            lineas.append(_("── %(t)s · riesgo %(r)s · %(n)s caso/s") % {
                "t": primero.titulo or regla, "r": primero.riesgo or "?", "n": len(hallazgos)})
            for proyecto, del_proyecto in self._pm_agrupar(hallazgos, "project_id").items():
                nombre = proyecto.display_name if proyecto else _("(sin proyecto)")
                lineas.append("   %s — %s" % (nombre, len(del_proyecto)))
                for hallazgo in del_proyecto:
                    marca = {"propuesta": "→", "pregunta": "?", "nota": "·"}[hallazgo.tipo]
                    lineas.append("     %s %s" % (marca, (hallazgo.resumen or "").strip()))
                    if hallazgo.detalle:
                        lineas.append("       %s" % hallazgo.detalle.strip())
        return "\n".join(lineas)

    @api.model
    def _pm_agrupar(self, hallazgos, campo):
        """Agrupa conservando el orden de aparición (que ya viene por bloque y por regla)."""
        salida = {}
        for hallazgo in hallazgos:
            salida.setdefault(hallazgo[campo], []).append(hallazgo)
        return salida


class SaguiRecipe(models.Model):
    _inherit = "sagui.recipe"

    def _pm_marcar_propuesta(self, env, ctx, marca):
        """La receta guarda TOKENS en ctx['review']: la propuesta se busca por el último token."""
        token = (ctx.get("review") or [None])[-1]
        if not token:
            return False
        propuesta = self.env["primate.sagui.pending.write"].sudo().search(
            [("token", "=", token)], limit=1)
        if propuesta:
            propuesta.write(marca)
        return propuesta


class SaguiAutomation(models.Model):
    _inherit = "sagui.automation"

    def _pm_marcar_propuesta(self, env, ctx, marca):
        """La automatización guarda IDS de sagui.automation.proposal en ctx['review']."""
        proposal_id = (ctx.get("review") or [None])[-1]
        if not proposal_id:
            return False
        propuesta = self.env["sagui.automation.proposal"].sudo().browse(proposal_id).exists()
        if propuesta:
            propuesta.write(marca)
        return propuesta
