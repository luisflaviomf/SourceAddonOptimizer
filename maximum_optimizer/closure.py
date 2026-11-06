from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


def _references(values: frozenset[str]) -> frozenset[str]:
    normalized = []
    for value in values:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("reference paths must be relative")
        normalized.append(path.as_posix().casefold())
    return frozenset(normalized)


@dataclass(frozen=True)
class ReferenceInventory:
    addon: frozenset[str] = field(default_factory=frozenset)
    framework: frozenset[str] = field(default_factory=frozenset)
    missing: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "addon", _references(self.addon))
        object.__setattr__(self, "framework", _references(self.framework))
        object.__setattr__(self, "missing", _references(self.missing))
        if (self.addon & self.framework) or (self.addon & self.missing) or (self.framework & self.missing):
            raise ValueError("reference resolver classes must be disjoint")

    @property
    def all(self) -> frozenset[str]:
        return self.addon | self.framework | self.missing


@dataclass(frozen=True)
class ClosureAudit:
    passed: bool
    external_references: tuple[str, ...]
    missing_references: tuple[str, ...]
    added_references: tuple[str, ...]
    framework_used_for_analysis: bool


def audit_reference_closure(
    original: ReferenceInventory,
    candidate: ReferenceInventory,
    framework_resolver_root: Path | None,
) -> ClosureAudit:
    framework_available = framework_resolver_root is not None
    external = () if framework_available else tuple(sorted(candidate.framework))
    missing = tuple(sorted(candidate.missing))
    added = tuple(sorted(candidate.all - original.all))
    return ClosureAudit(
        not external and not missing,
        external,
        missing,
        added,
        framework_available,
    )
