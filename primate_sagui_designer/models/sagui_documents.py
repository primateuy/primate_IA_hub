# -*- coding: utf-8 -*-
# REFERENCIA DE DISEÑO DESDE EL MÓDULO DOCUMENTOS.
#
# El usuario dice "la referencia está en Marketing / Web / Rediseño 2026" y Sagui resuelve la
# carpeta, lista lo que hay, separa QUÉ VA COMO REFERENCIA (el PDF/mockup a reproducir) de QUÉ VA
# COMO ASSET (logo, fotos), y lo muestra ANTES de usar nada.
#
# OJO con la versión: en Odoo 19 NO existe `documents.folder`. Las carpetas son
# `documents.document` con `type='folder'` y jerarquía por `folder_id` (_parent_name), con permisos
# vía `documents.access`. Toda la resolución de rutas se hace recorriendo ese árbol.
#
# Permisos: TODO se lee con self.env(user=user.id). Si el usuario no ve una carpeta, Sagui tampoco.
# Nunca sudo() para datos del cliente (regla dura del proyecto).
#
# Ambigüedad: si la carpeta no existe, hay varias candidatas o no hay acceso, se EXPLICA y se
# devuelven las opciones. Nunca se elige por el usuario.
import json
import logging
import re

from odoo import api, models, _

_logger = logging.getLogger(__name__)

REFERENCE_MIMETYPES = ("application/pdf",)
IMAGE_PREFIX = "image/"
MAX_LISTED = 60

# Pistas de nombre para clasificar. No deciden solas: se muestran al humano para que confirme.
LOGO_HINTS = ("logo", "isotipo", "imagotipo", "marca", "brand")
REFERENCE_HINTS = ("mockup", "maqueta", "diseño", "diseno", "design", "wireframe", "propuesta",
                   "referencia", "ref", "layout", "home", "landing")


