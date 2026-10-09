"""Regression coverage for web-chat reply watcher replacement.

The first attempt's watcher must stop when the same pending Claude chat is reopened:
otherwise old 900-second pollers run against the replacement tab and emit false failures.
"""
import asyncio

from emaraai_hub.drivers.web_chat import WebChatDriver


def test_retry_replaces_and_cancels_old_reply_watcher():
    async def exercise():
        driver = WebChatDriver.__new__(WebChatDriver)
        driver.chats = {"s": {"task": None}}
        started = []
        cancelled = []

        async def run(sid):
            started.append(sid)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(sid)
                raise

        driver._run = run
        driver._pump("s")
        first = driver.chats["s"]["task"]
        await asyncio.sleep(0)
        assert started == ["s"]

        driver._pump("s")
        second = driver.chats["s"]["task"]
        assert second is not first
        await asyncio.gather(first, return_exceptions=True)
        assert first.cancelled()
        assert cancelled == ["s"]
        await asyncio.sleep(0)
        assert started == ["s", "s"]
        assert not second.done()

        second.cancel()
        await asyncio.gather(second, return_exceptions=True)
    asyncio.run(exercise())


def test_completed_watcher_is_not_cancelled_on_replacement():
    async def exercise():
        driver = WebChatDriver.__new__(WebChatDriver)
        driver.chats = {"s": {"task": None}}
        entered = []

        async def run(sid):
            entered.append(sid)

        driver._run = run
        driver._pump("s")
        first = driver.chats["s"]["task"]
        await first
        assert first.done()
        assert not first.cancelled()
        driver._pump("s")
        await driver.chats["s"]["task"]
        assert entered == ["s", "s"]
        assert not first.cancelled()
    asyncio.run(exercise())


def test_three_fast_retries_leave_only_latest_watcher():
    async def exercise():
        driver = WebChatDriver.__new__(WebChatDriver)
        driver.chats = {"s": {"task": None}}

        async def run(sid):
            await asyncio.Event().wait()

        driver._run = run
        driver._pump("s")
        first = driver.chats["s"]["task"]
        driver._pump("s")
        second = driver.chats["s"]["task"]
        driver._pump("s")
        third = driver.chats["s"]["task"]
        await asyncio.sleep(0)
        assert first.cancelled()
        assert second.cancelled()
        assert not third.cancelled()
        assert not third.done()
        third.cancel()
        await asyncio.gather(third, return_exceptions=True)
    asyncio.run(exercise())
