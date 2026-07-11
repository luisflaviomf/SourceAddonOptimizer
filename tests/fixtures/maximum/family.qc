$modelname "vehicles/test.mdl"
$body "body" "reference.smd" // this comment must not become a token
$bodygroup "wheels"
{
    studio "wheel.smd"
    studio "WHEEL.smd"
}
$texturegroup "skins"
{
    { "paint/red" "Trim/Black" }
    { "PAINT/RED" "trim/chrome" }
}
$attachment "eyes" "root" 1 2 3
$attachment "EYES" "root" 4 5 6
$hboxset "default"
$hbox 0 "root" -1 -2 -3 1 2 3
$sequence "idle" "reference.smd"
$lod 20
{
    replacemodel "reference.smd" "wheel.smd"
}
$collisionmodel "reference.smd"
