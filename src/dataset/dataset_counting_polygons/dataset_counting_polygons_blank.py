from dataclasses import dataclass
from typing import Literal

from functools import cache
import numpy as np
from PIL import Image

from .dataset_counting_polygons_base import (
    DatasetCountingPolygonsBase,
    DatasetCountingPolygonsCfg,
)


@dataclass
class DatasetCountingPolygonsBlankCfg(DatasetCountingPolygonsCfg):
    name: Literal[
        "counting_polygons_blank", 
        "counting_polygons_blank_explicit_conditional",
        "counting_polygons_blank_ambiguous_conditional",
    ] = "counting_polygons_blank"
    hsl_lightness: float = 0.25
    hsl_saturation: float = 1.0


class DatasetCountingPolygonsBlank(
    DatasetCountingPolygonsBase[DatasetCountingPolygonsBlankCfg]
):

    @staticmethod
    @cache
    def get_blank_image(image_shape: tuple[int, int]) -> Image.Image:
        return Image.new("RGBA", image_shape, (255, 255, 255, 255))

    def _get_base_image(self, base_image_idx) -> Image.Image:
        # We need the tuple() cast for the caching
        return self.get_blank_image(tuple(self.cfg.image_shape))

    def _get_color(
        self, rng: np.random.Generator | None, base_image: Image.Image
    ) -> str | tuple[int, int, int]:
        hue = rng.uniform(0, 360) if rng else np.random.uniform(0, 360)
        lightness = self.cfg.hsl_lightness
        saturation = self.cfg.hsl_saturation

        return f"hsl({hue:.1f}, {saturation * 100}%, {lightness * 100}%)"

    @property
    def _num_available(self) -> int:
        return self._num_overlay_images

    def _split_idx(self, idx) -> tuple[int, int, None]:
        num_circles_idx, circles_image_idx = self.split_circles_idx(idx)
        return num_circles_idx, circles_image_idx, None
