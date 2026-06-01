import asyncio
from pprint import pprint
from app.graph.backtester import run_backtest

async def run_simulation():
    print("Running backtest simulation for RELIANCE...")
    res = await run_backtest(
        symbol="RELIANCE",
        period="1mo",
        interval="1h",
        initial_capital=100000.0,
        stop_loss_pct=1.5,
        take_profit_pct=3.0,
        size_pct=10.0,
    )
    print("\nMETRICS:")
    pprint(res["metrics"])
    print(f"\nTOTAL TRADES: {len(res['trades'])}")
    if res["trades"]:
        print("\nFIRST TRADE:")
        pprint(res["trades"][0])
        print("\nLAST TRADE:")
        pprint(res["trades"][-1])
    print(f"\nEQUITY CURVE POINTS: {len(res['equity_curve'])}")

if __name__ == "__main__":
    asyncio.run(run_simulation())
