"""R2 16-axis data adapters for Physical-Intelligence/openpi.

Copy to openpi/src/openpi/policies/r2_policy.py; see OPENPI.md for registration.
Order: left joints 1..7, left gripper, right joints 1..7, right gripper.
Default actions remain absolute targets in the converter's documented native units.
"""

import dataclasses

import numpy as np

from openpi import transforms


ACTION_DIM = 16
CAMERAS = ("head_color", "hand_left_color", "hand_right_color")
MODEL_CAMERAS = ("base_0_rgb", "left_wrist_0_rgb", "right_wrist_0_rgb")


def make_repack() -> transforms.Group:
    """Dataset-only renaming; policy inference receives state/images/prompt directly."""
    return transforms.Group(
        inputs=[
            transforms.RepackTransform(
                {
                    "state": "observation.state",
                    "images": {name: f"observation.images.{name}" for name in CAMERAS},
                    "actions": "action",
                    "prompt": "prompt",
                }
            )
        ]
    )


def _image(value) -> np.ndarray:
    value = np.asarray(value)
    if value.ndim != 3:
        raise ValueError(f"Expected an HWC/CHW RGB image, got {value.shape}")
    if value.shape[0] == 3 and value.shape[-1] != 3:
        value = value.transpose(1, 2, 0)
    if value.shape[-1] != 3:
        raise ValueError(f"Expected three RGB channels, got {value.shape}")
    if np.issubdtype(value.dtype, np.floating):
        # LeRobot decodes image/video features as float32 CHW in [0, 1].
        if not np.isfinite(value).all() or value.min() < 0 or value.max() > 1:
            raise ValueError("Floating RGB images must contain finite values in [0, 1]")
        value = np.rint(value * 255).astype(np.uint8)
    elif value.dtype != np.uint8:
        raise ValueError(f"Expected uint8 RGB or float RGB in [0, 1], got {value.dtype}")
    return value


@dataclasses.dataclass(frozen=True)
class R2Inputs(transforms.DataTransformFn):
    def __call__(self, data: dict) -> dict:
        state = np.asarray(data["state"], dtype=np.float32)
        if state.shape != (ACTION_DIM,) or not np.isfinite(state).all():
            raise ValueError(f"R2 state must be a finite ({ACTION_DIM},) vector")
        result = {
            "state": state,
            "image": {
                target: _image(data["images"][source])
                for source, target in zip(CAMERAS, MODEL_CAMERAS, strict=True)
            },
            "image_mask": {name: np.True_ for name in MODEL_CAMERAS},
        }
        if "actions" in data:
            actions = np.asarray(data["actions"], dtype=np.float32)
            if actions.shape[-1] != ACTION_DIM or not np.isfinite(actions).all():
                raise ValueError(f"R2 actions must end in {ACTION_DIM} finite values")
            result["actions"] = actions
        if "prompt" in data:
            result["prompt"] = data["prompt"]
        return result


@dataclasses.dataclass(frozen=True)
class R2Outputs(transforms.DataTransformFn):
    def __call__(self, data: dict) -> dict:
        actions = np.asarray(data["actions"])
        if actions.shape[-1] < ACTION_DIM:
            raise ValueError(f"Expected at least {ACTION_DIM} action dimensions")
        return {"actions": actions[..., :ACTION_DIM]}


@dataclasses.dataclass(frozen=True)
class R2TransformFactory:
    # Keep False to preserve converter targets exactly; training and inference must agree.
    use_delta_joint_actions: bool = False

    def __call__(self, model_config) -> transforms.Group:
        if model_config.action_dim < ACTION_DIM:
            raise ValueError("Model action_dim is smaller than R2's 16-dimensional action")
        group = transforms.Group(inputs=[R2Inputs()], outputs=[R2Outputs()])
        if self.use_delta_joint_actions:
            # Seven arm joints per side are relative to the current state. Both grippers stay absolute.
            mask = transforms.make_bool_mask(7, -1, 7, -1)
            group = group.push(
                inputs=[transforms.DeltaActions(mask)],
                outputs=[transforms.AbsoluteActions(mask)],
            )
        return group
