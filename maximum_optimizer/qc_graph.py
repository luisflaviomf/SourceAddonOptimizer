from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Mapping


_SOURCE_SUFFIXES = {".smd", ".dmx"}
_MAX_GRAPH_FILES = 4096
_MAX_INCLUDE_DEPTH = 64


@dataclass(frozen=True)
class Token:
    value: str
    start: int
    end: int
    line: int
    kind: str = "word"


@dataclass(frozen=True)
class QcReference:
    graph_file: Path
    directive: str
    line: int
    logical_path: str
    source_path: Path
    role: str
    token_start: int
    token_end: int
    group: str = ""


@dataclass(frozen=True)
class QcInclude:
    graph_file: Path
    line: int
    logical_path: str
    include_path: Path
    token_start: int
    token_end: int


@dataclass(frozen=True)
class QcGraphFile:
    path: Path
    output_path: Path
    text: str
    references: tuple[QcReference, ...]
    includes: tuple[QcInclude, ...]


@dataclass(frozen=True)
class QcBodygroup:
    graph_file: Path
    line: int
    name: str
    group: str
    choices: tuple[QcReference | None, ...]


@dataclass(frozen=True)
class QcGraph:
    root: Path
    family_root: Path
    files: tuple[QcGraphFile, ...]
    references: tuple[QcReference, ...]
    bodygroups: tuple[QcBodygroup, ...] = ()


def _lex(text: str) -> tuple[Token, ...]:
    tokens: list[Token] = []
    index = 0
    line = 1
    while index < len(text):
        char = text[index]
        if char in " \t\r":
            index += 1
            continue
        if char == "\n":
            tokens.append(Token("\n", index, index + 1, line, "newline"))
            line += 1
            index += 1
            continue
        if char == "/" and index + 1 < len(text) and text[index + 1] == "/":
            index += 2
            while index < len(text) and text[index] != "\n":
                index += 1
            continue
        if char in "{}":
            tokens.append(Token(char, index, index + 1, line, "brace"))
            index += 1
            continue
        if char == '"':
            quote = char
            start = index + 1
            index += 1
            value: list[str] = []
            while index < len(text) and text[index] != quote:
                value.append(text[index])
                index += 1
            if index >= len(text):
                raise ValueError(f"unterminated quoted QC token at line {line}")
            tokens.append(Token("".join(value), start, index, line, "quoted"))
            index += 1
            continue
        start = index
        while index < len(text):
            if text[index].isspace() or text[index] in "{}\"":
                break
            if text[index] == "/" and index + 1 < len(text) and text[index + 1] == "/":
                break
            index += 1
        if index == start:
            raise ValueError(f"invalid QC token at line {line}")
        tokens.append(Token(text[start:index], start, index, line))
    return tuple(tokens)


def _safe_relative(value: str, label: str, *, allow_parent: bool = False) -> Path:
    normalized = value.replace("\\", "/").strip()
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(normalized)
    if (
        not normalized
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or (not allow_parent and ".." in posix.parts)
    ):
        raise ValueError(f"unsafe {label}: {value}")
    return Path(*posix.parts)


def _contained_existing(root: Path, current_dir: Path, value: str, label: str) -> Path:
    relative = _safe_relative(value, label, allow_parent=True)
    unresolved = current_dir / relative
    current = root
    try:
        lexical = Path(os.path.abspath(os.fspath(unresolved)))
        relative_from_root = lexical.relative_to(Path(os.path.abspath(os.fspath(root))))
    except ValueError as exc:
        raise ValueError(f"{label} escapes family root: {value}") from exc
    for part in relative_from_root.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} traverses symlink: {value}")
    resolved = unresolved.resolve(strict=True)
    try:
        resolved.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise ValueError(f"{label} escapes family root: {value}") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} is not a file: {value}")
    return resolved


def _line_values(tokens: tuple[Token, ...], start: int, limit: int) -> tuple[list[Token], int]:
    values: list[Token] = []
    index = start
    while index < limit and tokens[index].kind not in {"newline", "brace"}:
        values.append(tokens[index])
        index += 1
    return values, index


def _block_bounds(tokens: tuple[Token, ...], index: int, limit: int) -> tuple[int, int, int] | None:
    while index < limit and tokens[index].kind == "newline":
        index += 1
    if index >= limit or tokens[index].value != "{":
        return None
    depth = 1
    cursor = index + 1
    while cursor < limit and depth:
        if tokens[cursor].value == "{":
            depth += 1
        elif tokens[cursor].value == "}":
            depth -= 1
        cursor += 1
    if depth:
        raise ValueError(f"unterminated QC block at line {tokens[index].line}")
    return index + 1, cursor - 1, cursor


