# -*- coding: utf-8 -*-
# MOTOR de reglas del rol Gestor de Proyectos.
#
# El recorrido es DETERMINÍSTICO y por dominio ORM, no lo hace el modelo. Razón: enumerar tareas y
# actividades es una consulta, y una consulta no se alucina ni se queda a mitad de camino por el
# tope de iteraciones del loop de tools. El modelo entra donde hace falta JUICIO (qué actividades
# dicen lo mismo, qué significa un desvío) y para redactar el informe.
#
# Todo corre con el env del USUARIO que ejecuta: los ACL y las record rules recortan solos, así que
# el gestor nunca ve ni propone sobre algo que esa persona no podría tocar.
#
# Un hallazgo es de uno de tres TIPOS, y la diferencia no es cosmética:
#   propuesta → hay un valor concreto y aplicable  → va al gate (pending.write / bandeja).
#   pregunta  → hay un problema pero NO un valor deducible → se reporta, NO se escribe nada.
#   nota      → es un hecho de gestión, no de higiene → sólo alimenta la nota y el informe.
# Inventar un valor para que una "pregunta" parezca una "propuesta" es exactamente lo que la skill
# prohíbe: sería una propuesta plausible y falsa, que es lo que mata la credibilidad de la bandeja.
import logging
from datetime import timedelta

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

# Estados cerrados de project.task en v19.
ESTADOS_CERRADOS = ("1_done", "1_canceled")

# Tope de hallazgos por regla en una corrida. Una auditoría que devuelve 4000 propuestas no se
# revisa: se ignora. Si se corta, el informe lo dice y se vuelve a correr con alcance más chico.
TOPE_POR_REGLA = 50

TIPO_PROPUESTA = "propuesta"
TIPO_PREGUNTA = "pregunta"
TIPO_NOTA = "nota"


