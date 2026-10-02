"""Tests de la commande python -m app.demo."""

import sqlite3

from app.demo.__main__ import main


def write_config(tmp_path, mode="demo"):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"mode: {mode}\n"
        "qlik:\n  log_paths: ['./logs']\n"
        "qvd:\n  directories: ['./vrais_qvd']\n"
        "database:\n  path: './data/m.db'\n",
        encoding="utf-8")
    return cfg


def test_cli_scenario_reussi(tmp_path, capsys):
    code = main(["successful", "--speed", "1000", "--config", str(write_config(tmp_path))])
    out = capsys.readouterr().out
    assert code == 0
    assert "Reload démarré — Application Ventes" in out
    assert "VENTES — 1 842 556 lignes" in out
    assert "Statut final : SUCCESS" in out
    rows = sqlite3.connect(tmp_path / "data" / "m.db").execute(
        "SELECT status FROM reloads").fetchall()
    assert rows == [("SUCCESS",)]


def test_cli_refuse_hors_mode_demo(tmp_path, capsys):
    code = main(["successful", "--config", str(write_config(tmp_path, "live"))])
    assert code == 2 and "mode demo" in capsys.readouterr().err
    assert not (tmp_path / "demo_data").exists()


def test_cli_refuse_dossier_demo_dans_dossier_qlik(tmp_path, capsys):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("qvd:\n  directories: ['.']\n", encoding="utf-8")
    assert main(["error", "--config", str(cfg)]) == 2
    assert "conflit" in capsys.readouterr().err


def write_notif_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mode: demo\n"
        "database:\n  path: './data/m.db'\n"
        "notifications:\n  email:\n    enabled: true\n    dry_run: true\n"
        "    recipients: ['${ALERT_EMAIL_RECIPIENT}']\n",
        encoding="utf-8")
    return cfg


def test_cli_scenario_erreur_affiche_l_email_dry_run(tmp_path, capsys):
    assert main(["error", "--speed", "1000", "--config", str(write_notif_config(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN (non envoyée) → ${ALERT_EMAIL_RECIPIENT}" in out
    assert "Sujet : #Error Reload DEMO | Ventes |" in out
    assert "ORA-00942" in out and "Votre Agent Claude" in out
    row = sqlite3.connect(tmp_path / "data" / "m.db").execute(
        "SELECT status FROM notification_log").fetchall()
    assert row == [("DRY_RUN",)]


def test_cli_scenario_reussi_sans_email(tmp_path, capsys):
    assert main(["successful", "--speed", "1000",
                 "--config", str(write_notif_config(tmp_path))]) == 0
    assert "Notification email" not in capsys.readouterr().out
    assert sqlite3.connect(tmp_path / "data" / "m.db").execute(
        "SELECT COUNT(*) FROM notification_log").fetchone()[0] == 0

