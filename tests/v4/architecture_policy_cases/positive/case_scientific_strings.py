"""Positive fixture: scientific keyword strings are not task dispatch."""

KEYWORD = "IRC=RCFC"
ROLE = "path_endpoint_forward"


def is_irc(keyword: str) -> bool:
    if keyword == "IRC=RCFC":
        return True
    return False
