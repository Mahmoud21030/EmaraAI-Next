"""Automatic driver: Playwright connected over CDP to the hub's own Chrome window
(started by integrations/automation_browser.py with a dedicated profile that is signed in to ChatGPT).

What it automates:
  * opens a new ChatGPT chat for a role and sends the boot message
  * selects the right plugin in the composer (@Master / @Agent) before each message
  * sends wake / continue prompts, stops a hung reply, closes the tab of a replaced chat
  * observes the chat state for the supervisor
  * when driver.auto_approve is on: presses "Always allow" on ChatGPT's tool-permission prompt,
    ONLY for prompts that name one of the hub's own plugins (approve_scope_text in the selectors file)
"""
from __future__ import annotations

import asyncio

from ..core.models import ChatObservation, ChatState
from ..infra.logging import get_logger
from .base import ChatDriver, DriverError, locate_js, observe_js, parse_observation

log = get_logger("driver")

APPROVE_JS = """(cfg) => {
  const btns = Array.from(document.querySelectorAll('button'));
  for (const label of cfg.labels) {
    const b = btns.find(x => (x.innerText || '').trim().toLowerCase().startsWith(label.toLowerCase()) && !x.disabled);
    if (!b) continue;
    let box = b, text = '';
    for (let i = 0; i < 6 && box; i++) { box = box.parentElement; text = box ? (box.innerText || '') : ''; if (text.length > 60) break; }
    if (cfg.scope.some(s => text.includes(s))) { b.click(); return label + ' :: ' + text.slice(0, 80).replace(/\\n/g, ' '); }
  }
  return '';
}"""


