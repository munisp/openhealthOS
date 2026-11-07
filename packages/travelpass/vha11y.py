"""Accessibility & device-budget checks — sunlight-readable boards are
a requirement, not a preference.

WCAG contrast math implemented clean-room (relative luminance per
WCAG 2.x): every (text, background) pair a PWA ships must pass AA —
4.5:1 for body text, 3:1 for large text. A palette that fails is a
bug with a number attached.

Device budget: the surfaces target $50 Androids on 2G/3G — pages carry
a stated byte budget and a test counts the bytes.
"""

A11Y_VERSION = "vha11y@1.0.0"

AA_BODY = 4.5
AA_LARGE = 3.0
PAGE_BYTE_BUDGET = 120_000          # html+css+js inline, per page


class A11yError(Exception):
    pass


def _channel(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color):
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(fg, bg):
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def assert_aa(fg, bg, *, large=False):
    ratio = contrast_ratio(fg, bg)
    floor = AA_LARGE if large else AA_BODY
    if ratio < floor:
        raise A11yError(
            f"contrast {ratio:.2f}:1 below AA {floor}:1 for "
            f"{fg} on {bg} — unreadable text is a broken surface")
    return round(ratio, 2)


def page_bytes_ok(n_bytes, *, budget=PAGE_BYTE_BUDGET):
    if n_bytes > budget:
        raise A11yError(
            f"page weighs {n_bytes} bytes over a {budget} budget — "
            "a $50 Android on 3G is the customer, not the exception")
    return True


# The shipped dark ops palette, pinned: (fg, bg, large?)
PALETTE_PAIRS = [
    ("#e8efe9", "#0d1312", False),   # body on base
    ("#7be3a3", "#0d1312", False),   # accent on base
    ("#9ec7ff", "#0d1312", False),   # info accent on base
    ("#e8efe9", "#12324a", False),   # body on header
    ("#7be3a3", "#12324a", False),   # accent on header
    ("#8fa39b", "#0d1312", False),   # muted on base
    ("#e8b54a", "#0d1312", False),   # amber flag on base
]


def audit_palette():
    return {(fg, bg): assert_aa(fg, bg, large=large)
            for fg, bg, large in PALETTE_PAIRS}


def report():
    ratios = audit_palette()
    worst = min(ratios.values())
    return {"version": A11Y_VERSION, "pairs": len(ratios),
            "worst_ratio": worst, "aa": "pass",
            "page_byte_budget": PAGE_BYTE_BUDGET}
