#!/usr/bin/env python3

import base64
import itertools
import os
import re
from io import BytesIO

import urwid
from PIL import Image, ImageOps
from textual_image._sixel import SixelOptions, image_to_sixels
from urwid.canvas import SolidCanvas
from urwid.display import raw as urwid_raw_display


_KITTY_BEGIN = "\x1b_G"
_KITTY_END = "\x1b\\"
_SAVE_CURSOR = "\x1b7"
_RESTORE_CURSOR = "\x1b8"
_CHUNK_SIZE = 4096
_SIXEL_COLORS = 192
_IMAGE_ID_START = max(1000, (os.getpid() & 0x7FFF) * 16)
_IMAGE_ID_COUNTER = itertools.count(_IMAGE_ID_START)
_CELL_WIDTH_PX = 0
_CELL_HEIGHT_PX = 0


def _kitty_command(params, payload=""):
    return f"{_KITTY_BEGIN}{params};{payload}{_KITTY_END}"


def detect_native_image_protocol(env=None):
    env = os.environ if env is None else env
    forced = str(env.get("BODIAN_IMAGE_PROTOCOL") or "").strip().lower()
    if forced in ("kitty", "sixel", "none"):
        return forced
    term = str(env.get("TERM") or "").lower()
    term_program = str(env.get("TERM_PROGRAM") or "").lower()
    if env.get("KITTY_WINDOW_ID") or "kitty" in term or term_program == "wezterm" or env.get("WEZTERM_EXECUTABLE"):
        return "kitty"
    if env.get("WT_SESSION") or "sixel" in term:
        return "sixel"
    return "none"


def _get_sixel_width_correction(env=None, cell_width_px=0, cell_height_px=0):
    env = os.environ if env is None else env
    configured = str(env.get("BODIAN_SIXEL_WIDTH_CORRECT") or "").strip()
    if configured:
        try:
            return max(0.1, float(configured))
        except ValueError:
            return 1.0
    return 1.0


def _normalize_png_binary(binary):
    with Image.open(BytesIO(binary)) as image:
        image = ImageOps.exif_transpose(image)
        image.load()
        if "A" in image.getbands():
            bbox = image.getchannel("A").getbbox()
            if bbox:
                image = image.crop(bbox)
        width, height = image.size
        if not width or not height:
            raise RuntimeError("封面尺寸无效")
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue(), width, height


def _load_rgba_image(binary):
    with Image.open(BytesIO(binary)) as image:
        image = ImageOps.exif_transpose(image)
        image.load()
        if "A" in image.getbands():
            bbox = image.getchannel("A").getbbox()
            if bbox:
                image = image.crop(bbox)
        width, height = image.size
        if not width or not height:
            raise RuntimeError("封面尺寸无效")
        return image.convert("RGBA")


def _cell_height_ratio():
    if _CELL_WIDTH_PX <= 0 or _CELL_HEIGHT_PX <= 0:
        _set_cell_pixel_size(*_get_cell_pixel_size())
    return _CELL_HEIGHT_PX / max(1.0, _CELL_WIDTH_PX)


def _fit_native_image_cells(image_width, image_height, maxcol, maxrow):
    area_ratio = maxcol / max(1.0, maxrow * _cell_height_ratio())
    image_ratio = image_width / image_height
    if image_ratio >= area_ratio:
        draw_cols = maxcol
        draw_rows = max(1, min(maxrow, int(round(draw_cols / (image_ratio * _cell_height_ratio())))))
    else:
        draw_rows = maxrow
        draw_cols = max(1, min(maxcol, int(round(draw_rows * image_ratio * _cell_height_ratio()))))
    return draw_cols, draw_rows


def _get_cell_pixel_size():
    if _CELL_WIDTH_PX > 0 and _CELL_HEIGHT_PX > 0:
        return _CELL_WIDTH_PX, _CELL_HEIGHT_PX
    if os.name != "nt":
        return 8, 16
    try:
        import ctypes

        class COORD(ctypes.Structure):
            _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

        class CONSOLE_FONT_INFOEX(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("nFont", ctypes.c_ulong),
                ("dwFontSize", COORD),
                ("FontFamily", ctypes.c_uint),
                ("FontWeight", ctypes.c_uint),
                ("FaceName", ctypes.c_wchar * 32),
            ]

        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        info = CONSOLE_FONT_INFOEX()
        info.cbSize = ctypes.sizeof(CONSOLE_FONT_INFOEX)
        if ctypes.windll.kernel32.GetCurrentConsoleFontEx(handle, False, ctypes.byref(info)):
            return max(1, int(info.dwFontSize.X)), max(1, int(info.dwFontSize.Y))
    except Exception:
        pass
    return 8, 16


