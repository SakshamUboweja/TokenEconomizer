"""Config: the stage configs default off."""
from tokeneconomizer.config import InjectionConfig, ToolingConfig


def test_defaults_all_off():
    assert InjectionConfig().enabled is False
    assert ToolingConfig().enabled is False
