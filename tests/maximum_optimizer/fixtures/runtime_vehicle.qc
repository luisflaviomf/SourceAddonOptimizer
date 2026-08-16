$modelname "LVS/Test/Runtime_Vehicle.mdl"
$cdmaterials "models/LVS/Test"
$body "chassis" "body.smd"
$model "interior" "interior.smd"
$bodygroup "wheels"
{
    studio "wheel_stock.smd"
    blank
    studio "wheel_sport.smd"
}
$texturegroup "vehicle_skins"
{
    { "paint" "tire" }
    { "paint_red" "tire_sport" }
}
