import asyncio
import threading

import pytest

from backend.queue.errors import JobError, NeedsInput
from backend.queue.events import EventBus
from backend.queue.handlers import HandlerRegistry
from backend.queue.scheduler import QueuedJob, pick_next


def test_publish_from_thread_reaches_subscriber():
    async def main():
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        q = bus.subscribe()
        threading.Thread(target=bus.publish, args=({"type": "x"},)).start()
        return await asyncio.wait_for(q.get(), 2)

    assert asyncio.run(main()) == {"type": "x"}


def test_unsubscribed_queue_receives_nothing():
    async def main():
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        q = bus.subscribe()
        bus.unsubscribe(q)
        bus.publish({"type": "x"})
        await asyncio.sleep(0.05)
        return q.empty()

    assert asyncio.run(main())


def test_publish_without_loop_is_noop():
    EventBus().publish({"type": "x"})


def J(i, prio=0, fam=None):
    return QueuedJob(id=i, priority=prio, model_family=fam)


def test_pick_next_priority_then_family_then_id():
    assert pick_next([], None) is None
    assert pick_next([J(3), J(1), J(2)], None).id == 1
    assert pick_next([J(1), J(2, prio=5)], None).id == 2
    jobs = [J(1, fam="zimage"), J(2, fam="h3"), J(3, fam="h3")]
    assert pick_next(jobs, "h3").id == 2
    assert pick_next(jobs, "wan22").id == 1
    # 优先级高于同族偏好
    assert pick_next([J(1, fam="h3"), J(2, prio=1, fam="zimage")], "h3").id == 2


def test_registry():
    reg = HandlerRegistry()

    @reg.register("demo", gpu=True)
    def demo(ctx, payload):
        return {"ok": True}

    spec = reg.get("demo")
    assert spec.gpu is True and spec.fn is demo
    assert reg.types() == ["demo"]
    with pytest.raises(JobError, match="未知任务类型"):
        reg.get("nope")


def test_needs_input_carries_request():
    e = NeedsInput({"key": "k", "user": "u"})
    assert e.request["key"] == "k"
