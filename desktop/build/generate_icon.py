"""Generate Logo B as a multi-size .ico for the Windows taskbar.

Two files come out of this:

  logo.png  — the full Logo B (256x256). White circular plate,
              concentric black rings, red center, arrow + fletching
              from the upper-right. Used for the tray icon and the
              landing page hero — places where the icon has room
              to breathe.

  logo.ico  — a SIMPLIFIED, CENTERED bullseye (no arrow), saved at
              16/24/32/48/64/128/256 sizes. Used for the .exe
              resource icon, which Windows reads for the taskbar,
              title bar, alt-tab, file-explorer thumbnails, etc.

Why two designs:

    The full Logo B is offset to the lower-left of the canvas to leave
    room for the arrow in the upper-right corner. That looks great at
    256x256, but at 16x16 (Windows taskbar size) the offset pushes
    the bullseye into a corner, the 1-px rings get blurred into the
    white plate, and what reaches the user's eye is a fuzzy red dot
    on no visible background. That was the bug: "Taskbar icon is
    still wrong. There's no white background on it."

    PIL's ICO writer downsamples a single master image to every size
    in `sizes=[...]`; it does not support per-size native rendering
    via append_images. So we deliberately give it a master that
    survives downsampling — the simplified centered design has only
    big shapes (white circle, two thick rings, red dot), no fine
    detail, and is centered so it doesn't drift off-canvas at small
    sizes.

    Result at 16x16: white plate visibly fills the icon, two black
    rings read as a bullseye, red dot in the middle. Brand reads at
    a glance even on a dark taskbar.

Run from the desktop/build directory:
    python generate_icon.py
"""
from pathlib import Path
from PIL import Image, ImageDraw

# Target colors (from shell.css and design handoff)
FG = (26, 22, 20, 255)        # var(--fg) #1a1614 — black-warm
RED = (192, 32, 42, 255)      # var(--logo-red) #c0202a
BG = (255, 255, 255, 255)     # pure white plate (per design handoff)
TRANSPARENT = (0, 0, 0, 0)


def draw_simplified_centered(size: int) -> Image.Image:
    """Centered simplified bullseye on a SOLID white rounded-square
    background — Win11 app-icon style.

    Why a solid background and not a transparent canvas: on a dark
    Windows taskbar, transparent corners show the taskbar through,
    making the icon look like "bullseye on dark patches" instead of
    "white app icon with bullseye". A solid white background fills
    the entire icon slot the way every other Win11 app icon does.

    Rounded corners (radius ~18% of size, capped) match Win11's
    rounded-rect aesthetic without being so aggressive they look
    iOS-y. The radius scales with size so 16x16 looks right and so
    does 256x256.
    """
    img = Image.new("RGBA", (size, size), TRANSPARENT)
    d = ImageDraw.Draw(img)

    # 1. Solid white rounded-square background. Pillow's
    #    rounded_rectangle takes a radius in pixels.
    corner_r = max(2, int(round(size * 0.18)))
    d.rounded_rectangle(
        [(0, 0), (size - 1, size - 1)],
        radius=corner_r,
        fill=BG,
    )

    cx = cy = size / 2.0

    # 2. Bullseye design layered on top. Slightly tighter than before
    #    because the background is now a square — the rings need
    #    breathing room from the corners.
    half = size / 2.0
    r_outer = half * 0.72
    r_mid = half * 0.46
    r_dot = half * 0.20
    stroke = max(2, int(round(half * 0.10)))

    d.ellipse(
        [cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer],
        outline=FG, width=stroke,
    )
    d.ellipse(
        [cx - r_mid, cy - r_mid, cx + r_mid, cy + r_mid],
        outline=FG, width=stroke,
    )
    d.ellipse(
        [cx - r_dot, cy - r_dot, cx + r_dot, cy + r_dot],
        fill=RED,
    )

    return img


