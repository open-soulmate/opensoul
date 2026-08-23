"""OpenLimb RPA Engine — GUI automation + screen vision.

Provides screenshot capture, OCR, keyboard/mouse simulation, window management,
and smart text-based operations.  Auto-detects Wayland vs X11 and picks the
right native tool for each action.
"""

import asyncio
import base64
import logging
import os
import re
import shutil
import tempfile
from enum import StrEnum
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Constants ────────────────────────────────────────────────────────────────
TIMEOUT = 10  # seconds — every subprocess gets this ceiling


# ── Display-server detection ─────────────────────────────────────────────────

class DisplayServer(StrEnum):
    WAYLAND = "wayland"
    X11 = "x11"
    UNKNOWN = "unknown"


def _detect_display_server() -> DisplayServer:
    """Return the active display-server type."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return DisplayServer.WAYLAND
    if os.environ.get("DISPLAY"):
        return DisplayServer.X11
    return DisplayServer.UNKNOWN


DISPLAY_SERVER = _detect_display_server()


def _has_cmd(name: str) -> bool:
    return shutil.which(name) is not None


# ── Async subprocess helper ─────────────────────────────────────────────────

async def _run(
    *args: str,
    timeout: float = TIMEOUT,
    capture_output: bool = True,
) -> tuple[int, str, str]:
    """Run a command via asyncio.create_subprocess_exec with timeout.

    Returns (returncode, stdout, stderr).
    """
    logger.debug("exec: %s", " ".join(args))
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(args)}")
    stdout = stdout_b.decode(errors="replace") if stdout_b else ""
    stderr = stderr_b.decode(errors="replace") if stderr_b else ""
    return proc.returncode or 0, stdout, stderr


# ── Request / Response models ────────────────────────────────────────────────

class ScreenshotResponse(BaseModel):
    base64: str
    format: str = "png"
    width: int | None = None
    height: int | None = None
    display_server: str


class OCRRequest(BaseModel):
    base64: str | None = None  # if omitted, takes a fresh screenshot
    region: dict | None = None  # {"x": 0, "y": 0, "w": 100, "h": 100}
    lang: str = "eng+chi_sim"      # tesseract language codes


class OCRResponse(BaseModel):
    text: str
    confidence: float | None = None
    lang: str


class TypeRequest(BaseModel):
    text: str
    delay_ms: int = 0  # per-char delay (ydotool only)


class TypeResponse(BaseModel):
    success: bool
    chars_typed: int


class KeyRequest(BaseModel):
    keys: str  # e.g. "ctrl+c", "alt+Tab", "Return", "super"
    repeat: int = 1


class KeyResponse(BaseModel):
    success: bool
    keys_sent: str


class ClickRequest(BaseModel):
    x: int
    y: int
    button: int = 1        # 1=left, 2=middle, 3=right
    clicks: int = 1         # 1=single, 2=double


class ClickResponse(BaseModel):
    success: bool
    x: int
    y: int
    button: int
    clicks: int


class MouseMoveRequest(BaseModel):
    x: int
    y: int
    duration_ms: int = 0  # 0 = instant


class MouseMoveResponse(BaseModel):
    success: bool
    x: int
    y: int


class DragRequest(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int
    button: int = 1
    duration_ms: int = 500


class DragResponse(BaseModel):
    success: bool
    from_x: int
    from_y: int
    to_x: int
    to_y: int


class WindowInfo(BaseModel):
    id: str
    title: str
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    focused: bool = False


class FocusRequest(BaseModel):
    window_id: str | None = None
    title: str | None = None  # substring match


class FocusResponse(BaseModel):
    success: bool
    window_id: str
    title: str


class ClickTextRequest(BaseModel):
    text: str
    occurrence: int = 1    # which match (1-based)
    button: int = 1


class ClickTextResponse(BaseModel):
    success: bool
    found: bool
    x: int | None = None
    y: int | None = None
    matched_text: str | None = None


class WaitTextRequest(BaseModel):
    text: str
    timeout: float = 10.0
    interval: float = 0.5


class WaitTextResponse(BaseModel):
    found: bool
    elapsed: float


class ReadRegionRequest(BaseModel):
    x: int
    y: int
    width: int
    height: int
    lang: str = "eng+chi_sim"


class ReadRegionResponse(BaseModel):
    text: str
    region: dict


# ── Internal helpers ─────────────────────────────────────────────────────────

def _check_tool(name: str) -> str:
    """Return path to *name* or raise HTTP 503."""
    p = shutil.which(name)
    if not p:
        raise HTTPException(
            503,
            f"Required tool '{name}' not found on PATH. "
            f"Install it with your package manager (e.g. pacman -S {name}).",
        )
    return p


def _screenshot_tool() -> str:
    """Pick the right screenshot binary."""
    if DISPLAY_SERVER == DisplayServer.WAYLAND:
        return _check_tool("grim")
    elif DISPLAY_SERVER == DisplayServer.X11:
        return _check_tool("scrot")
    else:
        # Try grim first, fall back to scrot
        if _has_cmd("grim"):
            return "grim"
        if _has_cmd("scrot"):
            return "scrot"
        raise HTTPException(
            503,
            "No screenshot tool found. Install 'grim' (Wayland) or 'scrot' (X11).",
        )


async def _take_screenshot_b64() -> tuple[str, int, int]:
    """Capture screen → (base64_png, width, height)."""
    tool = _screenshot_tool()
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        if tool.endswith("grim"):
            await _run("grim", tmp, timeout=TIMEOUT)
        else:
            await _run("scrot", "-o", tmp, timeout=TIMEOUT)

        data = Path(tmp).read_bytes()
        # Probe dimensions with pure Python (avoid pillow dep)
        w, h = _png_dimensions(data)
        return base64.b64encode(data).decode(), w, h
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _png_dimensions(data: bytes) -> tuple[int, int]:
    """Read IHDR from a PNG to extract width/height."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w = int.from_bytes(data[16:20], "big")
        h = int.from_bytes(data[20:24], "big")
        return w, h
    return 0, 0


