"""Резервный сервер crm2 (deploy/dr, D-084): синтаксис скриптов и логика flag-guard.sh без сети и Docker."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DR = ROOT / "deploy/dr"
SCRIPTS = [DR / n for n in ("dr-lib.sh", "failover.sh", "failback.sh", "drill.sh", "flag-guard.sh", "install.sh")] + [ROOT / "deploy/backup/gsho-backup-mirror.sh"]
GUARD = DR / "flag-guard.sh"


def test_dr_scripts_bash_n():
    for f in SCRIPTS:
        subprocess.run(["bash", "-n", str(f)], check=True)


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck не установлен")
def test_dr_scripts_shellcheck():
    p = subprocess.run(["shellcheck", "-x", "-f", "gcc", *map(str, SCRIPTS)], capture_output=True, text=True,
                       cwd=ROOT, env={**os.environ, "LC_ALL": "C.UTF-8"})
    assert p.returncode == 0, p.stdout + p.stderr


def _guard(tmp_path, flag: dict | None | str):
    flag_file = tmp_path / "failover.json"
    if isinstance(flag, dict):
        flag_file.write_text(json.dumps(flag))
    elif isinstance(flag, str):
        flag_file.write_text(flag)
    elif flag_file.exists():
        flag_file.unlink()
    reloads = tmp_path / "reloads"
    env = {**os.environ, "DR_FLAG_URL": str(flag_file), "DR_SNIPPET": str(tmp_path / "zz-dr-redirect.caddy"),
           "DR_CADDY_RELOAD_CMD": f"echo reload >> {reloads}"}
    subprocess.run(["bash", str(GUARD)], env=env, check=True, capture_output=True, text=True)
    snippet = tmp_path / "zz-dr-redirect.caddy"
    return (snippet.read_text() if snippet.exists() else None), (len(reloads.read_text().split()) if reloads.exists() else 0)


def test_guard_redirects_while_flag_active_and_clears_after(tmp_path):
    text, n = _guard(tmp_path, {"active": True, "target": "https://crm2.dobrov.pro"})
    assert "redir https://crm2.dobrov.pro{uri} 302" in text and n == 1
    text, n = _guard(tmp_path, {"active": True, "target": "https://crm2.dobrov.pro"})   # без изменений — без reload
    assert text and n == 1
    text, n = _guard(tmp_path, None)                                                    # флага нет → обычная работа
    assert text is None and n == 2


def test_guard_ignores_garbage_and_unsafe_target(tmp_path):
    assert _guard(tmp_path, "not json") == (None, 0)
    assert _guard(tmp_path, {"active": True, "target": "https://evil.example/x?y"}) == (None, 0)
    assert _guard(tmp_path, {"active": True, "target": "http://crm2.dobrov.pro"}) == (None, 0)


def test_guard_keeps_state_when_flag_unreachable(tmp_path):
    _guard(tmp_path, {"active": True, "target": "https://crm2.dobrov.pro"})
    env = {**os.environ, "DR_FLAG_URL": "https://127.0.0.1:9/failover.json", "DR_SNIPPET": str(tmp_path / "zz-dr-redirect.caddy"),
           "DR_CADDY_RELOAD_CMD": "true"}
    subprocess.run(["bash", str(GUARD)], env=env, check=True, capture_output=True)
    assert (tmp_path / "zz-dr-redirect.caddy").exists()   # нет ответа — переадресация остаётся


def test_frequent_backup_timer_every_15_min_and_light():
    timer = (ROOT / "deploy/backup/gsho-backup-frequent.timer").read_text()
    service = (ROOT / "deploy/backup/gsho-backup-frequent.service").read_text()
    assert "OnCalendar=*:0/15" in timer
    assert "Environment=BACKUP_LIGHT=1" in service and "SuccessExitStatus=75" in service
    script = (ROOT / "deploy/backup/gsho-backup.sh").read_text()
    assert '--keep-within "$KEEP_WITHIN"' in script and 'RETENTION_STATUS="skipped"' in script


def test_units_run_scripts_from_installed_location_not_code_tree():
    # восстановление переключает /opt/gsho-costing на commit копии — там deploy/dr может не быть (проверка 19.11)
    for unit in ("gsho-dr-drill.service", "gsho-dr-guard.service", "gsho-cert.service"):
        text = (DR / unit).read_text()
        assert "/usr/local/lib/gsho-dr/" in text and "/opt/gsho-costing/deploy/dr/" not in text, unit


def test_install_copies_scripts_outside_code_tree(tmp_path):
    lib, units, sbin = tmp_path / "lib", tmp_path / "units", tmp_path / "sbin"
    env = {**os.environ, "DR_LIB": str(lib), "DR_UNITS": str(units), "DR_SBIN": str(sbin)}
    subprocess.run(["bash", str(DR / "install.sh"), "--standby"], env=env, check=True, capture_output=True, text=True)
    for name in ("dr-lib.sh", "failover.sh", "failback.sh", "drill.sh", "flag-guard.sh", "docker-compose.drill.yml"):
        assert (lib / name).exists(), name
    assert (units / "gsho-dr-drill.service").exists() and (sbin / "gsho-backup.sh").exists()
    assert (lib / "fetch-cert.sh").exists() and (units / "gsho-cert.timer").exists()
    subprocess.run(["bash", str(DR / "install.sh"), "--primary"], env=env, check=True, capture_output=True, text=True)
    assert (units / "gsho-dr-guard.timer").exists()
    assert (sbin / "gsho-backup-mirror.sh").exists() and (units / "gsho-backup-mirror.timer").exists()


def test_restore_refuses_when_scripts_run_from_code_tree(tmp_path):
    code = tmp_path / "code"
    (code / "deploy/dr").mkdir(parents=True)
    shutil.copy(DR / "dr-lib.sh", code / "deploy/dr/dr-lib.sh")
    p = subprocess.run(["bash", "-c", f'GSHO_DIR={code}; . {code}/deploy/dr/dr-lib.sh; dr_restore_latest /nonexistent'],
                       capture_output=True, text=True)
    assert p.returncode != 0 and "install.sh" in p.stderr


def test_restore_rebuilds_until_build_succeeded_for_commit():
    # 19.11: первая проверка переключила код и упала до сборки; вторая решила, что сборка не нужна
    lib = (DR / "dr-lib.sh").read_text()
    assert 'built-commit' in lib
    assert lib.index('dr_compose build -q backend frontend clamav') < lib.index('echo "$commit" > "$DR_WORK/built-commit"')


def test_fetch_cert_does_not_start_stopped_stand():
    # резерв в покое: сертификат при загрузке ВМ не должен поднимать CRM на старых данных
    s = (ROOT / "deploy/fetch-cert.sh").read_text()
    assert "--status running --services" in s
    assert s.index("--status running --services") < s.index("up -d --force-recreate caddy")


def test_mirror_only_adds_and_skips_locks():
    # дубль в Timeweb не должен повторять удаления в источнике и не копирует блокировки restic
    s = (ROOT / "deploy/backup/gsho-backup-mirror.sh").read_text()
    assert "rclone copy" in s and "rclone sync" not in s
    assert "--exclude 'locks/**'" in s
    assert "read -r -s" in s   # секрет вводится скрыто
