"""测试公共 fixture。"""
import pytest

from backend.domain.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "studio.db")
    d.create_all()
    yield d
    d.engine.dispose()
