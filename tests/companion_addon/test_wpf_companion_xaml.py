from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAIN_WINDOW = ROOT / "GmodAddonCompressor-master" / "GmodAddonCompressor" / "MainWindow.xaml"


def test_read_only_profile_binding_is_explicitly_one_way() -> None:
    xaml = MAIN_WINDOW.read_text(encoding="utf-8")

    assert 'Text="{Binding LastProfilePath, Mode=OneWay}"' in xaml
