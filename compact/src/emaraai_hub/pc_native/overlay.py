"""What you see while an agent controls the desktop (mouse, keyboard, windows): a thin frame in the agent's colour, a
label saying WHO is controlling, and a coloured pointer that glides to the thing the agent is about to click.

Started by the PC bridge when a Windows UI tool runs:  python -m emaraai_hub.pc_native.overlay <heartbeat file>
The heartbeat file holds {"who", "color"}; "<heartbeat file>.point" holds "x,y,screen_width" of the next click.
Everything is click-through and always on top. It disappears by itself a few seconds after the last UI action
(the bridge touches the heartbeat file on every action), or at once when the file is removed.
"""
from __future__ import annotations

import json
import os
import sys
import time

STALE_SECONDS = 4.0
COLOR, KEY = "#5b8cff", "#010203"       # KEY is the colour made transparent
ARROW = (0, 0, 0, 22, 6, 17, 10, 26, 14, 24, 10, 16, 18, 16)      # a mouse pointer, tip at 0,0


def mix(color: str, other: str, share: float) -> str:
    a, b = [int(color[i:i + 2], 16) for i in (1, 3, 5)], [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(x * (1 - share) + y * share):02x}" for x, y in zip(a, b))


def read_meta(beat: str) -> dict:
    try:
        with open(beat, encoding="utf-8") as f:
            data = json.loads(f.read())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_point(beat: str, width: int) -> tuple[float, float, float] | None:
    """(x, y, written at) of the next click in real pixels. The UI script may see a scaled screen (display zoom)."""
    path = beat + ".point"
    try:
        seq = os.path.getmtime(path)
        with open(path, encoding="utf-8") as f:
            x, y, sw = (float(v) for v in f.read().strip().split(",")[:3])
    except (OSError, ValueError):
        return None
    scale = width / sw if sw > 0 else 1.0
    return x * scale, y * scale, seq


def main(beat: str) -> int:
    import ctypes
    import tkinter as tk
    try:        # real pixels: the click points come in screen pixels
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    root = tk.Tk()
    root.overrideredirect(True)
    w, h = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+0+0")
    root.attributes("-topmost", True)
    root.attributes("-transparentcolor", KEY)
    root.configure(bg=KEY)
    canvas = tk.Canvas(root, width=w, height=h, bg=KEY, highlightthickness=0)
    canvas.pack()
    state = {"who": None, "color": None, "x": w / 2, "y": h / 2, "tx": None, "ty": None, "seq": 0.0, "ripple": 0, "shown": False}

    # the agent's pointer: an arrow in its colour with its name; hidden until there is something to point at
    canvas.create_polygon(*ARROW, fill=COLOR, outline="white", width=2, tags=("pointer", "arrow"), state="hidden")
    canvas.create_rectangle(0, 0, 10, 10, fill=COLOR, outline=COLOR, tags=("pointer", "tagbox"), state="hidden")
    canvas.create_text(0, 0, text="", fill="white", font=("Segoe UI", 9, "bold"), anchor="w", tags=("pointer", "tagtext"), state="hidden")

    def draw(meta: dict) -> None:
        """The frame and the label, in the colour of whoever is controlling."""
        who, color = str(meta.get("who") or "EmaraAI")[:60], str(meta.get("color") or COLOR)
        if (who, color) == (state["who"], state["color"]):
            return
        state["who"], state["color"] = who, color
        canvas.delete("frame")
        canvas.create_rectangle(3, 3, w - 3, h - 3, outline=mix(color, "#000000", 0.55), width=6, tags="frame")     # thin: nothing on screen is covered
        canvas.create_rectangle(2, 2, w - 2, h - 2, outline=color, width=2, tags="frame")
        label = canvas.create_text(w // 2 + 10, 24, text=f"{who}  is controlling this PC", fill="white", font=("Segoe UI", 10, "bold"), tags="frame")
        x0, y0, x1, y1 = canvas.bbox(label)
        canvas.create_rectangle(x0 - 30, y0 - 6, x1 + 12, y1 + 6, fill="#161922", outline=color, width=2, tags="frame")
        canvas.create_oval(x0 - 24, (y0 + y1) // 2 - 6, x0 - 12, (y0 + y1) // 2 + 6, fill=color, outline=color, tags="frame")
        canvas.tag_raise(label)
        canvas.itemconfigure("arrow", fill=color)
        canvas.itemconfigure("tagbox", fill=color, outline=color)
        canvas.itemconfigure("tagtext", text=who.split(" ")[0])
        canvas.tag_raise("pointer")

    draw(read_meta(beat))
    root.update_idletasks()
    try:        # clicks and keys pass through to whatever is underneath; no taskbar button
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x80000 | 0x20 | 0x80 | 0x08000000)   # LAYERED | TRANSPARENT | TOOLWINDOW | NOACTIVATE
    except Exception:
        pass

    def place() -> None:
        x, y = state["x"], state["y"]
        canvas.coords("arrow", *[v + (x if i % 2 == 0 else y) for i, v in enumerate(ARROW)])
        canvas.coords("tagtext", x + 22, y + 34)
        box = canvas.bbox("tagtext")
        if box:
            canvas.coords("tagbox", box[0] - 5, box[1] - 3, box[2] + 5, box[3] + 3)
        canvas.tag_raise("tagtext")

    def animate() -> None:
        if state["tx"] is not None:
            dx, dy = state["tx"] - state["x"], state["ty"] - state["y"]
            if abs(dx) + abs(dy) > 1.5:
                state["x"] += dx * 0.2            # fast at first, gentle at the end
                state["y"] += dy * 0.2
                place()
            elif state["ripple"] < 14:              # arrived: a ring spreads from the spot that is clicked
                state["x"], state["y"] = state["tx"], state["ty"]
                place()
                state["ripple"] += 1
                r = 5 + state["ripple"] * 2.4
                canvas.delete("ring")
                if state["ripple"] < 14:
                    canvas.create_oval(state["x"] - r, state["y"] - r, state["x"] + r, state["y"] + r, outline=state["color"] or COLOR,
                                       width=3 if state["ripple"] < 8 else 2, tags="ring")
        root.after(16, animate)

    def check() -> None:
        try:
            fresh = time.time() - os.path.getmtime(beat) < STALE_SECONDS
        except OSError:
            fresh = False
        if not fresh:
            root.destroy()
            return
        draw(read_meta(beat))
        p = read_point(beat, w)
        if p and p[2] != state["seq"]:
            state.update(tx=p[0], ty=p[1], seq=p[2], ripple=0)
            if not state["shown"]:
                state["shown"] = True
                for tag in ("arrow", "tagbox", "tagtext"):
                    canvas.itemconfigure(tag, state="normal")
        root.attributes("-topmost", True)
        root.after(120, check)
    root.after(120, check)
    root.after(16, animate)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]) if len(sys.argv) > 1 else 2)
