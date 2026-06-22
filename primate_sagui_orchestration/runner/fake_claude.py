#!/usr/bin/env python3
# Claude Code FALSO para tests del runner: ejecuta la acción mínima del prompt y emite JSON como CC.
import json, sys, datetime, os
prompt = ""
if "-p" in sys.argv:
    i = sys.argv.index("-p")
    prompt = sys.argv[i+1] if i+1 < len(sys.argv) else ""
# efecto real mínimo: crea HELLO.txt (simula la edición que haría CC)
try:
    with open("HELLO.txt", "w") as fh:
        fh.write("hola desde fake_claude — %s\n" % datetime.datetime.now().isoformat())
except Exception as e:
    pass
print(json.dumps({
    "type": "result", "subtype": "success", "is_error": False,
    "result": "Creé HELLO.txt con la fecha/hora. (prompt: %s)" % prompt[:60],
    "total_cost_usd": 0.0123, "num_turns": 3, "duration_ms": 1500,
    "session_id": "fake-session-123",
}))
