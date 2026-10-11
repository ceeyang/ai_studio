"""数据库：SQLite + WAL。服务启动时用 Alembic 升到最新，测试里直接 create_all。"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.domain.models import Base

log = logging.getLogger(__name__)
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


class Database:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.url = f"sqlite:///{path.resolve().as_posix()}"
        self.engine = create_engine(self.url, connect_args={"check_same_thread": False,
                                                            "timeout": 30})
        event.listen(self.engine, "connect", _pragmas)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)
        self._local = threading.local()  # 每个线程开着几层会话、会话结束后要补做的事

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def upgrade(self) -> None:
        cfg = AlembicConfig()
        cfg.set_main_option("script_location", str(MIGRATIONS))
        cfg.set_main_option("sqlalchemy.url", self.url)
        command.upgrade(cfg, "head")

    def in_session(self) -> bool:
        """这个线程现在是否开着会话（可能拿着 SQLite 写锁，还没提交）。"""
        return getattr(self._local, "depth", 0) > 0

    def after_session(self, fn: Callable[[], None]) -> None:
        """等这个线程的会话全部结束（提交或回滚）后再执行 fn；没开会话就马上执行。
        用来写任务日志：在没提交的写事务里另开连接写库，会被自己的写锁卡到 busy_timeout 再报 database is locked。"""
        if not self.in_session():
            fn()
            return
        self._local.pending.append(fn)

    @contextmanager
    def session(self) -> Iterator[Session]:
        loc = self._local
        if getattr(loc, "depth", 0) == 0:
            loc.depth, loc.pending = 0, []
        loc.depth += 1
        s = self._factory()
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()
            loc.depth -= 1
            if loc.depth == 0 and loc.pending:
                pending, loc.pending = loc.pending, []
                for fn in pending:
                    try:
                        fn()
                    except Exception:  # 补写日志失败不影响任务本身
                        log.exception("会话结束后补执行失败")
