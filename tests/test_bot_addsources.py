import pytest
from unittest.mock import AsyncMock, MagicMock
from job_ftch.adapters.telegram_bot.bot import TelegramBotService
from job_ftch.domain.source_spec import TelegramChannelSourceSpec


@pytest.mark.asyncio
async def test_bot_addsources_command():
    runner = MagicMock()
    runner.tenant_ids.return_value = ["test_tenant"]
    
    # Mock get_runtime to return something with auth_provider
    runtime = MagicMock()
    runtime.auth_provider = MagicMock()
    runner.get_runtime.return_value = runtime
    
    sender = AsyncMock()
    config = MagicMock()
    config.admin_user_ids = [123]
    
    service = TelegramBotService(runner=runner, sender=sender, config=config)
    
    # Mock build_source_spec_from_input
    with MagicMock() as mock_build:
        import job_ftch.adapters.telegram_bot.bot as bot_module
        original_build = bot_module.build_source_spec_from_input
        bot_module.build_source_spec_from_input = AsyncMock(return_value=TelegramChannelSourceSpec(entity="test_chan"))
        
        try:
            await service.handle_command("/addsources test_tenant @test_chan @another_chan", chat_id=456, user_id=123)
            
            assert runner.add_source_spec.call_count == 2
            sender.send_message.assert_called_with(456, "Added 2 sources to test_tenant.")
        finally:
            bot_module.build_source_spec_from_input = original_build


@pytest.mark.asyncio
async def test_bot_addsources_partial_failure():
    runner = MagicMock()
    runner.tenant_ids.return_value = ["test_tenant"]
    
    runtime = MagicMock()
    runtime.auth_provider = MagicMock()
    runner.get_runtime.return_value = runtime
    
    sender = AsyncMock()
    config = MagicMock()
    config.admin_user_ids = [123]
    
    service = TelegramBotService(runner=runner, sender=sender, config=config)
    
    with MagicMock() as mock_build:
        import job_ftch.adapters.telegram_bot.bot as bot_module
        original_build = bot_module.build_source_spec_from_input
        
        async def mock_build_side_effect(link, **kwargs):
            if "fail" in link:
                raise ValueError("Invalid link")
            return TelegramChannelSourceSpec(entity=link)
            
        bot_module.build_source_spec_from_input = AsyncMock(side_effect=mock_build_side_effect)
        
        try:
            await service.handle_command("/addsources test_tenant @ok_chan @fail_chan", chat_id=456, user_id=123)
            
            assert runner.add_source_spec.call_count == 1
            args, kwargs = sender.send_message.call_args
            assert "Added 1 sources" in args[1]
            assert "Errors:" in args[1]
            assert "@fail_chan: Invalid link" in args[1]
        finally:
            bot_module.build_source_spec_from_input = original_build
