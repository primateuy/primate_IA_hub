# -*- coding: utf-8 -*-
# Importación de planillas (Excel/CSV/ODS) por Sagui. El LLM hace el mapeo inteligente
# columna->campo; la creación la hace el importador NATIVO de Odoo (base_import), nunca un
# bulk-create crudo. Todo pasa por el flujo de escritura-con-confirmación de sagui_assistant:
#   1) analizar_importacion (solo lectura): parse_preview -> headers + muestras + campos.
#   2) el LLM propone el mapeo y llama ejecutar_importacion -> registra una PROPUESTA (dry-run
#      para el preview) que el usuario confirma con "confirmar <token>".
#   3) al confirmar, _execute_import corre base_import.execute_import CON LOS PERMISOS DEL USUARIO.
#
# [VERIFICADO v19] API de base_import (addons/base_import/models/base_import.py):
#   - base_import.import: campos res_model, file (binario CRUDO, no base64), file_name, file_type.
#   - parse_preview(options, count) -> {fields, matches, headers, preview, num_rows, ...}.
#   - execute_import(fields, columns, options, dryrun=False) -> {ids, messages, ...} (usa load()).
#   - load() upsertea SOLO por '.id'/xmlid -> para upsert por default_code resolvemos el .id
#     en un pre-paso y lo inyectamos como columna.
#   - name_create_enabled_fields: dict {campo: True} para crear m2o faltantes por nombre.
import csv
import datetime
import io
import json
import logging
import secrets

from odoo import models, _

_logger = logging.getLogger(__name__)

IMPORT_MODEL_DEFAULT = "product.template"
# Campos por los que detectamos duplicados/upsert (en orden de prioridad).
DEDUP_FIELDS = ("default_code", "barcode")
# Tope de filas de una planilla (acota costo/uso; los archivos de productos son chicos).
MAX_IMPORT_ROWS = 5000

# v19 cambió product.template.type: ya NO existe 'product' (almacenable). Ahora el tipo es
# consu/service/combo y lo almacenable se controla con is_storable. Traducimos valores
# legacy o en español a la clave válida para que la importación no falle por el cambio.
PRODUCT_TYPE_ALIASES = {
    "product": "consu", "producto": "consu", "storable": "consu", "almacenable": "consu",
    "stockable": "consu", "goods": "consu", "bien": "consu", "bienes": "consu",
    "material": "consu", "mercaderia": "consu", "mercadería": "consu",
    "consu": "consu", "consumible": "consu", "consumable": "consu",
    "service": "service", "servicio": "service", "serv": "service",
    "services": "service", "servicios": "service",
    "combo": "combo", "kit": "combo",
}


