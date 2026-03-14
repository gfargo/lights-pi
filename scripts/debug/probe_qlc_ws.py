#!/usr/bin/env python3
"""
Probe QLC+ WebSocket to discover the exact message format for channel values.
Run on the Pi: python3 scripts/debug/probe_qlc_ws.py
"""
import asyncio
import time
import sys

try:
    import websockets
except ImportError:
    print("ERROR: websockets not installed. Run: pip3 install websockets")
    sys.exit(1)

QLC_WS = "ws://localhost:9999/qlcplusWS"

COMMANDS_TO_TRY = [
    "QLC+API|getChannelsValues|1|1|32",
    "QLC+API|getChannelsValues|0|1|32",
    "QLC+API|getChannelsValues|1|0|32",
    "QLC+API|getChannelValues",
    "getChannelsValues|1|1|32",
]

async def probe():
    print(f"Connecting to {QLC_WS} ...")
    try:
        async with websockets.connect(QLC_WS, open_timeout=5) as ws:
            print("Connected.\n")

            # First just listen for 2s to catch any unsolicited pushes
            print("--- Listening for unsolicited messages (2s) ---")
            deadline = time.time() + 2
            while time.time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.3)
                    print(f"  UNSOLICITED: {repr(msg[:300])}")
                except asyncio.TimeoutError:
                    pass

            # Try each command variant
            for cmd in COMMANDS_TO_TRY:
                print(f"\n--- Sending: {repr(cmd)} ---")
                await ws.send(cmd)
                deadline = time.time() + 2
                got = False
                while time.time() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                        print(f"  RESPONSE: {repr(msg[:300])}")
                        got = True
                    except asyncio.TimeoutError:
                        break
                if not got:
                    print("  (no response)")

            print("\n--- Done ---")
    except Exception as e:
        print(f"Connection error: {e}")

asyncio.run(probe())
