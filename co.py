import discord
import os
import os.path
import glob
from discord.ext import commands as dcmd
import json
import asyncio
import re


def get_mod_path():
    filepath = os.path.abspath(__file__)
    dirname, fname = os.path.split(filepath)
    return dirname


client = discord.Client()

audiodir = os.path.join(get_mod_path(), 'sounds')
description = 'Kernels of wisdom from fighter pilot legends.'
bot = dcmd.Bot(command_prefix='!co-', description=description)

print(f'{audiodir=}')

if not discord.opus.is_loaded():
    if not discord.opus._load_default():
        # the 'opus' library here is opus.dll on windows
        # or libopus.so on linux in the current directory
        # you should replace this with the location the
        # opus library is located in and with the proper filename.
        # note that on windows this DLL is automatically provided for you
        print("Unable to find default location of libopus-0.so... trying opus.dll")
        discord.opus.load_opus('opus')


@bot.command(description='Send friendly salutations to this bot.')
async def hello(ctx):
    author = ctx.author
    msg = 'Hello {}'.format(author.mention)
    await ctx.send(msg)


@bot.command(description='Solicit some wisdom from the CO.')
async def play(ctx, sound_name=''):
    if sound_name == '':
        msg = 'These are the topics I can tell you about:\n'
        msg += '```\n'
        msg += '\n'.join(sounds)
        msg += '```'
        await ctx.author.send(msg)
    else:
        await play_sound(ctx, sound_name)


async def join_channel(ctx, channel, errmsg=None):
    if ctx.voice_client is not None:
        try:
            await ctx.voice_client.move_to(channel)
        except dcmd.errors.BadArgument:
            if errmsg is not None:
                await ctx.send(errmsg)
    else:
        try:
            await channel.connect()
        except dcmd.errors.BadArgument:
            if errmsg is not None:
                await ctx.author.send(errmsg)


@bot.command(description="Join the given voice channel.")
async def join(ctx, *, channel: discord.VoiceChannel):
    """Joins a voice channel"""
    errmsg = "Can't find channel by the name of {}".format(channel)
    await join_channel(ctx, channel, errmsg)


@bot.command(description="Join the user's voice channel.")
async def summon(ctx):
    if ctx.author.voice and ctx.author.voice.channel:
        channel = ctx.author.voice.channel
        await join_channel(ctx, channel)
    else:
        await ctx.message.send("You are not connected to a voice channel")


@bot.command(description="Leave any connected voice channel.")
async def leave(ctx):
    try:
        await ctx.voice_client.disconnect()
    except AttributeError:
        await ctx.author.send("I'm not in any voice channel on this server!")


@bot.command(description="Stops any audio being played.")
async def stop(ctx):
    ctx.voice_client.stop()


async def get_volume(fname):
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-i", fname,
        "-af", "loudnorm=print_format=json",
        "-f", "null", "-",
        stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()
    # Lol ffmpeg doesn't meaningfully split output JSON from other junk.
    return json.loads("{" + stderr.decode().strip().split("{")[-1])

# Feed loudness measurements from the previous run into
# the one that actually plays the sound.
# Shoot for a (totally arbitrary; change me)
# -15 "integrated loudness target" and 0dB true peak
# https://ffmpeg.org/ffmpeg-filters.html#loudnorm


def filter_settings(loudness):
    crazy_ffmpeg_filter_lines = [
        "loudnorm=i=-15:tp=0:",
        f"measured_i={loudness['input_i']}:",
        f"measured_tp={loudness['input_tp']}:",
        f"measured_lra={loudness['input_lra']}:",
        f"measured_thresh={loudness['input_thresh']}:",
        "dual_mono=true:linear=true"
    ]
    return "".join(crazy_ffmpeg_filter_lines)


# Do some fuzzy search (case-insensitive, alphanumerics only)
def find_sound(name):
    # Get a alpha only, lowercase version of every sound name
    # and map it to the actual name
    def fuzzy_token(raw):
        re.sub(r'[\W_]+', '', raw).lower()

    # Could totally cache this instead of rebuilding it each time...
    fuzzmap = dict((fuzzy_token(s), f"{os.path.join(audiodir, s)}.ogg") for s in sounds)

    fuzzmap[fuzzy_token(name)]


async def play_sound(ctx, name):
    try:
        fname = find_sound(name)

        loudness = await get_volume(fname)
        audio_filter = filter_settings(loudness)

        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(
            source=fname,
            options="-af " + audio_filter
        ))
        try:
            ctx.voice_client.play(
                source, after=lambda e: print(
                    'Player error: %s' %
                    e) if e else None)
        except AttributeError:
            await ctx.author.send('I need to be in a voice channel to do that!')

    except KeyError:
        await ctx.author.send(f"I don't know anything about ```{name}```")

    except Exception as e:
        fmt = 'An error occurred while processing this request: ```py\n{}: {}\n```'
        await ctx.send(fmt.format(type(e).__name__, e))


@bot.event
async def on_ready():
    global sounds
    file_list = glob.glob('{}/*.ogg'.format(audiodir))
    sounds = [os.path.split(fname)[1][:-4] for fname in file_list]
    sounds = sorted(sounds)
    print('Logged in as')
    print(bot.user.name)
    print(bot.user.id)
    print('------')


if __name__ == '__main__':
    configfile = os.path.join(get_mod_path(), 'config.json')
    if not os.path.exists(configfile):
        print("Config file not found, please enter your auth token here:")
        token = input('--> ')
        token = token.strip()
        with open(configfile, 'w') as jsonconfig:
            json.dump({'token': token}, jsonconfig)

    with open(configfile, 'r') as jsonconfig:
        config = json.load(jsonconfig)
        token = config['token']
        if token == 'YOUR_TOKEN_HERE':
            raise ValueError("You must set a token in config.json.")
    bot.run(token)
