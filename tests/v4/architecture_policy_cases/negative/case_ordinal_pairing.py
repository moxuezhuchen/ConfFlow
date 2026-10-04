"""Negative fixture for the architecture policy (intentionally violating)."""


def pair(left: list, right: list) -> list:
    paired = []
    for i in range(len(left)):
        paired.append((left[i], right[i]))
    return paired
