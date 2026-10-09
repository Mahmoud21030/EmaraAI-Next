from __future__ import annotations

from .base import ChatDriver, load_selectors
from .manual import ManualDriver


def build_driver(settings, bridge=None) -> ChatDriver:
    kind = settings.driver.kind
    if kind == "extension":
        from .extension import ExtensionBridge, ExtensionDriver
        drv = ExtensionDriver(bridge or ExtensionBridge(), load_selectors(settings.path(settings.driver.selectors_file)),
                              settings.driver.send_settle_seconds, auto_approve=settings.driver.auto_approve,
                              mention_plugin=settings.driver.mention_plugin, force_thinking=settings.driver.force_thinking)
        drv.project_memory = settings.driver.chatgpt_project_memory     # chosen once, when ChatGPT creates the Project
        return drv
    selectors = load_selectors(settings.path(settings.driver.selectors_file))
    if kind == "playwright":
        from .playwright_cdp import PlaywrightDriver
        return PlaywrightDriver(settings.driver.cdp_url, selectors, settings.driver.send_settle_seconds,
                                auto_approve=settings.driver.auto_approve, mention_plugin=settings.driver.mention_plugin)
    if kind == "fake":
        from .fake import FakeDriver
        return FakeDriver()
    return ManualDriver()