async def _ocr_from_b64(
    img_b64: str,
    lang: str = "eng+chi_sim",
    region: dict | None = None,
) -> str:
    """Run tesseract on a base64-encoded PNG, optionally cropping to *region*."""
    _check_tool("tesseract")
    img_bytes = base64.b64decode(img_b64)
    fd, tmp_in = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    tmp_out = tmp_in + "_ocr"  # tesseract appends .txt
    try:
        Path(tmp_in).write_bytes(img_bytes)

        # Crop if region specified
        if region:
            crop_in = tmp_in
            fd2, tmp_in = tempfile.mkstemp(suffix=".png")
            os.close(fd2)
            x, y, w, h = region["x"], region["y"], region["w"], region["h"]
            # Use ImageMagick convert for cropping if available, else proceed as-is
            if _has_cmd("convert"):
                await _run(
                    "convert", crop_in,
                    "-crop", f"{w}x{h}+{x}+{y}", "+repage",
                    tmp_in,
                    timeout=TIMEOUT,
                )
            else:
                # No imagemagick — just OCR full image
                tmp_in = crop_in

        await _run(
            "tesseract", tmp_in, tmp_out,
            "-l", lang, "--psm", "6",
            timeout=TIMEOUT,
        )
        out = Path(tmp_out + ".txt")
        if out.exists():
            return out.read_text().strip()
        return ""
    finally:
        for p in (tmp_in, tmp_out + ".txt"):
            try:
                os.unlink(p)
            except OSError:
                pass


def _tool_for_keyboard() -> str:
    """Pick keyboard simulation tool."""
    if DISPLAY_SERVER == DisplayServer.WAYLAND:
        for name in ("ydotool", "wtype"):
            if _has_cmd(name):
                return name
        raise HTTPException(
            503,
            "No keyboard tool found for Wayland. Install 'ydotool' or 'wtype'.",
        )
    else:
        return _check_tool("xdotool")


def _tool_for_mouse() -> str:
    """Pick mouse simulation tool."""
    if DISPLAY_SERVER == DisplayServer.WAYLAND:
        if _has_cmd("ydotool"):
            return "ydotool"
        raise HTTPException(
            503,
            "No mouse tool found for Wayland. Install 'ydotool' (with ydotoold service).",
        )
    else:
        return _check_tool("xdotool")


