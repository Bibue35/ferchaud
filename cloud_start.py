"""Cloud entry point — runs trading bot + web server together."""
import os
import sys
import threading
import time
import signal


def run_bot():
    """Run the trading bot in a thread."""
    try:
        # Import and run main bot loop
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from main import main as bot_main
        bot_main()
    except Exception as e:
        print(f"[CLOUD] Bot error: {e}", flush=True)
        import traceback
        traceback.print_exc()


def run_web():
    """Run the web server."""
    import uvicorn
    port = int(os.getenv("PORT", 3000))
    print(f"[CLOUD] Starting web server on port {port}", flush=True)
    uvicorn.run("web.server:app", host="0.0.0.0", port=port)


def main():
    print("[CLOUD] Starting Ferchaud cloud deployment...", flush=True)
    print(f"[CLOUD] Trading mode: {os.getenv('TRADING_MODE', 'paper')}", flush=True)
    print(f"[CLOUD] Virtual cap: ${os.getenv('VIRTUAL_CAP', '5000')}", flush=True)

    # Start bot in background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True, name="TradingBot")
    bot_thread.start()
    print("[CLOUD] Trading bot started in background", flush=True)

    # Run web server in main thread (blocking)
    run_web()


if __name__ == "__main__":
    main()
