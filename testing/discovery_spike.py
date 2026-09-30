import argparse
import asyncio
import socket
import uuid

from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

SERVICE_TYPE = "_connector._tcp.local."


def get_lan_ip() -> str:
    # Connecting a UDP socket sends nothing. It only makes the OS pick the
    # network interface it would use to reach that address.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]


def decode_properties(raw: dict[bytes, bytes | None]) -> dict[str, str]:
    # TXT records arrive as bytes on both sides, e.g. {b"name": b"Bob"}.
    return {k.decode(): (v.decode() if v is not None else "") for k, v in raw.items()}


async def resolve(
    zc: Zeroconf, service_type: str, name: str, label: str, peers: dict[str, str]
) -> None:
    info = AsyncServiceInfo(service_type, name)
    if not await info.async_request(zc, 3000):
        print(f"{'Failed':<8} {name} did not answer within 3 s")
        return

    props = decode_properties(info.properties)
    display_name = props.get("name", name)
    peers[name] = display_name

    addresses = ", ".join(info.parsed_addresses())
    print(f"{label:<8} {display_name:<10} {addresses}:{info.port}  v={props.get('v', '?')}")


async def main() -> None:
    # 1. parse --name and --port
    parser = argparse.ArgumentParser(description="Connector discovery spike")
    parser.add_argument("--name", required=True, help="display name shown to others")
    parser.add_argument("--port", type=int, required=True, help="port to advertise")
    args = parser.parse_args()

    # 2. build a unique instance name: display name + short random suffix + SERVICE_TYPE
    instance_name = f"{args.name}-{uuid.uuid4().hex[:6]}.{SERVICE_TYPE}"
    ip = get_lan_ip()

    print(f"Name:     {args.name}")
    print(f"Instance: {instance_name}")
    print(f"Address:  {ip}:{args.port}")

    # 3. build an AsyncServiceInfo describing yourself
    info = AsyncServiceInfo(
        SERVICE_TYPE,
        instance_name,
        port=args.port,
        properties={"v": "1", "name": args.name},
        parsed_addresses=[ip],
    )

    peers: dict[str, str] = {}  # instance name -> display name
    tasks: set[asyncio.Task[None]] = set()  # keeps lookups alive until they finish

    # Defined inside main() so it can see instance_name, peers and tasks (a closure).
    def on_change(
        zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        if name == instance_name:
            return

        if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
            # The handler can't await, so the lookup runs as its own task.
            task = asyncio.create_task(
                resolve(zeroconf, service_type, name, state_change.name, peers)
            )
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        elif state_change is ServiceStateChange.Removed:
            print(f"{'Removed':<8} {peers.pop(name, name)}")

    # 4. create an AsyncZeroconf and register the service
    aiozc = AsyncZeroconf()
    browser = None
    try:
        await aiozc.async_register_service(info)
        print("Registered. Watching for other laptops, Ctrl+C to stop.")

        # 5. start an AsyncServiceBrowser with on_change as the handler
        browser = AsyncServiceBrowser(aiozc.zeroconf, [SERVICE_TYPE], handlers=[on_change])

        # 6. wait forever
        await asyncio.Event().wait()
    finally:
        # 7. finally: unregister and close
        if browser is not None:
            await browser.async_cancel()
        await aiozc.async_unregister_all_services()
        await aiozc.async_close()
        print("Unregistered and closed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
