"""Evolutionary fingerprint breeding (ADR-076).

Uses genetic algorithms to evolve browser fingerprint configurations based
on bypass success rates. Fingerprints that pass anti-bot checks more often
are selected for breeding; unsuccessful ones are replaced.

The algorithm is pure Python / stdlib — no ML frameworks needed.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger("job_ftch.bypass.fingerprint_evolution")

_HARDWARE_CONCURRENCY_POOL = (2, 4, 6, 8, 12, 16)
_DEVICE_MEMORY_POOL = (2, 4, 8, 16, 32)
_SCREEN_POOL = (
    (1366, 768),
    (1440, 900),
    (1536, 864),
    (1920, 1080),
    (2560, 1440),
)
_COLOR_DEPTH_POOL = (24, 30, 32)
_PIXEL_RATIO_POOL = (1.0, 1.25, 1.5, 2.0)
_WEBGL_RENDERER_POOL = (
    "ANGLE (Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (Intel(R) Iris(TM) Plus Graphics 640 Direct3D11 vs_5_0 ps_5_0)",
    "Mesa Intel(R) UHD Graphics 630 (CFL GT2)",
    "Apple GPU",
    "ANGLE (Apple, Apple M1, OpenGL 4.1)",
)
_PLATFORM_POOL = ("windows", "macos", "linux")
_BROWSER_FAMILY_POOL = ("chromium", "firefox", "safari")


@dataclass(slots=True)
class FingerprintGene:
    """A single evolvable fingerprint configuration."""

    gene_id: str
    hardware_concurrency: int
    device_memory: int
    screen_width: int
    screen_height: int
    color_depth: int
    pixel_ratio: float
    webgl_renderer: str
    canvas_seed: int
    font_spacing_seed: int
    platform: str
    browser_family: str
    success_count: int = 0
    failure_count: int = 0

    @property
    def fitness(self) -> float:
        """Fitness score: success rate with Laplace smoothing."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.5  # Prior: assume 50% until we have data.
        # Laplace smoothing: (successes + 1) / (total + 2).
        return (self.success_count + 1) / (total + 2)

    @property
    def total_attempts(self) -> int:
        return self.success_count + self.failure_count

    def record_success(self) -> FingerprintGene:
        return FingerprintGene(
            gene_id=self.gene_id,
            hardware_concurrency=self.hardware_concurrency,
            device_memory=self.device_memory,
            screen_width=self.screen_width,
            screen_height=self.screen_height,
            color_depth=self.color_depth,
            pixel_ratio=self.pixel_ratio,
            webgl_renderer=self.webgl_renderer,
            canvas_seed=self.canvas_seed,
            font_spacing_seed=self.font_spacing_seed,
            platform=self.platform,
            browser_family=self.browser_family,
            success_count=self.success_count + 1,
            failure_count=self.failure_count,
        )

    def record_failure(self) -> FingerprintGene:
        return FingerprintGene(
            gene_id=self.gene_id,
            hardware_concurrency=self.hardware_concurrency,
            device_memory=self.device_memory,
            screen_width=self.screen_width,
            screen_height=self.screen_height,
            color_depth=self.color_depth,
            pixel_ratio=self.pixel_ratio,
            webgl_renderer=self.webgl_renderer,
            canvas_seed=self.canvas_seed,
            font_spacing_seed=self.font_spacing_seed,
            platform=self.platform,
            browser_family=self.browser_family,
            success_count=self.success_count,
            failure_count=self.failure_count + 1,
        )


