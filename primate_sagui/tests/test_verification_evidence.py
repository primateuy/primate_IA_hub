# -*- coding: utf-8 -*-
"""Los DOS caminos de evidencia de sagui.verification.

El Check/Verify nació con un solo caso -el verificador visual del diseñador, que revisa capturas
con un modelo- y ahora tiene un segundo: post-condiciones de datos, que se revisan con una
consulta. Este archivo prueba que el segundo entra SIN romper el primero, porque el primero es el
que está en producción.

Nada de esto le pega a la API: el revisor determinístico no manda ninguna request (eso mismo se
verifica), y el de lenguaje se reemplaza por un doble.
"""

import base64
import json
import struct

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

RUBRICA = """
C1. Ninguna tarea abierta sin responsable.
C2. Ninguna tarea abierta sin etapa.
"""


def png_de(ancho, alto):
    """PNG mínimo con un IHDR real: es lo único que lee _measure_evidence (bytes 16:24)."""
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR"
            + struct.pack(">II", ancho, alto) + b"\x08\x06\x00\x00\x00relleno")


@tagged("post_install", "-at_install")
class VerificationCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Verification = cls.env["sagui.verification"]

    def _captura(self, nombre, ancho=1440, alto=900):
        return self.env["ir.attachment"].create({
            "name": nombre,
            "datas": base64.b64encode(png_de(ancho, alto)).decode(),
            "mimetype": "image/png",
        })

    def _check(self, ident, ok, **extra):
        base = {"id": ident, "titulo": "Post-condición %s" % ident, "ok": ok,
                "count": 0 if ok else 2, "ids": [] if ok else [11, 22],
                "detalle": "" if ok else "2 tareas abiertas sin responsable: 11, 22",
                "fix": "" if ok else "Asignar responsable o cerrarlas"}
        base.update(extra)
        return base

    def _texto(self, ident, ok, **extra):
        check = self._check(ident, ok, **extra)
        return {"label": check["titulo"], "text": check["detalle"] or "sin infracciones",
                "check": check}


class TestCaminoDeImagen(VerificationCommon):
    """El camino del diseñador. Si algo de esto cambia, se rompió lo que está en producción."""

    def test_una_captura_se_mide_y_no_es_de_texto(self):
        captura = self._captura("hero 1440px")
        dims = self.Verification._measure_evidence([{"label": "hero 1440px",
                                                     "attachment_id": captura.id}])
        self.assertEqual(dims[0]["kind"], "image")
        self.assertEqual((dims[0]["width"], dims[0]["height"]), (1440, 900))
        self.assertFalse(dims[0]["degraded"])

    def test_una_captura_escalada_sigue_siendo_evidencia_degradada(self):
        """El caso real: Odoo redimensiona el adjunto y la captura deja de servir."""
        captura = self._captura("hero 1440px", ancho=183, alto=1920)
        dims = self.Verification._measure_evidence([{"label": "hero 1440px",
                                                     "attachment_id": captura.id}])
        self.assertTrue(dims[0]["degraded"])
        self.assertIn("1440", dims[0]["detail"])

    def test_el_gate_de_infra_corta_antes_de_gastar_una_llamada(self):
        captura = self._captura("hero 1440px", ancho=183, alto=1920)
        llamadas = []
        self.patch(type(self.Verification), "_review_anthropic",
                   lambda s, m, r, c, i: llamadas.append(1) or "{}")

        res = self.Verification.run(rubric=RUBRICA, name="visual",
                                    evidence=[{"label": "hero 1440px",
                                               "attachment_id": captura.id}])

        self.assertEqual(res["verdict"], "error")
        self.assertTrue(res["infra"], "es un problema de recolección, no del diseño")
        self.assertFalse(llamadas, "con evidencia degradada no se revisa")

    def test_la_captura_viaja_como_bloque_de_imagen(self):
        captura = self._captura("hero 1440px")
        bloques = self.Verification._build_blocks(
            RUBRICA, None, [{"label": "hero", "attachment_id": captura.id}])
        tipos = [b["type"] for b in bloques]
        self.assertIn("image", tipos)

    def test_el_revisor_de_lenguaje_recibe_el_contrato_nuevo(self):
        """La firma de los adaptadores cambió: todos reciben (model, rubrica, contexto, items)."""
        captura = self._captura("hero 1440px")
        recibido = {}

        def doble(self_v, model, rubric_text, context, items):
            recibido.update({"rubric": rubric_text, "items": items})
            return json.dumps({"verdict": "pass", "blocks": {}, "findings": []})

        self.patch(type(self.Verification), "_review_anthropic", doble)

        res = self.Verification.run(rubric=RUBRICA, name="visual",
                                    evidence=[{"label": "hero 1440px",
                                               "attachment_id": captura.id}])

        self.assertEqual(res["verdict"], "pass")
        self.assertIn("C1", recibido["rubric"])
        self.assertEqual(recibido["items"][0]["attachment_id"], captura.id)


