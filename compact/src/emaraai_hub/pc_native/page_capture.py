"""Isolated browser rendering with validated, atomic screenshot output."""
from __future__ import annotations

import functools
import http.server
import os
import shutil
import struct
import tempfile
import threading
import zlib
from pathlib import Path
from urllib.parse import urlparse, unquote


def png_dimensions(path: Path) -> tuple[int, int]:
    """Validate complete PNG chunks and checksums before accepting evidence."""
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Screenshot exceeds the 64 MB limit.")
    with path.open("rb") as stream:
        if stream.read(8) != b"\x89PNG\r\n\x1a\n":
            raise ValueError("The browser output is not a valid PNG image.")
        dimensions, has_data = None, False
        while True:
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError("The browser output is a truncated PNG image.")
            length, kind = struct.unpack(">I4s", header)
            if length > 64 * 1024 * 1024:
                raise ValueError("PNG chunk exceeds the screenshot limit.")
            payload, checksum = stream.read(length), stream.read(4)
            if len(payload) != length or len(checksum) != 4 or zlib.crc32(kind + payload) != struct.unpack(">I", checksum)[0]:
                raise ValueError("The browser output contains a damaged PNG chunk.")
            if dimensions is None:
                if kind != b"IHDR" or length != 13:
                    raise ValueError("The browser output has no valid PNG header.")
                dimensions = struct.unpack(">II", payload[:8])
                if not all(dimensions):
                    raise ValueError("The browser output has invalid image dimensions.")
            elif kind == b"IHDR":
                raise ValueError("The browser output has duplicate PNG headers.")
            if kind == b"IDAT":
                has_data = has_data or bool(length)
            if kind == b"IEND":
                if length or not has_data or stream.read(1):
                    raise ValueError("The browser output has an invalid PNG ending.")
                return dimensions


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _render_browser(executable: str, profile: Path, address: str, image: Path, width: int, height: int) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ValueError("Reliable page capture requires Playwright. Install the project's playwright extra; browser_screenshot can capture an existing tab.") from None
    with sync_playwright() as engine:
        context = engine.chromium.launch_persistent_context(
            str(profile), executable_path=executable, headless=True,
            viewport={"width": width, "height": height}, device_scale_factor=1,
            args=["--disable-gpu", "--no-first-run", "--no-default-browser-check"], timeout=45000)
        try:
            page = context.new_page()
            page.goto(address, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(800)
            page.evaluate("() => Promise.race([document.fonts.ready, new Promise(resolve => setTimeout(resolve, 5000))])")
            viewport = page.evaluate("({width: innerWidth, height: innerHeight})")
            if viewport != {"width": width, "height": height}:
                raise ValueError(f"Browser viewport mismatch: requested {width}x{height}, got {viewport}.")
            page.screenshot(path=str(image), type="png", full_page=False, animations="disabled", timeout=15000)
            return viewport
        finally:
            context.close()


def capture_page(settings, target: str, output_path: str, width: int = 1366, height: int = 900) -> dict:
    from ..integrations.automation_browser import chrome_path
    if not 320 <= int(width) <= 3840 or not 240 <= int(height) <= 4320:
        raise ValueError("Screenshot dimensions are outside the supported range.")
    output = Path(output_path).expanduser().resolve()
    if output.suffix.lower() != ".png":
        raise ValueError("Screenshot output must be a .png file.")
    if not target.strip():
        raise ValueError("A page URL or HTML file is required.")
    parsed = urlparse(target)
    local = None
    if parsed.scheme == "file":
        local = Path(unquote(parsed.path).lstrip("/") if os.name == "nt" else unquote(parsed.path))
    elif parsed.scheme not in ("http", "https"):
        local = Path(target)
    if local is not None and not local.is_file():
        raise ValueError(f"HTML file not found: {local}")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Never depend on the user's TEMP permissions or share an interactive profile.
    scratch_root = settings.path(settings.data_dir) / "page-captures"
    scratch_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="shot-", dir=scratch_root))
    server = thread = None
    try:
        address = target
        if local is not None:
            local = local.resolve()
            handler = functools.partial(QuietHandler, directory=str(local.parent))
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            from urllib.parse import quote
            address = f"http://127.0.0.1:{server.server_port}/{quote(local.name)}"
        image = scratch / "result.png"
        viewport = _render_browser(chrome_path(settings), scratch / "profile", address, image, int(width), int(height))
        actual_width, actual_height = png_dimensions(image)
        if (actual_width, actual_height) != (int(width), int(height)):
            raise ValueError(f"Browser produced {actual_width}x{actual_height}, expected {width}x{height}.")
        # Keep any existing evidence untouched until a new screenshot was verified.
        import uuid
        staged = output.with_name(output.name + "." + uuid.uuid4().hex + ".part")
        try:
            shutil.copyfile(image, staged)
            staged.replace(output)
        finally:
            staged.unlink(missing_ok=True)
        return {"path": str(output), "bytes": output.stat().st_size, "page": target,
                "width": actual_width, "height": actual_height, "viewport": viewport, "isolated": True,
                "authentication": "fresh browser profile; use browser_screenshot for signed-in tabs"}
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        shutil.rmtree(scratch, ignore_errors=True)
