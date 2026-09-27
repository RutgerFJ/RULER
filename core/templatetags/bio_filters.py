from django import template

register = template.Library()

@register.filter(name='human_bp')
def human_bp(value):
    """
    Converts a raw base-pair number into a human-readable string (Gb, Mb, Kb, bp).
    Handles None or invalid inputs gracefully.
    """
    if value is None:
        return "0 bp"
    
    try:
        bp = float(value)
    except (ValueError, TypeError):
        return f"{value} bp"

    units = (
        (1_000_000_000, "Gb"),
        (1_000_000, "Mb"),
        (1_000, "Kb"),
    )

    for divider, unit in units:
        if bp >= divider:
            return f"{bp / divider:.2f} {unit}"

    return f"{bp:.0f} bp"