def draw_full_with_arrow(
    size: int,
    *,
    background: str = "transparent",
) -> Image.Image:
    """Full Logo B with arrow — the canonical design.

    Args:
        size: icon edge length in pixels.
        background: one of "transparent" (for tray + landing page where
            the design sits on a controlled page bg) or "rounded_white"
            (for the .exe / taskbar where dark Win11 backdrops would
            otherwise show through transparent corners and make the
            icon read as a "red dot on dark patches").

    Geometry: bullseye centered at (14, 18) on a 32-unit canvas, arrow
    from (22, 10) to (14, 18), fletching at the tail. The bullseye is
    deliberately offset to the lower-left to leave room for the arrow
    in the upper-right.
    """
    img = Image.new("RGBA", (size, size), TRANSPARENT)
    d = ImageDraw.Draw(img)

    if background == "rounded_white":
        # Solid white rounded-square fills the whole canvas. Win11
        # app-icon style — taskbar dark backdrop never bleeds through.
        corner_r = max(2, int(round(size * 0.18)))
        d.rounded_rectangle(
            [(0, 0), (size - 1, size - 1)],
            radius=corner_r,
            fill=BG,
        )

    scale = size / 32.0

    # White CIRCLE plate behind the rings (kept even when the canvas
    # is already white so the design renders identically to the
    # transparent-canvas version — same geometry, no edge surprises).
    plate_cx = 14 * scale
    plate_cy = 18 * scale
    plate_r = 13.6 * scale
    d.ellipse(
        [plate_cx - plate_r, plate_cy - plate_r,
         plate_cx + plate_r, plate_cy + plate_r],
        fill=BG,
    )

    # 2. Geometry
    cx, cy = 14 * scale, 18 * scale
    r_outer, r_mid, r_dot = 13 * scale, 8 * scale, 3.5 * scale
    stroke = max(2, int(round(1.5 * scale)))

    d.ellipse(
        [cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer],
        outline=FG, width=stroke,
    )
    d.ellipse(
        [cx - r_mid, cy - r_mid, cx + r_mid, cy + r_mid],
        outline=FG, width=stroke,
    )
    d.ellipse(
        [cx - r_dot, cy - r_dot, cx + r_dot, cy + r_dot],
        fill=RED,
    )

    # Arrow shaft + fletching
    arrow_stroke = max(2, int(round(1.8 * scale)))
    sx, sy = 22 * scale, 10 * scale
    ex, ey = 14 * scale, 18 * scale
    d.line([sx, sy, ex, ey], fill=FG, width=arrow_stroke)

    fletch_stroke = max(2, int(round(1.5 * scale)))
    d.line([sx, sy, 26 * scale, 6 * scale], fill=FG, width=fletch_stroke)
    d.line([sx, sy, 26 * scale, 10 * scale], fill=FG, width=fletch_stroke)
    d.line([sx, sy, 22 * scale, 6 * scale], fill=FG, width=fletch_stroke)

    return img


def main() -> None:
    here = Path(__file__).resolve().parent
    assets = here.parent / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    # logo.png — transparent canvas. Used by the system tray (where
    # the OS theme controls the surrounding background) and the
    # landing page (sits on the warm beige page bg).
    full_transparent = draw_full_with_arrow(256, background="transparent")
    png_path = assets / "logo.png"
    full_transparent.save(png_path, format="PNG")
    print(f"wrote {png_path} (transparent canvas)")

    # logo.ico — full Logo B with rounded white background. The white
    # fills the whole canvas so the dark Win11 taskbar doesn't show
    # through transparent corners. PIL downsamples the 256 master to
    # each Windows size; the design survives downsample because the
    # offsets and stroke widths scale with size.
    master_ico = draw_full_with_arrow(256, background="rounded_white")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48),
             (64, 64), (128, 128), (256, 256)]
    ico_path = assets / "logo.ico"
    master_ico.save(ico_path, format="ICO", sizes=sizes)
    print(f"wrote {ico_path} (full design + rounded white bg, sizes: "
          f"{[s[0] for s in sizes]})")


if __name__ == "__main__":
    main()
