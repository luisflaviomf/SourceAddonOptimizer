// Real-derived structural excerpt from the original Crowbar-decompiled
// diggercars/dodge_monaco/monaco_police/monaco_police.qc.
//
// It intentionally preserves both trailing-backslash $cdmaterials arguments,
// the nine skin-family identities, all twelve attachment names, all twenty-two
// sequence names, representative sequence blocks, and the collision source.
// Long material rows and animation block bodies not needed by the inventory
// regression are reduced to keep the repository fixture focused and portable.

$modelname "diggercars/dodge_monaco/monaco_police.mdl"

$cdmaterials "models\DiggerCars\dodge_monaco\"
$cdmaterials "models\DiggerCars\shared\"

$texturegroup "skinfamilies"
{
    { "skin"  "metal" "grey" "leather" "mars_skybolt_lightbar" "policestuff" "skin1" "skin8" }
    { "skin1" "metal" "grey" "leather" "mars_skybolt_lightbar" "policestuff" "skin1" "skin8" }
    { "skin2" "metal" "grey" "leather" "mars_skybolt_lightbar" "policestuff" "skin1" "skin8" }
    { "skin3" "metal" "grey" "leather" "mars_skybolt_lightbar" "policestuff" "skin1" "skin8" }
    { "skin4" "metal" "grey" "leather" "mars_skybolt_lightbar" "policestuff" "skin1" "skin8" }
    { "skin5" "metal" "grey" "leather" "mars_skybolt_lightbar" "policestuff" "skin1" "skin8" }
    { "skin6" "metal" "grey" "leather" "mars_skybolt_lightbar" "policestuff" "skin1" "skin8" }
    { "skin7" "metal" "grey" "leather" "mars_skybolt_lightbar" "policestuff" "skin1" "skin8" }
    { "skin8" "metal" "grey" "leather" "mars_skybolt_lightbar" "policestuff" "skin1" "skin8" }
}

$attachment "bar1l1a1" "bar1l1a1" 0 0 0 rotate 0 0 0
$attachment "bar1l1a2" "bar1l1a2" 0 0 0 rotate 0 0 0
$attachment "bar1l2a1" "bar1l2a1" 0 0 0 rotate 0 0 0
$attachment "bar1l2a2" "bar1l2a2" 0 0 0 rotate 0 0 0
$attachment "bar2l1a1" "bar2l1a1" 0 0 0 rotate 0 0 0
$attachment "bar2l1a2" "bar2l1a2" 0 0 0 rotate 0 0 0
$attachment "bar2l2a1" "bar2l2a1" 0 0 0 rotate 0 0 0
$attachment "bar2l2a2" "bar2l2a2" 0 0 0 rotate 0 0 0
$attachment "bar3a1" "bar3a1" 0 0 0 rotate 0 0 0
$attachment "bar3a2" "bar3a2" 0 0 0 rotate 0 0 0
$attachment "spot2a" "spot2a" 0 0 0 rotate 0 0 0
$attachment "spot1a" "spot1a" 0 0 0 rotate 0 0 0

$sequence "turning" { "turn_1" "turn_2" "turn_3" }
$sequence "gearing" { "k1" "k2" "k3" "k4" }
$sequence "gas" { "g1" "g2" }
$sequence "brake" { "b1" "b2" }
$sequence "handbrake" { "h1" "h2" }
$sequence "speedo" { "s1" "s2" "s3" "s4" "s5" "s6" "s7" "s8" }
$sequence "tacho" { "t1" "t2" "t3" }
$sequence "temp" { "tm1" "tm2" }
$sequence "fuel" { "fuel1" "fuel2" }
$sequence "oil" { "om1" "om2" }
$sequence "alt" { "alt1" "alt2" }
$sequence "hood" { "hood1" "hood2" }
$sequence "trunk" { "trunk1" "trunk2" }
$sequence "left_door" { "left_door1" "left_door2" }
$sequence "right_door" { "right_door1" "right_door2" }
$sequence "rear_left_door" { "rear_left_door1" "rear_left_door2" }
$sequence "rear_right_door" { "rear_right_door1" "rear_right_door2" }
$sequence "digital_1" { "d11" "d110" }
$sequence "digital_2" { "d21" "d210" }
$sequence "digital_3" { "d31" "d310" }
$sequence "siren_spin" { "engine_spin1" "engine_spin5" }
$sequence "idle"
{
    "monaco_police_anims\idle.smd"
    fadein 0.2
    fadeout 0.2
    fps 30
}

$collisionmodel "monaco_police_physics.smd"
{
    $mass 1000
    $inertia 1
    $damping 0
    $rotdamping 0
    $concave
    $maxconvexpieces 12
}
