from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from math import prod
from typing import Generic, TypeVar

from jaxtyping import Float
import torch
from torch import device, Tensor
from torch.nn import functional as F


@dataclass
class HistogramPdfEstimatorCfg:
    num_bins: int = 1000
    blur_kernel_size: int = 5
    blur_kernel_sigma: float = 0.2


class HistogramPdfEstimator:
    """Estimates the density of a set of samples and returns the inverse of the density as weights."""

    def __init__(
        self,
        initial_samples: Float[Tensor, "sample"],
        cfg: HistogramPdfEstimatorCfg,
    ) -> None:
        self.cfg = cfg
        self.device = initial_samples.device
        self.histogram = self.get_smooth_density_histogram(initial_samples)
        assert self.histogram.min().item() > 0.001, \
            "Histogram too inaccurate, please use a different time_sampler"

    def get_gaussian_1d_kernel(self) -> Float[Tensor, "kernel_size"]:
        if self.cfg.blur_kernel_size % 2 == 0:
            raise ValueError("Kernel size must be odd.")

        # Create a range of values centered at 0
        center = self.cfg.blur_kernel_size // 2
        x = torch.arange(-center, center + 1, dtype=torch.float32, device=self.device)

        # Compute the Gaussian function
        kernel = torch.exp(-0.5 * (x / self.cfg.blur_kernel_sigma) ** 2)

        # Normalize the kernel to ensure sum equals 1
        kernel /= kernel.sum()
        return kernel

    def get_smooth_density_histogram(
        self, 
        vals: Float[Tensor, "sample"]
    ) -> Float[Tensor, "num_bins"]:
        assert vals.min() >= 0, "Timesteps must be nonnegative"
        assert vals.max() <= 1, "Timesteps must be less or equal than 1"
        histogram_torch = torch.histc(vals, self.cfg.num_bins, min=0, max=1).to(
            self.device
        )

        kernel = self.get_gaussian_1d_kernel()

        # Reflective padding to avoid edge effects in convolution
        padded_hist = F.pad(
            histogram_torch.unsqueeze(0).unsqueeze(0),
            (self.cfg.blur_kernel_size // 2, self.cfg.blur_kernel_size // 2),
            mode="reflect",
        )
        histogram_torch_conv = F.conv1d(
            padded_hist, kernel.unsqueeze(0).unsqueeze(0)
        ).to(self.device)

        # remove unnecessary dimensions and normalize to pdf
        return histogram_torch_conv.squeeze() / histogram_torch_conv.mean()

    def __call__(
        self, 
        t: Float[Tensor, "sample"]
    ) -> Float[Tensor, "sample"]:
        bin_ids = (t * self.cfg.num_bins).long()
        bin_ids.clamp_(0, self.cfg.num_bins - 1)
        return self.histogram[bin_ids]


@dataclass
class TimeSamplerCfg:
    name: str
    histogram_pdf_estimator: HistogramPdfEstimatorCfg = field(
        default_factory=HistogramPdfEstimatorCfg
    )
    num_normalization_samples: int = 80000
    eps: float = 1e-6
    add_zeros: bool = False


T = TypeVar("T", bound=TimeSamplerCfg)


class TimeSampler(Generic[T], ABC):
    def __init__(
        self,
        cfg: T,
        resolution: tuple[int, int],
    ) -> None:
        self.cfg = cfg
        self.resolution = resolution
        self.dim = prod(self.resolution)

    @abstractmethod
    def get_time(
        self, 
        batch_size: int, 
        num_samples: int = 1,
        device: device | str = "cpu",
    ) -> Float[Tensor, "batch sample height width"]:
        pass

    def get_normalization_samples(
        self, 
        device: device | str
    ) -> Float[Tensor, "sample"]:
        return self.get_time(
            self.cfg.num_normalization_samples, device=device
        ).flatten()

    def get_normalization_weights(
        self, 
        t: Float[Tensor, "*batch"]
    ) -> Float[Tensor, "*#batch"]:
        if self.cfg.histogram_pdf_estimator is None:
            return torch.ones_like(t)

        if not hasattr(self, "histogram_pdf_estimator"):
            self.histogram_pdf_estimator = HistogramPdfEstimator(
                self.get_normalization_samples(t.device),
                self.cfg.histogram_pdf_estimator,
            )

        shape = t.shape
        probs = self.histogram_pdf_estimator(t.flatten())
        weights = (1 + self.cfg.eps) / (probs + self.cfg.eps)
        return weights.view(shape)

    def __call__(
        self, 
        batch_size: int, 
        num_samples: int = 1,
        device: device | str = "cpu",
    ) -> tuple[
        Float[Tensor, "batch sample height width"],
        Float[Tensor, "batch #sample height width"]
    ]:
        t = self.get_time(batch_size, num_samples, device)
        weights = self.get_normalization_weights(t)

        if self.cfg.add_zeros:
            t = t.flatten(-2).contiguous()
            weights = weights.flatten(-2).contiguous()
            zero_ratios = torch.rand((batch_size,), device=device)
            zero_mask = torch.linspace(1/self.dim, 1, self.dim, device=device) < zero_ratios[:, None, None]
            idx = torch.rand_like(t).argsort(dim=-1)
            t[zero_mask] = 0
            weights[zero_mask] = 0
            t = t.gather(-1, idx).reshape(batch_size, -1, *self.resolution)
            weights = weights.gather(-1, idx).reshape(batch_size, -1, *self.resolution)
                        
        return t, weights
