"""Fixed local TCP ingress. No credentials, dynamic targets, HTTP fetch, or dispatch."""
from __future__ import annotations

import asyncio
import os

TARGETS = {55439: ("db", 5432), 59999: ("auth", 9999), 8210: ("api", 8000),
           58029: ("mail", 8025)}
if os.environ.get("KAJH_GATEWAY_MODE") == "restore":
    TARGETS = {55440: ("restore-db", 5432)}
elif os.environ.get("KAJH_GATEWAY_MODE", "main") != "main":
    raise RuntimeError("Unknown fixed gateway mode")


async def relay(reader, writer):
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def accept(reader, writer, target):
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(asyncio.open_connection(*target), 5)
        tasks = [asyncio.create_task(relay(reader, upstream_writer)),
                 asyncio.create_task(relay(upstream_reader, writer))]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
    except (OSError, asyncio.TimeoutError):
        writer.close()


async def main():
    servers = []
    for port, target in TARGETS.items():
        async def callback(reader, writer, destination=target):
            await accept(reader, writer, destination)
        servers.append(await asyncio.start_server(callback, "0.0.0.0", port))
    await asyncio.gather(*(server.serve_forever() for server in servers))


if __name__ == "__main__":
    asyncio.run(main())
