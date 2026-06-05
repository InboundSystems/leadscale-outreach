"""
One-shot script: temporarily raises send limits and window for today's catch-up
run (45 emails = yesterday's 5 shortfall + today's 40), then restores config.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import importlib
import beauty_outreach.config as cfg
import beauty_outreach.sender as sender_mod
import beauty_outreach.campaign as campaign_mod

# --- patch limits in-memory ---
original_limit   = cfg.DAILY_SEND_LIMIT
original_window  = cfg.SEND_WINDOW_END
patched_limit    = 45
patched_window   = 20   # 8 PM local — enough headroom for ~45 emails

cfg.DAILY_SEND_LIMIT  = patched_limit
cfg.SEND_WINDOW_END   = patched_window
sender_mod.DAILY_SEND_LIMIT  = patched_limit
sender_mod.SEND_WINDOW_END   = patched_window

# Also patch campaign ramp ceiling
original_ramp = campaign_mod.CAMPAIGN_RAMP[:]
campaign_mod.CAMPAIGN_RAMP[-1] = (None, patched_limit)

print(f"[config] DAILY_SEND_LIMIT → {patched_limit}  |  SEND_WINDOW_END → {patched_window}:00")
print(f"[config] Yesterday shortfall: 5  |  Today quota: 40  |  Total target: {patched_limit}")
print("-" * 56)

try:
    from beauty_outreach.campaign import run_campaign_session
    result = run_campaign_session()
    print("-" * 56)
    print(f"  Initial emails sent: {result['initial_sent']}")
    print(f"  Follow-ups sent:     {result['followups_sent']}")
    print(f"  Total sent:          {result['total_sent']}")
    print(f"  Remaining in queue:  {result['remaining_in_queue']}")
    if result.get("skipped_reason"):
        print(f"  Note: {result['skipped_reason']}")
finally:
    # Always restore
    cfg.DAILY_SEND_LIMIT         = original_limit
    cfg.SEND_WINDOW_END          = original_window
    sender_mod.DAILY_SEND_LIMIT  = original_limit
    sender_mod.SEND_WINDOW_END   = original_window
    campaign_mod.CAMPAIGN_RAMP   = original_ramp
    print("-" * 56)
    print("[config] Limits restored to originals.")
