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
    while chunk := await reader.read(65536):
        writer.write(chunk)
        await writer.drain()
    # A client may finish its request with a TCP half-close and still expect
    # the upstream response. Forward the FIN without closing the read side.
    if writer.can_write_eof():
        writer.write_eof()
        await writer.drain()


async def accept(reader, writer, target):
    upstream_writer = None
    tasks = []
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(asyncio.open_connection(*target), 5)
        tasks = [asyncio.create_task(relay(reader, upstream_writer)),
                 asyncio.create_task(relay(upstream_reader, writer))]
        await asyncio.gather(*tasks)
    except (OSError, asyncio.TimeoutError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        writers = [writer] + ([upstream_writer] if upstream_writer is not None else [])
        for stream in writers:
            stream.close()
        await asyncio.gather(*(stream.wait_closed() for stream in writers), return_exceptions=True)


async def main():
    servers = []
    for port, target in TARGETS.items():
        async def callback(reader, writer, destination=target):
            await accept(reader, writer, destination)
        servers.append(await asyncio.start_server(callback, "0.0.0.0", port))
    await asyncio.gather(*(server.serve_forever() for server in servers))


if __name__ == "__main__":
    asyncio.run(main())
