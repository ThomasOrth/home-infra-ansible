import os
import re
import time
import asyncio
import logging
import telnetlib

from typing import Tuple, Generator
from contextlib import contextmanager

import discord
from discord.ext import commands

STATUS_PREFIXES = {
    "server_time": "🕒 **Server Time:**",
    "players": "👥 **Players Online:**",
}

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
DISCORD_MESSAGE_ID = int(os.getenv("DISCORD_MESSAGE_ID", "0"))
TELNET_HOST = os.getenv("TELNET_HOST", "127.0.0.1")
TELNET_PORT = int(os.getenv("TELNET_PORT", 8081))
TELNET_PASSWORD = os.getenv("TELNET_PASSWORD")
TELNET_TIMEOUT = int(os.getenv("TELNET_TIMEOUT", 10))
UPDATE_INTERVAL = int(os.getenv("UPDATE_INTERVAL", 60))


class ServerTime:
    """Represents the in-game time of a 7 Days to Die server."""

    def __init__(self, day: int, hour: int, minute: int):
        self.day = day
        self.hour = hour
        self.minute = minute

    def __str__(self) -> str:
        return f"Day {self.day}, {self.hour:02}:{self.minute:02}"

    @classmethod
    def from_string(cls, time_str: str) -> "ServerTime":
        """Parse a time string like 'Day 1, 12:34' into a ServerTime object."""
        pattern = r"Day (\d+), (\d{2}):(\d{2})"
        match = re.search(pattern, time_str)
        if match:
            day = int(match.group(1))
            hour = int(match.group(2))
            minute = int(match.group(3))
            return cls(day, hour, minute)
        raise ValueError(f"Invalid time format: {time_str}")

    @property
    def is_blood_moon_day(self) -> bool:
        """Check if the current day is a blood moon (every 7th day)."""
        return self.day % 7 == 0

    @property
    def is_blood_moon_night(self) -> bool:
        """Check if the current time is during a blood moon night."""
        return (self.is_blood_moon_day and self.hour >= 22) or (
            not self.day % 7 == 1 and self.hour < 4
        )

    @property
    def next_blood_moon(self) -> "ServerTime":
        """Calculate the next blood moon day and time."""
        next_blood_moon_day = (self.day // 7 + 1) * 7
        return ServerTime(next_blood_moon_day, 22, 0)

    @property
    def blood_moon_state(self) -> str:
        """Return a string indicating the blood moon state."""
        if self.is_blood_moon_day:
            return "🧟 Today"
        elif self.is_blood_moon_night:
            return "🔥Now!🔥"
        else:
            return f"Day {self.next_blood_moon.day}"


@contextmanager
def telnet_connection() -> Generator[telnetlib.Telnet, None, None]:
    """Yield an authenticated Telnet connection to the 7d2d server."""
    tn: telnetlib.Telnet | None = None
    try:
        tn = telnetlib.Telnet(TELNET_HOST, TELNET_PORT, TELNET_TIMEOUT)
        time.sleep(0.5)  # Give server time to respond
        if TELNET_PASSWORD:
            tn.read_until(b"Please enter password:", TELNET_TIMEOUT)
            tn.write(TELNET_PASSWORD.encode() + b"\n")
        # Discard banner/prompt after login
        tn.read_very_eager()
        yield tn
    finally:
        if tn is not None:
            tn.close()


def query_7d2d(tn: telnetlib.Telnet, query: str) -> str:
    """Send a query to the 7 Days to Die server and return the response."""
    tn.write(query.encode() + b"\n")
    time.sleep(0.5)  # Allow some time for the server to respond
    response = tn.read_very_eager().decode(errors="ignore").strip()
    return response


def query_7d2d_time(tn: telnetlib.Telnet) -> ServerTime | None:
    pattern = r"Day (\d+), (\d{2}):(\d{2})"
    response = query_7d2d(tn, "gettime")
    match = re.search(pattern, response)
    server_time = None
    if match:
        day = int(match.group(1))
        hour = int(match.group(2))
        minute = int(match.group(3))
        server_time = ServerTime(day, hour, minute)
    return server_time


def query_7d2d_players(tn: telnetlib.Telnet) -> int | None:
    pattern = r"Total of (\d+)\s+in the game"
    response = query_7d2d(tn, "lp")
    match = re.search(pattern, response)
    player_count = None
    if match:
        player_count = int(match.group(1))
    return player_count


def query_7d2d_status() -> Tuple[ServerTime | None, int | None]:
    try:
        with telnet_connection() as tn:
            return query_7d2d_time(tn), query_7d2d_players(tn)
    except Exception as exc:
        logging.exception("Telnet query failed")
        return None, None


def generate_status_text(time: ServerTime | None, players: int | None) -> str:
    players_str = str(players) if players is not None and players >= 0 else "N/A"
    time_str = (
        f"{time} *(next Horde Night: {time.blood_moon_state})*" if time else "N/A"
    )
    response = []
    response.append(f"{STATUS_PREFIXES.get('players')} {players_str}")
    response.append(f"{STATUS_PREFIXES.get('server_time')} {time_str}")
    return "\n".join(response)


async def status_loop(message: discord.Message):
    """Edit the specified message indefinitely with updated time/player info."""
    while True:
        time, players = await asyncio.get_event_loop().run_in_executor(
            None, query_7d2d_status
        )
        content = generate_status_text(time, players)
        try:
            await message.edit(content=content)
        except Exception:
            logging.warning("Failed to edit message with ID %s", DISCORD_MESSAGE_ID)
        await asyncio.sleep(UPDATE_INTERVAL)


def register_bot_events(bot: commands.Bot):
    @bot.event
    async def on_ready():
        logging.info("Logged in as %s (ID %s)", bot.user, bot.user.id)
        channel = bot.get_channel(DISCORD_CHANNEL_ID)
        if channel is None:
            logging.error(
                "Channel ID %s not found or bot has no access", DISCORD_CHANNEL_ID
            )
            return
        try:
            message = await channel.fetch_message(DISCORD_MESSAGE_ID)
        except Exception:
            logging.error("Failed to fetch message ID %s", DISCORD_MESSAGE_ID)
            return
        # Launch background status updater
        bot.loop.create_task(status_loop(message))

    @bot.command(name="status", help="Immediate snapshot of time & players")
    async def status_cmd(ctx: commands.Context):
        time, players = await asyncio.get_event_loop().run_in_executor(
            None, query_7d2d_status
        )
        content = generate_status_text(time, players)
        await ctx.send(content)


def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN must be set")
    if DISCORD_CHANNEL_ID == 0:
        raise RuntimeError("DISCORD_CHANNEL_ID must be set (ID of the status channel)")
    if DISCORD_MESSAGE_ID == 0:
        raise RuntimeError(
            "DISCORD_MESSAGE_ID must be set (ID of the message to update)"
        )

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)
    register_bot_events(bot)
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
