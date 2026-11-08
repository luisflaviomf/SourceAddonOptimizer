from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAIN_WINDOW = ROOT / "GmodAddonCompressor-master" / "GmodAddonCompressor" / "MainWindow.xaml"


def test_read_only_profile_binding_is_explicitly_one_way() -> None:
    xaml = MAIN_WINDOW.read_text(encoding="utf-8")

    assert 'Text="{Binding LastProfilePath, Mode=OneWay}"' in xaml


def test_models_and_pipeline_share_explicit_maximum_opt_in() -> None:
    xaml = MAIN_WINDOW.read_text(encoding="utf-8")

    # Models (redesigned, Portuguese) and the hidden Pipeline tab both expose the opt-in.
    assert xaml.count('Content="Usar com o Maximum v2"') + xaml.count('Content="Use with Models Maximum v2"') == 2
    assert 'x:Name="ModelsCompanionProfileCard"' in xaml
    assert 'x:Name="PipelineCompanionProfileCard"' in xaml
    assert xaml.count('IsChecked="{Binding UseWithMaximumV2, Mode=TwoWay') == 2
    assert xaml.count('IsEnabled="{Binding CanUseWithMaximumV2}"') == 2
