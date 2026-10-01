import sys

import pytest

from dbbackup.cli import main


def test_error_is_printed_only_once(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        sys, "argv",
        [
            "dbbackup", "--log-file", str(tmp_path / "t.log"),
            "test-connection", "--type", "sqlite", "-d", str(tmp_path / "absent.db"),
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
    assert capsys.readouterr().err.count("introuvable") == 1
