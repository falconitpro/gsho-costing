"""deploy/deploy-remote.sh (12 MIG-06, 03 §9): обязательная копия до git reset при BACKUP_REQUIRED=true; без флага — деплой как раньше;
демо-seed только при APP_ENV=dev. Внешние команды (git, docker, backup) заменены заглушками, пишущими журнал вызовов."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy/deploy-remote.sh"


def _stub(d: Path, name: str, body: str) -> None:
    p = d / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


@pytest.fixture()
def sandbox(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "calls.log"
    _stub(bin_, "git", f'echo "git $*" >> {log}; [ "$1" = rev-parse ] && echo abc1234; exit 0\n')
    _stub(bin_, "docker", f'echo "docker $*" >> {log}\n'
                          'case "$*" in *is_dev*) exit "${STUB_IS_DEV_RC:-0}" ;; *"ps -q clamav"*) echo "${STUB_CLAMAV_ID:-}" ;; esac; exit 0\n')
    _stub(bin_, "backup", f'echo "backup $*" >> {log}; exit "${{STUB_BACKUP_RC:-0}}"\n')
    # не root (как в CI) — деплой зовёт `sudo -n <backup>`; заглушка проверяет -n и запускает команду с тем же окружением
    _stub(bin_, "sudo", f'echo "sudo $*" >> {log}; [ "$1" = -n ] || exit 97; shift; exec "$@"\n')
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "GSHO_DIR": str(tmp_path), "GSHO_DEPLOY_ENV": str(tmp_path / "deploy.env"),
           "GSHO_BACKUP_BIN": str(bin_ / "backup"), "GSHO_BACKUP_RETRY_SLEEP": "0"}
    env.pop("BACKUP_REQUIRED", None)
    return tmp_path, env, log


def _deploy(env, **extra):
    return subprocess.run(["bash", str(DEPLOY)], env={**env, **extra}, capture_output=True, text=True, timeout=60)


def _calls(log):
    return log.read_text().splitlines() if log.exists() else []


def test_without_flag_deploys_as_before_and_no_backup(sandbox):
    tmp, env, log = sandbox
    p = _deploy(env)
    assert p.returncode == 0, p.stderr
    c = _calls(log)
    assert not any(x.startswith("backup") for x in c)
    assert any(x.startswith("git reset") for x in c) and any("up -d --build" in x for x in c)


def test_required_backup_runs_before_reset(sandbox):
    tmp, env, log = sandbox
    (tmp / "deploy.env").write_text("BACKUP_REQUIRED=true\n")
    p = _deploy(env)
    assert p.returncode == 0, p.stderr
    c = _calls(log)
    b = next(i for i, x in enumerate(c) if x.startswith("backup --reason=pre_deploy"))
    r = next(i for i, x in enumerate(c) if x.startswith("git reset"))
    assert b < r


def test_failed_required_backup_stops_before_reset_and_build(sandbox):
    tmp, env, log = sandbox
    p = _deploy(env, BACKUP_REQUIRED="true", STUB_BACKUP_RC="1")
    assert p.returncode == 1 and "обновление остановлено" in p.stderr
    c = _calls(log)
    assert any(x.startswith("backup") for x in c)
    assert not any(x.startswith("git reset") or x.startswith("git fetch") or x.startswith("docker") for x in c)


def test_already_running_is_retried_once(sandbox):
    tmp, env, log = sandbox
    p = _deploy(env, BACKUP_REQUIRED="true", STUB_BACKUP_RC="75")
    assert p.returncode == 1
    assert len([x for x in _calls(log) if x.startswith("backup")]) == 2


@pytest.mark.parametrize("is_dev_rc,seeded", [(0, True), (1, False)])
def test_seed_only_in_dev(sandbox, is_dev_rc, seeded):
    tmp, env, log = sandbox
    assert _deploy(env, STUB_IS_DEV_RC=str(is_dev_rc)).returncode == 0
    assert any("scripts.seed" in x for x in _calls(log)) is seeded


def test_scanner_rebuilt_only_if_owner_started_it(sandbox):
    tmp, env, log = sandbox
    assert _deploy(env).returncode == 0
    up = [x for x in _calls(log) if " up -d " in x]
    assert up and "clamav" not in up[-1] and "--profile" not in up[-1]
    log.unlink()
    assert _deploy(env, STUB_CLAMAV_ID="abc123").returncode == 0
    up = [x for x in _calls(log) if " up -d " in x]
    assert up[-1].endswith("backend frontend caddy clamav") and "--profile scanner" in up[-1]