def _set_cell_pixel_size(width, height):
    global _CELL_WIDTH_PX, _CELL_HEIGHT_PX
    width = max(1, int(width))
    height = max(1, int(height))
    if os.name == "nt":
        width = max(8, width)
        height = max(16, height)
    _CELL_WIDTH_PX = width
    _CELL_HEIGHT_PX = height


def _parse_cell_size_response(raw_text):
    match = re.search(r"\x1b\[(?:6|16);(\d+);(\d+)t", raw_text)
    if not match:
        return None
    height, width = match.groups()
    return int(width), int(height)


def _encode_sixel(binary, cols, rows, cell_width_px, cell_height_px, fill=False):
    image = _load_rgba_image(binary)
    target_width = max(
        1,
        int(
            round(
                int(cols)
                * max(1, int(cell_width_px))
                * _get_sixel_width_correction(cell_width_px=cell_width_px, cell_height_px=cell_height_px)
            )
        ),
    )
    target_height = max(1, int(rows) * max(1, int(cell_height_px)))
    if fill:
        image = ImageOps.fit(image, (target_width, target_height), method=Image.Resampling.LANCZOS)
    else:
        image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
    return image_to_sixels(image, SixelOptions(colors=_SIXEL_COLORS, quantize="maxcoverage"))


class CoverImageWidget(urwid.WidgetWrap):

    def __init__(self, on_activate=None, max_cols=None, max_rows=None, fill=False):
        self.text = urwid.Text(("muted", "未播放"), align="center")
        self.image_binary = None
        self.image_png_binary = None
        self.image_sixel_cache = {}
        self.image_width = 0
        self.image_height = 0
        self.image_revision = 0
        self.on_activate = on_activate
        self.max_cols = max_cols
        self.max_rows = max_rows
        self.fill = fill
        self.image_id = next(_IMAGE_ID_COUNTER)
        self.placement_id = self.image_id
        super().__init__(urwid.Filler(self.text, valign="middle"))

    def sizing(self):
        return frozenset([urwid.BOX, urwid.FLOW])

    def rows(self, size, focus=False):
        maxcol = size[0]
        if not self.image_binary or maxcol <= 0:
            return self.text.rows((maxcol,), focus)
        self.get_native_image_payload()
        if not self.image_width or not self.image_height:
            return self.text.rows((maxcol,), focus)
        image_area_cols = min(maxcol, self.max_cols) if self.max_cols else maxcol
        if self.fill:
            return max(1, self.max_rows or image_area_cols)
        _, draw_rows = _fit_native_image_cells(
            self.image_width, self.image_height, image_area_cols, image_area_cols * 4
        )
        return max(1, draw_rows)

    def selectable(self):
        return self.on_activate is not None

    def keypress(self, size, key):
        if self.on_activate and key in ("enter", " "):
            self.on_activate()
            return None
        return key

    def mouse_event(self, size, event, button, col, row, focus):
        if self.on_activate and button == 1 and event == "mouse press":
            self.on_activate()
            return True
        return False

    def set_placeholder(self, message, attr="muted"):
        self.image_binary = None
        self.image_png_binary = None
        self.image_sixel_cache.clear()
        self.image_width = 0
        self.image_height = 0
        self.image_revision += 1
        self.text.set_text((attr, message))
        self._invalidate()

    def set_image(self, binary):
        self.image_binary = binary
        self.image_png_binary = None
        self.image_sixel_cache.clear()
        self.image_width = 0
        self.image_height = 0
        self.image_revision += 1
        self._invalidate()

    def get_native_image_payload(self, protocol="kitty", cols=0, rows=0, cell_size=(8, 16)):
        if self.image_png_binary is None:
            self.image_png_binary, self.image_width, self.image_height = _normalize_png_binary(self.image_binary)
        if protocol == "kitty":
            return self.image_png_binary
        if protocol == "sixel":
            cache_key = (int(cols or 0), int(rows or 0), int(cell_size[0]), int(cell_size[1]))
            if cache_key not in self.image_sixel_cache:
                self.image_sixel_cache[cache_key] = _encode_sixel(
                    self.image_binary,
                    cache_key[0],
                    cache_key[1],
                    cache_key[2],
                    cache_key[3],
                    fill=self.fill,
                )
            return self.image_sixel_cache[cache_key]
        return b""

    def render(self, size, focus=False):
        if not self.image_binary or not size or size[0] <= 0:
            return super().render(size, focus=focus)
        outer_cols = size[0]
        outer_rows = size[1] if len(size) > 1 else self.rows(size, focus)
        if outer_rows <= 0:
            return super().render(size, focus=focus)
        self.get_native_image_payload()
        image_area_cols = min(outer_cols, self.max_cols) if self.max_cols else outer_cols
        image_area_rows = min(outer_rows, self.max_rows) if self.max_rows else outer_rows
        if self.fill:
            draw_cols, draw_rows = image_area_cols, image_area_rows
        else:
            draw_cols, draw_rows = _fit_native_image_cells(self.image_width, self.image_height, image_area_cols, image_area_rows)
        left = max(0, (outer_cols - draw_cols) // 2)
        top = max(0, (outer_rows - draw_rows) // 2)
        canvas = SolidCanvas(" ", outer_cols, outer_rows)
        canvas.coords[f"native-image-{self.image_id}"] = (
            left,
            top,
            {
                "kind": "native_image",
                "image_id": self.image_id,
                "placement_id": self.placement_id,
                "cols": draw_cols,
                "rows": draw_rows,
                "revision": self.image_revision,
                "widget": self,
            },
        )
        return canvas


class NativeImageScreen(urwid_raw_display.Screen):

    def __init__(self, *args, image_protocol=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.image_protocol = detect_native_image_protocol() if image_protocol is None else image_protocol
        self.native_images = {}
        self.last_screen_size = None
        self.cell_width_px, self.cell_height_px = _get_cell_pixel_size()
        _set_cell_pixel_size(self.cell_width_px, self.cell_height_px)

    def _start(self, *args, **kwargs):
        super()._start(*args, **kwargs)
        if self.image_protocol == "sixel":
            self._refresh_terminal_cell_size()

    def draw_screen(self, size, canvas):
        if self.last_screen_size is not None and self.last_screen_size != size:
            self.native_images.clear()
            if self.image_protocol == "sixel":
                self._refresh_terminal_cell_size()
            self.write("\x1b[2J\x1b[H")
        self.last_screen_size = size
        super().draw_screen(size, canvas)
        output = self._sync_native_images(self._collect_native_images(canvas))
        if output:
            self.write(output)
            self.flush()

    def stop(self):
        if self._started and self.native_images:
            self.write(self._clear_native_images())
            self.flush()
        self.native_images.clear()
        super().stop()

    def _collect_native_images(self, canvas):
        images = {}
        for x, y, data in canvas.coords.values():
            if not isinstance(data, dict) or data.get("kind") != "native_image":
                continue
            images[data["image_id"]] = {
                "image_id": data["image_id"],
                "placement_id": data["placement_id"],
                "x": x,
                "y": y,
                "cols": data["cols"],
                "rows": data["rows"],
                "revision": data["revision"],
                "widget": data["widget"],
            }
        return images

    def _sync_native_images(self, images):
        if self.image_protocol == "none":
            self.native_images.clear()
            return ""
        if self.image_protocol == "sixel":
            return self._sync_sixel_images(images)
        output = []
        for image_id in list(self.native_images):
            if image_id in images:
                continue
            output.append(self._delete_image(image_id))
            del self.native_images[image_id]
        for image_id, image in images.items():
            previous = self.native_images.get(image_id)
            if not previous or previous["revision"] != image["revision"]:
                output.append(self._transmit_image(image_id, image["widget"].get_native_image_payload(protocol="kitty")))
            output.append(self._place_image(image))
            self.native_images[image_id] = {
                "revision": image["revision"],
                "x": image["x"],
                "y": image["y"],
                "cols": image["cols"],
                "rows": image["rows"],
            }
        if not output:
            return ""
        return "".join([_SAVE_CURSOR, *output, _RESTORE_CURSOR])

    def _sync_sixel_images(self, images):
        output = []
        for image_id in list(self.native_images):
            if image_id in images:
                continue
            output.append(self._erase_image_area(self.native_images[image_id]))
            del self.native_images[image_id]
        for image_id, image in images.items():
            previous = self.native_images.get(image_id)
            if previous and (
                previous["revision"] != image["revision"]
                or previous["x"] != image["x"]
                or previous["y"] != image["y"]
                or previous["cols"] != image["cols"]
                or previous["rows"] != image["rows"]
            ):
                output.append(self._erase_image_area(previous))
            output.append(self._place_sixel_image(image))
            self.native_images[image_id] = {
                "revision": image["revision"],
                "x": image["x"],
                "y": image["y"],
                "cols": image["cols"],
                "rows": image["rows"],
            }
        if not output:
            return ""
        return "".join([_SAVE_CURSOR, *output, _RESTORE_CURSOR])

    def _transmit_image(self, image_id, binary):
        encoded = base64.b64encode(binary).decode("ascii")
        chunks = [encoded[index : index + _CHUNK_SIZE] for index in range(0, len(encoded), _CHUNK_SIZE)]
        output = []
        for index, chunk in enumerate(chunks):
            more = 1 if index + 1 < len(chunks) else 0
            if index == 0:
                params = f"a=t,t=d,f=100,i={image_id},q=2,m={more}"
            else:
                params = f"m={more},q=2"
            output.append(_kitty_command(params, chunk))
        return "".join(output)

    def _place_image(self, image):
        return (
            f"\x1b[{image['y'] + 1};{image['x'] + 1}H"
            + _kitty_command(
                f"a=p,i={image['image_id']},p={image['placement_id']},c={image['cols']},r={image['rows']},C=1,z=-1,q=2"
            )
        )

    def _delete_image(self, image_id):
        return _kitty_command(f"a=d,d=I,i={image_id},q=2")

    def _clear_native_images(self):
        if self.image_protocol == "sixel":
            return "".join(self._erase_image_area(image) for image in self.native_images.values())
        return "".join(self._delete_image(image_id) for image_id in self.native_images)

    def _refresh_terminal_cell_size(self):
        if os.name == "nt":
            self.cell_width_px, self.cell_height_px = _get_cell_pixel_size()
            _set_cell_pixel_size(self.cell_width_px, self.cell_height_px)
            return
        response = self._query_terminal("\x1b[14t\x1b[18t\x1b[16t", timeout=0.25)
        pixel_match = re.search(r"\x1b\[4;(\d+);(\d+)t", response)
        char_match = re.search(r"\x1b\[8;(\d+);(\d+)t", response)
        if pixel_match and char_match:
            pixel_height, pixel_width = (int(value) for value in pixel_match.groups())
            char_height, char_width = (int(value) for value in char_match.groups())
            if pixel_width > 0 and pixel_height > 0 and char_width > 0 and char_height > 0:
                self.cell_width_px = max(1, int(round(pixel_width / char_width)))
                self.cell_height_px = max(1, int(round(pixel_height / char_height)))
                _set_cell_pixel_size(self.cell_width_px, self.cell_height_px)
                return
        size = _parse_cell_size_response(response)
        if not size:
            return
        self.cell_width_px, self.cell_height_px = size
        _set_cell_pixel_size(self.cell_width_px, self.cell_height_px)

    def _query_terminal(self, query, timeout=0.08):
        try:
            self.write(query)
            self.flush()
            chunks = []
            deadline = timeout
            while True:
                raw = self._read_raw_input(deadline)
                if not raw:
                    break
                chunks.append(bytes(raw))
                deadline = 0.01
            return b"".join(chunks).decode("ascii", errors="ignore")
        except Exception:
            return ""

    def _place_sixel_image(self, image):
        return (
            f"\x1b[{image['y'] + 1};{image['x'] + 1}H"
            + image["widget"].get_native_image_payload(
                protocol="sixel",
                cols=image["cols"],
                rows=image["rows"],
                cell_size=(self.cell_width_px, self.cell_height_px),
            )
        )

    def _erase_image_area(self, image):
        output = []
        for row in range(image["rows"]):
            output.append(f"\x1b[{image['y'] + row + 1};{image['x'] + 1}H{' ' * image['cols']}")
        return "".join(output)
