"""El cron de builds pasa de 1 día a 10 minutos en las bases YA instaladas.

El registro vive en un archivo `noupdate="1"` —correcto: el intervalo de un cron es algo que
un admin puede querer ajustar y una actualización no debería pisarle—. Pero el valor viejo no
era una preferencia: era el motivo por el que un build muerto por timeout se quedaba callado
hasta el día siguiente, y el usuario lo vivía como «nunca me avisa».

Por eso se corrige UNA vez, acá, y sólo si sigue en el valor viejo: si alguien ya lo ajustó a
mano, se le respeta.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE ir_cron c
           SET interval_number = 10,
               interval_type = 'minutes',
               nextcall = (now() at time zone 'UTC') + interval '10 minutes'
          FROM ir_model_data d
         WHERE d.model = 'ir.cron'
           AND d.module = 'primate_sagui'
           AND d.name = 'cron_build_sites'
           AND c.id = d.res_id
           AND c.interval_type = 'days'
           AND c.interval_number = 1
    """)
    if cr.rowcount:
        _logger.info("Sagui: el cron de builds pasa a correr cada 10 minutos.")
