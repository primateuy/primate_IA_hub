# sagui_runner — puente Sagui ↔ Claude Code (Fase 1)

Runner local (corre en tu Mac) que poolea a Sagui (Odoo, en el server), invoca **Claude Code
headless** en el repo indicado y reporta el resultado. Fase 1 = **esqueleto caminante**: valida la
plomería punta a punta con datos dummy, sin lógica real de Odoo.

## Requisitos
- Python 3.11+ (solo stdlib, sin `pip install`).
- Claude Code (`claude`) instalado y logueado.
- **Para Fase 3 (upload/PR):** `git` y `gh` (GitHub CLI) instalados y **autenticados** en la Mac,
  y el repo del cliente con remoto `origin` y permisos de push. En el `fix` verde el runner commitea
  a `sagui/fix-<id>` (sin pushear); en el `upload` (tras tu aprobación en Sagui) hace `git push` +
  `gh pr create` y reporta la URL del PR.

## Configurar
```bash
cp .env.example .env   # y completá SAGUI_URL / SAGUI_TOKEN
```

## Modo mock (sin Sagui)
Lee `mock_job.json`, invoca Claude Code en `./sandbox` (repo git descartable) e imprime los payloads
de `start` y `done` por consola. Ideal para probar la invocación de CC y el parseo aislados.
```bash
python3 sagui_runner.py --mock
```
El prompt de prueba es trivial y seguro (crear `HELLO.txt`).

> Para testear la plomería SIN gastar tokens de Claude, apuntá a un `claude` falso
> (`fake_claude.py` emula la salida JSON de Claude Code y crea `HELLO.txt`):
> ```bash
> CLAUDE_BIN="$PWD/fake_claude.py" python3 sagui_runner.py --mock
> ```
> (`CLAUDE_BIN` debe ser un único ejecutable; `fake_claude.py` tiene shebang y es ejecutable.)

## Modo real
```bash
python3 sagui_runner.py
```
Loop: `GET /sagui/jobs/next` (Bearer) → si hay job: `POST /jobs/{id}/start` → invoca Claude Code →
`POST /jobs/{id}/done` con `{status, cost_usd, num_turns, summary}`. Reintenta si Sagui está caído;
el loop nunca muere por un job. `Ctrl+C` para cortar.

## Job
```json
{"job_id": "...", "type": "fix", "repo_path": "/ruta/abs/al/repo",
 "prompt": "...", "allowed_tools": "Read Write Edit", "max_turns": 5}
```
`allowed_tools` y `max_turns` **siempre** vienen del job (el runner no los hardcodea).

## Logs
Todo a stdout y a `sagui_runner.log`.

## Fuera de alcance (fase 1)
Correr tests (lo hace Sagui), distinguir fix/upload, el hook de Claude Code.
