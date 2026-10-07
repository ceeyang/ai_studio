import logging

from backend.logging_setup import setup_logging


def test_setup_logging_is_idempotent_and_writes(tmp_path):
    setup_logging(tmp_path)
    setup_logging(tmp_path)
    root = logging.getLogger()
    ours = [h for h in root.handlers if getattr(h, "_studio", False)]
    assert len(ours) == 1
    logging.getLogger("x").info("你好")
    for h in ours:
        h.flush()
    assert "你好" in (tmp_path / "studio.log").read_text(encoding="utf-8")
    for h in ours:
        root.removeHandler(h)
        h.close()
