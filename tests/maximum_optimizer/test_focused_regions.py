from __future__ import annotations

import math
import random
import unittest

from maximum_optimizer.regions import RegionManifest, build_region_manifest
from maximum_optimizer.visual_validation import FidelityProfile, REQUIRED_METRICS


def _profile(**limits: float) -> FidelityProfile:
    values = {metric: 1.0 for metric in REQUIRED_METRICS}
    values.update(limits)
    return FidelityProfile(1, "focused-test-v1", True, "a" * 64, values)


def _fixture():
    from maximum_optimizer.domain import FocusedRegionPolicy, WholeStateEvidence

    manifest = build_region_manifest((
        ("body.smd", "body", ("paint",)),
        ("grille.smd", "grille", ("chrome",)),
        ("wheel.smd", "wheel", ("rubber",)),
    ))
    keys = {
        entry.descriptor.object_name: entry.key for entry in manifest.entries
    }
    pairs = tuple(
        (entry.descriptor.source_identity, "b" * 64, "c" * 64)
        for entry in manifest.entries
    )
    first = WholeStateEvidence(
        0,
        "engine-default",
        (("000:body", 0),),
        0,
        ("bind",),
        pairs,
        "renders/engine-default/original/render_manifest.json",
        "d" * 64,
        "renders/engine-default/optimized/render_manifest.json",
        "e" * 64,
        (
            {"scope": keys["body"], "pose": "bind",
             "surface_bidirectional_p95": 0.08, "surface_max": 0.10},
            {"scope": keys["grille"], "pose": "bind",
             "surface_bidirectional_p95": 0.05, "surface_max": 0.19},
            {"scope": keys["wheel"], "pose": "bind",
             "surface_bidirectional_p95": 0.09, "surface_max": 0.10},
        ),
    )
    second = WholeStateEvidence(
        1,
        "bodygroup-grille-1",
        (("000:body", 0), ("001:grille", 1)),
        0,
        ("bind", "representative"),
        pairs,
        "renders/bodygroup-grille-1/original/render_manifest.json",
        "f" * 64,
        "renders/bodygroup-grille-1/optimized/render_manifest.json",
        "1" * 64,
        (
            {"scope": keys["grille"], "pose": "representative",
             "surface_bidirectional_p95": 0.07, "surface_max": 0.18},
        ),
    )
    return manifest, keys, (first, second), FocusedRegionPolicy(
        1, "surface-risk-top-k-v1", 2
    )


