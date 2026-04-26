from typing import Optional


def parse_amount(s: str) -> Optional[float]:
    """Parst deutschen oder englischen Betragsstring zu float."""
    s = s.strip().replace("€", "").replace("EUR", "").replace("+", "").strip()
    negative = s.startswith("-")
    s = s.lstrip("-").strip()
    if "," in s and "." in s:
        if s.rindex(",") > s.rindex("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None
