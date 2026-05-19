"""
FutureEdge Kill Switch CLI
"""

# ============================================================
# IMPORTS
# ============================================================

import asyncio
import sys

from services.kill_switch_service import (
    activate_kill_switch,
    release_kill_switch,
    get_kill_switch_status
)


# ============================================================
# HALT COMMAND
# ============================================================

async def halt():
    """
    Activate emergency halt.
    """

    await activate_kill_switch(
        duration_seconds=3600,
        reason="Manual operator halt"
    )

    print(
        "🛑 Trading halted for 1 hour"
    )


# ============================================================
# RESUME COMMAND
# ============================================================

async def resume():
    """
    Resume trading.
    """

    await release_kill_switch()

    print(
        "✅ Trading resumed"
    )


# ============================================================
# STATUS COMMAND
# ============================================================

async def status():
    """
    Show trading status.
    """

    result = await get_kill_switch_status()


    if result["halted"]:

        print(
            f"🛑 HALTED | "
            f"Reason: {result['reason']}"
        )

    else:

        print(
            "✅ Trading ACTIVE"
        )


# ============================================================
# MAIN CLI
# ============================================================

async def main():

    if len(sys.argv) < 2:

        print(
            "Usage:\n"
            "python scripts/kill_switch.py "
            "[halt|resume|status]"
        )

        return


    command = sys.argv[1]


    if command == "halt":

        await halt()


    elif command == "resume":

        await resume()


    elif command == "status":

        await status()


    else:

        print(
            f"Unknown command: {command}"
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    asyncio.run(main())