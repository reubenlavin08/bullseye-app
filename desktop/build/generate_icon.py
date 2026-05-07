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
    """Full Logo B with arrow — visually centered in the canvas.

    Args:
        size: icon edge length in pixels.
        background: one of "transparent" (for tray + landing page where
            the design sits on a controlled page bg) or "rounded_white"
            (for the .exe / taskbar where dark Win11 backdrops would
            otherwise show through transparent corners and make the
            icon read as a "red dot on dark patches").

    Geometry — re-centered 2026-05-06:
        Original logo had bullseye at (14, 18) with arrow tip at
        (22, 10), so the visual weight was lower-left. In the
        rounded-white .ico used for the taskbar that read as
        unprofessional — the design was clearly off-center within
        the white square.

        New layout: bullseye centered at (15, 17) on the 32-unit
        grid, slightly down-left of true center to compensate for
        the arrow's upper-right visual pull. Radii shrunk a touch
        (12 / 7.5 / 3.2 from 13 / 8 / 3.5) so the arrow tip and
        fletching tips don't crowd the canvas edge. Net effect: the
        FULL composition (target + arrow + fletching) reads as
        centered within the canvas / rounded-white square.
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
    plate_cx = 16 * scale
    plate_cy = 16 * scale
    plate_r = 12.0 * scale  # slightly larger than r_outer (11.5)
    d.ellipse(
        [plate_cx - plate_r, plate_cy - plate_r,
         plate_cx + plate_r, plate_cy + plate_r],
        fill=BG,
    )

    # 2. Geometry — bullseye dead-center on the canvas. The arrow
    #    extends upper-right from center; combined visual centroid
    #    is essentially the canvas center.
    cx, cy = 16 * scale, 16 * scale
    r_outer, r_mid, r_dot = 11.5 * scale, 7.0 * scale, 3.0 * scale
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

    # Arrow shaft: from (22, 10) to bullseye center (16, 16).
    # Tip at (16, 16) lands inside the red dot. Tail at (22, 10)
    # leaves room for fletching at (22-26, 6-10).
    arrow_stroke = max(2, int(round(1.8 * scale)))
    sx, sy = 22 * scale, 10 * scale
    ex, ey = 16 * scale, 16 * scale
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

    # logo.png — transparent canvas at 1024 (used by tray + landing).
    # Bumped from 256 → 1024 so the high-DPI render is genuinely
    # high-res; downstream consumers that need 256 can downscale.
    full_transparent = draw_full_with_arrow(1024, background="transparent")
    png_path = assets / "logo.png"
    full_transparent.save(png_path, format="PNG")
    print(f"wrote {png_path} (1024 transparent canvas)")

    # logo.ico — render the master at 1024 with the rounded-white
    # background, then PIL downsamples to each Windows size with
    # LANCZOS (high-quality). Bumping the master from 256 → 1024
    # gives PIL 4x more antialiasing data per output pixel; net
    # result is dramatically crisper rings + arrow at the small
    # icon sizes (16/24/32/48). The user reported the previous
    # 256-sourced .ico looked low-quality.
    master_ico = draw_full_with_arrow(1024, background="rounded_white")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48),
             (64, 64), (128, 128), (256, 256)]
    ico_path = assets / "logo.ico"
    master_ico.save(ico_path, format="ICO", sizes=sizes)
    print(f"wrote {ico_path} (1024 master, sizes: "
          f"{[s[0] for s in sizes]})")


if __name__ == "__main__":
    main()
