"""La causa del corte nombra la FASE en la que murió un build del diseñador.

Este test vivía en primate_sagui, donde no podía correr NUNCA: Odoo ejecuta los tests de cada
módulo apenas lo carga, y primate_sagui_designer -del que depende- se carga después. La guarda
"si el modelo no está, salteo" lo dejaba verde por omisión en cada corrida. Acá la dependencia
está garantizada por el manifest, así que corre de verdad.
"""
import json
import uuid

from odoo import fields
from odoo.tests.common import TransactionCase


class TestBuildCause(TransactionCase):

    def setUp(self):
        super().setUp()
        self.assistant = self.env["primate.sagui.assistant"]
        self.Pending = self.env["primate.sagui.pending.write"]
        self.channel = self.env["discuss.channel"].create(
            {"name": "Test causa", "channel_type": "channel"})

    def _pending(self, minutes_ago=None, attempts=0, operation="website_designer"):
        vals = {
            "token": uuid.uuid4().hex[:6], "channel_id": self.channel.id, "user_id": self.env.user.id,
            "operation": operation, "model_name": "website.page", "values_json": "{}",
            "summary": "x", "state": "processing", "build_attempts": attempts,
        }
        if minutes_ago is not None:
            vals["build_started_at"] = fields.Datetime.subtract(
                fields.Datetime.now(), minutes=minutes_ago)
        return self.Pending.create(vals)

    def test_the_cause_names_the_phase_for_a_design_run(self):
        """El diseñador sabe en qué fase murió y lo dice: cada fase falla distinto."""
        role = self.env.ref("primate_sagui_designer.role_web_designer")
        run = self.env["sagui.design.run"].create({
            "name": "x", "role_id": role.id, "source": "greenfield",
            "brief": "x", "state": "verifying"})
        pending = self._pending(minutes_ago=40, attempts=1, operation="website_designer")
        pending.values_json = json.dumps({"design_run_id": run.id})

        causa = self.assistant._build_interrupted_cause(pending)

        self.assertIn("VERIFICANDO", causa)
        self.assertIn("la página existe", causa)