class PlaywrightDriver(ChatDriver):
    kind = "playwright"
    can_observe = True
    can_open = True
    can_close = True
    can_locate = True
    can_eval = True

    def __init__(self, cdp_url: str, selectors: dict, settle_seconds: float = 1.5, *, auto_approve: bool = False, mention_plugin: bool = True):
        self.cdp_url = cdp_url
        self.sel = selectors
        self.settle = settle_seconds
        self.auto_approve = auto_approve
        self.mention_plugin = mention_plugin
        self._js = observe_js(selectors)
        self._pw = None
        self._browser = None
        self._pages: dict[str, object] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ connection
    async def _ctx(self):
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                try:
                    from playwright.async_api import async_playwright
                except ImportError as e:  # pragma: no cover
                    raise DriverError("playwright is not installed: pip install playwright") from e
                self._pw = self._pw or await async_playwright().start()
                try:
                    self._browser = await self._pw.chromium.connect_over_cdp(self.cdp_url)
                except Exception as e:
                    raise DriverError(f"automation browser is not running at {self.cdp_url}: {type(e).__name__}") from e
                self._pages.clear()
                log.info("connected to chrome over CDP", url=self.cdp_url)
            return self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()

    async def _page(self, session: dict, create_url: str | None = None):
        sid = session["id"]
        page = self._pages.get(sid)
        if page is not None and not page.is_closed():
            return page
        ctx = await self._ctx()
        url = (session.get("chat_ref") or {}).get("url")
        if url and "/c/" in url:  # only a real conversation URL identifies a tab; the bare new-chat URL matches anyone's tab
            for p in ctx.pages:
                if p.url.split("?")[0] == url.split("?")[0]:
                    self._pages[sid] = p
                    return p
        target = create_url or (url if url and "/c/" in url else None)
        if not target:
            raise DriverError("no page for session", missing=True)
        page = await ctx.new_page()
        await page.goto(target, wait_until="domcontentloaded")
        self._pages[sid] = page
        return page

    # ------------------------------------------------------------------ ChatDriver
    async def open_chat(self, session: dict, first_message: str, url: str) -> dict:
        page = await self._page(session, create_url=url)
        await page.wait_for_selector(self.sel["composer"], timeout=45000)
        await self._type_and_send(page, first_message, session.get("plugin_name", ""))
        for _ in range(20):  # the conversation URL appears once ChatGPT accepted the first message
            if "/c/" in page.url:
                break
            await asyncio.sleep(0.5)
        log.info("chat opened", session_id=session["id"], url=page.url)
        return {"url": page.url}

    async def send(self, session: dict, text: str) -> None:
        await self._type_and_send(await self._page(session), text, session.get("plugin_name", ""))

    async def observe(self, session: dict) -> ChatObservation:
        try:
            page = await self._page(session)
        except DriverError as e:
            return ChatObservation(ChatState.MISSING, error_text=str(e))
        try:
            if self.auto_approve:
                await self._approve(page, session["id"])
            return parse_observation(await page.evaluate(self._js))
        except Exception as e:
            log.warning("observe failed", session_id=session["id"], error=str(e))
            return ChatObservation(ChatState.UNKNOWN, error_text=str(e))

    async def _approve(self, page, session_id: str) -> None:
        clicked = await page.evaluate(APPROVE_JS, {"labels": self.sel.get("approve_button_texts") or ["Always allow"],
                                                   "scope": self.sel.get("approve_scope_text") or ["EmaraAI"]})
        if clicked:
            log.info("tool permission prompt approved", session_id=session_id, prompt=clicked)

    async def stop_generation(self, session: dict) -> None:
        page = await self._page(session)
        btn = await page.query_selector(self.sel["stop_button"])
        if btn:
            await btn.click()

    async def close_chat(self, chat_ref: dict) -> None:
        ctx = await self._ctx()
        url = (chat_ref.get("url") or "").split("?")[0]
        if "/c/" not in url:
            return
        for sid, page in list(self._pages.items()):
            if not page.is_closed() and page.url.split("?")[0] == url:
                await page.close()
                self._pages.pop(sid, None)
                return
        for p in ctx.pages:
            if p.url.split("?")[0] == url:
                await p.close()
                return

    async def locate(self, session: dict) -> list[dict]:
        ctx = await self._ctx()
        out: list[dict] = []
        for p in ctx.pages:
            try:
                if "/c/" in p.url and await p.evaluate(locate_js(session["id"])):
                    out.append({"url": p.url})
            except Exception:
                continue
        return out

    # ------------------------------------------------------------------ extras used by the hub
    async def chatgpt_page(self):
        """Any chatgpt.com tab of the automation browser (opens one if needed)."""
        ctx = await self._ctx()
        for p in ctx.pages:
            if "chatgpt.com" in p.url:
                return p
        page = await ctx.new_page()
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
        return page

    async def signed_in(self) -> bool:
        page = await self.chatgpt_page()
        try:
            return bool(await page.evaluate("fetch('/api/auth/session',{credentials:'include'}).then(r=>r.json()).then(s=>!!(s&&s.accessToken)).catch(()=>false)"))
        except Exception:
            return False

    async def ready(self) -> tuple[bool, str]:
        """Usable only when the hub Chrome is running AND signed in (never type into a logged-out ChatGPT)."""
        import time
        hit = getattr(self, "_ready_cache", None)
        if hit and time.monotonic() - hit[0] < (20 if hit[1][0] else 5):
            return hit[1]
        try:
            out = (True, "") if await self.signed_in() else (False, "sign in to ChatGPT in the hub Chrome window")
        except DriverError as e:
            out = (False, str(e))
        self._ready_cache = (time.monotonic(), out)
        return out

    async def eval_in_chatgpt(self, script: str):
        page = await self.chatgpt_page()
        return await page.evaluate(script)

    # ------------------------------------------------------------------ typing
    async def _mention(self, page, plugin_name: str) -> bool:
        """Type @<keyword> and pick the hub's plugin from ChatGPT's list, so the chat can use its tools."""
        keyword = plugin_name.split()[-1]  # "EmaraAI Agent" -> "Agent" (typing "@EmaraAI" would match an older plugin)
        await page.keyboard.type("@" + keyword, delay=40)
        try:
            option = page.get_by_text(plugin_name, exact=True).last
            await option.wait_for(state="visible", timeout=6000)
            await option.click()
            return True
        except Exception:
            for _ in range(len(keyword) + 1):
                await page.keyboard.press("Backspace")
            log.warning("plugin not found in the mention list", plugin=plugin_name)
            return False

    async def _type_and_send(self, page, text: str, plugin_name: str = "") -> None:
        box = await page.wait_for_selector(self.sel["composer"], timeout=30000)
        await box.click()
        if plugin_name and self.mention_plugin:
            if await self._mention(page, plugin_name):
                text = " " + text
        if await box.evaluate("e => e.tagName === 'TEXTAREA'"):
            await box.type(text[:1]) if False else await page.keyboard.insert_text(text)
        else:
            await page.keyboard.insert_text(text)
        await asyncio.sleep(self.settle)
        btn = await page.wait_for_selector(self.sel["send_button"], timeout=15000)
        await btn.click()
        log.info("message sent", chars=len(text), plugin=plugin_name or None)

    async def close(self) -> None:
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None
            self._browser = None