def _source_tokens(values: list[Token]) -> list[Token]:
    return [token for token in values if PurePosixPath(token.value.replace("\\", "/")).suffix.casefold() in _SOURCE_SUFFIXES]


def _block_command_sources(
    tokens: tuple[Token, ...], start: int, end: int, command: str, *, all_sources: bool
) -> list[Token]:
    result: list[Token] = []
    index = start
    wanted = command.casefold()
    while index < end:
        if tokens[index].value.casefold() == wanted:
            values, next_index = _line_values(tokens, index + 1, end)
            sources = _source_tokens(values)
            if sources:
                result.extend(sources if all_sources else sources[:1])
            index = max(next_index, index + 1)
        else:
            index += 1
    return result


def parse_qc_graph(root_qc: Path, family_root: Path) -> QcGraph:
    root = family_root.resolve(strict=True)
    root_qc = root_qc.resolve(strict=True)
    try:
        root_qc.relative_to(root)
    except ValueError as exc:
        raise ValueError("root QC is outside family root") from exc
    visited: set[Path] = set()
    active: set[Path] = set()
    files: list[QcGraphFile] = []
    bodygroups: list[QcBodygroup] = []

    def parse_file(path: Path, depth: int) -> None:
        if depth > _MAX_INCLUDE_DEPTH:
            raise ValueError("QC include depth limit exceeded")
        if path in active:
            raise ValueError(f"QC include cycle: {path}")
        if path in visited:
            return
        if len(visited) >= _MAX_GRAPH_FILES:
            raise ValueError("QC graph file limit exceeded")
        visited.add(path)
        active.add(path)
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"QC graph file is not UTF-8: {path}") from exc
        tokens = _lex(text)
        references: list[QcReference] = []
        includes: list[QcInclude] = []

        def add_ref(token: Token, role: str, directive: str, *, group: str = "") -> QcReference | None:
            if token.value.casefold() == "blank":
                return None
            source = _contained_existing(root, path.parent, token.value, "source reference")
            reference = QcReference(
                path, directive, token.line, token.value, source, role,
                token.start, token.end, group,
            )
            references.append(reference)
            return reference

        index = 0
        while index < len(tokens):
            token = tokens[index]
            if not token.value.startswith("$"):
                index += 1
                continue
            directive = token.value.casefold()
            args, after_args = _line_values(tokens, index + 1, len(tokens))
            block = _block_bounds(tokens, after_args, len(tokens))
            next_index = block[2] if block is not None else max(after_args + 1, index + 1)
            block_start, block_end = (block[0], block[1]) if block is not None else (0, 0)

            if directive in {"$body", "$model"}:
                sources = _source_tokens(args[1:])
                if len(sources) != 1:
                    raise ValueError(f"{directive} requires exactly one visual source at line {token.line}")
                add_ref(sources[0], "visual", directive)
            elif directive == "$bodygroup":
                if not args:
                    raise ValueError(f"$bodygroup has no name at line {token.line}")
                group_id = f"{path.relative_to(root).as_posix()}:{token.line}"
                choices: list[QcReference | None] = []
                cursor = block_start
                while cursor < block_end:
                    if tokens[cursor].kind == "newline":
                        cursor += 1
                        continue
                    command = tokens[cursor].value.casefold()
                    if command == "blank":
                        choices.append(None)
                        cursor += 1
                    elif command == "studio":
                        argument_index = cursor + 1
                        if (
                            argument_index >= block_end
                            or tokens[argument_index].kind in {"newline", "brace"}
                            or tokens[argument_index].value.casefold() in {"studio", "blank"}
                            or not _source_tokens([tokens[argument_index]])
                        ):
                            raise ValueError(f"$bodygroup studio has no source at line {tokens[cursor].line}")
                        choices.append(add_ref(
                            tokens[argument_index], "visual", "$bodygroup/studio", group=group_id
                        ))
                        cursor += 2
                    else:
                        raise ValueError(
                            f"unknown bodygroup command at line {tokens[cursor].line}"
                        )
                if not choices:
                    raise ValueError(f"$bodygroup has no studio/blank source at line {token.line}")
                all_block_sources = _source_tokens(list(tokens[block_start:block_end]))
                selected_sources = [choice for choice in choices if choice is not None]
                if {(item.start, item.end) for item in all_block_sources} != {
                    (item.token_start, item.token_end) for item in selected_sources
                }:
                    raise ValueError(f"unknown bodygroup geometry command at line {token.line}")
                bodygroups.append(QcBodygroup(
                    path, token.line, args[0].value, group_id, tuple(choices)
                ))
            elif directive == "$lod":
                sources = _block_command_sources(tokens, block_start, block_end, "replacemodel", all_sources=True)
                if not sources:
                    raise ValueError(f"$lod has no replacemodel sources at line {token.line}")
                all_block_sources = _source_tokens(list(tokens[block_start:block_end]))
                if {(item.start, item.end) for item in all_block_sources} != {
                    (item.start, item.end) for item in sources
                }:
                    raise ValueError(f"unknown LOD geometry command at line {token.line}")
                for source in sources:
                    add_ref(
                        source,
                        "visual",
                        "$lod/replacemodel",
                        group=f"{path.relative_to(root).as_posix()}:{token.line}",
                    )
            elif directive in {"$collisionmodel", "$collisionjoints"}:
                sources = _source_tokens(args)
                if len(sources) != 1:
                    raise ValueError(f"{directive} requires exactly one collision source at line {token.line}")
                add_ref(sources[0], "collision", directive)
            elif directive in {"$sequence", "$animation"}:
                sources = _source_tokens(args[1:])
                if block is not None:
                    sources.extend(_source_tokens(list(tokens[block_start:block_end])))
                for source in sources:
                    add_ref(source, "animation", directive)
            elif directive == "$include":
                if len(args) != 1:
                    raise ValueError(f"$include requires one path at line {token.line}")
                include_path = _contained_existing(root, path.parent, args[0].value, "include")
                if include_path.suffix.casefold() not in {".qc", ".qci"}:
                    raise ValueError(f"unsupported include extension: {args[0].value}")
                includes.append(QcInclude(path, token.line, args[0].value, include_path, args[0].start, args[0].end))
                parse_file(include_path, depth + 1)
            else:
                unknown_sources = _source_tokens(args)
                if block is not None:
                    unknown_sources.extend(_source_tokens(list(tokens[block_start:block_end])))
                if unknown_sources:
                    raise ValueError(
                        f"unknown geometry/source directive {directive} at line {token.line}"
                    )
            index = next_index

        output_path = path.with_name(f"{path.stem}_OPT{path.suffix.lower()}")
        files.append(QcGraphFile(path, output_path, text, tuple(references), tuple(includes)))
        active.remove(path)

    parse_file(root_qc, 0)
    files.sort(key=lambda item: item.path.relative_to(root).as_posix().casefold())
    references = tuple(reference for graph_file in files for reference in graph_file.references)
    roles_by_source: dict[Path, set[str]] = {}
    for reference in references:
        roles_by_source.setdefault(reference.source_path, set()).add(reference.role)
    conflicts = [path for path, roles in roles_by_source.items() if "collision" in roles and len(roles) > 1]
    if conflicts:
        raise ValueError(f"ambiguous collision/visual source role: {conflicts[0]}")
    return QcGraph(root_qc, root, tuple(files), references, tuple(bodygroups))


