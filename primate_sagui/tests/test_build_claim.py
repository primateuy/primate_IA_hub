"""La toma de builds por el cron: ventana, exclusión y aviso con causa.

El cron pasó de correr una vez por día a cada 10 minutos, y eso abre un riesgo nuevo: un build
SANO tarda varios minutos, así que la barrida siguiente podría retomarlo y generar el sitio dos
veces, cobrando los tokens dos veces. Estos tests fijan las tres garantías.
"""
import json

from odoo import fields
from odoo.tests.common import TransactionCase


class TestBuildClaim(TransactionCase):

    def setUp(self):
        super().setUp()
        self.assistant = self.env["primate.sagui.assistant"]
        self.Pending = self.env["primate.sagui.pending.write"]
        self.channel = self.env["discuss.channel"].create(
            {"name": "Test builds", "channel_type": "channel"})

    def _pending(self, minutes_ago=None, attempts=0, operation="website"):
        vals = {
            "token": "aa11bb", "channel_id": self.channel.id, "user_id": self.env.user.id,
            "operation": operation, "model_name": "website.page", "values_json": "{}",
            "summary": "x", "state": "processing", "build_attempts": attempts,
        }
        if minutes_ago is not None:
            vals["build_started_at"] = fields.Datetime.subtract(
                fields.Datetime.now(), minutes=minutes_ago)
        return self.Pending.create(vals)

    def test_a_build_that_never_started_is_claimed(self):
        pending = self._pending()

        self.assertIn(pending, self.assistant._claim_pending_builds(self.Pending.sudo()))

    def test_a_running_build_is_left_alone(self):
        """LA GARANTÍA QUE PAGA EL CRON DE 10 MINUTOS.

        Un build en curso hace 5 minutos está trabajando, no colgado. Retomarlo generaría el
        sitio dos veces y cobraría los tokens dos veces.
        """
        self._pending(minutes_ago=5, attempts=1)

        self.assertFalse(self.assistant._claim_pending_builds(self.Pending.sudo()))

    def test_a_build_stuck_past_the_window_is_reclaimed(self):
        pending = self._pending(minutes_ago=40, attempts=1)

        self.assertIn(pending, self.assistant._claim_pending_builds(self.Pending.sudo()))

    def test_two_crons_do_not_claim_the_same_pending(self):
        """El cron corre por intervalo Y por _trigger(): pueden solaparse."""
        self._pending()

        primera = self.assistant._claim_pending_builds(self.Pending.sudo())
        segunda = self.assistant._claim_pending_builds(self.Pending.sudo())

        self.assertTrue(primera, "la primera corrida tiene que tomarlo")
        self.assertFalse(segunda, "la segunda no puede volver a tomar lo mismo")

    def test_claiming_stamps_when_the_build_started(self):
        pending = self._pending()

        self.assistant._claim_pending_builds(self.Pending.sudo())

        self.assertTrue(pending.build_started_at, "sin sello, la ventana no puede medirse")

    def test_the_interruption_notice_always_says_a_cause(self):
        """Un aviso que dice «se cortó» y nada más no deja decidir nada."""
        con_sello = self._pending(minutes_ago=40, attempts=1)
        sin_sello = self._pending(attempts=1)

        for pending in (con_sello, sin_sello):
            causa = self.assistant._build_interrupted_cause(pending)
            self.assertTrue((causa or "").strip(), "la causa no puede venir vacía")

    def test_the_cause_names_the_phase_for_a_design_run(self):
        """El diseñador sabe en qué fase murió y lo dice: cada fase falla distinto."""
        if "sagui.design.run" not in self.env:
            self.skipTest("el módulo del diseñador no está instalado")
        role = self.env["sagui.role"].search([("key", "=", "web_designer")], limit=1)
        if not role:
            self.skipTest("falta el rol del diseñador")
        run = self.env["sagui.design.run"].create({
            "name": "x", "role_id": role.id, "source": "greenfield",
            "brief": "x", "state": "verifying"})
        pending = self._pending(minutes_ago=40, attempts=1, operation="website_designer")
        pending.values_json = json.dumps({"design_run_id": run.id})

        causa = self.assistant._build_interrupted_cause(pending)

        self.assertIn("VERIFICANDO", causa)
        self.assertIn("la página existe", causa)
