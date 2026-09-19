"""Stream a trading process without giving it ownership of the UI terminal."""
import asyncio
import json
import signal
import sys
from dataclasses import asdict
from pathlib import Path

READY = 'BUDA_ORDER_READY'


class OrderProcess:
    def __init__(self):
        self.process = None
        self.ready = False
        self.stop_requested = False
        self.stop_sent = False

    def stop(self):
        self.stop_requested = True
        if self.process is not None and self.ready and not self.stop_sent:
            if self.process.returncode is None:
                try:
                    self.process.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass
            self.stop_sent = True

    async def run(self, params, market, on_line, *, command=None):
        execution = asyncio.create_task(self._run(params, market, on_line, command))
        try:
            return await asyncio.shield(execution)
        except asyncio.CancelledError:
            # A cancelled UI worker must still wait for the bot's cleanup.
            self.stop()
            await asyncio.shield(execution)
            raise

    async def _run(self, params, market, on_line, command):
        command = command or [sys.executable, '-u', '-m', 'src.tui.order_runner']
        self.process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=Path(__file__).resolve().parents[2], start_new_session=True,
        )
        payload = json.dumps({'params': params, 'market': asdict(market)}, default=str)
        self.process.stdin.write(payload.encode())
        await self.process.stdin.drain()
        self.process.stdin.close()
        return await self._read_output(on_line)

    async def _read_output(self, on_line):
        display_error = None
        while True:
            try:
                line = await self.process.stdout.readline()
            except ValueError as error:
                # StreamReader discards oversized lines. Keep draining so the
                # child can finish cleanup without blocking on a full pipe.
                display_error = display_error or error
                self.stop()
                continue
            if not line:
                break
            text = line.decode(errors='replace').rstrip('\r\n')
            if text == READY:
                self.ready = True
                if self.stop_requested:
                    self.stop()
            elif display_error is None:
                try:
                    on_line(text)
                except Exception as error:
                    display_error = error
                    self.stop()
        code = await self.process.wait()
        if display_error is not None:
            raise display_error
        return code
