"""Project metadata; does not claim that future product capabilities are live."""

from dataclasses import asdict, dataclass

from ai_commerce import __version__


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    project: str = "AI Commerce"
    package: str = "ai_commerce"
    version: str = __version__
    independent: bool = True
    foundation: str = "A01_BOOTSTRAP"
    engineering_flow: str = "EF01_BUILD_VERIFIED_FOR_SCOPE"
    product_runtime: str = "NOT_IMPLEMENTED"

    def to_dict(self) -> dict[str, str | bool]:
        return asdict(self)
