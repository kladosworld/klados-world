"""Include only the reviewed legal publication resources in the installed wheel."""

import os
import runpy
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel":
            return
        private_input = os.environ.get("KLADOS_SHARED_PURPOSE_BUILD_INPUT")
        if private_input:
            resource = Path(private_input)
            if not resource.is_absolute() or resource.is_symlink() or not resource.is_file():
                raise ValueError("Invalid shared purpose build input")
            implementation = runpy.run_path(
                str(Path(self.root) / "tee_arena/society/shared_purpose.py")
            )
            # Build inputs must match the reviewed pins after parsing.
            registry = implementation["load_registry"](resource)
            implementation["validate_reviewed_registry"](registry)
            build_data.setdefault("force_include", {})[str(resource)] = (
                "config/shared-purposes-v1.json"
            )
        source = Path(self.root) / "docs" / "legal"
        if source.is_symlink():
            raise ValueError("Legal resource directory must not be a symlink")
        for name in ("TERMS.md", "PRIVACY.md", "APPROVED.json"):
            path = source / name
            if path.is_symlink():
                raise ValueError("Legal resources must not be symlinks")
            if path.is_file():
                build_data.setdefault("force_include", {})[str(path)] = "arena/legal/" + name