def rewritten_qc_graph_texts(
    graph: QcGraph,
    optimized_sources: Mapping[Path, Path],
) -> dict[Path, str]:
    file_outputs = {graph_file.path: graph_file.output_path for graph_file in graph.files}
    result: dict[Path, str] = {}
    for graph_file in graph.files:
        replacements: list[tuple[int, int, str]] = []
        for include in graph_file.includes:
            target = file_outputs[include.include_path]
            logical = os.path.relpath(target, graph_file.output_path.parent).replace("\\", "/")
            replacements.append((include.token_start, include.token_end, logical))
        for reference in graph_file.references:
            if reference.role != "visual":
                continue
            target = optimized_sources.get(reference.source_path)
            if target is None:
                raise ValueError(f"visual source has no explicit optimized output: {reference.source_path}")
            logical = os.path.relpath(target, graph_file.output_path.parent).replace("\\", "/")
            replacements.append((reference.token_start, reference.token_end, logical))
        replacements.sort()
        for previous, current in zip(replacements, replacements[1:]):
            if previous[1] > current[0]:
                raise ValueError("overlapping QC rewrite spans")
        text = graph_file.text
        for start, end, value in reversed(replacements):
            text = text[:start] + value + text[end:]
        result[graph_file.output_path] = text
    return result
