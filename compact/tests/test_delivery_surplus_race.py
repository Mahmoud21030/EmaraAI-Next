"""Regression: reducing tab capacity while a surplus tab is being closed is safe.

The asynchronous close may race with another pool/reset operation removing the slot.
"""
import pytest
from emaraai_hub.drivers.delivery import DeliveryManager
from tests.test_delivery import Browser


@pytest.mark.asyncio
async def test_surplus_removed_while_close_waits_does_not_crash(hub):
    browser = Browser()
    hub.settings.delivery.min_tabs = 0
    hub.settings.delivery.max_tabs = 4
    hub.settings.delivery.idle_close_minutes = 0
    manager = DeliveryManager(hub, browser, hub.settings.delivery)
    manager.pool._fit()
    assert len(manager.pool.tabs) == 4
    hub.settings.delivery.max_tabs = 2
    closed = []

    async def close_replaced(tab):
        closed.append(tab.name)
        # Simulate concurrent shutdown/reset while async browser-close is pending.
        manager.pool.tabs.remove(tab)

    manager.pool.close = close_replaced
    await manager.maintain()
    assert closed == ["TAB-03", "TAB-04"]
    assert len(manager.pool.tabs) == 2


@pytest.mark.asyncio
async def test_normal_surplus_close_still_shrinks_pool(hub):
    browser = Browser()
    hub.settings.delivery.min_tabs = 0
    hub.settings.delivery.max_tabs = 3
    hub.settings.delivery.idle_close_minutes = 0
    manager = DeliveryManager(hub, browser, hub.settings.delivery)
    manager.pool._fit()
    hub.settings.delivery.max_tabs = 1
    await manager.maintain()
    assert [x.name for x in manager.pool.tabs] == ["TAB-01"]
