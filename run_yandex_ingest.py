import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, '/app')

from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.config import get_settings
from job_ftch.domain.source_spec import CareerSiteSpec


async def main():
    settings = get_settings()
    configs_dir = Path(settings.configs_dir or '.')
    
    # Load existing tenants
    tenants = load_tenants(configs_dir)
    tenant_id = 'ai_jobs'
    
    # Create runner
    runner = TenantRunner.from_tenants(tenants, base_settings=settings)
    
    # Get the user's profile (480637186)
    store = runner.get_runtime(tenant_id).store
    user_id = '480637186'
    profile_id = f'user_{user_id}'
    
    # Check if profile exists
    profile = await store.get_candidate_profile(user_id, profile_id)
    if profile:
        print(f'Profile found: {profile.profile_id}')
        sp = profile.profile.search_profiles[0] if profile.profile.search_profiles else None
        pos = len(sp.positive_example_texts) if sp else 0
        neg = len(sp.negative_example_texts) if sp else 0
        print(f'  Search profile: {pos}+ / {neg}-')
        if profile.profile.resume:
            print(f'  Resume: {profile.profile.resume.raw_text[:200]}...')
    else:
        print('ERROR: Profile not found!')
        return
    
    # List all sources for this tenant
    sources = await runner.list_sources(tenant_id)
    print(f'\nSources for {tenant_id}:')
    for s in sources:
        print(f'  - {s["source_id"]} (enabled={s["enabled"]})')
    
    # Check active candidate profiles
    active_ids = await store.get_active_candidate_profile_ids(user_id)
    print(f'\nActive profile IDs for user {user_id}: {active_ids}')
    
    print('\nAdding Yandex as a source...')
    
    # Add Yandex career site source
    yandex_spec = CareerSiteSpec(
        url='https://career.habr.com/ru/vacancies?employment=remote&q=python&qid=10&sort=date',
        source_name='yandex_remote_python',
    )
    
    try:
        result = await runner.add_source_spec(tenant_id, yandex_spec, added_via='debug_script')
        print(f'Added source: {result["source_id"]}')
    except Exception as e:
        print(f'Error adding source: {e}')
        # Check if already exists
        sources = await runner.list_sources(tenant_id)
        yandex_sources = [s for s in sources if 'yandex' in s['source_id'].lower()]
        if yandex_sources:
            print(f'Yandex source already exists: {yandex_sources}')
        else:
            raise
    
    # Run the tenant pipeline
    print(f'\nRunning pipeline for {tenant_id}...')
    summary = await runner.run_tenant(tenant_id)
    
    print(f'\nPipeline run completed:')
    print(f'  Jobs fetched: {summary.items_fetched}')
    print(f'  Jobs emitted: {summary.items_emitted}')
    print(f'  Jobs saved: {summary.items_saved}')
    print(f'  Rejected: {summary.items_rejected}')
    
    # Show job IDs if any
    if summary.items_saved > 0:
        jobs = await runner.latest_jobs(tenant_id, limit=5)
        print(f'\nLatest jobs:')
        for job in jobs:
            print(f'  - {job.title} @ {job.company} (score={job.quality_score:.2f})')


if __name__ == '__main__':
    asyncio.run(main())
