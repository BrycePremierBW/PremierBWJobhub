from types import SimpleNamespace

from jobhub import database_runtime_guard as guard


class FakePool:
    def __init__(self, name):
        self.name = name
        self.closed = False

    def closeall(self):
        self.closed = True


def fake_database_module():
    calls = []
    env = {"JOBHUB_DB_MAXCONN": "5"}

    def get_pool():
        pool = FakePool(f"pool-{len(calls) + 1}")
        calls.append(pool)
        return pool

    def pool_factory(*args, **kwargs):
        return FakePool("factory")

    return SimpleNamespace(
        DATABASE_URL="postgresql://example.invalid/jobhub",
        USE_POSTGRES=True,
        os=SimpleNamespace(environ=env),
        get_postgres_pool=get_pool,
        ThreadedConnectionPool=pool_factory,
        calls=calls,
    )


def test_pool_is_created_once_and_reused(monkeypatch):
    module = fake_database_module()
    monkeypatch.setattr(
        guard.database_timeout_guard,
        "_guard_pool_factory",
        lambda factory: factory,
    )

    assert guard.install_database_runtime_guard(module) is True
    first = module.get_postgres_pool()
    second = module.get_postgres_pool()

    assert first is second
    assert len(module.calls) == 1
    assert guard.install_database_runtime_guard(module) is False


def test_pool_is_replaced_when_pool_configuration_changes(monkeypatch):
    module = fake_database_module()
    monkeypatch.setattr(
        guard.database_timeout_guard,
        "_guard_pool_factory",
        lambda factory: factory,
    )
    guard.install_database_runtime_guard(module)

    first = module.get_postgres_pool()
    module.os.environ["JOBHUB_DB_MAXCONN"] = "8"
    second = module.get_postgres_pool()

    assert first is not second
    assert first.closed is True
    assert len(module.calls) == 2


def test_no_database_url_returns_none_without_creating_pool(monkeypatch):
    module = fake_database_module()
    module.DATABASE_URL = ""
    monkeypatch.setattr(
        guard.database_timeout_guard,
        "_guard_pool_factory",
        lambda factory: factory,
    )
    guard.install_database_runtime_guard(module)

    assert module.get_postgres_pool() is None
    assert module.calls == []


def test_pool_size_is_clamped():
    module = fake_database_module()
    module.os.environ["JOBHUB_DB_MAXCONN"] = "500"
    assert guard._normalised_maxconn(module) == 50
    module.os.environ["JOBHUB_DB_MAXCONN"] = "0"
    assert guard._normalised_maxconn(module) == 1
