"""E20 shared harness: frozen task policy and fixtures used identically by both experiment arms."""

ROBOSUITE_VERSION = "1.5.2"
PATCHED_JOINT_VEL_SHA256 = "53801168e9fb8e7fada70a4e144609700801e1fa09a4486e38c2b0e282f8f4da"


def assert_patched_robosuite() -> None:
    """Both E20 arms call this first: refuse to run on any robosuite other than 1.5.2 with
    patches/robosuite-1.5.2-joint-velocity.patch applied (scripts/apply_robosuite_patch.py)."""
    import hashlib
    import importlib.metadata
    from pathlib import Path

    import robosuite.controllers.parts.generic.joint_vel as jv

    version = importlib.metadata.version("robosuite")
    digest = hashlib.sha256(Path(jv.__file__).read_bytes()).hexdigest()
    if version != ROBOSUITE_VERSION or digest != PATCHED_JOINT_VEL_SHA256:
        raise RuntimeError(f"E20 needs robosuite {ROBOSUITE_VERSION} with the joint-velocity patch; "
                           f"found {version}, joint_vel.py sha256 {digest}")