class FingerprintEvolution:
    """Evolve fingerprint configurations using a genetic algorithm.

    Usage::

        evo = FingerprintEvolution(population_size=50, seed=42)
        population = evo.seed_population()

        # After using fingerprints in real bypass attempts:
        evo.record_outcome("gene-abc", success=True)
        evo.record_outcome("gene-xyz", success=False)

        # Breed next generation:
        next_gen = evo.evolve()
    """

    def __init__(
        self,
        *,
        population_size: int = 50,
        mutation_rate: float = 0.05,
        elitism_count: int = 5,
        seed: int | None = None,
    ) -> None:
        self._population_size = population_size
        self._mutation_rate = mutation_rate
        self._elitism_count = min(elitism_count, population_size)
        self._rng = random.Random(seed)
        self._population: list[FingerprintGene] = []
        self._generation = 0
        self._gene_index: dict[str, int] = {}

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def population(self) -> list[FingerprintGene]:
        return list(self._population)

    def seed_population(self) -> list[FingerprintGene]:
        """Generate the initial random population."""
        self._population = [self._random_gene() for _ in range(self._population_size)]
        self._rebuild_index()
        self._generation = 0
        return list(self._population)

    def record_outcome(self, gene_id: str, *, success: bool) -> None:
        """Record a bypass attempt outcome for a gene."""
        idx = self._gene_index.get(gene_id)
        if idx is None:
            return
        gene = self._population[idx]
        self._population[idx] = gene.record_success() if success else gene.record_failure()

    def evolve(self) -> list[FingerprintGene]:
        """Run one generation: select → crossover → mutate → replace."""
        if not self._population:
            return self.seed_population()

        # Sort by fitness (descending).
        ranked = sorted(self._population, key=lambda g: g.fitness, reverse=True)

        # Elitism: keep top N unchanged.
        new_population: list[FingerprintGene] = []
        for elite in ranked[: self._elitism_count]:
            new_population.append(
                FingerprintGene(
                    gene_id=self._new_id(),
                    hardware_concurrency=elite.hardware_concurrency,
                    device_memory=elite.device_memory,
                    screen_width=elite.screen_width,
                    screen_height=elite.screen_height,
                    color_depth=elite.color_depth,
                    pixel_ratio=elite.pixel_ratio,
                    webgl_renderer=elite.webgl_renderer,
                    canvas_seed=elite.canvas_seed,
                    font_spacing_seed=elite.font_spacing_seed,
                    platform=elite.platform,
                    browser_family=elite.browser_family,
                )
            )

        # Fill the rest with crossover + mutation.
        parents = self.select_fittest()
        while len(new_population) < self._population_size:
            parent_a = self._rng.choice(parents)
            parent_b = self._rng.choice(parents)
            child = self.crossover(parent_a, parent_b)
            child = self.mutate(child)
            new_population.append(child)

        self._population = new_population[: self._population_size]
        self._rebuild_index()
        self._generation += 1
        return list(self._population)

    def select_fittest(self) -> list[FingerprintGene]:
        """Tournament selection: top 20% by fitness."""
        ranked = sorted(self._population, key=lambda g: g.fitness, reverse=True)
        cutoff = max(2, len(ranked) // 5)
        return ranked[:cutoff]

    def crossover(
        self,
        parent_a: FingerprintGene,
        parent_b: FingerprintGene,
    ) -> FingerprintGene:
        """Single-point crossover of two parent genes."""
        # Crossover point: randomly pick which attributes come from which parent.
        attrs = [
            "hardware_concurrency",
            "device_memory",
            "screen_width",
            "screen_height",
            "color_depth",
            "pixel_ratio",
            "webgl_renderer",
            "canvas_seed",
            "font_spacing_seed",
            "platform",
            "browser_family",
        ]
        point = self._rng.randint(1, len(attrs) - 1)
        child_attrs: dict[str, Any] = {"gene_id": self._new_id()}
        for i, attr in enumerate(attrs):
            source = parent_a if i < point else parent_b
            child_attrs[attr] = getattr(source, attr)
        return FingerprintGene(**child_attrs)

    def mutate(self, gene: FingerprintGene) -> FingerprintGene:
        """Random attribute mutations at the configured rate."""
        mutations: dict[str, Any] = {}
        if self._rng.random() < self._mutation_rate:
            mutations["hardware_concurrency"] = self._rng.choice(_HARDWARE_CONCURRENCY_POOL)
        if self._rng.random() < self._mutation_rate:
            mutations["device_memory"] = self._rng.choice(_DEVICE_MEMORY_POOL)
        if self._rng.random() < self._mutation_rate:
            w, h = self._rng.choice(_SCREEN_POOL)
            mutations["screen_width"] = w
            mutations["screen_height"] = h
        if self._rng.random() < self._mutation_rate:
            mutations["color_depth"] = self._rng.choice(_COLOR_DEPTH_POOL)
        if self._rng.random() < self._mutation_rate:
            mutations["pixel_ratio"] = self._rng.choice(_PIXEL_RATIO_POOL)
        if self._rng.random() < self._mutation_rate:
            mutations["webgl_renderer"] = self._rng.choice(_WEBGL_RENDERER_POOL)
        if self._rng.random() < self._mutation_rate:
            mutations["canvas_seed"] = self._rng.randint(0, 2**31)
        if self._rng.random() < self._mutation_rate:
            mutations["font_spacing_seed"] = self._rng.randint(0, 2**31)

        if not mutations:
            return gene

        return FingerprintGene(
            gene_id=gene.gene_id,
            hardware_concurrency=mutations.get("hardware_concurrency", gene.hardware_concurrency),
            device_memory=mutations.get("device_memory", gene.device_memory),
            screen_width=mutations.get("screen_width", gene.screen_width),
            screen_height=mutations.get("screen_height", gene.screen_height),
            color_depth=mutations.get("color_depth", gene.color_depth),
            pixel_ratio=mutations.get("pixel_ratio", gene.pixel_ratio),
            webgl_renderer=mutations.get("webgl_renderer", gene.webgl_renderer),
            canvas_seed=mutations.get("canvas_seed", gene.canvas_seed),
            font_spacing_seed=mutations.get("font_spacing_seed", gene.font_spacing_seed),
            platform=gene.platform,
            browser_family=gene.browser_family,
        )

    def best_gene(self) -> FingerprintGene | None:
        """Return the gene with highest fitness, or None if population is empty."""
        if not self._population:
            return None
        return max(self._population, key=lambda g: g.fitness)

    def _random_gene(self) -> FingerprintGene:
        """Generate a single random gene."""
        w, h = self._rng.choice(_SCREEN_POOL)
        return FingerprintGene(
            gene_id=self._new_id(),
            hardware_concurrency=self._rng.choice(_HARDWARE_CONCURRENCY_POOL),
            device_memory=self._rng.choice(_DEVICE_MEMORY_POOL),
            screen_width=w,
            screen_height=h,
            color_depth=self._rng.choice(_COLOR_DEPTH_POOL),
            pixel_ratio=self._rng.choice(_PIXEL_RATIO_POOL),
            webgl_renderer=self._rng.choice(_WEBGL_RENDERER_POOL),
            canvas_seed=self._rng.randint(0, 2**31),
            font_spacing_seed=self._rng.randint(0, 2**31),
            platform=self._rng.choice(_PLATFORM_POOL),
            browser_family=self._rng.choice(_BROWSER_FAMILY_POOL),
        )

    def _new_id(self) -> str:
        return uuid.UUID(int=self._rng.getrandbits(128), version=4).hex[:12]

    def _rebuild_index(self) -> None:
        self._gene_index = {g.gene_id: i for i, g in enumerate(self._population)}
