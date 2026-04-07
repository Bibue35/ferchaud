"""Cloud entry point — runs web server + trading bot together.
Web server starts FIRST (for healthcheck), bot starts after."""
import os
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run_bot():
    """Run the trading bot in a background thread — delayed start."""
    time.sleep(10)  # Wait for web server to be ready
    print("[CLOUD] Starting trading bot...", flush=True)
    try:
        from main import main as bot_main
        bot_main()
    except Exception as e:
        print(f"[CLOUD] Bot error (non-fatal): {e}", flush=True)
        traceback.print_exc()
        # Don't crash — web server keeps running


def main():
    port = int(os.getenv("PORT", "3000"))
    print(f"[CLOUD] Ferchaud starting on port {port}", flush=True)
    print(f"[CLOUD] Trading mode: {os.getenv('TRADING_MODE', 'paper')}", flush=True)

    # Start bot in daemon thread (won't block healthcheck)
    bot_thread = threading.Thread(target=run_bot, daemon=True, name="TradingBot")
    bot_thread.start()

    # Start web server IMMEDIATELY (main thread, blocking)
    import uvicorn
    uvicorn.run("web.server:app", host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
