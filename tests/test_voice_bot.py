import sys
import os

os.environ.setdefault("AUDIO_BUCKET", "test-bucket")

from unittest.mock import patch, MagicMock

# Patch boto3.client before importing code that uses it
with patch("boto3.client", return_value=MagicMock()):
    import pytest
    import discord
    from discord.ext import commands
    from unittest.mock import AsyncMock

    import cobot.voice_bot as voice_bot


@pytest.fixture
def bot():
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix='!', intents=intents)
    return bot


@pytest.mark.asyncio
async def test_depunctuate():
    assert voice_bot.depunctuate('Test-Name_123!') == 'testname123'
    assert voice_bot.depunctuate('Hello World!') == 'helloworld'


@pytest.mark.asyncio
async def test_get_fuzzy_match_scores():
    sounds = {'hello': 'hello', 'world': 'world'}
    scores = voice_bot.get_fuzzy_match_scores('helo', sounds)
    assert 'hello' in scores
    assert isinstance(scores['hello'], int)


@pytest.mark.asyncio
async def test_sound_name_to_filename():
    assert voice_bot.sound_name_to_filename('test') == 'test.ogg'


@pytest.mark.asyncio
async def test_voice_channel_autocomplete():
    interaction = MagicMock()
    interaction.guild = MagicMock()
    alpha = MagicMock()
    alpha.name = 'Alpha'
    alpha.id = 1
    bravo = MagicMock()
    bravo.name = 'Bravo'
    bravo.id = 2
    charlie = MagicMock()
    charlie.name = 'Charlie'
    charlie.id = 3
    interaction.guild.voice_channels = [alpha, bravo, charlie]
    result = await voice_bot.voice_channel_autocomplete(interaction, 'a')
    names = [c.name for c in result]
    assert 'Alpha' in names


@pytest.mark.asyncio
async def test_join_voice_channel_moves(monkeypatch):
    interaction = MagicMock()
    interaction.guild.voice_client = MagicMock()
    channel = MagicMock()
    interaction.guild.voice_client.move_to = AsyncMock()
    await voice_bot.join_voice_channel(interaction, channel)
    interaction.guild.voice_client.move_to.assert_awaited_with(channel)


@pytest.mark.asyncio
async def test_join_voice_channel_connect(monkeypatch):
    interaction = MagicMock()
    interaction.guild.voice_client = None
    channel = MagicMock()
    channel.connect = AsyncMock()
    await voice_bot.join_voice_channel(interaction, channel)
    channel.connect.assert_awaited()
