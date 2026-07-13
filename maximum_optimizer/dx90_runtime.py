from __future__ import annotations

import hashlib
import csv
import io
import json
import math
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from PIL import Image

from .benchmarking import canonical_json_bytes, compiled_kind, safe_existing_root


_RUN_ID = re.compile(r"[a-z0-9][a-z0-9-]{7,47}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RUNTIME_PREFIX = "models/maximum_dx90_runtime"


class Dx90RuntimeError(ValueError):
    pass


def console_has_runtime_lua_error(text: str) -> bool:
    if not isinstance(text, str):
        raise Dx90RuntimeError("runtime console must be text")
    lowered = text.lower()
    return "maximum_dx90_runtime_" in lowered and any(
        marker in lowered for marker in ("[error]", "lua error", "attempt to call")
    )


def parse_gmod_tasklist_pids(output: str) -> tuple[int, ...]:
    if not isinstance(output, str):
        raise Dx90RuntimeError("tasklist output must be text")
    pids: list[int] = []
    for row in csv.reader(io.StringIO(output)):
        if len(row) >= 2 and row[0].lower() == "gmod.exe" and row[1].isdigit():
            pids.append(int(row[1]))
    return tuple(sorted(set(pids)))


def steam_build_fingerprint(appmanifest_text: str) -> dict[str, Any]:
    if not isinstance(appmanifest_text, str):
        raise Dx90RuntimeError("Steam app manifest must be text")
    build_match = re.search(r'"buildid"\s+"([0-9]+)"', appmanifest_text)
    depots = sorted(
        (
            match.group(1),
            match.group(2),
            match.group(3),
        )
        for match in re.finditer(
            r'"([0-9]+)"\s*\{\s*"manifest"\s+"([0-9]+)"\s*"size"\s+"([0-9]+)"',
            appmanifest_text,
            flags=re.DOTALL,
        )
    )
    beta_keys = sorted(set(re.findall(r'"BetaKey"\s+"([^"\r\n]+)"', appmanifest_text)))
    if build_match is None or not depots or not beta_keys:
        raise Dx90RuntimeError("Steam app manifest lacks build, depot, or branch identity")
    body = {
        "build_id": build_match.group(1),
        "installed_depots": [
            {"depot_id": depot, "manifest_id": manifest, "size_bytes": int(size)}
            for depot, manifest, size in depots
        ],
        "beta_keys": beta_keys,
    }
    return {**body, "sha256": _manifest_digest(body)}


@dataclass(frozen=True)
class RuntimeArtifact:
    family_id: str
    source: Path
    staged_relative: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class RuntimePlan:
    run_id: str
    corpus_id: str
    manifest_sha256: str
    family_ids: tuple[str, ...]
    artifacts: tuple[RuntimeArtifact, ...]
    model_paths: tuple[str, ...]


@dataclass(frozen=True)
class StagedRuntimeProbe:
    game_root: Path
    model_root: Path
    lua_path: Path
    data_root: Path
    lua_sha256: str
    created_models_directory: bool = False

    def cleanup(self) -> None:
        expected_model = self.game_root / "models/maximum_dx90_runtime" / self.model_root.name
        expected_lua = self.game_root / "lua/autorun" / self.lua_path.name
        expected_data = self.game_root / "data/maximum_dx90_runtime" / self.data_root.name
        if self.model_root != expected_model or self.lua_path != expected_lua or self.data_root != expected_data:
            raise Dx90RuntimeError("refusing cleanup outside exact DX90 runtime namespace")
        shutil.rmtree(self.model_root, ignore_errors=True)
        shutil.rmtree(self.data_root, ignore_errors=True)
        if self.lua_path.exists() and self.lua_path.is_file():
            self.lua_path.unlink()
        for parent in (
            self.model_root.parent,
            self.data_root.parent,
        ):
            try:
                parent.rmdir()
            except OSError:
                pass
        if self.created_models_directory:
            try:
                (self.game_root / "models").rmdir()
            except OSError:
                pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest_digest(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _strict_manifest(experiment: Mapping[str, Any]) -> Mapping[str, Any]:
    manifest = experiment.get("candidate_manifest")
    if not isinstance(manifest, dict):
        raise Dx90RuntimeError("candidate manifest is missing")
    expected = {"schema_version", "corpus_id", "family_ids", "families", "sha256"}
    if set(manifest) != expected or manifest.get("schema_version") != 1:
        raise Dx90RuntimeError("candidate manifest schema is invalid")
    body = {key: manifest[key] for key in ("schema_version", "corpus_id", "family_ids", "families")}
    if not isinstance(manifest.get("sha256"), str) or manifest["sha256"] != _manifest_digest(body):
        raise Dx90RuntimeError("candidate manifest digest mismatch")
    if (
        not isinstance(manifest.get("family_ids"), list)
        or not manifest["family_ids"]
        or len(manifest["family_ids"]) != len(set(manifest["family_ids"]))
        or not isinstance(manifest.get("families"), list)
        or len(manifest["families"]) != len(manifest["family_ids"])
    ):
        raise Dx90RuntimeError("candidate manifest family binding is invalid")
    evidence = experiment.get("runtime_evidence")
    if not isinstance(evidence, dict) or evidence.get("status") != "pending" or (
        evidence.get("candidate_manifest_sha256") != manifest["sha256"]
    ):
        raise Dx90RuntimeError("runtime plan requires exact pending evidence binding")
    return manifest


def build_runtime_plan(
    experiment: Mapping[str, Any], candidate_root: Path, run_id: str
) -> RuntimePlan:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise Dx90RuntimeError("run_id must contain 8-48 lowercase alphanumeric/hyphen characters")
    manifest = _strict_manifest(experiment)
    try:
        root = safe_existing_root(Path(candidate_root).resolve(strict=True), "DX90 runtime candidates")
    except Exception as exc:
        raise Dx90RuntimeError(str(exc)) from exc

    artifacts: list[RuntimeArtifact] = []
    model_paths: list[str] = []
    for expected_family_id, family in zip(
        manifest["family_ids"], manifest["families"], strict=True
    ):
        if not isinstance(family, dict) or family.get("family_id") != expected_family_id:
            raise Dx90RuntimeError("candidate family identity/order mismatch")
        compiled_stem = family.get("compiled_stem")
        kept = family.get("kept")
        omitted = family.get("omitted_dx80")
        if (
            not isinstance(compiled_stem, str)
            or not compiled_stem
            or not isinstance(kept, list)
            or not kept
            or not isinstance(omitted, dict)
            or not str(omitted.get("path", "")).lower().endswith(".dx80.vtx")
        ):
            raise Dx90RuntimeError("candidate family sidecar manifest is invalid")
        family_root = root / expected_family_id
        try:
            safe_family = safe_existing_root(
                family_root.resolve(strict=True), f"DX90 runtime family {expected_family_id}"
            )
        except Exception as exc:
            raise Dx90RuntimeError(str(exc)) from exc
        declared_paths = [item.get("path") for item in kept if isinstance(item, dict)]
        if len(declared_paths) != len(kept) or any(not isinstance(item, str) for item in declared_paths):
            raise Dx90RuntimeError("candidate kept sidecar declarations are invalid")
        actual_paths = {
            path.relative_to(safe_family).as_posix()
            for path in safe_family.rglob("*")
            if path.is_file()
        }
        if actual_paths != set(declared_paths):
            suffix = " (DX80 present)" if any(
                path.lower().endswith(".dx80.vtx") for path in actual_paths
            ) else ""
            raise Dx90RuntimeError(
                f"candidate sidecar set is not exact for {expected_family_id}{suffix}"
            )
        kinds: set[str] = set()
        compiled_name = Path(compiled_stem).name
        for item in kept:
            relative = item["path"]
            size = item.get("size_bytes")
            digest = item.get("sha256")
            kind = compiled_kind(relative)
            if (
                not kind
                or not isinstance(size, int)
                or size < 0
                or not isinstance(digest, str)
                or not _SHA256.fullmatch(digest)
                or Path(relative).name.lower() != f"{compiled_name}{kind}".lower()
                or kind == ".dx80.vtx"
            ):
                raise Dx90RuntimeError("candidate kept sidecar declaration is invalid or contains DX80")
            source = safe_family / Path(relative)
            if source.is_symlink() or source.stat().st_size != size or _sha256(source) != digest:
                raise Dx90RuntimeError(f"candidate size/hash mismatch: {relative}")
            staged = f"{_RUNTIME_PREFIX}/{run_id}/{expected_family_id}/{compiled_name}{kind}"
            artifacts.append(RuntimeArtifact(expected_family_id, source, staged, size, digest))
            kinds.add(kind)
        if not {".mdl", ".vvd", ".dx90.vtx"}.issubset(kinds):
            raise Dx90RuntimeError(f"candidate {expected_family_id} lacks required DX90 sidecars")
        model_paths.append(
            f"{_RUNTIME_PREFIX}/{run_id}/{expected_family_id}/{compiled_name}.mdl"
        )
    return RuntimePlan(
        run_id=run_id,
        corpus_id=manifest["corpus_id"],
        manifest_sha256=manifest["sha256"],
        family_ids=tuple(manifest["family_ids"]),
        artifacts=tuple(artifacts),
        model_paths=tuple(model_paths),
    )


def _lua_spec(plan: RuntimePlan) -> dict[str, Any]:
    by_family: dict[str, list[dict[str, Any]]] = {family: [] for family in plan.family_ids}
    for artifact in plan.artifacts:
        by_family[artifact.family_id].append(
            {
                "path": artifact.staged_relative,
                "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256,
            }
        )
    families = []
    for family_id, model_path in zip(plan.family_ids, plan.model_paths, strict=True):
        families.append(
            {
                "family_id": family_id,
                "model_path": model_path,
                "dx80_path": model_path[:-4] + ".dx80.vtx",
                "artifacts": by_family[family_id],
            }
        )
    return {
        "run_id": plan.run_id,
        "corpus_id": plan.corpus_id,
        "candidate_manifest_sha256": plan.manifest_sha256,
        "required_capabilities": [
            "animation",
            "bodygroups_skins",
            "damage",
            "dynamic_model_load",
            "physics",
            "rendering",
        ],
        "families": families,
    }


def render_probe_lua(plan: RuntimePlan) -> str:
    """Render the shared, self-starting Lua probe used only by the disposable runner."""
    encoded_spec = json.dumps(
        json.dumps(_lua_spec(plan), sort_keys=True, separators=(",", ":")),
        ensure_ascii=True,
    )
    # Keep this script intentionally self-contained. The Python verifier treats every
    # value below as an observation, not as proof, and independently hashes artifacts/images.
    return f'''-- generated DX90-only disposable runtime probe; run_id={plan.run_id}
if SERVER then AddCSLuaFile() end
local spec = util.JSONToTable({encoded_spec})
local dataRoot = "maximum_dx90_runtime/" .. spec.run_id
local netName = "maxdx90_done_{plan.run_id[-12:]}"
local poseNetName = "maxdx90_pose_{plan.run_id[-12:]}"
local function save(name, value)
    file.CreateDir("maximum_dx90_runtime")
    file.CreateDir(dataRoot)
    file.Write(dataRoot .. "/" .. name .. ".json", util.TableToJSON(value, true))
end
local function validateEnvironment(realm)
    local apiAvailable = isfunction(GetAddonStatus)
    local noaddons, noworkshop = nil, nil
    if apiAvailable then noaddons, noworkshop = GetAddonStatus() end
    local result = {{realm=realm, addon_status_api_available=apiAvailable, noaddons=noaddons, noworkshop=noworkshop, artifacts={{}}, errors={{}}}}
    if apiAvailable and not noaddons then table.insert(result.errors, "noaddons=false") end
    if apiAvailable and not noworkshop then table.insert(result.errors, "noworkshop=false") end
    for _, family in ipairs(spec.families) do
        if file.Exists(family.dx80_path, "GAME") then
            table.insert(result.errors, "DX80 exists: " .. family.dx80_path)
        end
        for _, artifact in ipairs(family.artifacts) do
            local bytes = file.Read(artifact.path, "GAME")
            local actual = bytes and util.SHA256(bytes) or nil
            table.insert(result.artifacts, {{path=artifact.path, size_bytes=bytes and #bytes or -1, sha256=actual}})
            if not bytes or #bytes ~= artifact.size_bytes or actual ~= artifact.sha256 then
                table.insert(result.errors, "artifact mismatch: " .. artifact.path)
            end
        end
    end
    return result
end

if SERVER then
    util.AddNetworkString(netName)
    util.AddNetworkString(poseNetName)
    local damageSeen = {{}}
    local animatedEntities = {{}}
    net.Receive(poseNetName, function(_, ply)
        local familyId = net.ReadString()
        local useMaximum = net.ReadBool()
        local animated = animatedEntities[familyId]
        if not game.SinglePlayer() or not IsValid(ply) or not IsValid(animated) then return end
        local poseId = animated:LookupPoseParameter("hood")
        local sequenceId = animated:LookupSequence("hood")
        if not poseId or poseId < 0 or not sequenceId or sequenceId < 0 then return end
        local minimum, maximum = animated:GetPoseParameterRange(poseId)
        if not minimum or not maximum or maximum <= minimum then return end
        animated:ResetSequence(sequenceId)
        animated:ResetSequenceInfo()
        animated:SetPlaybackRate(0)
        animated:SetPoseParameter("hood", useMaximum and maximum or minimum)
        if animated.InvalidateBoneCache then animated:InvalidateBoneCache() end
        if animated.SetupBones then animated:SetupBones() end
        timer.Simple(0.35, function()
            if not IsValid(ply) or not IsValid(animated) then return end
            net.Start(poseNetName)
            net.WriteString(familyId)
            net.WriteBool(useMaximum)
            net.Send(ply)
        end)
    end)
    hook.Add("EntityTakeDamage", "maximum_dx90_damage_" .. spec.run_id, function(ent, dmg)
        if ent.MaximumDx90Family then
            damageSeen[ent.MaximumDx90Family] = {{damage=dmg:GetDamage(), valid=IsValid(ent)}}
        end
    end)
    hook.Add("InitPostEntity", "maximum_dx90_server_" .. spec.run_id, function()
        timer.Simple(2, function()
            local report = validateEnvironment("server")
            report.candidate_manifest_sha256 = spec.candidate_manifest_sha256
            report.families = {{}}
            local entities = {{}}
            local runtimePlayer = player.GetAll()[1]
            local runtimeOrigin = IsValid(runtimePlayer) and runtimePlayer:GetPos() or Vector(0, 0, 0)
            for index, family in ipairs(spec.families) do
                local row = {{family_id=family.family_id, model_path=family.model_path}}
                row.valid_model = util.IsValidModel(family.model_path)
                local animated = ents.Create("prop_dynamic_override")
                if IsValid(animated) then
                    animated:SetModel(family.model_path)
                    animated:SetPos(runtimeOrigin + Vector(index * 220, 300, 100))
                    animated:SetAngles(Angle(0, 0, 0))
                    animated:SetNW2String("MaximumDx90RuntimeFamily", family.family_id)
                    animated:Spawn()
                    animated:Activate()
                end
                row.animation_entity_valid = IsValid(animated)
                row.animation_loaded_model = IsValid(animated) and animated:GetModel() or nil
                animatedEntities[family.family_id] = animated
                local ent = ents.Create("prop_physics")
                if IsValid(ent) then
                    ent:SetModel(family.model_path)
                    ent:SetPos(Vector(index * 220, 0, 180))
                    ent:Spawn()
                    ent:Activate()
                    ent.MaximumDx90Family = family.family_id
                end
                row.entity_valid = IsValid(ent)
                row.loaded_model = IsValid(ent) and ent:GetModel() or nil
                local phys = IsValid(ent) and ent:GetPhysicsObject() or nil
                row.physics_object_valid = IsValid(phys)
                row.physics_mass = IsValid(phys) and phys:GetMass() or nil
                if IsValid(phys) then
                    phys:EnableGravity(false)
                    phys:EnableMotion(true)
                    phys:Wake()
                    row.physics_start = ent:GetPos()
                    phys:SetVelocity(Vector(120, 0, 0))
                end
                entities[family.family_id] = ent
                table.insert(report.families, row)
            end
            timer.Simple(1, function()
                for _, row in ipairs(report.families) do
                    local ent = entities[row.family_id]
                    row.physics_end = IsValid(ent) and ent:GetPos() or nil
                    row.physics_moved = row.physics_start and row.physics_end and row.physics_start:DistToSqr(row.physics_end) > 1 or false
                    if IsValid(ent) then ent:TakeDamage(17, game.GetWorld(), game.GetWorld()) end
                end
                timer.Simple(0.25, function()
                    for _, row in ipairs(report.families) do
                        local seen = damageSeen[row.family_id]
                        row.damage_observed = seen ~= nil and seen.damage == 17
                    end
                    report.capabilities = {{
                        dynamic_model_load=true,
                        physics=true,
                        damage=true,
                    }}
                    for _, row in ipairs(report.families) do
                        report.capabilities.dynamic_model_load = report.capabilities.dynamic_model_load and row.valid_model and row.entity_valid and row.loaded_model == row.model_path
                        report.capabilities.physics = report.capabilities.physics and row.physics_object_valid and row.physics_mass and row.physics_mass > 0 and row.physics_moved
                        report.capabilities.damage = report.capabilities.damage and row.damage_observed
                    end
                    save("server", report)
                end)
            end)
        end)
    end)
    net.Receive(netName, function(_, ply)
        if game.SinglePlayer() and IsValid(ply) then
            timer.Simple(1, function() game.ConsoleCommand("quit\\n") end)
        end
    end)
else
    local active, camera, drawing = nil, nil, false
    local pendingPoseCallbacks = {{}}
    local function poseCallbackKey(familyId, useMaximum)
        return familyId .. ":" .. tostring(useMaximum)
    end
    local function requestServerPose(familyId, useMaximum, callback)
        local key = poseCallbackKey(familyId, useMaximum)
        pendingPoseCallbacks[key] = callback
        net.Start(poseNetName)
        net.WriteString(familyId)
        net.WriteBool(useMaximum)
        net.SendToServer()
    end
    net.Receive(poseNetName, function()
        local familyId = net.ReadString()
        local useMaximum = net.ReadBool()
        local key = poseCallbackKey(familyId, useMaximum)
        local callback = pendingPoseCallbacks[key]
        pendingPoseCallbacks[key] = nil
        if callback then timer.Simple(0.25, callback) end
    end)
    local function findRuntimeDynamic(familyId)
        for _, entity in ipairs(ents.GetAll()) do
            if entity:GetNW2String("MaximumDx90RuntimeFamily", "") == familyId then
                return entity
            end
        end
        return nil
    end
    local function snapshotBones(entity)
        local result = {{}}
        if not IsValid(entity) then return result end
        entity:InvalidateBoneCache()
        entity:SetupBones()
        for boneId=0, entity:GetBoneCount()-1 do
            local matrix = entity:GetBoneMatrix(boneId)
            if matrix then
                local position = matrix:GetTranslation()
                local angles = matrix:GetAngles()
                local localPosition = entity:WorldToLocal(position)
                local localAngles = entity:WorldToLocalAngles(angles)
                table.insert(result, {{
                    id=boneId,
                    name=entity:GetBoneName(boneId),
                    position={{localPosition.x, localPosition.y, localPosition.z}},
                    angles={{localAngles.p, localAngles.y, localAngles.r}},
                }})
            end
        end
        return result
    end
    local captureWidth, captureHeight = 800, 600
    local captureTarget = GetRenderTarget(
        "maximum_dx90_" .. spec.run_id,
        captureWidth,
        captureHeight,
        false
    )
    local captureRequest = nil
    hook.Add("PostRender", "maximum_dx90_capture_" .. spec.run_id, function()
        if not captureRequest then return end
        local request = captureRequest
        captureRequest = nil
        render.PushRenderTarget(captureTarget)
        render.Clear(0, 0, 0, 255, true, true)
        if camera then
            cam.Start3D(
                camera.origin,
                camera.angles,
                50,
                0,
                0,
                captureWidth,
                captureHeight
            )
            render.SuppressEngineLighting(true)
            render.SetColorModulation(1, 1, 1)
            render.SetBlend(1)
            if drawing and IsValid(active) then active:DrawModel() end
            render.SuppressEngineLighting(false)
            cam.End3D()
        end
        local png = render.Capture({{
            format="png", x=0, y=0,
            w=captureWidth, h=captureHeight, alpha=false,
        }})
        render.PopRenderTarget()
        if png then file.Write(dataRoot .. "/" .. request .. ".png", png) end
    end)
    hook.Add("InitPostEntity", "maximum_dx90_client_" .. spec.run_id, function()
        timer.Simple(4, function()
            local report = validateEnvironment("client")
            report.candidate_manifest_sha256 = spec.candidate_manifest_sha256
            report.families = {{}}
            local index = 1
            local function nextFamily()
                active = nil
                if index > #spec.families then
                    local caps = {{dynamic_model_load=true, rendering=true, bodygroups_skins=true, animation=true}}
                    for _, row in ipairs(report.families) do
                        caps.dynamic_model_load = caps.dynamic_model_load and row.entity_valid and row.loaded_model == row.model_path
                        caps.rendering = caps.rendering and row.baseline_png ~= nil and row.model_png ~= nil
                        caps.bodygroups_skins = caps.bodygroups_skins and row.bodygroups_skins
                        caps.animation = caps.animation and row.animation
                    end
                    report.capabilities = caps
                    save("client", report)
                    net.Start(netName)
                    net.SendToServer()
                    return
                end
                local family = spec.families[index]
                local row = {{family_id=family.family_id, model_path=family.model_path}}
                active = findRuntimeDynamic(family.family_id)
                row.entity_valid = IsValid(active)
                row.loaded_model = IsValid(active) and active:GetModel() or nil
                row.bodygroups_skins = row.entity_valid
                row.bodygroups = {{}}
                if IsValid(active) then
                    active:SetNoDraw(false)
                    active:SetMaterial("models/debug/debugwhite")
                    for groupId=0, active:GetNumBodyGroups()-1 do
                        local count = active:GetBodygroupCount(groupId)
                        local group = {{id=groupId, count=count, values={{}}}}
                        for value=0, count-1 do
                            active:SetBodygroup(groupId, value)
                            local actual = active:GetBodygroup(groupId)
                            table.insert(group.values, actual)
                            if actual ~= value then row.bodygroups_skins = false end
                        end
                        active:SetBodygroup(groupId, 0)
                        table.insert(row.bodygroups, group)
                    end
                    row.skin_count = active:SkinCount()
                    row.skin_values = {{}}
                    for value=0, math.max(row.skin_count - 1, 0) do
                        active:SetSkin(value)
                        local actual = active:GetSkin()
                        table.insert(row.skin_values, actual)
                        if actual ~= value then row.bodygroups_skins = false end
                    end
                    active:SetSkin(0)
                    row.animation = false
                    row.sequence_count = active:GetSequenceCount()
                    row.pose_parameter_count = active:GetNumPoseParameters()
                    row.pose_parameters = {{}}
                    for poseId=0, row.pose_parameter_count-1 do
                        local minimum, maximum = active:GetPoseParameterRange(poseId)
                        table.insert(row.pose_parameters, {{
                            id=poseId,
                            name=active:GetPoseParameterName(poseId),
                            minimum=minimum,
                            maximum=maximum,
                        }})
                    end
                    row.sequences = {{}}
                    for sequenceId=0, row.sequence_count-1 do
                        local info = active:GetSequenceInfo(sequenceId)
                        table.insert(row.sequences, {{
                            id=sequenceId,
                            name=active:GetSequenceName(sequenceId),
                            duration=active:SequenceDuration(sequenceId),
                            lastframe=info and info.lastframe or nil,
                        }})
                    end
                    local animationSetter = nil
                    local preferredPoseNames = {{
                        "hood", "vehicle_hood", "left_door", "right_door",
                        "rear_left_door", "rear_right_door", "trunk",
                        "vehicle_steer", "steer",
                    }}
                    local poseIds, seenPoseIds = {{}}, {{}}
                    for _, preferredName in ipairs(preferredPoseNames) do
                        local poseId = active:LookupPoseParameter(preferredName)
                        if poseId and poseId >= 0 and not seenPoseIds[poseId] then
                            table.insert(poseIds, poseId)
                            seenPoseIds[poseId] = true
                        end
                    end
                    for poseId=0, row.pose_parameter_count-1 do
                        if not seenPoseIds[poseId] then table.insert(poseIds, poseId) end
                    end
                    for _, poseId in ipairs(poseIds) do
                        local minimum, maximum = active:GetPoseParameterRange(poseId)
                        local name = active:GetPoseParameterName(poseId)
                        local poseSequenceId = name and active:LookupSequence(name) or -1
                        if name and poseSequenceId >= 0 and minimum and maximum and maximum > minimum then
                            local baseSequenceId = poseSequenceId
                            active:ResetSequence(baseSequenceId)
                            active:ResetSequenceInfo()
                            active:SetPlaybackRate(0)
                            active:SetPoseParameter(name, minimum)
                            active:InvalidateBoneCache()
                            active:SetupBones()
                            local first = active:GetPoseParameter(name)
                            active:SetPoseParameter(name, maximum)
                            active:InvalidateBoneCache()
                            active:SetupBones()
                            local second = active:GetPoseParameter(name)
                            row.animation_mode = "pose_parameter"
                            row.animation_blend_sequence = poseSequenceId
                            row.animation_blend_sequence_name = active:GetSequenceName(poseSequenceId)
                            row.animation_base_sequence = baseSequenceId
                            row.animation_base_sequence_name = active:GetSequenceName(baseSequenceId)
                            row.animation_pose_name = name
                            row.animation_pose_minimum = minimum
                            row.animation_pose_maximum = maximum
                            row.animation_value_a = first
                            row.animation_value_b = second
                            row.animation = isnumber(first) and isnumber(second) and math.abs(second - first) > 0.25
                            animationSetter = function(useMaximum)
                                active:ResetSequence(baseSequenceId)
                                active:ResetSequenceInfo()
                                active:SetPlaybackRate(0)
                                active:SetPoseParameter(name, useMaximum and maximum or minimum)
                                active:InvalidateBoneCache()
                                active:SetupBones()
                            end
                            if row.animation then break end
                        end
                    end
                    if not row.animation then
                        for seq=0, row.sequence_count-1 do
                            local info = active:GetSequenceInfo(seq)
                            local duration = active:SequenceDuration(seq)
                            if info and info.lastframe and info.lastframe > 0 and duration and duration > 0 then
                                active:ResetSequence(seq)
                                active:ResetSequenceInfo()
                                active:SetPlaybackRate(0)
                                active:SetCycle(0)
                                local first = active:GetCycle()
                                active:SetCycle(0.5)
                                local second = active:GetCycle()
                                row.animation_mode = "sequence_cycle"
                                row.animation_sequence = seq
                                row.animation_sequence_name = active:GetSequenceName(seq)
                                row.animation_duration = duration
                                row.animation_lastframe = info.lastframe
                                row.animation_value_a = first
                                row.animation_value_b = second
                                row.animation = math.abs(second - first) > 0.25
                                animationSetter = function(useMaximum)
                                    active:ResetSequence(seq)
                                    active:ResetSequenceInfo()
                                    active:SetPlaybackRate(0)
                                    active:SetCycle(useMaximum and 0.5 or 0)
                                    active:InvalidateBoneCache()
                                end
                                if row.animation then break end
                            end
                        end
                    end
                    if animationSetter then animationSetter(false) end
                    local mins, maxs = active:GetRenderBounds()
                    local center = active:LocalToWorld((mins + maxs) * 0.5)
                    local radius = math.max((maxs - mins):Length(), 64)
                    local origin = center + Vector(radius * 0.85, radius * 0.85, radius * 0.35)
                    camera = {{origin=origin, angles=(center-origin):Angle()}}
                end
                table.insert(report.families, row)
                drawing = false
                timer.Simple(0.5, function()
                    row.baseline_png = family.family_id .. "-baseline"
                    captureRequest = row.baseline_png
                    timer.Simple(0.5, function()
                        drawing = true
                        row.model_png = family.family_id .. "-model"
                        captureRequest = row.model_png
                        timer.Simple(0.5, function()
                            requestServerPose(family.family_id, false, function()
                                row.animation_bones_a = snapshotBones(active)
                                row.animation_a_png = family.family_id .. "-animation-a"
                                captureRequest = row.animation_a_png
                                timer.Simple(0.5, function()
                                    requestServerPose(family.family_id, true, function()
                                        row.animation_bones_b = snapshotBones(active)
                                        row.animation_b_png = family.family_id .. "-animation-b"
                                        captureRequest = row.animation_b_png
                                        timer.Simple(0.75, function()
                                            drawing = false
                                            index = index + 1
                                            nextFamily()
                                        end)
                                    end)
                                end)
                            end)
                        end)
                    end)
                end)
            end
            nextFamily()
        end)
    end)
end
'''


def stage_runtime_probe(plan: RuntimePlan, install_root: Path) -> StagedRuntimeProbe:
    try:
        install = safe_existing_root(Path(install_root).resolve(strict=True), "Garry's Mod install")
        game = safe_existing_root((install / "garrysmod").resolve(strict=True), "Garry's Mod game root")
    except Exception as exc:
        raise Dx90RuntimeError(str(exc)) from exc
    if not (install / "gmod.exe").is_file():
        raise Dx90RuntimeError("Garry's Mod install lacks gmod.exe")
    for required in (game / "lua/autorun", game / "data"):
        if not required.is_dir() or required.is_symlink():
            raise Dx90RuntimeError(f"unsafe or missing Garry's Mod directory: {required}")
    models_directory = game / "models"
    created_models_directory = False
    if os.path.lexists(models_directory):
        if not models_directory.is_dir() or models_directory.is_symlink():
            raise Dx90RuntimeError(f"unsafe Garry's Mod models directory: {models_directory}")
    else:
        models_directory.mkdir()
        created_models_directory = True
    model_root = game / _RUNTIME_PREFIX / plan.run_id
    lua_path = game / "lua/autorun" / f"maximum_dx90_runtime_{plan.run_id}.lua"
    data_root = game / "data/maximum_dx90_runtime" / plan.run_id
    if any(os.path.lexists(path) for path in (model_root, lua_path, data_root)):
        raise Dx90RuntimeError("reserved DX90 runtime path already exists")
    staged = StagedRuntimeProbe(
        game, model_root, lua_path, data_root, "", created_models_directory
    )
    try:
        model_root.mkdir(parents=True)
        for artifact in plan.artifacts:
            relative = Path(artifact.staged_relative)
            destination = game / relative
            if model_root not in destination.parents or os.path.lexists(destination):
                raise Dx90RuntimeError("runtime artifact escaped namespace or already exists")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(artifact.source, destination)
            if destination.stat().st_size != artifact.size_bytes or _sha256(destination) != artifact.sha256:
                raise Dx90RuntimeError(f"staged runtime artifact mismatch: {artifact.staged_relative}")
        actual = {
            path.relative_to(game).as_posix()
            for path in model_root.rglob("*")
            if path.is_file()
        }
        expected = {artifact.staged_relative for artifact in plan.artifacts}
        if actual != expected or any(path.lower().endswith(".dx80.vtx") for path in actual):
            raise Dx90RuntimeError("staged runtime sidecar set is not exact or contains DX80")
        lua_bytes = render_probe_lua(plan).encode("utf-8")
        lua_path.write_bytes(lua_bytes)
        lua_digest = hashlib.sha256(lua_bytes).hexdigest()
        return StagedRuntimeProbe(
            game, model_root, lua_path, data_root, lua_digest, created_models_directory
        )
    except Exception:
        staged.cleanup()
        raise


def _verify_capture_pairs(
    family_ids: tuple[str, ...],
    capture_root: Path,
    first_suffix: str,
    second_suffix: str,
    label: str,
) -> tuple[dict[str, Any], ...]:
    try:
        root = safe_existing_root(Path(capture_root).resolve(strict=True), "DX90 runtime captures")
    except Exception as exc:
        raise Dx90RuntimeError(str(exc)) from exc
    metrics: list[dict[str, Any]] = []
    for family_id in family_ids:
        if not isinstance(family_id, str) or not family_id or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in family_id
        ):
            raise Dx90RuntimeError("capture family id is unsafe")
        baseline_path = root / f"{family_id}-{first_suffix}.png"
        model_path = root / f"{family_id}-{second_suffix}.png"
        if not baseline_path.is_file() or not model_path.is_file():
            raise Dx90RuntimeError(f"{label} capture pair is incomplete for {family_id}")
        try:
            with Image.open(baseline_path) as opened:
                if opened.format != "PNG":
                    raise Dx90RuntimeError(f"baseline capture is not PNG for {family_id}")
                baseline = opened.convert("RGB")
            with Image.open(model_path) as opened:
                if opened.format != "PNG":
                    raise Dx90RuntimeError(f"model capture is not PNG for {family_id}")
                model = opened.convert("RGB")
        except (OSError, ValueError) as exc:
            raise Dx90RuntimeError(f"cannot parse capture pair for {family_id}: {exc}") from exc
        if baseline.size != model.size or baseline.width <= 0 or baseline.height <= 0:
            raise Dx90RuntimeError(f"capture dimensions differ for {family_id}")
        changed = 0
        maximum = 0
        absolute_sum = 0
        for left, right in zip(
            baseline.get_flattened_data(), model.get_flattened_data(), strict=True
        ):
            channel_deltas = tuple(abs(a - b) for a, b in zip(left, right, strict=True))
            if any(channel_deltas):
                changed += 1
            maximum = max(maximum, *channel_deltas)
            absolute_sum += sum(channel_deltas)
        if changed == 0 or maximum == 0:
            raise Dx90RuntimeError(f"{label} capture pair is identical; no rendered difference for {family_id}")
        total_pixels = baseline.width * baseline.height
        metrics.append(
            {
                "family_id": family_id,
                "pair": label,
                "width": baseline.width,
                "height": baseline.height,
                "changed_pixels": changed,
                "changed_fraction": changed / total_pixels,
                "max_channel_delta": maximum,
                "mean_absolute_channel_delta": absolute_sum / (total_pixels * 3),
                "baseline_sha256": _sha256(baseline_path),
                "model_sha256": _sha256(model_path),
            }
        )
    return tuple(metrics)


def verify_capture_pairs(
    family_ids: tuple[str, ...], capture_root: Path
) -> tuple[dict[str, Any], ...]:
    return _verify_capture_pairs(family_ids, capture_root, "baseline", "model", "rendering")


def verify_animation_capture_pairs(
    family_ids: tuple[str, ...], capture_root: Path
) -> tuple[dict[str, Any], ...]:
    metrics = _verify_capture_pairs(
        family_ids, capture_root, "animation-a", "animation-b", "animation"
    )
    for metric in metrics:
        if metric["changed_pixels"] < 4 or metric["max_channel_delta"] < 8:
            raise Dx90RuntimeError(
                "animation capture difference is too weak for " + metric["family_id"]
            )
        if metric["changed_fraction"] > 0.25:
            raise Dx90RuntimeError(
                "animation capture changed a global pixel fraction for " + metric["family_id"]
            )
    return metrics


def runtime_outputs_ready(plan: RuntimePlan, data_root: Path) -> bool:
    root = Path(data_root)
    expected = {"server.json", "client.json"}
    for family_id in plan.family_ids:
        expected.update(
            {
                f"{family_id}-baseline.png",
                f"{family_id}-model.png",
                f"{family_id}-animation-a.png",
                f"{family_id}-animation-b.png",
            }
        )
    return root.is_dir() and all((root / name).is_file() for name in expected)


def _verify_animation_bones(row: Mapping[str, Any], family_id: str) -> None:
    snapshots: list[dict[tuple[int, str], tuple[float, ...]]] = []
    for field in ("animation_bones_a", "animation_bones_b"):
        value = row.get(field)
        if not isinstance(value, list) or not value:
            raise Dx90RuntimeError(f"animation bone snapshot is missing for {family_id}")
        parsed: dict[tuple[int, str], tuple[float, ...]] = {}
        for bone in value:
            if not isinstance(bone, Mapping):
                raise Dx90RuntimeError(f"animation bone snapshot is invalid for {family_id}")
            bone_id, name = bone.get("id"), bone.get("name")
            position, angles = bone.get("position"), bone.get("angles")
            if (
                not isinstance(bone_id, int)
                or isinstance(bone_id, bool)
                or bone_id < 0
                or not isinstance(name, str)
                or not name
                or not isinstance(position, list)
                or not isinstance(angles, list)
                or len(position) != 3
                or len(angles) != 3
            ):
                raise Dx90RuntimeError(f"animation bone snapshot is invalid for {family_id}")
            values = tuple(position + angles)
            if any(
                not isinstance(item, (int, float))
                or isinstance(item, bool)
                or not math.isfinite(item)
                for item in values
            ):
                raise Dx90RuntimeError(f"animation bone transform is invalid for {family_id}")
            key = (bone_id, name)
            if key in parsed:
                raise Dx90RuntimeError(f"animation bone snapshot is duplicated for {family_id}")
            parsed[key] = tuple(float(item) for item in values)
        snapshots.append(parsed)
    before, after = snapshots
    if before.keys() != after.keys():
        raise Dx90RuntimeError(f"animation bone coverage differs for {family_id}")
    maximum_delta = max(
        abs(left - right)
        for key in before
        for left, right in zip(before[key], after[key], strict=True)
    )
    if maximum_delta <= 1e-4:
        raise Dx90RuntimeError(f"animation bone transforms are identical for {family_id}")


def verify_realm_reports(
    plan: RuntimePlan,
    server: Mapping[str, Any],
    client: Mapping[str, Any],
    console_text: str,
) -> tuple[str, ...]:
    expected_artifacts = [
        {
            "path": item.staged_relative,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
        }
        for item in plan.artifacts
    ]
    addon_status_fallback_required = False
    for realm, report in (("server", server), ("client", client)):
        if not isinstance(report, Mapping) or report.get("realm") != realm:
            raise Dx90RuntimeError(f"{realm} runtime report is missing or has wrong realm")
        api_available = report.get("addon_status_api_available")
        if api_available is True:
            if report.get("noaddons") is not True or report.get("noworkshop") is not True:
                raise Dx90RuntimeError(f"{realm} did not prove -noaddons and -noworkshop")
        elif api_available is False:
            if report.get("noaddons") is not None or report.get("noworkshop") is not None:
                raise Dx90RuntimeError(f"{realm} unavailable addon-status API returned invented flags")
            addon_status_fallback_required = True
        else:
            raise Dx90RuntimeError(f"{realm} addon-status API availability is invalid")
        if report.get("candidate_manifest_sha256") != plan.manifest_sha256:
            raise Dx90RuntimeError(f"{realm} runtime report manifest binding differs")
        if report.get("errors") != []:
            raise Dx90RuntimeError(f"{realm} runtime report contains errors: {report.get('errors')!r}")
        if report.get("artifacts") != expected_artifacts:
            raise Dx90RuntimeError(f"{realm} runtime artifact hashes differ from exact candidates")
        rows = report.get("families")
        if not isinstance(rows, list) or [row.get("family_id") for row in rows if isinstance(row, dict)] != list(plan.family_ids):
            raise Dx90RuntimeError(f"{realm} runtime family order/coverage differs")

    expected_server_caps = {"dynamic_model_load": True, "physics": True, "damage": True}
    expected_client_caps = {
        "dynamic_model_load": True,
        "rendering": True,
        "bodygroups_skins": True,
        "animation": True,
    }
    if server.get("capabilities") != expected_server_caps:
        raise Dx90RuntimeError("server did not prove dynamic_model_load, physics and damage")
    if client.get("capabilities") != expected_client_caps:
        raise Dx90RuntimeError("client did not prove dynamic_model_load, rendering, bodygroups_skins and animation")

    for index, family_id in enumerate(plan.family_ids):
        model_path = plan.model_paths[index]
        server_row = server["families"][index]
        if (
            server_row.get("family_id") != family_id
            or server_row.get("model_path") != model_path
            or server_row.get("valid_model") is not True
            or server_row.get("entity_valid") is not True
            or server_row.get("loaded_model") != model_path
        ):
            raise Dx90RuntimeError(f"dynamic_model_load failed for {family_id}")
        mass = server_row.get("physics_mass")
        if (
            server_row.get("physics_object_valid") is not True
            or not isinstance(mass, (int, float))
            or isinstance(mass, bool)
            or not (0 < mass < float("inf"))
            or server_row.get("physics_moved") is not True
        ):
            raise Dx90RuntimeError(f"physics failed for {family_id}")
        if server_row.get("damage_observed") is not True:
            raise Dx90RuntimeError(f"damage failed for {family_id}")

        client_row = client["families"][index]
        if (
            client_row.get("family_id") != family_id
            or client_row.get("model_path") != model_path
            or client_row.get("entity_valid") is not True
            or client_row.get("loaded_model") != model_path
        ):
            raise Dx90RuntimeError(f"client dynamic_model_load failed for {family_id}")
        if client_row.get("bodygroups_skins") is not True:
            raise Dx90RuntimeError(f"bodygroups_skins failed for {family_id}")
        if client_row.get("animation") is not True:
            raise Dx90RuntimeError(f"animation failed for {family_id}")
        _verify_animation_bones(client_row, family_id)
        if (
            client_row.get("baseline_png") != f"{family_id}-baseline"
            or client_row.get("model_png") != f"{family_id}-model"
        ):
            raise Dx90RuntimeError(f"rendering capture declaration failed for {family_id}")
        if (
            client_row.get("animation_a_png") != f"{family_id}-animation-a"
            or client_row.get("animation_b_png") != f"{family_id}-animation-b"
        ):
            raise Dx90RuntimeError(f"animation capture declaration failed for {family_id}")

    if not isinstance(console_text, str):
        raise Dx90RuntimeError("runtime console log is invalid")
    if addon_status_fallback_required and (
        "Game is ran with -noaddons, not loading legacy/folder addons!" not in console_text
        or "Mounted 0 of 0 workshop addons!" not in console_text
    ):
        raise Dx90RuntimeError("runtime console did not prove -noaddons and zero Workshop mounts")
    for line in console_text.splitlines():
        lowered = line.lower()
        if "models/maximum_dx90_runtime/" in lowered and any(
            marker in lowered
            for marker in (
                "error vertex file",
                "error loading",
                "failed",
                "couldn't load",
                "could not load",
                "missing",
                "not found",
            )
        ):
            raise Dx90RuntimeError(f"runtime console contains candidate load error: {line.strip()}")
    return (
        "animation",
        "bodygroups_skins",
        "damage",
        "dynamic_model_load",
        "physics",
        "rendering",
    )
