import pytest

from dbbackup.config import ConnectionParams
from dbbackup.exceptions import ConfigError


def test_default_port_is_applied():
    params = ConnectionParams(db_type="postgresql", database="shop")
    assert params.port == 5432


def test_password_is_masked_in_repr():
    params = ConnectionParams(db_type="mysql", database="shop", password="topsecret")
    assert "topsecret" not in repr(params)
    assert "***" in repr(params)


def test_unsupported_dbms():
    with pytest.raises(ConfigError):
        ConnectionParams(db_type="oracle", database="x")


def test_database_required():
    with pytest.raises(ConfigError):
        ConnectionParams(db_type="sqlite", database="")
