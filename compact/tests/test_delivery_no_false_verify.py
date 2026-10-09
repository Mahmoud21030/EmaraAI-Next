"""Regression: a delivery is verified only after its prompt is observed."""
import pytest

from emaraai_hub.drivers.delivery import DeliveryManager
from tests.test_delivery import Browser, add, drain


@pytest.mark.asyncio
async def test_uncertain_prompt_does_not_emit_verified(hub):
    browser = Browser()
    base_call = browser.call

    async def unconfirmed(op, args=None, timeout=0):
        result = await base_call(op, args, timeout)
        if op == "pool_send":
            result["after"]["last_user"] = "an unrelated prompt"
        elif op == "pool_verify":
            result["last_user"] = "an unrelated prompt"
        return result

    browser.call = unconfirmed
    hub.settings.delivery.min_tabs = 0
    hub.settings.delivery.max_attempts = 1
    hub.settings.delivery.switch_cooldown_seconds = 0
    manager = DeliveryManager(hub, browser, hub.settings.delivery)
    events = []
    real_emit = manager._emit

    def capture(event, *args, **kwargs):
        events.append(event)
        return real_emit(event, *args, **kwargs)

    manager._emit = capture
    delivery = add(manager, 1, "please confirm me")
    await drain(manager)
    assert "message_sent" in events
    assert "failed" in events
    assert "verified" not in events
    assert delivery.status == "failed"


@pytest.mark.asyncio
async def test_confirmed_prompt_emits_verified_once(hub):
    browser = Browser()
    hub.settings.delivery.min_tabs = 0
    hub.settings.delivery.max_attempts = 1
    hub.settings.delivery.switch_cooldown_seconds = 0
    manager = DeliveryManager(hub, browser, hub.settings.delivery)
    events = []
    real_emit = manager._emit

    def capture(event, *args, **kwargs):
        events.append(event)
        return real_emit(event, *args, **kwargs)

    manager._emit = capture
    delivery = add(manager, 2, "please confirm me")
    await drain(manager)
    assert events.count("verified") == 1
    assert "failed" not in events
    assert delivery.status == "delivered"