def _ydotool_key_seq(keys: str) -> list[str]:
    """Translate 'ctrl+c' → ydotool key sequence tokens.

    ydotool key syntax: 'keydown:LeftCtrl keydown:c keyup:c keyup:LeftCtrl'
    """
    KEYMAP = {
        "ctrl": "LeftCtrl", "control": "LeftCtrl",
        "alt": "LeftAlt", "lalt": "LeftAlt", "ralt": "RightAlt",
        "shift": "LeftShift", "lshift": "LeftShift", "rshift": "RightShift",
        "super": "LeftMeta", "meta": "LeftMeta", "win": "LeftMeta",
        "return": "Return", "enter": "Return",
        "tab": "Tab", "escape": "Escape", "esc": "Escape",
        "backspace": "BackSpace", "delete": "Delete", "del": "Delete",
        "space": "space",
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "home": "Home", "end": "End", "pageup": "PageUp", "pagedown": "PageDown",
        "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
        "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
        "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
    }
    parts = keys.lower().split("+")
    resolved = [KEYMAP.get(p.strip(), p.strip()) for p in parts]

    downs = [f"keydown:{k}" for k in resolved]
    ups = [f"keyup:{k}" for k in reversed(resolved)]
    return downs + ups


def _xdotool_key_translate(keys: str) -> str:
    """Translate 'ctrl+c' → xdotool-style 'ctrl+c'."""
    return keys.lower().replace("return", "Return").replace("super", "super")


def _tool_for_windows() -> str:
    """Pick window-listing tool."""
    if DISPLAY_SERVER == DisplayServer.WAYLAND:
        # wlrctl or swaymsg (for sway) — try both
        for name in ("wlrctl", "swaymsg", "hyprctl"):
            if _has_cmd(name):
                return name
        raise HTTPException(
            503,
            "No window management tool found. Install 'wlrctl', 'swaymsg', or 'hyprctl'.",
        )
    else:
        return _check_tool("xdotool")


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/screenshot", response_model=ScreenshotResponse)
async def take_screenshot():
    """Capture the current screen and return a base64-encoded PNG."""
    b64, w, h = await _take_screenshot_b64()
    return ScreenshotResponse(
        base64=b64,
        width=w,
        height=h,
        display_server=DISPLAY_SERVER.value,
    )


@router.post("/ocr", response_model=OCRResponse)
async def ocr_screen(req: OCRRequest):
    """OCR the full screen (or a provided image / region)."""
    if req.base64:
        text = await _ocr_from_b64(req.base64, lang=req.lang, region=req.region)
    else:
        b64, _, _ = await _take_screenshot_b64()
        text = await _ocr_from_b64(b64, lang=req.lang, region=req.region)
    return OCRResponse(text=text, lang=req.lang)


@router.post("/type", response_model=TypeResponse)
async def type_text(req: TypeRequest):
    """Simulate keyboard text input."""
    tool = _tool_for_keyboard()

    if tool == "ydotool":
        args = ["ydotool", "type"]
        if req.delay_ms > 0:
            args += ["--delay", str(req.delay_ms)]
        args.append(req.text)
        await _run(*args, timeout=TIMEOUT)
    elif tool == "wtype":
        await _run("wtype", req.text, timeout=TIMEOUT)
    else:
        # xdotool
        await _run("xdotool", "type", "--clearmodifiers", req.text, timeout=TIMEOUT)

    return TypeResponse(success=True, chars_typed=len(req.text))


@router.post("/key", response_model=KeyResponse)
async def press_keys(req: KeyRequest):
    """Simulate key presses (e.g. 'ctrl+c', 'alt+Tab')."""
    tool = _tool_for_keyboard()

    for _ in range(req.repeat):
        if tool == "ydotool":
            seq = _ydotool_key_seq(req.keys)
            token = " ".join(seq)
            await _run("ydotool", "key", token, timeout=TIMEOUT)
        elif tool == "wtype":
            # wtype -M ctrl -P c -m ctrl  (for ctrl+c)
            # Simplified: just pass the raw keys — wtype has limited combo support
            parts = req.keys.lower().split("+")
            mod_args = []
            key_val = parts[-1]
            for mod in parts[:-1]:
                mod_map = {"ctrl": "ctrl", "alt": "alt", "shift": "shift", "super": "super"}
                m = mod_map.get(mod)
                if m:
                    mod_args += ["-M", m]
            key_map = {"return": "Return", "enter": "Return", "tab": "Tab",
                        "escape": "Escape", "esc": "Escape", "backspace": "BackSpace",
                        "space": "space", "super": "super"}
            key_val = key_map.get(key_val, key_val)
            await _run("wtype", *mod_args, "-P", key_val, "-m", "ctrl" if len(parts) > 1 else "", timeout=TIMEOUT)
        else:
            translated = _xdotool_key_translate(req.keys)
            await _run("xdotool", "key", translated, timeout=TIMEOUT)

    return KeyResponse(success=True, keys_sent=req.keys)


