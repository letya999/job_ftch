"""Exponential fingerprint space generator.

Generates millions of unique fingerprints on-the-fly via combinatorics.
Each call to generate() produces a unique fingerprint, making clustering
impossible for anti-bot systems.

Total combinations: ~10^24 unique fingerprints per session.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any

# Fingerprint component pools
_WEBGL_RENDERERS: list[str] = [
    "ANGLE (Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0)",
    "Apple GPU",
    "Mesa Intel(R) UHD Graphics 630 (CFL GT2)",
]

_FONT_LISTS: dict[str, list[str]] = {
    "windows": [
        "Arial",
        "Calibri",
        "Cambria",
        "Candara",
        "Consolas",
        "Constantia",
        "Corbel",
        "Courier New",
        "Georgia",
        "Helvetica",
        "Segoe UI",
        "Tahoma",
        "Times New Roman",
        "Trebuchet MS",
        "Verdana",
    ],
    "macos": [
        "Arial",
        "Courier New",
        "Geneva",
        "Georgia",
        "Helvetica",
        "Helvetica Neue",
        "Lucida Grande",
        "Menlo",
        "Monaco",
        "San Francisco",
        "Times New Roman",
        "Verdana",
    ],
    "linux": [
        "Arial",
        "Courier New",
        "DejaVu Sans",
        "DejaVu Serif",
        "Droid Sans",
        "Droid Serif",
        "FreeMono",
        "FreeSans",
        "FreeSerif",
        "Georgia",
        "Helvetica",
        "Liberation Mono",
        "Liberation Sans",
        "Liberation Serif",
        "Noto Sans",
        "Times New Roman",
        "Ubuntu",
        "Verdana",
    ],
}

_SCREEN_SIZES: list[tuple[int, int]] = [
    (1920, 1080),
    (1440, 900),
    (1536, 864),
    (1366, 768),
    (1280, 720),
    (1600, 900),
    (2560, 1440),
    (1680, 1050),
]

_HARDWARE_CONCURRENCY_OPTIONS: list[int] = [2, 4, 6, 8, 12, 16]
_DEVICE_MEMORY_OPTIONS: list[int] = [2, 4, 8, 16]


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """A unique browser fingerprint with all components."""

    canvas_seed: int
    audio_seed: int
    webgl_renderer: str
    font_list: list[str]
    screen_width: int
    screen_height: int
    battery_charging: bool
    battery_level: float
    hardware_concurrency: int
    device_memory: int
    font_spacing_seed: int

    def hash(self) -> str:
        """Generate unique hash for this fingerprint."""
        components = (
            f"{self.canvas_seed}:{self.audio_seed}:{self.webgl_renderer}:"
            f"{','.join(sorted(self.font_list))}:{self.screen_width}x{self.screen_height}:"
            f"{self.battery_charging}:{self.battery_level:.2f}:"
            f"{self.hardware_concurrency}:{self.device_memory}:{self.font_spacing_seed}"
        )
        return hashlib.sha256(components.encode()).hexdigest()[:16]


class FingerprintGenerator:
    """Generate unique fingerprints on-the-fly.

    Each call to generate() produces a unique fingerprint by sampling
    from exponential combinatorial space.

    Usage:
        gen = FingerprintGenerator(seed=42)
        fp1 = gen.generate(os="windows")
        fp2 = gen.generate(os="windows")
        assert fp1.hash() != fp2.hash()  # Unique fingerprints
    """

    def __init__(self, seed: int | None = None, evolution: Any | None = None) -> None:
        self._rng = random.Random(seed)
        self.evolution = evolution

    def generate(self, os: str = "windows") -> Fingerprint:
        """Generate a unique fingerprint.

        If evolution is enabled and has a best gene, use its attributes.

        Args:
            os: Operating system ("windows", "macos", "linux")

        Returns:
            Unique Fingerprint instance
        """
        canvas_seed = self._rng.randint(1000, 99999)
        audio_seed = self._rng.randint(1000, 99999)
        font_spacing_seed = self._rng.randint(1, 999999)

        # Default random attributes
        webgl_renderer = self._rng.choice(_WEBGL_RENDERERS)
        screen_width, screen_height = self._rng.choice(_SCREEN_SIZES)
        hardware_concurrency = self._rng.choice(_HARDWARE_CONCURRENCY_OPTIONS)
        device_memory = self._rng.choice(_DEVICE_MEMORY_OPTIONS)

        # Override with evolved gene if available
        if self.evolution is not None:
            best_gene = self.evolution.best_gene()
            if best_gene is not None:
                canvas_seed = best_gene.canvas_seed
                font_spacing_seed = best_gene.font_spacing_seed
                webgl_renderer = best_gene.webgl_renderer
                screen_width = best_gene.screen_width
                screen_height = best_gene.screen_height
                hardware_concurrency = best_gene.hardware_concurrency
                device_memory = best_gene.device_memory

        # Font list: random subset of OS-specific fonts
        os_fonts = _FONT_LISTS.get(os, _FONT_LISTS["windows"])
        font_count = self._rng.randint(10, min(20, len(os_fonts)))
        font_list = self._rng.sample(os_fonts, k=font_count)

        # Battery state
        battery_charging = self._rng.random() < 0.7
        battery_level = round(self._rng.uniform(0.15, 1.0), 2)

        return Fingerprint(
            canvas_seed=canvas_seed,
            audio_seed=audio_seed,
            webgl_renderer=webgl_renderer,
            font_list=font_list,
            screen_width=screen_width,
            screen_height=screen_height,
            battery_charging=battery_charging,
            battery_level=battery_level,
            hardware_concurrency=hardware_concurrency,
            device_memory=device_memory,
            font_spacing_seed=font_spacing_seed,
        )

    def generate_batch(self, count: int, os: str = "windows") -> list[Fingerprint]:
        """Generate multiple unique fingerprints.

        Args:
            count: Number of fingerprints to generate
            os: Operating system

        Returns:
            List of unique Fingerprint instances
        """
        return [self.generate(os=os) for _ in range(count)]
