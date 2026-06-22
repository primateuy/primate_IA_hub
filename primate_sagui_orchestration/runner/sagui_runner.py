#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sagui_runner.py — Runner local: puente Sagui (Odoo) ↔ Claude Code headless.

FASE 1 (esqueleto caminante): pollea a Sagui por jobs, invoca Claude Code en modo
headless dentro del repo indicado, y reporta el resultado. Datos dummy, sin lógica
real de Odoo. El objetivo es validar la plomería punta a punta.

Uso:
    python sagui_runner.py            # modo real: pollea Sagui en loop
    python sagui_runner.py --mock     # modo mock: lee mock_job.json, no postea (imprime payloads)

Config (variables de entorno, o un archivo .env junto a este script):
    SAGUI_URL        base de la API de Sagui (ej. http://localhost:8069)
    SAGUI_TOKEN      bearer token para autenticar contra Sagui
    POLL_INTERVAL    segundos entre polls (default 10)
    CLAUDE_BIN       binario de Claude Code (default 'claude')
    CLAUDE_PERMISSION_MODE  permission mode no interactivo (default 'acceptEdits')
    CLAUDE_TIMEOUT   timeout en segundos para la invocación de CC (default 1200)
    TESTS_TIMEOUT    timeout en segundos para el paso VERIFY (correr tests_cmd) (default 600)

Liviano y sin dependencias externas: usa solo la stdlib (urllib + subprocess).
allowed_tools y max_turns SIEMPRE vienen del job; el runner no los hardcodea.
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

AQUI = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(AQUI, "sagui_runner.log")
# Campos obligatorios de un job bien formado.
CAMPOS_JOB = ("job_id", "type", "repo_path", "prompt", "allowed_tools", "max_turns")
# Timeout (seg) para operaciones git/gh (push puede tardar).
GIT_TIMEOUT = 180

_logger = logging.getLogger("sagui_runner")


# --------------------------------------------------------------------------- config
def cargar_dotenv():
    """Carga un .env simple (KEY=VALUE) junto al script SIN pisar variables ya seteadas."""
    ruta = os.path.join(AQUI, ".env")
    if not os.path.isfile(ruta):
        return
    with open(ruta, "r", encoding="utf-8") as fh:
        for linea in fh:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            clave, valor = clave.strip(), valor.strip().strip('"').strip("'")
            os.environ.setdefault(clave, valor)


@dataclass
class Config:
    sagui_url: str
    sagui_token: str
    poll_interval: int
    claude_bin: str
    permission_mode: str
    claude_timeout: int
    tests_timeout: int

    @classmethod
    def desde_env(cls):
        return cls(
            sagui_url=(os.environ.get("SAGUI_URL") or "").rstrip("/"),
            sagui_token=os.environ.get("SAGUI_TOKEN") or "",
            poll_interval=int(os.environ.get("POLL_INTERVAL") or 10),
            claude_bin=os.environ.get("CLAUDE_BIN") or "claude",
            permission_mode=os.environ.get("CLAUDE_PERMISSION_MODE") or "acceptEdits",
            claude_timeout=int(os.environ.get("CLAUDE_TIMEOUT") or 1200),
            tests_timeout=int(os.environ.get("TESTS_TIMEOUT") or 600),
        )


def configurar_logging():
    fmt = "%(asctime)s %(levelname)-7s %(message)s"
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE, encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


# --------------------------------------------------------------------------- HTTP (stdlib)
def _http(metodo, url, token, payload=None, timeout=30):
    """Request JSON con Bearer. Devuelve (status_code, dict|None). Levanta en error de red/HTTP."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=metodo)
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        cuerpo = resp.read().decode("utf-8").strip()
        status = resp.status
    if not cuerpo:
        return status, None
    try:
        return status, json.loads(cuerpo)
    except json.JSONDecodeError:
        return status, {"_raw": cuerpo}


def obtener_job(cfg):
    """GET /sagui/jobs/next → dict del job o None si no hay nada para hacer."""
    _status, data = _http("GET", "%s/sagui/jobs/next" % cfg.sagui_url, cfg.sagui_token,
                          timeout=min(cfg.poll_interval, 30))
    if not data:
        return None
    # Sagui puede envolver el job o devolverlo plano; aceptamos ambos.
    job = data.get("job", data) if isinstance(data, dict) else None
    return job if (job and job.get("job_id")) else None


def marcar_start(cfg, job_id):
    _http("POST", "%s/sagui/jobs/%s/start" % (cfg.sagui_url, job_id), cfg.sagui_token, payload={})
    _logger.info("[%s] start → POSTeado", job_id)


def marcar_done(cfg, job_id, done):
    _http("POST", "%s/sagui/jobs/%s/done" % (cfg.sagui_url, job_id), cfg.sagui_token, payload=done)
    _logger.info("[%s] done → POSTeado: %s", job_id, _resumen_done(done))


# --------------------------------------------------------------------------- job
def validar_job(job):
    """(ok, motivo). Verifica que estén los campos obligatorios y los tipos básicos."""
    if not isinstance(job, dict):
        return False, "el job no es un objeto JSON"
    faltan = [c for c in CAMPOS_JOB if c not in job]
    if faltan:
        return False, "faltan campos: %s" % ", ".join(faltan)
    try:
        int(job["max_turns"])
    except (TypeError, ValueError):
        return False, "max_turns no es un entero"
    if not str(job["prompt"]).strip():
        return False, "prompt vacío"
    return True, ""


def _repo_dir(job):
    """Resuelve el cwd del repo. Rutas relativas (mock) → contra la carpeta del script; las
    absolutas (jobs reales de Sagui) no se tocan. Devuelve (path, error|None)."""
    repo = job.get("repo_path") or ""
    if not os.path.isabs(repo):
        repo = os.path.normpath(os.path.join(AQUI, repo))
    if not os.path.isdir(repo):
        return repo, "repo_path no existe o no es un directorio: %s" % repo
    return repo, None


def invocar_claude(cfg, job):
    """Invoca Claude Code headless en el repo del job y normaliza el resultado.
    Devuelve un dict {status, cost_usd, num_turns, summary} listo para el done."""
    repo, err = _repo_dir(job)
    if err:
        return _error_done(err)

    # allowed_tools y max_turns SIEMPRE del job. permission-mode no interactivo para desatendido.
    cmd = [
        cfg.claude_bin, "-p", str(job["prompt"]),
        "--output-format", "json",
        "--max-turns", str(int(job["max_turns"])),
        "--allowedTools", str(job["allowed_tools"]),
        "--permission-mode", cfg.permission_mode,
    ]
    _logger.info("[%s] invocando Claude Code (cwd=%s, max_turns=%s, tools=%r)",
                 job["job_id"], repo, job["max_turns"], job["allowed_tools"])
    try:
        proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=cfg.claude_timeout)
    except FileNotFoundError:
        return _error_done("no se encontró el binario de Claude Code '%s' (configurá CLAUDE_BIN)" % cfg.claude_bin)
    except subprocess.TimeoutExpired:
        return _error_done("Claude Code superó el timeout de %ss" % cfg.claude_timeout)
    except Exception as exc:  # noqa: BLE001 - defensivo: cualquier fallo del subprocess
        return _error_done("error lanzando Claude Code: %s" % exc)

    salida = (proc.stdout or "").strip()
    if not salida:
        err = (proc.stderr or "").strip()[:500] or "sin salida"
        return _error_done("Claude Code no devolvió JSON (returncode=%s): %s" % (proc.returncode, err))
    try:
        data = json.loads(salida)
    except json.JSONDecodeError:
        return _error_done("JSON inválido de Claude Code: %s" % salida[:500])

    subtype = data.get("subtype")
    es_error = bool(data.get("is_error")) or (subtype is not None and subtype != "success")
    return {
        "status": "error" if es_error else "ok",
        "cost_usd": float(data.get("total_cost_usd") or 0.0),
        "num_turns": int(data.get("num_turns") or 0),
        "summary": (str(data.get("result") or "")[:2000]) or ("subtype=%s" % subtype),
    }


def _error_done(motivo):
    _logger.error("Claude Code: %s", motivo)
    return {"status": "error", "cost_usd": 0.0, "num_turns": 0, "summary": motivo}


def _resumen_done(done):
    return "status=%s cost=$%.4f turns=%s tests=%s" % (
        done.get("status"), float(done.get("cost_usd") or 0.0),
        done.get("num_turns"), done.get("tests_status", "—"))


def _tail(texto, lineas=50, max_chars=4000):
    """Últimas ~N líneas de salida, truncadas (defensa contra outputs enormes)."""
    cola = "\n".join((texto or "").splitlines()[-lineas:])
    return cola[-max_chars:]


def correr_tests(cfg, job, cc_status):
    """Paso VERIFY: el RUNNER corre tests_cmd (independiente de Claude Code). Solo para jobs 'fix'
    con CC ok y tests_cmd presente. Devuelve {tests_status, tests_output_tail}.
      tests_status: 'ok' (exit 0) · 'fail' (exit≠0 / timeout / error) · 'skipped' (sin cmd o CC falló).
    Resiliente: timeout, comando inexistente y output enorme NO cuelgan el loop."""
    tests_cmd = (job.get("tests_cmd") or "").strip()
    if job.get("type") != "fix" or not tests_cmd or cc_status != "ok":
        motivo = "sin tests_cmd" if not tests_cmd else ("CC no terminó ok" if cc_status != "ok" else "no es fix")
        _logger.info("[%s] tests SKIPPED (%s)", job.get("job_id"), motivo)
        return {"tests_status": "skipped", "tests_output_tail": ""}

    repo, err = _repo_dir(job)
    if err:
        return {"tests_status": "fail", "tests_output_tail": err}

    _logger.info("[%s] corriendo tests (cwd=%s): %s", job.get("job_id"), repo, tests_cmd)
    try:
        # shell=True: tests_cmd es una línea de shell autosuficiente (venv, binario de Odoo, DB...).
        proc = subprocess.run(tests_cmd, shell=True, cwd=repo, capture_output=True,
                              text=True, timeout=cfg.tests_timeout)
    except subprocess.TimeoutExpired as exc:
        salida = (exc.output or "") if isinstance(exc.output, str) else ""
        _logger.warning("[%s] tests TIMEOUT (%ss)", job.get("job_id"), cfg.tests_timeout)
        return {"tests_status": "fail",
                "tests_output_tail": _tail("TIMEOUT tras %ss\n%s" % (cfg.tests_timeout, salida))}
    except Exception as exc:  # noqa: BLE001 - comando inexistente / shell roto, etc.
        _logger.warning("[%s] error corriendo tests: %s", job.get("job_id"), exc)
        return {"tests_status": "fail", "tests_output_tail": "error corriendo tests: %s" % exc}

    estado = "ok" if proc.returncode == 0 else "fail"
    salida = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    _logger.info("[%s] tests %s (exit=%s)", job.get("job_id"), estado.upper(), proc.returncode)
    return {"tests_status": estado, "tests_output_tail": _tail(salida)}


# --------------------------------------------------------------------------- FASE 3: git / PR
def _git(args, cwd, check=False):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                          timeout=GIT_TIMEOUT, check=check)


def commitear_fix(cfg, job, tests_status):
    """Captura del trabajo (fase 3): tras un fix VERDE, commitea los cambios del working tree a una
    rama de trabajo (sagui/fix-<id>) SIN pushear. Así el upload (gated) la pushea después. Devuelve
    {work_branch, commit_sha} o {} (si no es fix, tests no ok, o no hay cambios). No cuelga el loop."""
    if job.get("type") != "fix" or tests_status != "ok":
        return {}
    repo, err = _repo_dir(job)
    if err:
        _logger.warning("[%s] commit fix: %s", job.get("job_id"), err)
        return {}
    work = (job.get("work_branch") or "").strip() or ("sagui/fix-%s" % job.get("job_id"))
    try:
        st = _git(["status", "--porcelain"], repo)
        if not (st.stdout or "").strip():
            _logger.info("[%s] commit fix: working tree limpio, no hay nada para subir.", job.get("job_id"))
            return {}
        _git(["checkout", "-B", work], repo, check=True)          # crea/switchea, conserva los cambios
        _git(["add", "-A"], repo, check=True)
        _git(["-c", "user.name=Sagui", "-c", "user.email=sagui@primate.uy",
              "commit", "-m", "Sagui fix (job %s)" % job.get("job_id")], repo, check=True)
        sha = _git(["rev-parse", "HEAD"], repo).stdout.strip()
        _logger.info("[%s] commit fix OK en %s (%s)", job.get("job_id"), work, sha[:8])
        return {"work_branch": work, "commit_sha": sha}
    except subprocess.CalledProcessError as exc:
        _logger.warning("[%s] commit fix falló: %s", job.get("job_id"), (exc.stderr or exc.stdout or "")[:200])
        return {}
    except Exception as exc:  # noqa: BLE001
        _logger.warning("[%s] commit fix error: %s", job.get("job_id"), exc)
        return {}


def _upload_error(motivo):
    _logger.warning("upload: %s", motivo)
    return {"status": "error", "cost_usd": 0.0, "num_turns": 0, "summary": motivo, "pr_url": ""}


def _extract_pr_url(texto):
    for linea in (texto or "").splitlines():
        linea = linea.strip()
        if linea.startswith("https://"):
            return linea
    return (texto or "").strip()


def subir_pr(cfg, job):
    """Job 'upload' (fase 3): pushea la rama de trabajo y abre un PR con `gh`. SIN claude ni tests.
    Devuelve {status, cost_usd, num_turns, summary, pr_url}. Resiliente: gh ausente/sin auth, push
    falla, PR ya existe."""
    repo, err = _repo_dir(job)
    if err:
        return _upload_error(err)
    work = (job.get("work_branch") or "").strip()
    base = (job.get("branch") or "main").strip()
    if not work:
        return _upload_error("el job upload no trae work_branch")
    title = (job.get("pr_title") or "Sagui fix").strip()
    body = job.get("pr_body") or ""
    _logger.info("[%s] upload: push %s + PR (base %s)", job.get("job_id"), work, base)
    try:
        push = _git(["push", "-u", "origin", work], repo)
    except Exception as exc:  # noqa: BLE001
        return _upload_error("error en git push: %s" % exc)
    if push.returncode != 0:
        return _upload_error("git push falló: %s" % _tail(push.stderr or push.stdout, 10))
    try:
        gh = subprocess.run(["gh", "pr", "create", "--base", base, "--head", work,
                             "--title", title, "--body", body],
                            cwd=repo, capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except FileNotFoundError:
        return _upload_error("'gh' (GitHub CLI) no está instalado/auth en la Mac")
    except Exception as exc:  # noqa: BLE001
        return _upload_error("error en gh pr create: %s" % exc)
    salida = (gh.stdout or "").strip()
    if gh.returncode == 0 and salida:
        url = _extract_pr_url(salida)
        _logger.info("[%s] upload OK: %s", job.get("job_id"), url)
        return {"status": "ok", "cost_usd": 0.0, "num_turns": 0, "summary": "PR abierto: %s" % url, "pr_url": url}
    err_txt = (gh.stderr or "") + salida
    if "already exists" in err_txt.lower():   # PR ya existe → recuperar la url
        view = subprocess.run(["gh", "pr", "view", work, "--json", "url", "-q", ".url"],
                              cwd=repo, capture_output=True, text=True, timeout=GIT_TIMEOUT)
        url = (view.stdout or "").strip()
        if view.returncode == 0 and url:
            return {"status": "ok", "cost_usd": 0.0, "num_turns": 0,
                    "summary": "PR ya existía: %s" % url, "pr_url": url}
    return _upload_error("gh pr create falló: %s" % _tail(err_txt, 10))


def procesar_job(cfg, job, postear=True):
    """Ciclo de un job: validar → start → invocar → done. Devuelve el payload de done."""
    ok, motivo = validar_job(job)
    if not ok:
        _logger.warning("Job malformado, lo descarto (%s): %s", motivo, job)
        done = {"status": "error", "cost_usd": 0.0, "num_turns": 0, "summary": "job malformado: %s" % motivo}
        if postear and isinstance(job, dict) and job.get("job_id"):
            marcar_done(cfg, job["job_id"], done)
        return done

    job_id = job["job_id"]
    if postear:
        marcar_start(cfg, job_id)
    else:
        _logger.info("[MOCK] start payload → %s", json.dumps({"job_id": job_id}, ensure_ascii=False))

    if job.get("type") == "upload":
        # FASE 3: el upload NO usa Claude Code ni tests — solo git push + PR.
        done = subir_pr(cfg, job)
    else:
        done = invocar_claude(cfg, job)
        # VERIFY: el runner corre los tests (independiente de CC) y suma el resultado al done.
        done.update(correr_tests(cfg, job, done.get("status")))
        # CAPTURA: si el fix quedó verde, commitea los cambios a la rama de trabajo (para el upload).
        done.update(commitear_fix(cfg, job, done.get("tests_status")))

    if postear:
        marcar_done(cfg, job_id, done)
    else:
        _logger.info("[MOCK] done payload → %s",
                     json.dumps(dict(done, job_id=job_id), ensure_ascii=False, indent=2))
    return done


# --------------------------------------------------------------------------- modos
def run_mock(cfg):
    ruta = os.path.join(AQUI, "mock_job.json")
    _logger.info("MODO MOCK — leyendo %s (no se postea nada a Sagui)", ruta)
    try:
        with open(ruta, "r", encoding="utf-8") as fh:
            job = json.load(fh)
    except FileNotFoundError:
        _logger.error("No existe %s. Creá uno (ver mock_job.example.json).", ruta)
        return 1
    except json.JSONDecodeError as exc:
        _logger.error("mock_job.json no es JSON válido: %s", exc)
        return 1
    procesar_job(cfg, job, postear=False)
    _logger.info("MODO MOCK — fin.")
    return 0


def run_loop(cfg):
    if not cfg.sagui_url or not cfg.sagui_token:
        _logger.error("Faltan SAGUI_URL y/o SAGUI_TOKEN (env o .env). No puedo pollear.")
        return 1
    _logger.info("Runner iniciado. Poll cada %ss a %s/sagui/jobs/next", cfg.poll_interval, cfg.sagui_url)
    while True:
        try:
            job = obtener_job(cfg)
            if job:
                _logger.info("Job recibido: %s (type=%s)", job.get("job_id"), job.get("type"))
                procesar_job(cfg, job, postear=True)
            else:
                _logger.debug("Sin jobs pendientes.")
        except KeyboardInterrupt:
            _logger.info("Interrumpido por el usuario. Chau.")
            return 0
        except urllib.error.URLError as exc:
            _logger.warning("No pude contactar a Sagui (%s). Reintento en %ss.", exc, cfg.poll_interval)
        except Exception:  # noqa: BLE001 - el loop NUNCA debe morir por un job/iteración
            _logger.exception("Error inesperado en la iteración; sigo poleando.")
        time.sleep(cfg.poll_interval)


# --------------------------------------------------------------------------- main
def main(argv=None):
    parser = argparse.ArgumentParser(description="Runner local Sagui ↔ Claude Code (fase 1).")
    parser.add_argument("--mock", action="store_true",
                        help="Lee mock_job.json e imprime los payloads en vez de pollear/postear a Sagui.")
    args = parser.parse_args(argv)

    cargar_dotenv()
    configurar_logging()
    cfg = Config.desde_env()
    try:
        return run_mock(cfg) if args.mock else run_loop(cfg)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