class SaguiDocuments(models.AbstractModel):
    _inherit = "primate.sagui.assistant"

    # ------------------------------------------------------------------ tool specs
    def _tool_specs(self):
        specs = super()._tool_specs()
        # Documentos es Enterprise: si no está instalado, ni se ofrecen las tools.
        if "documents.document" not in self.env:
            return specs
        specs += [
            {
                "name": "documents_listar_carpeta",
                "description": "Lista los archivos de una carpeta del módulo DOCUMENTOS para usarlos "
                               "como referencia de diseño. Aceptá el id de la carpeta, su nombre, o "
                               "una ruta con barras ('Marketing / Web / Rediseño 2026'). Devuelve "
                               "los archivos clasificados en REFERENCIA (PDF/mockup a reproducir) y "
                               "ASSETS (logo, fotos). Usalo SIEMPRE que el usuario diga que la "
                               "referencia está en Documentos, en vez de pedirle que la adjunte. Si "
                               "hay varias carpetas candidatas devuelve la lista: mostrásela y "
                               "preguntá cuál, NO elijas vos.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "carpeta": {"type": "string", "description": "Id, nombre o ruta de la carpeta."},
                        "incluir_subcarpetas": {"type": "boolean",
                            "description": "Buscar también dentro de las subcarpetas (default false)."},
                    },
                    "required": ["carpeta"],
                },
            },
            {
                "name": "documents_tomar_archivo",
                "description": "Toma archivos concretos de Documentos (por sus ids, los que devolvió "
                               "documents_listar_carpeta) y los deja listos como referencia de "
                               "diseño. Devuelve el attachment_id que después le pasás a "
                               "analizar_sitio (referencia) o al rol diseñador. Confirmá con el "
                               "usuario qué archivos vas a usar antes de llamar a esto.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "documento_ids": {"type": "array", "items": {"type": "integer"},
                            "description": "Ids de documents.document a usar."},
                        "rol": {"type": "string", "enum": ["referencia", "logo", "foto"],
                            "description": "Para qué se usa cada archivo (default 'referencia')."},
                    },
                    "required": ["documento_ids"],
                },
            },
        ]
        return specs

    def _system_prompt(self):
        prompt = super()._system_prompt()
        if "documents.document" not in self.env:
            return prompt
        prompt += (
            "\n\nREFERENCIA DESDE DOCUMENTOS: si el usuario dice que el diseño/logo está en una "
            "carpeta de Documentos, usá documents_listar_carpeta con lo que te haya dicho (nombre o "
            "ruta). Mostrale la clasificación (qué vas a usar como REFERENCIA y qué como ASSETS) y "
            "pedile confirmación antes de tomar los archivos. Si la carpeta no existe o hay varias "
            "candidatas, mostrá las opciones y preguntá: NUNCA adivines cuál era.\n"
        )
        return prompt

    def _run_tool(self, name, args, user, channel=None, proposals=None):
        if name == "documents_listar_carpeta":
            return self._tool_documents_listar(args, user)
        if name == "documents_tomar_archivo":
            return self._tool_documents_tomar(args, user)
        return super()._run_tool(name, args, user, channel=channel, proposals=proposals)

    # ==================================================================
    #  Tool: listar una carpeta
    # ==================================================================
    def _tool_documents_listar(self, args, user):
        env = self.env(user=user.id)
        if "documents.document" not in env:
            return _("El módulo Documentos no está instalado en esta base.")

        folder, problema = self._resolve_folder(env, args.get("carpeta"))
        if problema:
            return problema

        domain = [("type", "=", "binary")]
        if args.get("incluir_subcarpetas"):
            domain += [("folder_id", "child_of", folder.id)]
        else:
            domain += [("folder_id", "=", folder.id)]
        docs = env["documents.document"].search(domain, limit=MAX_LISTED, order="name")

        referencias, assets, descartados = [], [], []
        for doc in docs:
            mimetype = (doc.mimetype or "").split(";")[0].strip().lower()
            info = {
                "id": doc.id, "nombre": doc.name, "mimetype": mimetype,
                "tamano_kb": round((doc.file_size or 0) / 1024),
            }
            lower = (doc.name or "").lower()
            if mimetype in REFERENCE_MIMETYPES:
                info["motivo"] = "PDF: candidato natural a referencia de diseño"
                referencias.append(info)
            elif mimetype.startswith(IMAGE_PREFIX):
                if any(h in lower for h in LOGO_HINTS):
                    info["motivo"] = "el nombre sugiere que es el logo"
                    info["rol_sugerido"] = "logo"
                    assets.append(info)
                elif any(h in lower for h in REFERENCE_HINTS):
                    info["motivo"] = "el nombre sugiere que es un mockup/diseño"
                    referencias.append(info)
                else:
                    info["motivo"] = "imagen suelta: la trato como foto del sitio"
                    info["rol_sugerido"] = "foto"
                    assets.append(info)
            else:
                info["motivo"] = "no es PDF ni imagen"
                descartados.append(info)

        return self._json({
            "carpeta": {"id": folder.id, "nombre": folder.name, "ruta": self._folder_path(folder)},
            "referencias_candidatas": referencias,
            "assets_candidatos": assets,
            "descartados": descartados,
            "total_en_carpeta": len(docs),
            "nota": "Mostrale al usuario esta clasificación en palabras (qué usarías como "
                    "REFERENCIA y qué como ASSETS) y PEDILE CONFIRMACIÓN. Si hay más de un "
                    "candidato a referencia, preguntá cuál. Recién con la confirmación llamá a "
                    "documents_tomar_archivo con los ids elegidos.",
        })

    # ==================================================================
    #  Tool: tomar archivos concretos
    # ==================================================================
    def _tool_documents_tomar(self, args, user):
        env = self.env(user=user.id)
        if "documents.document" not in env:
            return _("El módulo Documentos no está instalado en esta base.")

        ids = [int(i) for i in (args.get("documento_ids") or []) if str(i).strip().isdigit()]
        if not ids:
            return _("Necesito los ids de los documentos a usar (los devuelve "
                     "documents_listar_carpeta).")
        rol = (args.get("rol") or "referencia").strip().lower()

        docs = env["documents.document"].browse(ids).exists()
        if not docs:
            return _("No encontré esos documentos o no podés verlos.")
        try:
            docs.check_access("read")
        except Exception:  # noqa: BLE001
            return _("No tenés permiso para leer alguno de esos documentos.")

        tomados, omitidos = [], []
        for doc in docs:
            mimetype = (doc.mimetype or "").split(";")[0].strip().lower()
            if mimetype not in REFERENCE_MIMETYPES and not mimetype.startswith(IMAGE_PREFIX):
                omitidos.append({"id": doc.id, "nombre": doc.name,
                                 "motivo": _("no es PDF ni imagen")})
                continue
            att = self._copy_to_attachment(env, doc, rol)
            if not att:
                omitidos.append({"id": doc.id, "nombre": doc.name,
                                 "motivo": _("el documento no tiene archivo adjunto")})
                continue
            tomados.append({"documento_id": doc.id, "nombre": doc.name,
                            "attachment_id": att.id, "rol": rol, "mimetype": mimetype})

        if not tomados:
            return self._json({"tomados": [], "omitidos": omitidos,
                               "nota": "No pude tomar ningún archivo utilizable."})
        return self._json({
            "tomados": tomados, "omitidos": omitidos,
            "nota": "Listo. Usá el attachment_id de la REFERENCIA para analizar_sitio (flujo con "
                    "PDF) o pasáselo al rol diseñador. Los assets (logo/fotos) van como material "
                    "real del plan.",
        })

    # ==================================================================
    #  Resolución de carpetas (id | nombre | ruta con barras)
    # ==================================================================
    @api.model
    def _resolve_folder(self, env, referencia):
        """Devuelve (folder, mensaje_de_problema). Nunca adivina entre varias candidatas."""
        texto = str(referencia or "").strip()
        if not texto:
            return None, _("Decime qué carpeta de Documentos querés que use.")

        Doc = env["documents.document"]

        # 1) Por id.
        if texto.isdigit():
            folder = Doc.browse(int(texto)).exists()
            if not folder:
                return None, _("No existe la carpeta con id %s o no podés verla.") % texto
            try:
                folder.check_access("read")
            except Exception:  # noqa: BLE001
                return None, _("No tenés acceso a la carpeta con id %s.") % texto
            if folder.type != "folder":
                return None, _("El documento %s no es una carpeta.") % texto
            return folder, None

        # 2) Por ruta ("Marketing / Web / Rediseño 2026"): se camina el árbol nivel por nivel.
        partes = [p.strip() for p in re.split(r"\s*/\s*", texto) if p.strip()]
        if len(partes) > 1:
            padre = None
            for i, parte in enumerate(partes):
                dominio = [("type", "=", "folder"), ("name", "=ilike", parte)]
                dominio += [("folder_id", "=", padre.id)] if padre else [("folder_id", "=", False)]
                actual = Doc.search(dominio, limit=5)
                if not actual and padre:
                    # Puede que el usuario haya salteado un nivel intermedio.
                    actual = Doc.search([("type", "=", "folder"), ("name", "=ilike", parte),
                                         ("folder_id", "child_of", padre.id)], limit=5)
                if not actual:
                    recorrido = " / ".join(partes[:i]) or _("(raíz)")
                    hijas = self._children_names(Doc, padre)
                    return None, _(
                        "No encontré «%(parte)s» dentro de %(recorrido)s. Lo que hay ahí es: "
                        "%(hijas)s. ¿Cuál es?"
                    ) % {"parte": parte, "recorrido": recorrido,
                         "hijas": ", ".join(hijas) or _("nada visible para vos")}
                if len(actual) > 1:
                    return None, self._ambiguo(actual, parte)
                padre = actual
            return padre, None

        # 3) Por nombre suelto.
        candidatas = Doc.search([("type", "=", "folder"), ("name", "=ilike", texto)], limit=10)
        if not candidatas:
            candidatas = Doc.search([("type", "=", "folder"), ("name", "ilike", texto)], limit=10)
        if not candidatas:
            raices = self._children_names(Doc, None)
            return None, _(
                "No encontré ninguna carpeta que se llame «%(texto)s» y que vos puedas ver. "
                "Las carpetas de primer nivel son: %(raices)s."
            ) % {"texto": texto, "raices": ", ".join(raices) or _("ninguna")}
        if len(candidatas) > 1:
            return None, self._ambiguo(candidatas, texto)
        return candidatas, None

    @api.model
    def _ambiguo(self, candidatas, texto):
        opciones = ["%s (id %s, en %s)" % (c.name, c.id, self._folder_path(c) or _("raíz"))
                    for c in candidatas]
        return _(
            "Hay %(n)s carpetas que coinciden con «%(texto)s»: %(opciones)s. ¿Cuál querés? "
            "(pasame el id). No elijo yo."
        ) % {"n": len(candidatas), "texto": texto, "opciones": "; ".join(opciones)}

    @api.model
    def _children_names(self, Doc, padre):
        dominio = [("type", "=", "folder")]
        dominio += [("folder_id", "=", padre.id)] if padre else [("folder_id", "=", False)]
        return Doc.search(dominio, limit=25).mapped("name")

    @api.model
    def _folder_path(self, folder):
        """Ruta legible 'Padre / Hija', recorriendo folder_id con los permisos del usuario."""
        partes, actual, guard = [], folder, 0
        while actual and guard < 12:
            partes.append(actual.name or "?")
            actual = actual.folder_id
            guard += 1
        return " / ".join(reversed(partes))

    # ==================================================================
    #  Copia a un adjunto propio de Sagui
    # ==================================================================
    @api.model
    def _copy_to_attachment(self, env, doc, rol):
        """Copia el archivo del documento a un ir.attachment PROPIO de Sagui.

        No se reusa el adjunto de Documentos a propósito: el pipeline de generación escribe
        metadatos (primate_landing_*) sobre el adjunto, y eso no tiene por qué ensuciar el
        documento del cliente. Se lee con los permisos del usuario y se crea a su nombre.
        """
        origen = doc.attachment_id
        if not origen:
            return None
        try:
            raw = origen.raw
        except Exception:  # noqa: BLE001
            return None
        if not raw:
            return None
        # Un asset que se va a MOSTRAR en un sitio público tiene que ser legible por un
        # visitante anónimo. Si no, Odoo le sirve su placeholder de cámara y el sitio queda con
        # una foto rota que además dispara el veto B4 de la rúbrica. La REFERENCIA no: es
        # material de entrada para el modelo, nunca se publica, y se queda privada.
        publico = rol != "referencia"
        return env["ir.attachment"].create({
            "name": doc.name or origen.name,
            "raw": raw,
            "mimetype": doc.mimetype or origen.mimetype,
            "public": publico,
            "description": _("Referencia de diseño (%(rol)s) tomada de Documentos: %(ruta)s")
                           % {"rol": rol, "ruta": self._folder_path(doc.folder_id)},
        })

    @api.model
    def _json(self, payload):
        return json.dumps(payload, ensure_ascii=False, default=str)
