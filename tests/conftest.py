import pytest

ENV = (
    "SCREENTIME_DB_PATH",
    "SCREENTIME_TIMEZONE",
    "INFLUX_URL",
    "INFLUX_TOKEN",
    "INFLUX_ORG",
    "INFLUX_BUCKET",
    "HA_URL",
    "HA_TOKEN",
)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Keep the developer's real config and environment out of tests."""
    monkeypatch.setenv("SCREENTIME_CONFIG", str(tmp_path / "missing.toml"))
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def offline_taxonomy(monkeypatch):
    """No App Store lookups and no taxonomy state leaking between tests."""
    from screentime import taxonomy

    def no_network(*args, **kwargs):
        raise OSError("network disabled in tests")

    monkeypatch.setattr(taxonomy.requests, "get", no_network)
    monkeypatch.setattr(taxonomy, "local_lookup", lambda bundle_ids: {})  # no Spotlight
    monkeypatch.setattr(taxonomy, "OVERRIDES", {})
    monkeypatch.setattr(taxonomy, "LOOKUP", {})