@router.post("/click", response_model=ClickResponse)
async def click(req: ClickRequest):
    """Click at screen coordinates."""
    tool = _tool_for_mouse()

    if tool == "ydotool":
        btn = req.button  # ydotool: 1=left, 2=right, 3=middle
        for _ in range(req.clicks):
            await _run(
                "ydotool", "mousemove", "--absolute",
                "-x", str(req.x), "-y", str(req.y),
                timeout=TIMEOUT,
            )
            await _run(
                "ydotool", "click", str(btn),
                timeout=TIMEOUT,
            )
    else:
        # xdotool
        await _run(
            "xdotool", "mousemove", str(req.x), str(req.y),
            timeout=TIMEOUT,
        )
        btn = req.button
        for _ in range(req.clicks):
            await _run("xdotool", "click", str(btn), timeout=TIMEOUT)

    return ClickResponse(
        success=True,
        x=req.x,
        y=req.y,
        button=req.button,
        clicks=req.clicks,
    )


@router.post("/mouse", response_model=MouseMoveResponse)
async def mouse_move(req: MouseMoveRequest):
    """Move the mouse pointer to (x, y)."""
    tool = _tool_for_mouse()

    if tool == "ydotool":
        await _run(
            "ydotool", "mousemove", "--absolute",
            "-x", str(req.x), "-y", str(req.y),
            timeout=TIMEOUT,
        )
    else:
        await _run(
            "xdotool", "mousemove", str(req.x), str(req.y),
            timeout=TIMEOUT,
        )

    return MouseMoveResponse(success=True, x=req.x, y=req.y)


@router.post("/drag", response_model=DragResponse)
async def drag(req: DragRequest):
    """Drag from (x1,y1) to (x2,y2)."""
    tool = _tool_for_mouse()

    if tool == "ydotool":
        await _run(
            "ydotool", "mousemove", "--absolute",
            "-x", str(req.x1), "-y", str(req.y1),
            timeout=TIMEOUT,
        )
        await _run("ydotool", "mousedown", str(req.button), timeout=TIMEOUT)
        await asyncio.sleep(req.duration_ms / 1000)
        await _run(
            "ydotool", "mousemove", "--absolute",
            "-x", str(req.x2), "-y", str(req.y2),
            timeout=TIMEOUT,
        )
        await _run("ydotool", "mouseup", str(req.button), timeout=TIMEOUT)
    else:
        await _run(
            "xdotool", "mousemove", str(req.x1), str(req.y1),
            timeout=TIMEOUT,
        )
        await _run("xdotool", "mousedown", str(req.button), timeout=TIMEOUT)
        await asyncio.sleep(req.duration_ms / 1000)
        await _run(
            "xdotool", "mousemove", str(req.x2), str(req.y2),
            timeout=TIMEOUT,
        )
        await _run("xdotool", "mouseup", str(req.button), timeout=TIMEOUT)

    return DragResponse(
        success=True,
        from_x=req.x1, from_y=req.y1,
        to_x=req.x2, to_y=req.y2,
    )


