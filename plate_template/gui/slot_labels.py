"""Small, high-contrast identities without a box hiding the spot colour."""
from tkinter import font as tkfont


def draw_slot(canvas, x, y, pitch, slot, background, *, tags=(), min_size=12):
    text = str(slot)
    font = tkfont.Font(root=canvas, family="Segoe UI", size=-max(min_size, int(pitch * .26)), weight="bold")
    while font.measure(text) > pitch * .70 and abs(font.cget("size")) > 8:
        font.configure(size=font.cget("size") + 1)
    # WCAG relative luminance: choose the stronger black/white contrast.
    rgb = [int(background[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
    luminance = sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))
    ink = "#000000" if luminance > .179 else "#ffffff"
    return canvas.create_text(x, y, text=text, fill=ink, font=font, tags=tags)
