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
    """Centered simplified bullseye on a white circular plate.

    Geometry sized as fractions of `size` so it scales cleanly. Stroke
    is computed once at the master size (256) and PIL's downsample
    handles the smaller variants — works because every shape is large,
    centered, and high-contrast.
    """
    img = Image.new("RGBA", (size, size), TRANSPARENT)
    d = ImageDraw.Draw(img)

    cx = cy = size / 2.0

    # White plate: 96% of canvas, centered. The 2% margin keeps the
    # plate read as a CIRCLE on dark backdrops (no antialiased white
    # pixels touching the canvas edge that would produce a gray
    # fringe).
    plate_r = (size / 2.0) * 0.96
    d.ellipse(
        [cx - plate_r, cy - plate_r, cx + plate_r, cy + plate_r],
        fill=BG,
    )

    # Two concentric black rings + red center. Radii expressed as
    # fractions of half the canvas so the proportions match the full
    # logo B. Stroke is ~7% of half-canvas (clamped to >=2 px so it
    # survives 16x16 downsample).
    half = size / 2.0
    r_outer = half * 0.78
    r_mid = half * 0.50
    r_dot = half * 0.22
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


def draw_full_with_arrow(size: int) -> Image.Image:
    """Full Logo B with arrow — for the tray icon (logo.png) only.

    Geometry mirrors the canonical SVG: bullseye centered at (14, 18)
    on a 32-unit canvas, arrow from (22, 10) to (14, 18), fletching
    at the tail. Plate is a circle just larger than the outer ring.
    """
    img = Image.new("RGBA", (size, size), TRANSPARENT)
    d = ImageDraw.Draw(img)
    scale = size / 32.0

    # 1. White CIRCLE plate behind the rings
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

    # logo.png: full design, used by tray + landing.
    full = draw_full_with_arrow(256)
    png_path = assets / "logo.png"
    full.save(png_path, format="PNG")
    print(f"wrote {png_path}")

    # logo.ico: simplified centered, all the standard Windows sizes.
    # Master rendered at 256 then downsampled by PIL to each size.
    # Simplified geometry survives downsample cleanly.
    master = draw_simplified_centered(256)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48),
             (64, 64), (128, 128), (256, 256)]
    ico_path = assets / "logo.ico"
    master.save(ico_path, format="ICO", sizes=sizes)
    print(f"wrote {ico_path} (sizes: {[s[0] for s in sizes]})")

    # Also drop a 256-PNG of the simplified design for debugging.
    debug_path = assets / "_logo_simplified_preview.png"
    master.save(debug_path, format="PNG")
    print(f"wrote {debug_path} (preview)")


if __name__ == "__main__":
    main()
