"""Juntar addons (worker `addonmerge`, batch_merge_addons.py): what is searched, what is copied, what is
never deleted."""
import json
import shutil
from pathlib import Path

import batch_merge_addons as merge


def make_addon(root: Path, name: str, files: dict, manifest: dict | None = None, bom: bool = False) -> Path:
    addon = root / name
    for rel, data in files.items():
        path = addon / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    if manifest is not None:
        text = json.dumps(manifest)
        (addon / "addon.json").write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    return addon


def run(root: Path, work: Path, *extra: str) -> int:
    return merge.main([str(root), "--work", str(work), "--recursive", "--allow-content-only",
                       "--package-mode", "local-only", *extra])


def files_of(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def summary(work: Path) -> dict:
    return json.loads((work / "reports" / merge.SUMMARY_FILE_NAME).read_text(encoding="utf-8"))


def lvs_like(root: Path) -> None:
    make_addon(root, "lvs_framework", {"lua/autorun/lvs.lua": "a", "materials/lvs/a.vmt": "m"},
               {"title": "[LVS] Framework", "type": "vehicle", "tags": ["fun"]})
    make_addon(root, "lvs_cars_pack", {"lua/entities/car.lua": "b", "models/lvs/car.mdl": b"\x00\x01"},
               {"title": "[LVS] Cars", "type": "vehicle", "tags": ["fun", "roleplay"]})


def test_bundle_named_like_the_sources_keeps_them(tmp_path):
    """The new addon named "lvs" inside a folder of lvs_* addons: the name prefix alone made the search
    skip every source (0 addons found)."""
    root = tmp_path / "LVS"
    lvs_like(root)
    assert run(root, tmp_path / "w", "--bundle-name", "lvs", "--title", "lvs") == 0
    merged = files_of(root / "lvs")
    assert {"lua/autorun/lvs.lua", "materials/lvs/a.vmt", "lua/entities/car.lua", "models/lvs/car.mdl",
            "addon.json"} == set(merged)
    manifest = json.loads(merged["addon.json"])
    assert manifest[merge.MERGE_MARKER_KEY] == ["lvs_cars_pack", "lvs_framework"]
    assert manifest["title"] == "lvs" and manifest["type"] == "vehicle" and manifest["tags"] == ["fun", "roleplay"]
    assert summary(tmp_path / "w")["scan"]["addon_count"] == 2


def test_earlier_outputs_are_left_out_and_replaced(tmp_path):
    root = tmp_path / "LVS"
    lvs_like(root)
    assert run(root, tmp_path / "w1", "--bundle-name", "lvs", "--title", "lvs") == 0
    # the optimizer writes "<addon>_optimized" next to the addon and copies its addon.json
    shutil.copytree(root / "lvs", root / "lvs_optimized8")
    (root / "lvs_optimized8" / "lua" / "autorun" / "!gmot_x.lua").write_text("stream", encoding="utf-8")
    (root / "lvs" / "old.txt").write_text("left from the first run", encoding="utf-8")
    assert run(root, tmp_path / "w2", "--bundle-name", "lvs", "--title", "lvs") == 0
    report = summary(tmp_path / "w2")
    assert report["scan"]["addon_count"] == 2
    assert [Path(p).name for p in report["details"]["previous_outputs_left_out"]] == ["lvs_optimized8"]
    merged = files_of(root / "lvs")
    assert "old.txt" not in merged and "lua/autorun/!gmot_x.lua" not in merged and len(merged) == 5


def test_outputs_made_before_the_marker(tmp_path):
    """Only the old default title proves an old output. A title equal to the new name does not: an addon
    titled "LVS" among the sources of a new addon named "lvs" is a source."""
    root = tmp_path / "LVS"
    lvs_like(root)
    make_addon(root, "lvs_merged", {"lua/autorun/lvs.lua": "a"},
               {"title": "Merged Addon Bundle (2 addons)", "type": "vehicle"})
    assert run(root, tmp_path / "w") == 0                       # default name: <folder>_merged
    assert summary(tmp_path / "w")["scan"]["addon_count"] == 2
    assert merge.MERGE_MARKER_KEY in json.loads((root / "LVS_merged" / "addon.json").read_text(encoding="utf-8"))

    make_addon(root, "lvs_extras", {"lua/extras.lua": "e"}, {"title": "LVS", "type": "vehicle"})
    assert run(root, tmp_path / "w2", "--bundle-name", "lvs", "--title", "lvs") == 0
    assert summary(tmp_path / "w2")["scan"]["addon_count"] == 3
    assert "lua/extras.lua" in files_of(root / "lvs")


def test_never_empties_a_folder_it_did_not_write(tmp_path, capsys):
    root = tmp_path / "LVS"
    lvs_like(root)
    before = files_of(root / "lvs_framework")
    assert run(root, tmp_path / "w", "--bundle-name", "lvs_framework") == 2
    assert "already exists and was not made by this version of the addon merge" in capsys.readouterr().out
    assert files_of(root / "lvs_framework") == before
    # the search alone only warns
    assert run(root, tmp_path / "w2", "--bundle-name", "lvs_framework", "--scan-only") == 0
    assert "already exists" in summary(tmp_path / "w2")["details"]["output_problem"]
    # an empty folder, or one a stopped run left (only the marked addon.json), is fine
    (root / "empty" / "lua").mkdir(parents=True)
    assert run(root, tmp_path / "w3", "--bundle-name", "empty") == 0
    stopped = make_addon(root, "stopped", {"lua/half.lua": "x"},
                         {"title": "anything", "type": "vehicle", merge.MERGE_MARKER_KEY: ["a"]})
    assert run(root, tmp_path / "w4", "--bundle-name", "stopped") == 0
    assert "lua/half.lua" not in files_of(stopped)


def test_ignore_list_is_read_like_gmad(tmp_path):
    """"readme.txt" is the file at the addon root only, "*.psd" is any folder; a pattern another addon's
    file needs does not go to the merged addon.json."""
    root = tmp_path / "pack"
    make_addon(root, "a", {"readme.txt": "r", "lua/notes/readme.txt": "kept", "materials/x.psd": "p",
                           "lua/a.lua": "a"},
               {"title": "A", "type": "weapon", "ignore": ["readme.txt", "*.psd", "docs", "*.md"]}, bom=True)
    make_addon(root, "b", {"docs/x.lua": "x", "materials/y.psd": "needed", "lua/b.lua": "b"},
               {"title": "B", "type": "weapon", "ignore": []})
    assert run(root, tmp_path / "w") == 0
    merged = files_of(root / "pack_merged")
    assert set(merged) == {"addon.json", "lua/notes/readme.txt", "lua/a.lua", "docs/x.lua", "materials/y.psd",
                           "lua/b.lua"}
    manifest = json.loads(merged["addon.json"])
    assert manifest["ignore"] == ["readme.txt", "docs", "*.md"]   # "*.psd" would take b's file out


def test_same_path_first_addon_by_name_wins_and_identical_is_one(tmp_path):
    root = tmp_path / "pack"
    make_addon(root, "b_addon", {"materials/shared.vtf": b"B", "lua/same.lua": "same"})
    make_addon(root, "a_addon", {"materials/shared.vtf": b"A", "lua/same.lua": "same"})
    assert run(root, tmp_path / "w") == 0
    merged = files_of(root / "pack_merged")
    assert merged["materials/shared.vtf"] == b"A" and merged["lua/same.lua"] == b"same"
    report = summary(tmp_path / "w")
    assert report["scan"]["same_path_different_collisions"] == 1
    assert report["scan"]["same_path_identical_collisions"] == 1
    assert report["merge"]["overridden_conflict_count"] == 1
    assert run(root, tmp_path / "w2", "--conflict-policy", "fail", "--bundle-name", "strict") == 2


def test_merged_addon_and_its_sources_are_not_neighbours(tmp_path):
    """The merge writes next to its sources by default; the optimizer looks at neighbour addons for the
    models that draw an addon's textures. The sources are the same files: looked at, every model is
    measured twice."""
    from model_screen.cli import _sibling_roots
    from texlab.density import sibling_roots
    root = tmp_path / "LVS"
    for name in ("lvs_framework", "lvs_cars_pack", "other_pack"):
        make_addon(root, name, {"models/" + name + "/a.mdl": b"m", "materials/" + name + "/a.vmt": "v",
                                "lua/" + name + ".lua": "l"}, {"title": name, "type": "vehicle"})
    shutil.move(str(root / "other_pack"), str(tmp_path / "other_pack"))
    assert run(root, tmp_path / "w") == 0
    shutil.move(str(tmp_path / "other_pack"), str(root / "other_pack"))      # a real neighbour, not merged
    merged = root / "LVS_merged"
    optimized = root / "LVS_merged_optimized8"
    shutil.copytree(merged, optimized)
    # texture stage: runs on the optimized copy; model stage: on the addon the user picked
    assert [d.name for d in sibling_roots(merged)] == ["other_pack"]
    assert [d.name for d in sibling_roots(optimized)] == ["other_pack"]
    assert [d.name for d in _sibling_roots(str(merged))] == ["other_pack"]
    assert [d.name for d in _sibling_roots(str(merged), "lua")] == ["other_pack"]
    # seen from a source (or its optimized output), the merged addon is a copy too
    assert [d.name for d in sibling_roots(root / "lvs_framework")] == ["lvs_cars_pack", "other_pack"]
    shutil.copytree(root / "lvs_framework", root / "lvs_framework_optimized8")
    assert [d.name for d in sibling_roots(root / "lvs_framework_optimized8")] == ["lvs_cars_pack", "other_pack"]


def test_two_copies_of_the_same_addon_are_reported(tmp_path, capsys):
    root = tmp_path / "pack"
    files = {f"materials/models/car/part{i}.vtf": bytes([i]) for i in range(30)}
    make_addon(root, "car", files, {"title": "Car", "type": "vehicle"})
    make_addon(root, "car_optimized8", {k: v + b"!" for k, v in files.items()} | {"lua/autorun/!gmot_c.lua": "s"},
               {"title": "Car", "type": "vehicle"})
    make_addon(root, "other", {"lua/other.lua": "o", "materials/models/car/part0.vtf": b"z"},
               {"title": "Other", "type": "vehicle"})
    assert run(root, tmp_path / "w", "--scan-only") == 0
    pairs = summary(tmp_path / "w")["details"]["likely_same_addon_pairs"]
    assert [p["addons"] for p in pairs] == [["car", "car_optimized8"]]
    assert pairs[0]["shared_path_count"] == 30
    assert "look like two copies of the same addon" in capsys.readouterr().out