@router.get("/windows")
async def list_windows() -> list[WindowInfo]:
    """List all visible windows."""
    tool = _tool_for_windows()
    windows: list[WindowInfo] = []

    if tool == "xdotool":
        rc, out, _ = await _run("xdotool", "search", "--onlyvisible", "--name", "", timeout=TIMEOUT)
        if rc == 0 and out.strip():
            for wid in out.strip().splitlines():
                wid = wid.strip()
                if not wid:
                    continue
                _, title_out, _ = await _run("xdotool", "getwindowname", wid, timeout=TIMEOUT)
                _, geom_out, _ = await _run("xdotool", "getwindowgeometry", "--shell", wid, timeout=TIMEOUT)
                x = y = w = h = 0
                focused = False
                for line in geom_out.splitlines():
                    if line.startswith("X="):
                        x = int(line.split("=")[1])
                    elif line.startswith("Y="):
                        y = int(line.split("=")[1])
                    elif line.startswith("WIDTH="):
                        w = int(line.split("=")[1])
                    elif line.startswith("HEIGHT="):
                        h = int(line.split("=")[1])
                _, focus_out, _ = await _run("xdotool", "getactivewindow", timeout=TIMEOUT)
                focused = focus_out.strip() == wid
                windows.append(WindowInfo(
                    id=wid, title=title_out.strip(),
                    x=x, y=y, width=w, height=h, focused=focused,
                ))
    elif tool == "swaymsg":
        rc, out, _ = await _run("swaymsg", "-t", "get_tree", timeout=TIMEOUT)
        if rc == 0:
            import json
            _collect_sway_windows(json.loads(out), windows)
    elif tool == "hyprctl":
        rc, out, _ = await _run("hyprctl", "clients", "-j", timeout=TIMEOUT)
        if rc == 0:
            import json
            for c in json.loads(out):
                at = c.get("at", [0, 0])
                sz = c.get("size", [0, 0])
                windows.append(WindowInfo(
                    id=str(c.get("address", "")),
                    title=c.get("title", ""),
                    x=at[0], y=at[1], width=sz[0], height=sz[1],
                    focused=c.get("focusHistoryID", -1) == 0,
                ))
    elif tool == "wlrctl":
        rc, out, _ = await _run("wlrctl", "toplevel", "list", timeout=TIMEOUT)
        if rc == 0:
            for line in out.strip().splitlines():
                windows.append(WindowInfo(id=line, title=line))

    return windows


def _collect_sway_windows(node: dict, out: list):
    """Recursively walk sway tree to find leaf windows."""
    if node.get("type") == "con" and node.get("name"):
        r = node.get("rect", {})
        out.append(WindowInfo(
            id=str(node.get("id", "")),
            title=node.get("name", ""),
            x=r.get("x", 0), y=r.get("y", 0),
            width=r.get("width", 0), height=r.get("height", 0),
            focused=node.get("focused", False),
        ))
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        _collect_sway_windows(child, out)


@router.post("/focus", response_model=FocusResponse)
async def focus_window(req: FocusRequest):
    """Focus a window by id or title substring."""
    tool = _tool_for_windows()

    if req.window_id:
        wid = req.window_id
    else:
        # Find by title
        windows = await list_windows()
        match = next((w for w in windows if req.title and req.title.lower() in w.title.lower()), None)
        if not match:
            raise HTTPException(404, f"No window matching title '{req.title}'")
        wid = match.id
        req.title = match.title

    if tool == "xdotool":
        await _run("xdotool", "windowactivate", "--sync", wid, timeout=TIMEOUT)
    elif tool == "swaymsg":
        await _run("swaymsg", f"[con_id={wid}]", "focus", timeout=TIMEOUT)
    elif tool == "hyprctl":
        await _run("hyprctl", "dispatch", "focuswindow", f"address:{wid}", timeout=TIMEOUT)
    elif tool == "wlrctl":
        await _run("wlrctl", "toplevel", "focus", wid, timeout=TIMEOUT)

    return FocusResponse(success=True, window_id=wid, title=req.title or wid)