class FocusedRegionSelectionTests(unittest.TestCase):
    def test_ranking_is_stable_under_shuffled_states_and_uses_normalized_risk(self):
        from maximum_optimizer.focused_regions import select_focus_targets

        manifest, keys, states, policy = _fixture()
        profile = _profile(surface_bidirectional_p95=0.1, surface_max=0.2)
        expected = select_focus_targets(states, manifest, profile, policy)
        shuffled = list(states)
        random.Random(7).shuffle(shuffled)
        actual = select_focus_targets(tuple(shuffled), manifest, profile, policy)

        self.assertEqual(
            tuple(item.region_key for item in expected),
            (keys["grille"], keys["wheel"]),
        )
        self.assertEqual(actual, expected)
        self.assertEqual(expected[0].state_name, "engine-default")
        self.assertEqual(expected[0].anchor_pose, "bind")
        self.assertEqual(expected[0].normalized_max, 0.95)
        self.assertRegex(expected[0].selector_input_sha256, r"^[0-9a-f]{64}$")

    def test_selector_hash_binds_unselected_rows_policy_profile_and_manifest(self):
        from maximum_optimizer.domain import FocusedRegionPolicy, WholeStateEvidence
        from maximum_optimizer.focused_regions import select_focus_targets

        manifest, _keys, states, policy = _fixture()
        profile = _profile(surface_bidirectional_p95=0.1, surface_max=0.2)
        baseline = select_focus_targets(states, manifest, profile, policy)
        rows = list(states[0].geometry_rows)
        changed_body = dict(rows[0])
        changed_body["surface_bidirectional_p95"] = 0.081
        rows[0] = changed_body
        changed = WholeStateEvidence(
            states[0].state_index, states[0].state_name, states[0].bodygroups,
            states[0].lod_index, states[0].poses, states[0].source_pairs,
            states[0].reference_manifest, states[0].reference_manifest_sha256,
            states[0].candidate_manifest, states[0].candidate_manifest_sha256,
            tuple(rows),
        )
        mutated = select_focus_targets((changed, states[1]), manifest, profile, policy)
        changed_policy = select_focus_targets(
            states, manifest, profile,
            FocusedRegionPolicy(1, "surface-risk-top-k-v1", 3),
        )
        changed_profile = select_focus_targets(
            states, manifest,
            _profile(surface_bidirectional_p95=0.11, surface_max=0.2), policy,
        )

        self.assertNotEqual(
            baseline[0].selector_input_sha256, mutated[0].selector_input_sha256
        )
        self.assertNotEqual(
            baseline[0].selector_input_sha256,
            changed_policy[0].selector_input_sha256,
        )
        self.assertNotEqual(
            baseline[0].selector_input_sha256,
            changed_profile[0].selector_input_sha256,
        )

    def test_source_aliases_are_canonical_and_duplicate_aliases_are_rejected(self):
        from maximum_optimizer.domain import WholeStateEvidence
        from maximum_optimizer.focused_regions import select_focus_targets

        manifest, _keys, states, policy = _fixture()
        profile = _profile(surface_bidirectional_p95=0.1, surface_max=0.2)
        base = states[0]
        aliases = tuple(
            (source.upper().replace("/", "\\"), reference, candidate)
            for source, reference, candidate in base.source_pairs
        )
        aliased = WholeStateEvidence(
            base.state_index, base.state_name, base.bodygroups, base.lod_index,
            base.poses, aliases, base.reference_manifest,
            base.reference_manifest_sha256, base.candidate_manifest,
            base.candidate_manifest_sha256, base.geometry_rows,
        )
        canonical = select_focus_targets((base,), manifest, profile, policy)
        normalized = select_focus_targets((aliased,), manifest, profile, policy)
        self.assertEqual(normalized, canonical)

        duplicated = WholeStateEvidence(
            base.state_index, base.state_name, base.bodygroups, base.lod_index,
            base.poses, base.source_pairs + (("GRILLE.SMD", "2" * 64, "3" * 64),),
            base.reference_manifest, base.reference_manifest_sha256,
            base.candidate_manifest, base.candidate_manifest_sha256,
            base.geometry_rows,
        )
        with self.assertRaisesRegex(ValueError, "duplicate source identity"):
            select_focus_targets((duplicated,), manifest, profile, policy)

    def test_equal_risk_anchor_uses_earliest_state_then_pose(self):
        from maximum_optimizer.domain import WholeStateEvidence
        from maximum_optimizer.focused_regions import select_focus_targets

        manifest, keys, states, policy = _fixture()
        profile = _profile(surface_bidirectional_p95=0.1, surface_max=0.2)
        tie_row = {
            "scope": keys["grille"], "pose": "aaa",
            "surface_bidirectional_p95": 0.05, "surface_max": 0.19,
        }
        late = WholeStateEvidence(
            1, states[1].state_name, states[1].bodygroups, 0, ("aaa",),
            states[1].source_pairs, states[1].reference_manifest,
            states[1].reference_manifest_sha256, states[1].candidate_manifest,
            states[1].candidate_manifest_sha256, (tie_row,),
        )
        target = select_focus_targets(
            (late, states[0]), manifest, profile, policy
        )[0]
        self.assertEqual(target.state_index, 0)
        self.assertEqual(target.anchor_pose, "bind")

    def test_selector_returns_exact_bounded_prefix(self):
        from maximum_optimizer.domain import FocusedRegionPolicy
        from maximum_optimizer.focused_regions import select_focus_targets

        manifest, _keys, states, _policy = _fixture()
        profile = _profile(surface_bidirectional_p95=0.1, surface_max=0.2)
        targets = select_focus_targets(
            states, manifest, profile,
            FocusedRegionPolicy(1, "surface-risk-top-k-v1", 4),
        )

        self.assertEqual(len(targets), 3)
        self.assertEqual(tuple(item.rank for item in targets), (0, 1, 2))
        for top_k in (0, 5, True):
            with self.subTest(top_k=top_k), self.assertRaises(ValueError):
                FocusedRegionPolicy(1, "surface-risk-top-k-v1", top_k)

    def test_zero_limits_accept_only_exact_zero(self):
        from maximum_optimizer.domain import WholeStateEvidence
        from maximum_optimizer.focused_regions import select_focus_targets

        manifest, _keys, states, policy = _fixture()
        zero = _profile(surface_bidirectional_p95=0.0, surface_max=0.0)
        row = dict(states[0].geometry_rows[0])
        row["surface_bidirectional_p95"] = 0.0
        row["surface_max"] = 0.0
        exact = WholeStateEvidence(
            states[0].state_index, states[0].state_name, states[0].bodygroups,
            states[0].lod_index, states[0].poses, states[0].source_pairs,
            states[0].reference_manifest, states[0].reference_manifest_sha256,
            states[0].candidate_manifest, states[0].candidate_manifest_sha256,
            (row,),
        )
        self.assertEqual(
            select_focus_targets((exact,), manifest, zero, policy)[0].normalized_max,
            0.0,
        )
        with self.assertRaisesRegex(ValueError, "zero focused limit"):
            select_focus_targets((states[0],), manifest, zero, policy)

    def test_selector_rejects_ambiguous_or_unbounded_evidence(self):
        from maximum_optimizer.domain import WholeStateEvidence
        from maximum_optimizer.focused_regions import select_focus_targets

        manifest, _keys, states, policy = _fixture()
        profile = _profile(surface_bidirectional_p95=0.1, surface_max=0.2)
        base = states[0]
        mutations = []
        mutations.append((base, base))
        unknown = dict(base.geometry_rows[0]); unknown["scope"] = "r-" + "0" * 64
        mutations.append((WholeStateEvidence(
            base.state_index, base.state_name, base.bodygroups, base.lod_index,
            base.poses, base.source_pairs, base.reference_manifest,
            base.reference_manifest_sha256, base.candidate_manifest,
            base.candidate_manifest_sha256, (unknown,),
        ),))
        nonfinite = dict(base.geometry_rows[0]); nonfinite["surface_max"] = math.inf
        mutations.append((WholeStateEvidence(
            base.state_index, base.state_name, base.bodygroups, base.lod_index,
            base.poses, base.source_pairs, base.reference_manifest,
            base.reference_manifest_sha256, base.candidate_manifest,
            base.candidate_manifest_sha256, (nonfinite,),
        ),))
        mutations.append((WholeStateEvidence(
            base.state_index, base.state_name, base.bodygroups, base.lod_index,
            base.poses, (), base.reference_manifest, base.reference_manifest_sha256,
            base.candidate_manifest, base.candidate_manifest_sha256,
            (base.geometry_rows[0],),
        ),))
        mutations.append(tuple(
            WholeStateEvidence(
                index, f"state-{index}", base.bodygroups, 0, base.poses,
                base.source_pairs, base.reference_manifest,
                base.reference_manifest_sha256, base.candidate_manifest,
                base.candidate_manifest_sha256, (base.geometry_rows[0],),
            )
            for index in range(17)
        ))
        mutations.append(())
        extra = dict(base.geometry_rows[0]); extra["unexpected"] = 0.0
        mutations.append((WholeStateEvidence(
            base.state_index, base.state_name, base.bodygroups, base.lod_index,
            base.poses, base.source_pairs, base.reference_manifest,
            base.reference_manifest_sha256, base.candidate_manifest,
            base.candidate_manifest_sha256, (extra,),
        ),))
        boolean = dict(base.geometry_rows[0]); boolean["surface_max"] = True
        mutations.append((WholeStateEvidence(
            base.state_index, base.state_name, base.bodygroups, base.lod_index,
            base.poses, base.source_pairs, base.reference_manifest,
            base.reference_manifest_sha256, base.candidate_manifest,
            base.candidate_manifest_sha256, (boolean,),
        ),))
        for index, changed in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(ValueError):
                select_focus_targets(changed, manifest, profile, policy)

        invalid_manifest = RegionManifest(
            manifest.entries, schema_version=2,
        )
        with self.assertRaises(ValueError):
            select_focus_targets((base,), invalid_manifest, profile, policy)

    def test_nested_geometry_rows_are_deeply_immutable(self):
        from maximum_optimizer.domain import WholeStateEvidence

        manifest, _keys, states, _policy = _fixture()
        del manifest
        original = dict(states[0].geometry_rows[0])
        evidence = WholeStateEvidence(
            states[0].state_index, states[0].state_name, states[0].bodygroups,
            states[0].lod_index, states[0].poses, states[0].source_pairs,
            states[0].reference_manifest, states[0].reference_manifest_sha256,
            states[0].candidate_manifest, states[0].candidate_manifest_sha256,
            (original,),
        )
        original["surface_max"] = 999.0
        self.assertNotEqual(evidence.geometry_rows[0]["surface_max"], 999.0)
        with self.assertRaises(TypeError):
            evidence.geometry_rows[0]["surface_max"] = 0.0


if __name__ == "__main__":
    unittest.main()
