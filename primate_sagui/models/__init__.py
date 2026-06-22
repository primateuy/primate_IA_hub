from . import design_engine
from . import sagui_conversation
from . import sagui_pending_write
from . import sagui_assistant
from . import sagui_greenfield
from . import sagui_import
from . import sagui_website
from . import discuss_channel
from . import sagui_executable_task
from . import sagui_automation
from . import sagui_recipe
from . import res_config_settings
# Al final: define modelos nuevos (sagui.connector*) y un _inherit de primate.sagui.assistant
# (que ya debe estar definido por sagui_assistant, arriba).
from . import sagui_connector