class SaguiPmEngine(models.AbstractModel):
    _name = "primate.sagui.pm.engine"
    _description = "Motor de reglas del Gestor de Proyectos"

    # ==================================================================
    #  Catálogo de reglas
    # ==================================================================
    @api.model
    def _reglas(self):
        """Las reglas del playbook, en el mismo orden y con las mismas claves que el SKILL.md.

        `riesgo` es lo que después habilita o no el paso a modo 'auto': sólo las bajas son
        candidatas, y sólo con evidencia de corridas aprobadas sin cambios. Nada arranca en auto.
        """
        return [
            # -------- Bloque A: higiene de la tarea --------
            {"clave": "tarea_sin_etapa", "bloque": "A", "riesgo": "bajo",
             "titulo": _("Tareas abiertas sin etapa"), "buscar": "_buscar_tarea_sin_etapa"},
            {"clave": "tarea_sin_responsable", "bloque": "A", "riesgo": "medio",
             "titulo": _("Tareas abiertas sin responsable"),
             "buscar": "_buscar_tarea_sin_responsable"},
            {"clave": "tarea_sin_proyecto", "bloque": "A", "riesgo": "medio",
             "titulo": _("Tareas abiertas sin proyecto"),
             "buscar": "_buscar_tarea_sin_proyecto"},
            {"clave": "tarea_area_desalineada", "bloque": "A", "riesgo": "medio",
             "titulo": _("Tareas con área distinta a la de su proyecto"),
             "buscar": "_buscar_tarea_area_desalineada"},
            # -------- Bloque B: tareas frenadas --------
            {"clave": "tarea_sin_movimiento", "bloque": "B", "riesgo": "alto",
             "titulo": _("Tareas sin movimiento"), "buscar": "_buscar_tarea_sin_movimiento"},
            # -------- Bloque C: actividades --------
            {"clave": "actividad_vencida", "bloque": "C", "riesgo": "medio",
             "titulo": _("Actividades vencidas"), "buscar": "_buscar_actividad_vencida"},
            # -------- Bloque D: proyecto --------
            {"clave": "proyecto_sin_area", "bloque": "D", "riesgo": "bajo",
             "titulo": _("Proyectos sin área"), "buscar": "_buscar_proyecto_sin_area"},
            {"clave": "proyecto_sin_cliente", "bloque": "D", "riesgo": "medio",
             "titulo": _("Proyectos sin cliente"), "buscar": "_buscar_proyecto_sin_cliente"},
            {"clave": "proyecto_sin_plan", "bloque": "D", "riesgo": "medio",
             "titulo": _("Proyectos sin plan"), "buscar": "_buscar_proyecto_sin_plan"},
            {"clave": "desvio_contra_plan", "bloque": "D", "riesgo": "nota",
             "titulo": _("Desvío contra el plan"), "buscar": "_buscar_desvio_contra_plan"},
        ]

    # ==================================================================
    #  Alcance
    # ==================================================================
    @api.model
    def _resolver_alcance(self, alcance="todo", area_code=None, project_id=None, dias=None,
                          desde=None, area_id=None):
        """Traduce los parámetros de la receta a los proyectos y umbrales de la corrida.

        El alcance se recorta por el ÁREA DEL PROYECTO, no por la de cada tarea. Es lo que hace
        cierta la promesa "con alcance = Técnica ninguna propuesta toca proyectos de otra área":
        una tarea administrativa dentro de un proyecto técnico entra en el alcance técnico, que es
        lo correcto -el que responde por ella es el responsable del proyecto-.
        """
        Project = self.env["project.project"]
        dominio = [("active", "=", True)]
        area = self.env["primate.area"]
        proyecto = Project
        if alcance == "area":
            # Se acepta el CODE o el ID: la receta pasa un many2one (y el modelo lee el id del
            # texto sustituido), pero el code es la clave estable y la que usa todo lo demás.
            if area_id:
                area = self.env["primate.area"].browse(int(area_id)).exists()
            else:
                area = self.env["primate.area"]._by_code(area_code)
            if not area:
                raise ValueError(_("No existe el área «%s».") % (area_code or area_id))
            dominio.append(("area_id", "=", area.id))
        elif alcance == "proyecto":
            proyecto = Project.browse(int(project_id)).exists()
            if not proyecto:
                raise ValueError(_("El proyecto %s no existe o no lo podés ver.") % project_id)
            dominio.append(("id", "=", proyecto.id))
            area = proyecto.area_id
        # search() ya aplica las record rules del usuario: lo que no puede ver, no está.
        proyectos = Project.search(dominio)
        return {
            "alcance": alcance,
            "area": area,
            "proyecto": proyecto,
            "proyectos": proyectos,
            "dias": self._dias_sin_movimiento(dias),
            "desde": desde,
            "hoy": fields.Date.context_today(self),
        }

    @api.model
    def _dias_sin_movimiento(self, dias=None):
        """N días. Sale del MISMO umbral del dashboard, no de un número propio.

        Dos números distintos para la misma idea -uno que pinta la alerta y otro que dispara la
        propuesta- es lo que hace que nadie confíe en ninguno de los dos. Si el dashboard no está
        instalado se usa su mismo default y su misma clave de ir.config_parameter.
        """
        if dias:
            return max(int(dias), 1)
        clave = "primate_project_dashboard.alert_no_activity_days"
        valor = self.env["ir.config_parameter"].sudo().get_param(clave)
        try:
            return max(int(float(valor)), 1)
        except (TypeError, ValueError):
            return 7

    # ==================================================================
    #  Ejecución
    # ==================================================================
    @api.model
    def auditar(self, alcance="todo", area_code=None, project_id=None, dias=None, desde=None,
                area_id=None):
        """Corre todas las reglas y devuelve (contexto, hallazgos). NO escribe ni propone nada:
        de eso se encarga quien la llama, que es el que tiene el gate del modo propose."""
        ctx = self._resolver_alcance(alcance, area_code, project_id, dias, desde, area_id)
        hallazgos = []
        for regla in self._reglas():
            try:
                encontrados = getattr(self, regla["buscar"])(ctx) or []
            except Exception as e:  # noqa: BLE001
                # Una regla que revienta no puede llevarse puesta la auditoría entera: se reporta
                # como regla caída y las demás siguen. Silenciarla sería peor: el informe diría
                # "0 hallazgos" y eso se lee como "está todo bien".
                _logger.exception("Sagui PM: falló la regla %s", regla["clave"])
                hallazgos.append(self._hallazgo(
                    regla, TIPO_NOTA, resumen=_("La regla «%(t)s» no se pudo evaluar: %(e)s") % {
                        "t": regla["titulo"], "e": e}, error=True))
                continue
            recortado = len(encontrados) > TOPE_POR_REGLA
            for hallazgo in encontrados[:TOPE_POR_REGLA]:
                hallazgos.append(hallazgo)
            if recortado:
                hallazgos.append(self._hallazgo(
                    regla, TIPO_NOTA, resumen=_(
                        "«%(t)s»: se encontraron %(n)s casos y se listan los primeros %(tope)s. "
                        "Corré la auditoría con un alcance más chico.") % {
                        "t": regla["titulo"], "n": len(encontrados), "tope": TOPE_POR_REGLA}))
        return ctx, hallazgos

    @api.model
    def _hallazgo(self, regla, tipo, resumen, registro=None, proyecto=None, area=None,
                  operacion=None, valores=None, detalle=None, error=False):
        """Un hallazgo normalizado. `operacion`/`valores` sólo tienen sentido si tipo=propuesta."""
        registro = registro or self.env["project.task"].browse()
        proyecto = proyecto if proyecto is not None else getattr(registro, "project_id", None)
        if area is None:
            area = getattr(registro, "area_id", None) or (proyecto.area_id if proyecto else None)
        destinatario = self._destinatario(registro, proyecto, area)
        return {
            "destinatario_id": destinatario.id if destinatario else False,
            "regla": regla["clave"],
            "bloque": regla["bloque"],
            "riesgo": regla["riesgo"],
            "titulo": regla["titulo"],
            "tipo": tipo,
            "modelo": registro._name if registro else False,
            "res_id": registro.id if registro else False,
            "res_name": registro.display_name if registro else "",
            "project_id": proyecto.id if proyecto else False,
            "area_id": area.id if area else False,
            "resumen": resumen,
            "detalle": detalle or "",
            "operacion": operacion,
            "valores": valores or {},
            "error": error,
        }

    @api.model
    def _destinatario(self, registro, proyecto, area):
        """A QUIÉN se le propone, según la columna "a quién" del playbook.

        En orden: el responsable del registro, si no el del proyecto, si no el del área. Sin esto
        la corrida continua tendría que mandarle todo a una sola persona, y una bandeja que le
        llega entera a alguien que sólo puede resolver un cuarto no se mira.
        """
        Users = self.env["res.users"]
        for campo in ("user_ids", "user_id"):
            valor = getattr(registro, campo, None) if registro else None
            if valor:
                return valor[0] if campo == "user_ids" else valor
        if proyecto and proyecto.user_id:
            return proyecto.user_id
        if area and area.user_id:
            return area.user_id
        return Users.browse()

    # ==================================================================
    #  Bloque A — higiene de la tarea
    # ==================================================================
    @api.model
    def _tareas_abiertas(self, ctx, extra=None):
        dominio = [
            ("project_id", "in", ctx["proyectos"].ids),
            ("state", "not in", list(ESTADOS_CERRADOS)),
        ]
        if ctx.get("desde"):
            dominio.append(("write_date", ">=", ctx["desde"]))
        return self.env["project.task"].search(dominio + (extra or []))

    @api.model
    def _buscar_tarea_sin_etapa(self, ctx):
        regla = self._regla("tarea_sin_etapa")
        salida = []
        for tarea in self._tareas_abiertas(ctx, [("stage_id", "=", False)]):
            etapa = self._primera_etapa(tarea.project_id)
            if not etapa:
                # El proyecto no tiene etapas: el hallazgo es del PROYECTO, no de cada tarea.
                continue
            salida.append(self._hallazgo(
                regla, TIPO_PROPUESTA, registro=tarea,
                resumen=_("«%(t)s» está abierta y sin etapa.") % {"t": tarea.display_name},
                detalle=_("Primera etapa del proyecto «%(p)s»: %(e)s.") % {
                    "p": tarea.project_id.display_name, "e": etapa.display_name},
                operacion="write", valores={"stage_id": etapa.id}))
        return salida

    @api.model
    def _primera_etapa(self, proyecto):
        if not proyecto:
            return self.env["project.task.type"].browse()
        return self.env["project.task.type"].search(
            [("project_ids", "in", proyecto.id)], order="sequence, id", limit=1)

    @api.model
    def _buscar_tarea_sin_responsable(self, ctx):
        """Sin responsable. Sólo se PROPONE un nombre si hay evidencia de UNO SOLO.

        Evidencia = quien la creó, quien le cargó horas, quien viene escribiendo en el chatter. Si
        hay varios candidatos o ninguno, es una PREGUNTA: elegir "el más probable" entre dos
        personas es exactamente la propuesta plausible y falsa que la skill prohíbe.
        """
        regla = self._regla("tarea_sin_responsable")
        salida = []
        for tarea in self._tareas_abiertas(ctx, [("user_ids", "=", False)]):
            candidatos = self._candidatos_responsable(tarea)
            if len(candidatos) == 1:
                candidato = candidatos[0]
                salida.append(self._hallazgo(
                    regla, TIPO_PROPUESTA, registro=tarea,
                    resumen=_("«%(t)s» está abierta y sin responsable.") % {"t": tarea.display_name},
                    detalle=_("Único con actividad en la tarea: %(u)s.") % {"u": candidato.display_name},
                    operacion="write", valores={"user_ids": [(6, 0, [candidato.id])]}))
            else:
                motivo = (_("hay %s personas con actividad en la tarea") % len(candidatos)
                          if candidatos else _("nadie tiene actividad en la tarea"))
                salida.append(self._hallazgo(
                    regla, TIPO_PREGUNTA, registro=tarea,
                    resumen=_("«%(t)s» está abierta y sin responsable.") % {"t": tarea.display_name},
                    detalle=_("No propongo a nadie: %(m)s. ¿La asignás o la cerrás?") % {"m": motivo}))
        return salida

    @api.model
    def _candidatos_responsable(self, tarea):
        """Usuarios internos con actividad real en la tarea, sin repetir."""
        usuarios = self.env["res.users"].browse()
        if tarea.create_uid and not tarea.create_uid._is_public():
            usuarios |= tarea.create_uid
        if "timesheet_ids" in tarea._fields:
            usuarios |= tarea.timesheet_ids.mapped("user_id")
        autores = tarea.message_ids.mapped("author_id")
        usuarios |= self.env["res.users"].search([("partner_id", "in", autores.ids)])
        bot = self.env.ref("primate_sagui.user_sagui_bot", raise_if_not_found=False)
        odoobot = self.env.ref("base.user_root", raise_if_not_found=False)
        excluidos = (bot or self.env["res.users"]) | (odoobot or self.env["res.users"])
        return list(usuarios - excluidos)

    @api.model
    def _buscar_tarea_sin_proyecto(self, ctx):
        """Sólo con alcance 'todo': una tarea sin proyecto no pertenece a ningún área, así que no
        puede caer dentro de un alcance por área sin violar la promesa del alcance."""
        if ctx["alcance"] != "todo":
            return []
        regla = self._regla("tarea_sin_proyecto")
        dominio = [("project_id", "=", False), ("state", "not in", list(ESTADOS_CERRADOS))]
        if ctx.get("desde"):
            dominio.append(("write_date", ">=", ctx["desde"]))
        salida = []
        for tarea in self.env["project.task"].search(dominio):
            salida.append(self._hallazgo(
                regla, TIPO_PREGUNTA, registro=tarea,
                resumen=_("«%(t)s» está abierta y no cuelga de ningún proyecto.") % {
                    "t": tarea.display_name},
                detalle=_("No adivino a qué proyecto va. ¿A cuál la movemos, o es privada a "
                          "propósito?")))
        return salida

    @api.model
    def _buscar_tarea_area_desalineada(self, ctx):
        """Tarea cuya área no es la de su proyecto.

        NUNCA propone alinear. El área de la tarea se hereda del proyecto pero es editable a
        propósito -una tarea administrativa dentro de un proyecto técnico es correcta-, y cambiar
        el área de un proyecto no re-baja el área a sus tareas justamente para no pisar esa
        decisión. Esta regla es el ÚNICO mecanismo que detecta el desalineado, y por eso pregunta
        con los dos valores al lado en vez de elegir uno.
        """
        regla = self._regla("tarea_area_desalineada")
        salida = []
        for tarea in self._tareas_abiertas(ctx, [("area_id", "!=", False)]):
            del_proyecto = tarea.project_id.area_id
            if not del_proyecto or tarea.area_id == del_proyecto:
                continue
            salida.append(self._hallazgo(
                regla, TIPO_PREGUNTA, registro=tarea,
                resumen=_("«%(t)s» es de %(a)s y su proyecto es de %(b)s.") % {
                    "t": tarea.display_name, "a": tarea.area_id.display_name,
                    "b": del_proyecto.display_name},
                detalle=_("Tarea: %(a)s. Proyecto «%(p)s»: %(b)s. Puede estar bien (una tarea de "
                          "otra área dentro del proyecto) o puede ser que el proyecto haya "
                          "cambiado de área después. ¿Cuál vale?") % {
                    "a": tarea.area_id.display_name, "p": tarea.project_id.display_name,
                    "b": del_proyecto.display_name}))
        return salida

    # ==================================================================
    #  Bloque B — tareas frenadas
    # ==================================================================
    @api.model
    def _buscar_tarea_sin_movimiento(self, ctx):
        """Sin NINGUNA de las tres señales hace más de N días.

        Las tres son conjunción, no disyunción: una tarea con timesheets de ayer no está frenada
        aunque nadie haya comentado nada. Los cambios de etapa no se miran aparte porque
        `stage_id` tiene tracking y cada cambio deja su mensaje en el chatter.
        """
        regla = self._regla("tarea_sin_movimiento")
        corte = fields.Datetime.now() - timedelta(days=ctx["dias"])
        salida = []
        for tarea in self._tareas_abiertas(ctx):
            ultimo = self._ultimo_movimiento(tarea)
            if ultimo and ultimo >= corte:
                continue
            salida.append(self._hallazgo(
                regla, TIPO_PREGUNTA, registro=tarea,
                resumen=_("«%(t)s» no se movió desde el %(f)s (%(d)s días).") % {
                    "t": tarea.display_name,
                    "f": fields.Date.to_string(ultimo.date()) if ultimo else _("siempre"),
                    "d": (fields.Datetime.now() - ultimo).days if ultimo else "?"},
                detalle=_("Sin mensajes, sin cambios de etapa y sin horas cargadas. "
                          "¿La cerramos, la reasignamos o la marcamos bloqueada?")))
        return salida

    @api.model
    def _ultimo_movimiento(self, tarea):
        """El más reciente de: último mensaje, último timesheet, creación."""
        fechas = [tarea.create_date]
        mensajes = tarea.message_ids.mapped("date")
        if mensajes:
            fechas.append(max(mensajes))
        if "timesheet_ids" in tarea._fields and tarea.timesheet_ids:
            # account.analytic.line.date es Date; se compara en Datetime.
            ultimo = max(tarea.timesheet_ids.mapped("date"))
            if ultimo:
                fechas.append(fields.Datetime.to_datetime(ultimo))
        fechas = [f for f in fechas if f]
        return max(fechas) if fechas else None

    # ==================================================================
    #  Bloque C — actividades
    # ==================================================================
    @api.model
    def _buscar_actividad_vencida(self, ctx):
        """Vencida hace más de N días. Se propone REPROGRAMAR, que es la opción reversible.

        Cerrarla es la otra salida y se dice en el resumen, pero no se propone: cerrar una
        actividad borra el recordatorio y no hay 'deshacer'. La propuesta concreta es la barata
        de revertir; la cara la decide una persona.
        """
        regla = self._regla("actividad_vencida")
        corte = ctx["hoy"] - timedelta(days=ctx["dias"])
        nueva_fecha = ctx["hoy"] + timedelta(days=7)
        dominio = [
            ("date_deadline", "<", corte),
            ("res_model", "in", ["project.task", "project.project"]),
        ]
        salida = []
        for actividad in self.env["mail.activity"].search(dominio):
            registro = self._registro_de(actividad)
            proyecto = self._proyecto_de(registro)
            if proyecto not in ctx["proyectos"]:
                continue
            atraso = (ctx["hoy"] - actividad.date_deadline).days
            salida.append(self._hallazgo(
                regla, TIPO_PROPUESTA, registro=actividad, proyecto=proyecto,
                area=proyecto.area_id if proyecto else None,
                resumen=_("«%(a)s» sobre «%(r)s» venció hace %(d)s días (%(u)s).") % {
                    "a": actividad.summary or actividad.activity_type_id.display_name,
                    "r": registro.display_name if registro else "?", "d": atraso,
                    "u": actividad.user_id.display_name},
                detalle=_("Propongo reprogramarla al %(f)s. Si ya no aplica, cerrala: eso no lo "
                          "propongo yo porque no se puede deshacer.") % {
                    "f": fields.Date.to_string(nueva_fecha)},
                operacion="write", valores={"date_deadline": fields.Date.to_string(nueva_fecha)}))
        return salida

    @api.model
    def _registro_de(self, actividad):
        modelo = actividad.res_model
        if not modelo or modelo not in self.env:
            return None
        return self.env[modelo].browse(actividad.res_id).exists()

    @api.model
    def _proyecto_de(self, registro):
        if not registro:
            return self.env["project.project"].browse()
        if registro._name == "project.project":
            return registro
        return registro.project_id if "project_id" in registro._fields else \
            self.env["project.project"].browse()

    # ==================================================================
    #  Bloque D — proyecto
    # ==================================================================
    @api.model
    def _buscar_proyecto_sin_area(self, ctx):
        """Área mayoritaria de sus tareas. Empate o sin tareas -> pregunta.

        Es la MISMA regla que usó la migración del Selection a primate.area, a propósito: si dos
        mecanismos deducen el área de un proyecto de dos maneras distintas, el que corre segundo
        parece estar corrigiendo al primero.
        """
        regla = self._regla("proyecto_sin_area")
        salida = []
        for proyecto in ctx["proyectos"].filtered(lambda p: not p.area_id):
            conteo = {}
            for area, cantidad in self.env["project.task"]._read_group(
                    [("project_id", "=", proyecto.id), ("area_id", "!=", False)],
                    ["area_id"], ["__count"]):
                conteo[area] = cantidad
            if not conteo:
                salida.append(self._hallazgo(
                    regla, TIPO_PREGUNTA, registro=proyecto, proyecto=proyecto,
                    resumen=_("«%(p)s» no tiene área.") % {"p": proyecto.display_name},
                    detalle=_("Sus tareas tampoco, así que no la puedo deducir. ¿Cuál es?")))
                continue
            tope = max(conteo.values())
            ganadoras = [area for area, cantidad in conteo.items() if cantidad == tope]
            if len(ganadoras) > 1:
                salida.append(self._hallazgo(
                    regla, TIPO_PREGUNTA, registro=proyecto, proyecto=proyecto,
                    resumen=_("«%(p)s» no tiene área.") % {"p": proyecto.display_name},
                    detalle=_("Sus tareas empatan entre %(a)s. No desempato solo: ¿cuál es?") % {
                        "a": ", ".join(a.display_name for a in ganadoras)}))
                continue
            ganadora = ganadoras[0]
            salida.append(self._hallazgo(
                regla, TIPO_PROPUESTA, registro=proyecto, proyecto=proyecto, area=ganadora,
                resumen=_("«%(p)s» no tiene área.") % {"p": proyecto.display_name},
                detalle=_("%(n)s de sus %(t)s tareas con área son de %(a)s.") % {
                    "n": tope, "t": sum(conteo.values()), "a": ganadora.display_name},
                operacion="write", valores={"area_id": ganadora.id}))
        return salida

    @api.model
    def _buscar_proyecto_sin_cliente(self, ctx):
        regla = self._regla("proyecto_sin_cliente")
        return [
            self._hallazgo(
                regla, TIPO_PREGUNTA, registro=proyecto, proyecto=proyecto,
                resumen=_("«%(p)s» no tiene cliente.") % {"p": proyecto.display_name},
                detalle=_("No lo deduzco del nombre del proyecto. ¿Quién es?"))
            for proyecto in ctx["proyectos"].filtered(lambda p: not p.partner_id)
        ]

    @api.model
    def _buscar_proyecto_sin_plan(self, ctx):
        """Sin fechas, o -si el dashboard está- sin hitos con avance planificado."""
        regla = self._regla("proyecto_sin_plan")
        con_hitos = "planned_progress" in self.env["project.milestone"]._fields \
            if "project.milestone" in self.env else False
        salida = []
        for proyecto in ctx["proyectos"]:
            faltantes = []
            if not proyecto.date_start:
                faltantes.append(_("fecha de inicio"))
            if not proyecto.date:
                faltantes.append(_("fecha de fin"))
            if con_hitos:
                hitos = self.env["project.milestone"].search_count([
                    ("project_id", "=", proyecto.id), ("planned_progress", ">", 0)])
                if hitos < 2:
                    faltantes.append(_("al menos dos hitos con avance planificado (tiene %s)") % hitos)
            if not faltantes:
                continue
            salida.append(self._hallazgo(
                regla, TIPO_PREGUNTA, registro=proyecto, proyecto=proyecto,
                resumen=_("«%(p)s» no tiene plan completo.") % {"p": proyecto.display_name},
                detalle=_("Le falta: %(f)s. Sin eso queda en gris en el dashboard y no tiene "
                          "semáforo.") % {"f": ", ".join(faltantes)}))
        return salida

    @api.model
    def _buscar_desvio_contra_plan(self, ctx):
        """Usa la lógica del dashboard, no una propia. Si no está instalado, no se inventa nada."""
        regla = self._regla("desvio_contra_plan")
        Project = self.env["project.project"]
        if not hasattr(Project, "_primate_health_metrics"):
            return [self._hallazgo(regla, TIPO_NOTA, resumen=_(
                "Sin datos de desvío: el dashboard ejecutivo de proyectos no está instalado."))]
        from odoo.addons.primate_project_dashboard.models import dashboard_params
        params = dashboard_params.get_params(self.env)
        metricas = ctx["proyectos"]._primate_health_metrics(params, ctx["hoy"])
        salida = []
        for proyecto in ctx["proyectos"]:
            valores = metricas.get(proyecto.id) or {}
            estado = valores.get("health_state")
            if estado not in ("critical", "at_risk"):
                continue
            real, plan = valores.get("progress_real"), valores.get("progress_planned")
            salida.append(self._hallazgo(
                regla, TIPO_NOTA, registro=proyecto, proyecto=proyecto,
                resumen=_("«%(p)s» está en %(e)s.") % {
                    "p": proyecto.display_name,
                    "e": _("rojo") if estado == "critical" else _("amarillo")},
                detalle=_("Avance real %(r)s%% contra %(pl)s%% planificado.") % {
                    "r": round(real, 1) if real is not None else "?",
                    "pl": round(plan, 1) if plan is not None else "?"}))
        return salida

    # ==================================================================
    @api.model
    def _regla(self, clave):
        for regla in self._reglas():
            if regla["clave"] == clave:
                return regla
        raise KeyError(clave)