class TestCaminoDeTexto(VerificationCommon):
    def test_la_evidencia_de_texto_no_necesita_adjunto(self):
        items = self.Verification._collect(None, None, [self._texto("C1", ok=True)])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "text")
        self.assertFalse(items[0].get("attachment_id"))

    def test_la_evidencia_de_texto_nunca_esta_degradada_por_dimensiones(self):
        """No tiene ancho ni alto: medirla no significa nada."""
        dims = self.Verification._measure_evidence([self._texto("C1", ok=False)])
        self.assertEqual(dims[0]["kind"], "text")
        self.assertFalse(dims[0]["degraded"])

    def test_una_consulta_que_no_pudo_correr_SI_es_evidencia_degradada(self):
        """Es el equivalente exacto de la captura escalada: falla de recolección, no de gestión."""
        item = self._texto("C1", ok=False, error="dominio inválido: campo 'foo' inexistente")
        dims = self.Verification._measure_evidence([item])
        self.assertTrue(dims[0]["degraded"])
        self.assertIn("dominio inválido", dims[0]["detail"])

    def test_la_evidencia_de_texto_viaja_como_bloque_de_texto(self):
        bloques = self.Verification._build_blocks(RUBRICA, None, [self._texto("C1", ok=False)])
        self.assertTrue(all(b["type"] == "text" for b in bloques))
        self.assertIn("sin responsable", json.dumps(bloques, ensure_ascii=False))


class TestRevisorDeterministico(VerificationCommon):
    def _correr(self, checks):
        return self.Verification.run(
            rubric=RUBRICA, name="post-condiciones", reviewer={"provider": "rules"},
            evidence=[self._texto(c["id"], c["ok"], **{k: v for k, v in c.items()
                                                       if k not in ("id", "ok")})
                      for c in checks])

    def test_todo_en_orden_da_pass_sin_hallazgos(self):
        res = self._correr([{"id": "C1", "ok": True}, {"id": "C2", "ok": True}])
        self.assertEqual(res["verdict"], "pass")
        self.assertFalse(res["findings"])
        self.assertEqual(res["blocks"], {"C1": "pass", "C2": "pass"})

    def test_una_post_condicion_violada_da_fail_con_los_ids(self):
        res = self._correr([{"id": "C1", "ok": False}, {"id": "C2", "ok": True}])
        self.assertEqual(res["verdict"], "fail")
        self.assertEqual(len(res["findings"]), 1)
        hallazgo = res["findings"][0]
        self.assertEqual(hallazgo["rubric"], "C1")
        self.assertEqual(hallazgo["severity"], "FAIL")
        self.assertIn("11", hallazgo["seen"])
        self.assertTrue(hallazgo["fix"], "sin fix el hallazgo se descartaría por vago")
        self.assertEqual(res["blocks"], {"C1": "fail", "C2": "pass"})

    def test_no_gasta_un_solo_token(self):
        """La razón de ser del proveedor: la respuesta es exacta, preguntarla sólo agrega riesgo."""
        llamadas = []
        self.patch(type(self.env["primate.ai.connector"]), "call",
                   lambda s, *a, **k: llamadas.append(1) or {})

        res = self._correr([{"id": "C1", "ok": False}])

        self.assertEqual(res["verdict"], "fail")
        self.assertFalse(llamadas, "el revisor determinístico no manda ninguna request")

    def test_un_hallazgo_sin_detalle_igual_sale_completo(self):
        """_parse descarta los hallazgos vagos: el revisor tiene que llenar seen y fix siempre."""
        res = self._correr([{"id": "C1", "ok": False, "detalle": "", "fix": ""}])
        self.assertEqual(len(res["findings"]), 1, "no se perdió por vago")
        self.assertTrue(res["findings"][0]["seen"])
        self.assertTrue(res["findings"][0]["fix"])

    def test_una_consulta_caida_corta_con_veredicto_de_infra(self):
        res = self.Verification.run(
            rubric=RUBRICA, name="post-condiciones", reviewer={"provider": "rules"},
            evidence=[self._texto("C1", ok=False, error="el modelo 'foo.bar' no existe")])
        self.assertEqual(res["verdict"], "error")
        self.assertTrue(res["infra"])
        self.assertFalse(res["findings"], "no se reprueba lo verificado por un error de consulta")


class TestEvidenciaMixta(VerificationCommon):
    def test_imagen_y_texto_conviven_en_la_misma_verificacion(self):
        captura = self._captura("hero 1440px")
        recibido = {}

        def doble(self_v, model, rubric_text, context, items):
            recibido["items"] = items
            return json.dumps({"verdict": "pass", "blocks": {}, "findings": []})

        self.patch(type(self.Verification), "_review_anthropic", doble)

        res = self.Verification.run(
            rubric=RUBRICA, name="mixta",
            evidence=[{"label": "hero 1440px", "attachment_id": captura.id},
                      self._texto("C1", ok=True)])

        self.assertEqual(res["verdict"], "pass")
        self.assertEqual([i["kind"] for i in recibido["items"]], ["image", "text"])
        self.assertEqual(res["evidence_ids"], captura.ids,
                         "sólo la imagen genera un adjunto asociado")

    def test_sin_ninguna_evidencia_no_se_verifica(self):
        res = self.Verification.run(rubric=RUBRICA, name="vacía", evidence=[])
        self.assertEqual(res["verdict"], "error")
        self.assertIn("evidencia", res["error"].lower())
