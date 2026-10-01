from dbbackup.logger import redact


def test_redact_key_value():
    assert redact("password=secret123") == "password=***"
    assert redact("connexion avec PWD: abc") == "connexion avec PWD: ***"


def test_redact_cli_flag():
    assert redact("mysqldump --password hunter2 db") == "mysqldump --password *** db"


def test_redact_leaves_normal_text():
    assert redact("Backup terminé avec succès") == "Backup terminé avec succès"