class SaguiImport(models.AbstractModel):
    _inherit = "primate.sagui.assistant"

    # ---------- tool specs (se suman a las del assistant base) ----------
    def _tool_specs(self):
        specs = super()._tool_specs()
        # Las tools de importación van DETRÁS del flujo de escritura: solo si hay whitelist.
        if self._write_whitelist():
            specs += [
                {
                    "name": "analizar_importacion",
                    "description": "Analiza una planilla adjunta (Excel/CSV/ODS) para importarla. "
                                   "SOLO LECTURA: devuelve las columnas (headers), valores de "
                                   "muestra por columna, la cantidad de filas, los campos "
                                   "importables del modelo y una sugerencia de mapeo de Odoo. "
                                   "Usá esto ANTES de proponer la importación, con el attachment_id "
                                   "que aparece en la nota de la planilla adjunta.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "attachment_id": {"type": "integer", "description": "ID del ir.attachment de la planilla"},
                            "model": {"type": "string", "description": "Modelo destino (default 'product.template')"},
                        },
                        "required": ["attachment_id"],
                    },
                },
                {
                    "name": "ejecutar_importacion",
                    "description": "PROPONE importar la planilla con el mapeo columna->campo que "
                                   "vos definís. NO importa nada: registra una propuesta con un "
                                   "token que el usuario debe confirmar en el chat (igual que las "
                                   "otras escrituras). La creación real la hace el importador de "
                                   "Odoo. Antes de llamar, explicá en una frase qué vas a importar.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "attachment_id": {"type": "integer"},
                            "model": {"type": "string", "description": "Default 'product.template'"},
                            "mapping": {
                                "type": "object",
                                "description": "Mapa header_de_la_planilla -> campo técnico de Odoo. "
                                               "Ej: {\"Código\": \"default_code\", \"Nombre\": \"name\", "
                                               "\"Precio Venta\": \"list_price\", \"Categoría\": \"categ_id\", "
                                               "\"UdM\": \"uom_id\"}. Los m2o (categ_id, uom_id) se resuelven "
                                               "por NOMBRE. Omití del mapa las columnas a ignorar.",
                            },
                            "options": {
                                "type": "object",
                                "description": "Opciones: 'update_existing' (bool, default false) "
                                               "actualiza los productos existentes que coincidan por "
                                               "default_code/barcode en vez de crear nuevos; "
                                               "'name_create_fields' (lista de campos m2o, ej. "
                                               "[\"categ_id\"]) crea por nombre los relacionados que no "
                                               "existan (si no, una fila con un relacionado inexistente da error).",
                            },
                        },
                        "required": ["attachment_id", "mapping"],
                    },
                },
            ]
        return specs

    # ---------- system prompt: instrucciones de importación ----------
    def _system_prompt(self):
        prompt = super()._system_prompt()
        if self._write_whitelist():
            prompt += "\n\n" + self.env["sagui.skill"].content_of("spreadsheet-import")
        return prompt

    # ---------- dispatch de tools ----------
    def _run_tool(self, name, args, user, channel=None, proposals=None):
        if name == "analizar_importacion":
            return self._tool_analizar_importacion(args, user)
        if name == "ejecutar_importacion":
            return self._propose_import(args, user, channel, proposals)
        return super()._run_tool(name, args, user, channel=channel, proposals=proposals)

    # ---------- ejecución de una propuesta de importación confirmada ----------
    def _execute_pending(self, channel, author, pending):
        if pending.operation == "import":
            return self._execute_import(channel, author, pending)
        return super()._execute_pending(channel, author, pending)

    # ======================================================================
    #  Tool 1: analizar_importacion (SOLO LECTURA)
    # ======================================================================
    def _tool_analizar_importacion(self, args, user):
        env = self.env(user=user.id)  # permisos del usuario
        model = args.get("model") or IMPORT_MODEL_DEFAULT
        att = self._get_import_attachment(env, args.get("attachment_id"))
        if isinstance(att, str):
            return att  # mensaje de error para el modelo

        imp = env["base_import.import"].create({
            "res_model": model,
            "file": att.raw,
            "file_name": att.name or "import",
            "file_type": att.mimetype or "",
        })
        options = self._import_options_default()
        preview = imp.parse_preview(options)
        if preview.get("error"):
            return json.dumps({"error": preview["error"]}, ensure_ascii=False)

        headers = preview.get("headers") or []
        examples = preview.get("preview") or []   # lista por columna de valores de muestra
        matches = preview.get("matches") or {}    # {indice(str): [field_path]}
        muestras = {
            h: (examples[i] if i < len(examples) else [])
            for i, h in enumerate(headers)
        }
        sugerencias = {
            headers[int(i)]: "/".join(p)
            for i, p in matches.items()
            if p and int(i) < len(headers)
        }
        # parse_preview cuenta TODAS las filas (incluida la de headers): restamos la cabecera.
        num_rows = preview.get("num_rows")
        if isinstance(num_rows, int) and headers:
            num_rows = max(num_rows - 1, 0)
        # Campos importables de primer nivel (nombre, etiqueta, requerido), acotado en tokens.
        campos = [
            {"campo": f["name"], "etiqueta": f.get("string"), "requerido": bool(f.get("required"))}
            for f in (preview.get("fields") or [])
            if f.get("name")
        ]
        return json.dumps({
            "model": model,
            "num_filas": num_rows,
            "columnas": headers,
            "muestras_por_columna": muestras,
            "sugerencia_mapeo_odoo": sugerencias,
            "campos_importables": campos,
        }, default=str, ensure_ascii=False)

    # ======================================================================
    #  Tool 2: ejecutar_importacion -> PROPONE (no ejecuta)
    # ======================================================================
    def _propose_import(self, args, user, channel, proposals):
        if not channel:
            return _("No puedo proponer importaciones fuera de un canal de chat.")
        whitelist = self._write_whitelist()
        model = args.get("model") or IMPORT_MODEL_DEFAULT
        if model not in whitelist:
            return _(
                "No tengo permitido escribir en '%(model)s'. Habilitados: %(wl)s."
            ) % {"model": model, "wl": ", ".join(sorted(whitelist)) or _("(ninguno)")}

        mapping = args.get("mapping") or {}
        if not isinstance(mapping, dict) or not mapping:
            return _("Falta el mapeo de columnas a campos.")
        options_in = args.get("options") or {}
        if not isinstance(options_in, dict):
            options_in = {}

        env_user = self.env(user=user.id)
        att = self._get_import_attachment(env_user, args.get("attachment_id"))
        if isinstance(att, str):
            return att
        try:
            env_user[model].check_access("create")
        except Exception as e:  # noqa: BLE001
            return _("No tenés permiso para crear en %(model)s: %(err)s") % {"model": model, "err": e}

        # Dry-run: valida el mapeo y arma el preview con conteos (sin tocar la base).
        preview = self._import_dry_run(env_user, model, att, mapping, options_in)
        if preview.get("error"):
            return _("No pude preparar la importación: %s. Revisá el mapeo.") % preview["error"]

        summary = self._summary_import(model, att, mapping, options_in, preview)
        token = secrets.token_hex(3)
        pending = self.env["primate.sagui.pending.write"].sudo().create({
            "token": token,
            "channel_id": channel.id,
            "user_id": user.id,
            "operation": "import",
            "model_name": model,
            "values_json": json.dumps({
                "attachment_id": att.id,
                "mapping": mapping,
                "options": options_in,
            }, ensure_ascii=False),
            "summary": summary,
            "state": "pending",
        })
        if proposals is not None:
            proposals.append(pending.id)
        return _(
            "PROPUESTA REGISTRADA (token %(token)s). NO se importó nada. Pedile al usuario que "
            "responda 'confirmar %(token)s' para aplicar o 'cancelar %(token)s' para descartar. "
            "Resumen: %(summary)s"
        ) % {"token": token, "summary": summary}

    # ======================================================================
    #  Ejecución real (al confirmar)
    # ======================================================================
    def _execute_import(self, channel, author, pending):
        env_user = self.env(user=author.id)  # CRÍTICO: importar como el usuario
        try:
            data = json.loads(pending.values_json or "{}")
            model = pending.model_name
            if model not in self._write_whitelist():
                raise ValueError(_("la escritura en %s ya no está habilitada") % model)
            att = self._get_import_attachment(env_user, data.get("attachment_id"))
            if isinstance(att, str):
                raise ValueError(att)
            prep = self._prepare_import(env_user, model, att, data.get("mapping") or {},
                                        data.get("options") or {})
            if prep.get("error"):
                raise ValueError(prep["error"])

            # Validar primero con un dry-run: base_import es atómico (una fila mala bloquea
            # todo y, en ejecución real, deja la transacción con cache envenenado que
            # explota en el flush siguiente). Si hay errores, reportamos y NO ejecutamos.
            check = prep["imp"].execute_import(
                prep["fields"], prep["columns"], dict(prep["options"]), dryrun=True)
            check_errors = [m for m in (check.get("messages") or []) if m.get("type") == "error"]
            if check_errors or not check.get("ids"):
                pending.write({"state": "error",
                               "result_info": "%s fila(s) con error" % len(check_errors)})
                self._post_bot_reply(channel, self._import_error_text(check_errors))
                return

            # Limpio -> ejecutar de verdad.
            result = prep["imp"].execute_import(
                prep["fields"], prep["columns"], dict(prep["options"]), dryrun=False)
            ids = result.get("ids") or []
            messages = result.get("messages") or []
            errors = [m for m in messages if m.get("type") == "error"]
            matched = prep["matched"] if prep["update_existing"] else 0
            updated = min(matched, len(ids))
            created = len(ids) - updated

            pending.write({
                "state": "done",
                "result_info": "creados=%s actualizados=%s errores=%s" % (created, updated, len(errors)),
            })
            self._post_bot_reply(channel, self._import_result_text(
                prep["num_rows"], created, updated, errors, prep.get("type_changes")))
        except Exception as e:  # noqa: BLE001
            _logger.exception("Sagui: falló la importación %s", pending.token)
            pending.write({"state": "error", "result_info": str(e)[:200]})
            self._post_bot_reply(channel, _("❌ No pude importar (%(token)s): %(err)s") % {
                "token": pending.token, "err": e})

    # ======================================================================
    #  Helpers
    # ======================================================================
    def _get_import_attachment(self, env, attachment_id):
        """Devuelve el ir.attachment (leído con permisos del usuario) o un str de error."""
        try:
            att = env["ir.attachment"].browse(int(attachment_id or 0))
            att.check_access("read")
        except Exception:  # noqa: BLE001
            return _("No encontré el adjunto %s o no podés verlo.") % attachment_id
        if not att.exists():
            return _("El adjunto %s ya no existe.") % attachment_id
        mimetype = (att.mimetype or "").split(";")[0].strip().lower()
        ext = (att.name or "").lower()
        ok = mimetype in {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "application/vnd.oasis.opendocument.spreadsheet",
            "text/csv",
        } or ext.endswith((".xlsx", ".xls", ".ods", ".csv"))
        if not ok:
            return _("El adjunto '%s' no parece una planilla (Excel/CSV/ODS).") % (att.name or "?")
        return att

    def _import_options_default(self):
        """Opciones base para parse_preview/execute_import (verificadas contra base_import)."""
        return {
            "has_headers": True,
            "advanced": True,
            "keep_matches": False,
            "quoting": '"',
            "separator": ",",
            "date_format": "",
            "datetime_format": "",
            "float_thousand_separator": ",",
            "float_decimal_separator": ".",
            "name_create_enabled_fields": {},
            "import_skip_records": [],
            "import_set_empty_fields": [],
            "fallback_values": {},
            "fields": [],
        }

    def _import_dry_run(self, env_user, model, att, mapping, options_in):
        """Prepara la importación y la corre en dryrun para armar el preview con conteos."""
        prep = self._prepare_import(env_user, model, att, mapping, options_in)
        if prep.get("error"):
            return prep
        try:
            result = prep["imp"].execute_import(
                prep["fields"], prep["columns"], dict(prep["options"]), dryrun=True
            )
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        messages = result.get("messages") or []
        errors = [m for m in messages if m.get("type") == "error"]
        return {
            "num_rows": prep["num_rows"],
            "ok_rows": len(result.get("ids") or []),
            "matched": prep["matched"],
            "update_existing": prep["update_existing"],
            "n_errors": len(errors),
            "errors": [m.get("message") for m in errors[:5]],
            "type_changes": prep.get("type_changes") or {},
        }

    def _prepare_import(self, env_user, model, att, mapping, options_in):
        """Arma el base_import.import listo para ejecutar.

        Lee las filas, detecta duplicados por default_code/barcode y, si update_existing,
        reconstruye un CSV con una columna '.id' (el id existente) para que base_import
        haga upsert (load() solo upsertea por .id/xmlid). Devuelve dict con
        {imp, fields, columns, options, num_rows, matched, update_existing} o {error}.
        """
        options = self._import_options_default()
        name_create = options_in.get("name_create_fields") or []
        if isinstance(name_create, list) and name_create:
            options["name_create_enabled_fields"] = {f: True for f in name_create}

        base_imp = env_user["base_import.import"].create({
            "res_model": model,
            "file": att.raw,
            "file_name": att.name or "import",
            "file_type": att.mimetype or "",
        })
        try:
            _file_length, data_rows = base_imp._read_file(dict(options))
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        data_rows = list(data_rows or [])
        if len(data_rows) < 2:
            return {"error": _("la planilla no tiene filas de datos")}
        headers = [str(h) for h in data_rows[0]]
        rows = data_rows[1:]
        if len(rows) > MAX_IMPORT_ROWS:
            return {"error": _("la planilla tiene %s filas; el máximo es %s") % (len(rows), MAX_IMPORT_ROWS)}

        fields_list = [mapping.get(h) or False for h in headers]
        columns = list(headers)
        if not any(fields_list):
            return {"error": _("el mapeo no coincide con ninguna columna de la planilla")}

        update_existing = bool(options_in.get("update_existing"))
        row_ids, matched = self._resolve_existing_ids(env_user, model, headers, rows, mapping)
        # Normaliza el tipo de producto a las claves válidas de v19 (muta `rows`).
        type_changes = self._normalize_product_type(env_user, model, headers, rows, mapping)

        add_id = update_existing and matched
        # Hay que reconstruir el archivo como CSV si: (a) inyectamos la columna .id para
        # upsert, o (b) normalizamos algún valor (el archivo original no lo refleja).
        needs_rebuild = bool(add_id) or bool(type_changes)

        imp = base_imp
        if needs_rebuild:
            new_headers = list(headers) + ([".id"] if add_id else [])
            new_rows = []
            for row, rid in zip(rows, row_ids):
                cells = [self._cell_to_str(c) for c in row]
                if add_id:
                    cells.append(str(rid) if rid else "")
                new_rows.append(cells)
            csv_bytes = self._rows_to_csv(new_headers, new_rows)
            imp = env_user["base_import.import"].create({
                "res_model": model,
                "file": csv_bytes,
                "file_name": (att.name or "import") + ".csv",
                "file_type": "text/csv",
            })
            if add_id:
                fields_list = fields_list + [".id"]
                columns = columns + [".id"]

        return {
            "imp": imp,
            "fields": fields_list,
            "columns": columns,
            "options": options,
            "num_rows": len(rows),
            "matched": matched,
            "update_existing": update_existing,
            "type_changes": type_changes,
        }

    def _normalize_product_type(self, env_user, model, headers, rows, mapping):
        """Traduce los valores de la columna mapeada a 'type' en product.template a las
        claves válidas de v19 (consu/service/combo). Muta `rows` y devuelve un dict
        {valor_original: clave} con lo que se cambió (para informarlo en el preview)."""
        if model != "product.template":
            return {}
        idx = next((i for i, h in enumerate(headers) if mapping.get(h) == "type"), None)
        if idx is None:
            return {}
        sel = env_user["product.template"]._fields["type"].selection
        if callable(sel):
            sel = sel(env_user["product.template"])
        valid = {k for k, _label in sel}
        changes = {}
        for r in rows:
            if idx >= len(r):
                continue
            raw = self._cell_to_str(r[idx]).strip()
            if not raw:
                continue
            low = raw.lower()
            if low in valid:
                continue  # ya es una clave válida
            mapped = PRODUCT_TYPE_ALIASES.get(low)
            if mapped and mapped != raw:
                r[idx] = mapped
                changes[raw] = mapped
        return changes

    def _resolve_existing_ids(self, env_user, model, headers, rows, mapping):
        """Para cada fila, resuelve el id de un registro existente por default_code y luego
        barcode. Devuelve (lista_de_ids_alineada_a_rows, cantidad_de_coincidencias)."""
        idx = {}
        for field in DEDUP_FIELDS:
            for i, h in enumerate(headers):
                if mapping.get(h) == field:
                    idx[field] = i
                    break
        if not idx:
            return [None] * len(rows), 0

        Model = env_user[model]
        maps = {}
        for field, i in idx.items():
            if field not in Model._fields:
                continue
            values = {self._cell_to_str(r[i]).strip() for r in rows if i < len(r) and self._cell_to_str(r[i]).strip()}
            if not values:
                continue
            existing = Model.search([(field, "in", list(values))])
            maps[field] = {rec[field]: rec.id for rec in existing if rec[field]}

        row_ids, matched = [], 0
        for r in rows:
            rid = None
            for field in DEDUP_FIELDS:
                if field in idx and field in maps:
                    val = self._cell_to_str(r[idx[field]]).strip()
                    if val and val in maps[field]:
                        rid = maps[field][val]
                        break
            row_ids.append(rid)
            if rid:
                matched += 1
        return row_ids, matched

    def _cell_to_str(self, value):
        """Convierte una celda (de openpyxl/xlrd/csv) a texto estable para re-serializar."""
        if value is None:
            return ""
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, float) and value.is_integer():
            return str(int(value))  # evita "7.0" en códigos numéricos
        if isinstance(value, (datetime.datetime, datetime.date)):
            return value.isoformat()
        return str(value)

    def _rows_to_csv(self, headers, rows):
        """Serializa headers+rows a bytes CSV (utf-8) para alimentar base_import."""
        buf = io.StringIO()
        writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(headers)
        writer.writerows(rows)
        return buf.getvalue().encode("utf-8")

    def _summary_import(self, model, att, mapping, options_in, preview):
        """Preview legible de la importación propuesta (fuente de verdad: el dry-run)."""
        label = self.env[model]._description or model
        pares = "\n  - ".join("«%s» → %s" % (col, field) for col, field in mapping.items())
        modo = _("ACTUALIZAR existentes + crear") if options_in.get("update_existing") else _("CREAR")
        lineas = [
            _("Importar «%(file)s» a %(label)s (%(model)s) — modo %(modo)s.") % {
                "file": att.name or "archivo", "label": label, "model": model, "modo": modo},
            _("Filas: %(n)s · válidas: %(ok)s · ya existentes (por código/barcode): %(dup)s · con error: %(err)s") % {
                "n": preview.get("num_rows"), "ok": preview.get("ok_rows"),
                "dup": preview.get("matched"), "err": preview.get("n_errors")},
            _("Mapeo:\n  - %s") % pares,
        ]
        if preview.get("matched") and not options_in.get("update_existing"):
            lineas.append(_("⚠️ Hay %s fila(s) cuyo código ya existe: se CREARÁN igualmente "
                            "(activá update_existing para actualizarlas en vez de duplicar).")
                          % preview["matched"])
        if preview.get("type_changes"):
            pares = ", ".join("'%s'→%s" % (k, v) for k, v in preview["type_changes"].items())
            lineas.append(_("ℹ️ Tipo de producto normalizado a v19: %s.") % pares)
        if preview.get("errors"):
            lineas.append(_("Primeros errores: ") + " · ".join(str(e) for e in preview["errors"]))
        return "\n".join(lineas)

    def _import_result_text(self, num_rows, created, updated, errors, type_changes=None):
        msg = _("✅ Importación lista: **%(created)s creados**, **%(updated)s actualizados** "
                "de %(n)s filas.") % {"created": created, "updated": updated, "n": num_rows}
        if type_changes:
            pares = ", ".join("'%s'→%s" % (k, v) for k, v in type_changes.items())
            msg += _("\nℹ️ Normalicé el tipo de producto a v19: %s.") % pares
        if errors:
            ejemplos = " · ".join(str(m.get("message")) for m in errors[:5])
            msg += _("\n⚠️ %(n)s fila(s) con error: %(ej)s") % {"n": len(errors), "ej": ejemplos}
        return msg

    def _import_error_text(self, errors):
        """Mensaje claro cuando el dry-run de validación encuentra filas con error: no se
        importó nada (base_import es atómico). Indica fila y motivo para corregir."""
        ejemplos = []
        for m in errors[:8]:
            rows = m.get("rows") or {}
            fila = rows.get("from")
            # +2: la fila 0 de datos es la línea 2 del archivo (línea 1 = headers).
            loc = (_(" (fila %s)") % (fila + 2)) if isinstance(fila, int) else ""
            ejemplos.append("• %s%s" % (m.get("message"), loc))
        return _(
            "❌ No importé nada: %(n)s fila(s) con error (la importación es todo-o-nada). "
            "Corregí la planilla o el mapeo y volvé a pedírmelo.\n%(ej)s"
        ) % {"n": len(errors), "ej": "\n".join(ejemplos)}
