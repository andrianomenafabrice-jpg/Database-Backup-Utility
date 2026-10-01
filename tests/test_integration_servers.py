"""Tests avec de vrais serveurs.

Lancer les conteneurs : bash scripts/test-dbs.sh up
Puis : DBBACKUP_IT=1 pytest tests/test_integration_servers.py
"""

import os
import subprocess

import pytest

from dbbackup.adapters import get_adapter
from dbbackup.backup import run_backup
from dbbackup.config import ConnectionParams
from dbbackup.restore import run_restore

pytestmark = pytest.mark.skipif(
    os.environ.get("DBBACKUP_IT") != "1",
    reason="Définir DBBACKUP_IT=1 avec les conteneurs de test lancés",
)


def docker_exec(container, *args, env=None):
    cmd = ["docker", "exec"]
    for key, value in (env or {}).items():
        cmd += ["-e", f"{key}={value}"]
    cmd += [container, *args]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout


def test_postgres_roundtrip_and_selective(tmp_path):
    container = "dbbackup-pg"

    def psql(sql):
        return docker_exec(container, "psql", "-U", "postgres", "-d", "testdb", "-tAc", sql)

    psql("DROP TABLE IF EXISTS clients, commandes")
    psql("CREATE TABLE clients (id serial PRIMARY KEY, nom text)")
    psql("CREATE TABLE commandes (id serial PRIMARY KEY, montant int)")
    psql("INSERT INTO clients (nom) VALUES ('Rakoto'), ('Rasoa')")
    psql("INSERT INTO commandes (montant) VALUES (10)")

    adapter = get_adapter(ConnectionParams(
        db_type="postgresql", database="testdb", username="postgres",
        password="secret", docker_container=container,
    ))
    adapter.test_connection()
    backup = run_backup(adapter, tmp_path)

    psql("DROP TABLE clients, commandes")
    run_restore(adapter, backup.file_path, overwrite=True)
    assert psql("SELECT nom FROM clients ORDER BY id").split() == ["Rakoto", "Rasoa"]
    assert psql("SELECT count(*) FROM commandes").strip() == "1"

    psql("DROP TABLE clients, commandes")
    run_restore(adapter, backup.file_path, tables=["clients"], overwrite=True)
    assert psql("SELECT count(*) FROM clients").strip() == "2"
    assert psql("SELECT to_regclass('commandes')").strip() == ""


def test_mysql_roundtrip_and_selective(tmp_path):
    container = "dbbackup-mysql"

    def sql(statement):
        return docker_exec(
            container, "mysql", "-h127.0.0.1", "-uroot", "-N", "-B", "testdb", "-e", statement,
            env={"MYSQL_PWD": "secret"},
        )

    sql("DROP TABLE IF EXISTS clients, commandes")
    sql("CREATE TABLE clients (id INT PRIMARY KEY AUTO_INCREMENT, nom VARCHAR(50))")
    sql("CREATE TABLE commandes (id INT PRIMARY KEY AUTO_INCREMENT, montant INT)")
    sql("INSERT INTO clients (nom) VALUES ('Rakoto'), ('Rasoa')")
    sql("INSERT INTO commandes (montant) VALUES (10)")

    adapter = get_adapter(ConnectionParams(
        db_type="mysql", database="testdb", username="root",
        password="secret", docker_container=container,
    ))
    adapter.test_connection()
    backup = run_backup(adapter, tmp_path)

    sql("DROP TABLE clients, commandes")
    run_restore(adapter, backup.file_path, overwrite=True)
    assert sql("SELECT nom FROM clients ORDER BY id").split() == ["Rakoto", "Rasoa"]
    assert sql("SELECT COUNT(*) FROM commandes").strip() == "1"

    sql("DROP TABLE clients, commandes")
    run_restore(adapter, backup.file_path, tables=["clients"], overwrite=True)
    assert sql("SELECT COUNT(*) FROM clients").strip() == "2"
    count = sql(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'testdb' AND table_name = 'commandes'"
    )
    assert count.strip() == "0"


def test_mongodb_roundtrip_and_selective(tmp_path):
    container = "dbbackup-mongo"

    def mongosh(js):
        return docker_exec(
            container, "mongosh", "--quiet", "-u", "root", "-p", "secret",
            "--authenticationDatabase", "admin", "testdb", "--eval", js,
        )

    mongosh("db.clients.drop(); db.commandes.drop()")
    mongosh("db.clients.insertMany([{nom: 'Rakoto'}, {nom: 'Rasoa'}])")
    mongosh("db.commandes.insertOne({montant: 10})")

    adapter = get_adapter(ConnectionParams(
        db_type="mongodb", database="testdb", username="root",
        password="secret", docker_container=container,
    ))
    adapter.test_connection()
    backup = run_backup(adapter, tmp_path)

    mongosh("db.clients.drop(); db.commandes.drop()")
    run_restore(adapter, backup.file_path, overwrite=True)
    assert mongosh("print(db.clients.countDocuments())").strip() == "2"
    assert mongosh("print(db.commandes.countDocuments())").strip() == "1"

    mongosh("db.clients.drop(); db.commandes.drop()")
    run_restore(adapter, backup.file_path, tables=["clients"], overwrite=True)
    assert mongosh("print(db.clients.countDocuments())").strip() == "2"
    assert mongosh("print(db.commandes.countDocuments())").strip() == "0"