@router.post("/click-text", response_model=ClickTextResponse)
async def click_text(req: ClickTextRequest):
    """Find text on screen via OCR and click its center position."""
    b64, screen_w, screen_h = await _take_screenshot_b64()

    _check_tool("tesseract")
    # Get hOCR output for bounding boxes
    img_bytes = base64.b64decode(b64)
    fd, tmp_in = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    tmp_out = tmp_in + "_hocr"
    try:
        Path(tmp_in).write_bytes(img_bytes)
        await _run(
            "tesseract", tmp_in, tmp_out,
            "-l", "eng+chi_sim",
            "--psm", "6",
            "hocr",
            timeout=TIMEOUT,
        )
        hocr_path = Path(tmp_out + ".hocr")
        if not hocr_path.exists():
            return ClickTextResponse(success=False, found=False)

        hocr = hocr_path.read_text()
        # Parse bbox from hOCR — find matching word/line
        pattern = re.compile(
            r'bbox\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)["\s].*?>(.*?)<',
            re.IGNORECASE,
        )
        matches = []
        for m in pattern.finditer(hocr):
            x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            text = re.sub(r"<.*?>", "", m.group(5)).strip()
            if req.text.lower() in text.lower():
                matches.append(((x1 + x2) // 2, (y1 + y2) // 2, text))

        if len(matches) < req.occurrence:
            return ClickTextResponse(success=False, found=False)

        cx, cy, matched = matches[req.occurrence - 1]
        # Scale to screen coordinates if hOCR dimensions differ from screen
        await _run(
            "ydotool" if DISPLAY_SERVER == DisplayServer.WAYLAND else "xdotool",
            *(
                ["mousemove", "--absolute", "-x", str(cx), "-y", str(cy)]
                if DISPLAY_SERVER == DisplayServer.WAYLAND
                else ["mousemove", str(cx), str(cy)]
            ),
            timeout=TIMEOUT,
        )
        btn_str = "1" if req.button == 1 else str(req.button)
        if DISPLAY_SERVER == DisplayServer.WAYLAND:
            await _run("ydotool", "click", btn_str, timeout=TIMEOUT)
        else:
            await _run("xdotool", "click", btn_str, timeout=TIMEOUT)

        return ClickTextResponse(
            success=True, found=True,
            x=cx, y=cy, matched_text=matched,
        )
    finally:
        for p in (tmp_in, tmp_out + ".hocr"):
            try:
                os.unlink(p)
            except OSError:
                pass


@router.post("/wait-text", response_model=WaitTextResponse)
async def wait_text(req: WaitTextRequest):
    """Wait until *text* appears on screen (polling OCR)."""
    import time

    deadline = time.monotonic() + req.timeout
    elapsed = 0.0
    while time.monotonic() < deadline:
        b64, _, _ = await _take_screenshot_b64()
        text = await _ocr_from_b64(b64)
        if req.text.lower() in text.lower():
            return WaitTextResponse(found=True, elapsed=round(elapsed, 2))
        await asyncio.sleep(req.interval)
        elapsed = time.monotonic() - (deadline - req.timeout)

    return WaitTextResponse(found=False, elapsed=round(req.timeout, 2))


@router.post("/read-region", response_model=ReadRegionResponse)
async def read_region(req: ReadRegionRequest):
    """Read text from a specific screen region."""
    b64, _, _ = await _take_screenshot_b64()
    text = await _ocr_from_b64(
        b64,
        lang=req.lang,
        region={"x": req.x, "y": req.y, "w": req.width, "h": req.height},
    )
    return ReadRegionResponse(
        text=text,
        region={"x": req.x, "y": req.y, "width": req.width, "height": req.height},
    )


@router.post("/scroll")
async def scroll(amount: int = 3, direction: str = "down"):
    """Scroll the mouse wheel. direction: 'up' or 'down'."""
    tool = _tool_for_mouse()

    _scroll_val = amount if direction == "up" else -amount
    if tool == "ydotool":
        # ydotool mousemove with wheel: use button 4/5 or wheel flag
        btn = "4" if direction == "up" else "5"
        for _ in range(amount):
            await _run("ydotool", "click", btn, timeout=TIMEOUT)
    else:
        btn = "4" if direction == "up" else "5"
        for _ in range(amount):
            await _run("xdotool", "click", btn, timeout=TIMEOUT)

    return {"success": True, "direction": direction, "amount": amount}


@router.get("/health")
async def rpa_health():
    """Limb RPA health check — lists available tools."""
    tools = {}
    for name in ("grim", "scrot", "tesseract", "ydotool", "wtype",
                  "xdotool", "convert", "swaymsg", "hyprctl", "wlrctl"):
        tools[name] = shutil.which(name) is not None
    return {
        "status": "ok",
        "component": "Limb RPA Engine",
        "display_server": DISPLAY_SERVER.value,
        "tools": tools,
    }
