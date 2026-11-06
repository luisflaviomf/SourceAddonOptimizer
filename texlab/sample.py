"""Build a small, self-contained LVS test addon from a list of vehicle entities.

Usage:
    python -m texlab.sample --out <new-folder> --entity lvs_wheeldrive_supra ... --root <addon> ...

Everything a chosen vehicle needs from the given roots is copied with its
relative path: the entity Lua, every model it names, those models' materials
(all skin families) and every texture those materials reference.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from .materials import MaterialResolver
from .source_model import load_model

MODEL_REF = re.compile(r'["\'](models/[^"\']+?\.mdl)["\']', re.IGNORECASE)
MATERIAL_REF = re.compile(r'Material\(\s*["\']([^"\']+)["\']', re.IGNORECASE)
MODEL_SIDECARS = (".mdl", ".vvd", ".dx90.vtx", ".dx80.vtx", ".sw.vtx", ".phy", ".ani")


def _index(roots: list[Path]) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for root in roots:
        for p in root.rglob("*"):
            if p.is_file():
                index.setdefault(p.relative_to(root).as_posix().lower(), p)
    return index


def build_sample(out: Path, roots: list[Path], entities: list[str], dep_roots: list[Path] | None = None) -> dict:
    out = Path(out)
    if out.exists():
        raise SystemExit(f"refusing to overwrite existing sample: {out}")
    dep_roots = [Path(r) for r in (dep_roots or [])]
    index = _index(roots)
    dep_index = _index(dep_roots)
    resolver = MaterialResolver(roots + dep_roots)
    wanted: dict[str, Path] = {}
    report = {"entities": {}, "missing_materials": [], "missing_textures": [], "missing_models": [], "dependency_files": []}

    def want(rel: str) -> bool:
        p = index.get(rel.lower())
        if p is not None:
            wanted[rel.lower()] = p
            return True
        if rel.lower() in dep_index:
            report["dependency_files"].append(rel.lower())
            return True
        return False

    for entity in entities:
        files = [k for k in index if k.startswith(f"lua/entities/{entity.lower()}/") or k == f"lua/entities/{entity.lower()}.lua"]
        if not files:
            raise SystemExit(f"entity not found: {entity}")
        models: set[str] = set()
        for rel in files:
            want(rel)
            text = index[rel].read_text(encoding="utf-8", errors="replace")
            models.update(m.lower().replace("\\", "/") for m in MODEL_REF.findall(text))
            for mat in MATERIAL_REF.findall(text):
                mat = mat.lower().replace("\\", "/").removeprefix("materials/").removesuffix(".vmt").removesuffix(".png")
                want(f"materials/{mat}.vmt") or want(f"materials/{mat}.png")
        report["entities"][entity] = sorted(models)
        for model_rel in sorted(models):
            stem = model_rel[:-4]
            if not want(model_rel):
                report["missing_models"].append(model_rel)
                continue
            for ext in MODEL_SIDECARS[1:]:
                want(stem + ext)
            model = load_model(index.get(model_rel) or dep_index[model_rel])
            for slot in range(len(model.skin_families[0]) if model.skin_families else len(model.textures)):
                for tex_name in model.texture_names_for_slot(slot):
                    material = resolver.model_material(model.cdmaterials, tex_name)
                    if material is None:
                        report["missing_materials"].append(f"{model_rel}: {tex_name}")
                        continue
                    want(f"materials/{material.path}.vmt")
                    for ref in material.textures.values():
                        if not want(f"materials/{ref}.vtf"):
                            report["missing_textures"].append(f"{material.path}: {ref}")

    # Shared Lua the entities need at runtime (autorun of the same addons, small).
    for rel, p in index.items():
        if rel.startswith("lua/autorun/"):
            wanted.setdefault(rel, p)

    for rel, src in sorted(wanted.items()):
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    (out / "addon.json").write_text(json.dumps({"title": "texlab LVS sample", "type": "tool", "tags": []}, indent=2))
    for key in ("missing_materials", "missing_textures", "missing_models", "dependency_files"):
        report[key] = sorted(set(report[key]))
    report["files"] = len(wanted)
    report["bytes"] = sum(p.stat().st_size for p in wanted.values())
    (out.parent / f"{out.name}.sample.json").write_text(json.dumps(report, indent=2))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", action="append", required=True)
    ap.add_argument("--dep-root", action="append", default=[])
    ap.add_argument("--entity", action="append", required=True)
    args = ap.parse_args(argv)
    report = build_sample(Path(args.out), [Path(r) for r in args.root], args.entity, [Path(r) for r in args.dep_root])
    print(json.dumps({k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in report.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
