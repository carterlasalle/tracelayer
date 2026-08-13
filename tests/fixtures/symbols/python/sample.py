# Marker comment for fixtures (deliberately not a real marker).


def foo(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


class Bar:
    def baz(self) -> None:
        pass

    @property
    def qux(self) -> int:
        return 1


@deco  # noqa: F821 — deliberately undefined; fixture exercises parser tolerance
def decorated() -> None:
    pass
