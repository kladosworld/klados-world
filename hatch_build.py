"""Include only the reviewed legal publication resources in the installed wheel."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel":
            return
        source = Path(self.root) / "docs" / "legal"
        if source.is_symlink():
            raise ValueError("Legal resource directory must not be a symlink")
        for name in ("TERMS.md", "PRIVACY.md", "APPROVED.json"):
            path = source / name
            if path.is_symlink():
                raise ValueError("Legal resources must not be symlinks")
            if path.is_file():
                build_data.setdefault("force_include", {})[str(path)] = "arena/legal/" + name
