from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Literal


ResolverKind = Literal["addon", "framework", "missing"]


@dataclass(frozen=True)
class MaterialSemantics:
    translucent: bool = False
    alpha_test: bool = False
    additive: bool = False
    refractive: bool = False
    two_sided: bool = False
    resolver: ResolverKind = "missing"
    confidence: float = 0.35
    source_relative: str | None = None

    @classmethod
    def opaque(cls) -> "MaterialSemantics":
        return cls(resolver="addon", confidence=1.0)

    @property
    def requires_render(self) -> bool:
        return self.translucent or self.alpha_test or self.additive or self.refractive or self.two_sided

    @property
    def risk(self) -> float:
        flags = sum((self.translucent, self.alpha_test, self.additive, self.refractive, self.two_sided))
        return min(1.0, 0.2 * flags + 0.35 * (1.0 - self.confidence))


def _canonical_material(value: str) -> PurePosixPath:
    if type(value) is not str or not value.strip():
        raise ValueError("material identity is empty")
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("material identity must be relative")
    if path.suffix.casefold() == ".vmt":
        path = path.with_suffix("")
    return PurePosixPath(*(part.casefold() for part in path.parts if part not in ("", ".")))


def _enabled(text: str, name: str) -> bool:
    match = re.search(
        rf'(?i)(?:"{re.escape(name)}"|{re.escape(name)})\s+(?:"([^"\r\n]+)"|([^\s}}]+))',
        text,
    )
    if match is None:
        return False
    value = (match.group(1) or match.group(2) or "").strip().casefold()
    return value not in {"", "0", "0.0", "false", "no"}


def _read_semantics(path: Path, resolver: ResolverKind, relative: PurePosixPath) -> MaterialSemantics:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = "\n".join(line.split("//", 1)[0] for line in text.splitlines())
    shader_match = re.search(r'^\s*"?([^"\s{]+)"?\s*\{', text, flags=re.IGNORECASE)
    shader = shader_match.group(1).casefold() if shader_match else ""
    return MaterialSemantics(
        translucent=_enabled(text, "$translucent"),
        alpha_test=_enabled(text, "$alphatest"),
        additive=_enabled(text, "$additive"),
        refractive="refract" in shader or _enabled(text, "$refracttexture"),
        two_sided=_enabled(text, "$nocull"),
        resolver=resolver,
        confidence=1.0 if resolver == "addon" else 0.9,
        source_relative=PurePosixPath("materials", *relative.parts).with_suffix(".vmt").as_posix(),
    )


def resolve_material_semantics(
    material: str,
    addon_root: Path,
    framework_root: Path | None,
    material_directories: tuple[PurePosixPath, ...] = (),
) -> MaterialSemantics:
    relative = _canonical_material(material)
    roots: tuple[tuple[ResolverKind, Path], ...] = (("addon", Path(addon_root)),) + (
        (("framework", Path(framework_root)),) if framework_root is not None else ()
    )
    for resolver, root in roots:
        candidates = (PurePosixPath(),) + tuple(material_directories)
        for directory in candidates:
            candidate_relative = PurePosixPath(*directory.parts, *relative.parts)
            path = root / "materials" / Path(*candidate_relative.parts).with_suffix(".vmt")
            if path.is_file():
                return _read_semantics(path, resolver, candidate_relative)
    return MaterialSemantics()
