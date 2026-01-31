import asyncio
import time
import os
from unittest.mock import MagicMock, AsyncMock, patch
from registrarmonitor.automation.downloader import DataDownloader

# Mock data size (e.g., 200MB)
FILE_SIZE = 200 * 1024 * 1024
MOCK_CONTENT = b"0" * FILE_SIZE

async def heartbeat(stop_event, latencies):
    print("Heartbeat started")
    while not stop_event.is_set():
        start = time.perf_counter()
        await asyncio.sleep(0.001)  # 1ms sleep
        dt = time.perf_counter() - start
        latencies.append(dt)
    print("Heartbeat stopped")

async def run_benchmark():
    print(f"Benchmarking download with {FILE_SIZE/1024/1024}MB file...")

    mock_response = MagicMock()
    mock_response.content = MOCK_CONTENT
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get.return_value = mock_response

    latencies = []
    stop_event = asyncio.Event()

    with patch("httpx.AsyncClient", return_value=mock_client):
        downloader = DataDownloader()

        monitor_task = asyncio.create_task(heartbeat(stop_event, latencies))
        # Yield to let heartbeat start
        await asyncio.sleep(0)

        start_time = time.perf_counter()
        filename = await downloader.download()
        end_time = time.perf_counter()

        stop_event.set()
        await monitor_task

        if filename and os.path.exists(filename):
            os.remove(filename)

    total_time = end_time - start_time
    print(f"Latencies (count {len(latencies)}): {latencies[:10]} ...")
    max_latency = max(latencies) if latencies else 0
    blocking_overhead = max_latency - 0.001

    print(f"Total operation time: {total_time:.4f}s")
    print(f"Max event loop latency: {max_latency:.4f}s")
    if blocking_overhead > 0.05:
        print(f"⚠️  Event loop BLOCKED for ~{blocking_overhead:.4f}s during write!")
    else:
        print("✅ Event loop remained responsive.")

if __name__ == "__main__":
    asyncio.run(run_benchmark())
