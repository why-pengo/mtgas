import re

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

_MANA_SYMBOL_RE = re.compile(r"\{([^}]+)\}")


@register.filter(is_safe=True)
def mana_icons(value: str) -> str:
    """Convert a mana cost string like '{2}{W}{W}' into Scryfall SVG img tags."""
    if not value:
        return "—"

    def _symbol_to_img(match: re.Match) -> str:
        symbol = match.group(1).upper()
        url = f"https://svgs.scryfall.io/card-symbols/{symbol}.svg"
        return (
            f'<img src="{url}" class="mana-symbol" alt="{{{symbol}}}" '
            f'title="{{{symbol}}}" loading="lazy">'
        )

    return mark_safe(_MANA_SYMBOL_RE.sub(_symbol_to_img, value))


@register.filter
def cmc_value(mana_cost: str) -> int:
    """Return a numeric sort value for a mana cost string (sum of generic + coloured pips)."""
    total = 0
    for sym in _MANA_SYMBOL_RE.findall(mana_cost or ""):
        try:
            total += int(sym)
        except ValueError:
            if sym.upper() not in ("X", "Y", "Z"):
                total += 1
    return total
