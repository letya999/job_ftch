"""Tests for evolutionary fingerprint breeding (ADR-076)."""

from __future__ import annotations

from job_ftch.infrastructure.bypass.fingerprint_evolution import (
    FingerprintEvolution,
    FingerprintGene,
)


class TestFingerprintGene:
    """Test FingerprintGene data class."""

    def test_fitness_with_no_data_is_prior(self) -> None:
        gene = FingerprintGene(
            gene_id="test",
            hardware_concurrency=8,
            device_memory=8,
            screen_width=1920,
            screen_height=1080,
            color_depth=24,
            pixel_ratio=1.0,
            webgl_renderer="ANGLE",
            canvas_seed=1,
            font_spacing_seed=1,
            platform="windows",
            browser_family="chromium",
        )
        assert gene.fitness == 0.5

    def test_fitness_with_all_successes(self) -> None:
        gene = FingerprintGene(
            gene_id="test",
            hardware_concurrency=8,
            device_memory=8,
            screen_width=1920,
            screen_height=1080,
            color_depth=24,
            pixel_ratio=1.0,
            webgl_renderer="ANGLE",
            canvas_seed=1,
            font_spacing_seed=1,
            platform="windows",
            browser_family="chromium",
            success_count=10,
            failure_count=0,
        )
        # Laplace: (10+1)/(10+2) = 11/12 ≈ 0.917
        assert gene.fitness > 0.9

    def test_fitness_with_all_failures(self) -> None:
        gene = FingerprintGene(
            gene_id="test",
            hardware_concurrency=8,
            device_memory=8,
            screen_width=1920,
            screen_height=1080,
            color_depth=24,
            pixel_ratio=1.0,
            webgl_renderer="ANGLE",
            canvas_seed=1,
            font_spacing_seed=1,
            platform="windows",
            browser_family="chromium",
            success_count=0,
            failure_count=10,
        )
        # Laplace: (0+1)/(10+2) = 1/12 ≈ 0.083
        assert gene.fitness < 0.1

    def test_record_success_increments(self) -> None:
        gene = FingerprintGene(
            gene_id="test",
            hardware_concurrency=8,
            device_memory=8,
            screen_width=1920,
            screen_height=1080,
            color_depth=24,
            pixel_ratio=1.0,
            webgl_renderer="ANGLE",
            canvas_seed=1,
            font_spacing_seed=1,
            platform="windows",
            browser_family="chromium",
        )
        updated = gene.record_success()
        assert updated.success_count == 1
        assert updated.failure_count == 0

    def test_record_failure_increments(self) -> None:
        gene = FingerprintGene(
            gene_id="test",
            hardware_concurrency=8,
            device_memory=8,
            screen_width=1920,
            screen_height=1080,
            color_depth=24,
            pixel_ratio=1.0,
            webgl_renderer="ANGLE",
            canvas_seed=1,
            font_spacing_seed=1,
            platform="windows",
            browser_family="chromium",
        )
        updated = gene.record_failure()
        assert updated.success_count == 0
        assert updated.failure_count == 1


class TestFingerprintEvolution:
    """Test the genetic algorithm."""

    def test_seed_population_correct_size(self) -> None:
        evo = FingerprintEvolution(population_size=20, seed=42)
        pop = evo.seed_population()
        assert len(pop) == 20

    def test_seed_population_deterministic(self) -> None:
        evo1 = FingerprintEvolution(population_size=10, seed=42)
        evo2 = FingerprintEvolution(population_size=10, seed=42)
        pop1 = evo1.seed_population()
        pop2 = evo2.seed_population()
        for g1, g2 in zip(pop1, pop2, strict=True):
            assert g1.hardware_concurrency == g2.hardware_concurrency
            assert g1.webgl_renderer == g2.webgl_renderer

    def test_record_outcome_updates_fitness(self) -> None:
        evo = FingerprintEvolution(population_size=10, seed=42)
        pop = evo.seed_population()
        gene_id = pop[0].gene_id
        evo.record_outcome(gene_id, success=True)
        evo.record_outcome(gene_id, success=True)
        updated = evo.population[0]
        assert updated.success_count == 2
        assert updated.fitness > 0.5

    def test_evolve_produces_new_generation(self) -> None:
        evo = FingerprintEvolution(population_size=20, seed=42)
        evo.seed_population()
        assert evo.generation == 0
        evo.evolve()
        assert evo.generation == 1

    def test_evolve_preserves_elite(self) -> None:
        evo = FingerprintEvolution(
            population_size=20,
            elitism_count=3,
            seed=42,
        )
        pop = evo.seed_population()
        # Make first 3 genes very fit.
        for gene in pop[:3]:
            for _ in range(10):
                evo.record_outcome(gene.gene_id, success=True)
        elite_configs = [
            (g.hardware_concurrency, g.webgl_renderer)
            for g in sorted(evo.population, key=lambda g: g.fitness, reverse=True)[:3]
        ]
        evo.evolve()
        new_configs = [(g.hardware_concurrency, g.webgl_renderer) for g in evo.population[:3]]
        # Elite configurations should be preserved.
        assert elite_configs == new_configs

    def test_crossover_combines_parents(self) -> None:
        evo = FingerprintEvolution(seed=42)
        evo.seed_population()
        parent_a = evo.population[0]
        parent_b = evo.population[1]
        child = evo.crossover(parent_a, parent_b)
        # Child should have attributes from both parents.
        assert child.gene_id not in {parent_a.gene_id, parent_b.gene_id}

    def test_mutate_can_change_attributes(self) -> None:
        evo = FingerprintEvolution(mutation_rate=1.0, seed=42)
        evo.seed_population()
        gene = evo.population[0]
        mutated = evo.mutate(gene)
        # With 100% mutation rate, at least something should change.
        changed = (
            mutated.hardware_concurrency != gene.hardware_concurrency
            or mutated.device_memory != gene.device_memory
            or mutated.screen_width != gene.screen_width
            or mutated.webgl_renderer != gene.webgl_renderer
        )
        assert changed

    def test_best_gene_returns_highest_fitness(self) -> None:
        evo = FingerprintEvolution(population_size=10, seed=42)
        pop = evo.seed_population()
        # Make one gene very successful.
        target_id = pop[5].gene_id
        for _ in range(20):
            evo.record_outcome(target_id, success=True)
        best = evo.best_gene()
        assert best is not None
        assert best.gene_id == target_id

    def test_generation_increments(self) -> None:
        evo = FingerprintEvolution(population_size=10, seed=42)
        evo.seed_population()
        for _ in range(5):
            evo.evolve()
        assert evo.generation == 5

    def test_population_size_stays_constant(self) -> None:
        evo = FingerprintEvolution(population_size=30, seed=42)
        evo.seed_population()
        for _ in range(3):
            evo.evolve()
        assert len(evo.population) == 30

    def test_empty_population_best_gene_returns_none(self) -> None:
        evo = FingerprintEvolution(seed=42)
        assert evo.best_gene() is None

    def test_record_outcome_unknown_gene_is_noop(self) -> None:
        evo = FingerprintEvolution(population_size=5, seed=42)
        evo.seed_population()
        evo.record_outcome("nonexistent", success=True)
        # No crash, no change.
        assert all(g.success_count == 0 for g in evo.population)
