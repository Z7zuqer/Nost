from MyNFI import MyNFI


class AggressiveNFI(MyNFI):
  """Aggressive variant of MyNFI: 10x leverage, more signals, shorts enabled.

  Inherits MyNFI for the exchange-side TP machinery (order_filled hook,
  bot_loop_start reconciliation, _place_exchange_tp helper, etc.) — only
  the parameter overrides differ.
  """

  def version(self) -> str:
    # Skip MyNFI's "-mine" suffix; use our own marker.
    from NostalgiaForInfinityX7 import NostalgiaForInfinityX7
    return NostalgiaForInfinityX7.version(self) + "-aggressive"

  # ---------------------------------------------------------------------------
  # Leverage — 10x across all modes (parent default 3x, MyNFI used 5x)
  # ---------------------------------------------------------------------------
  futures_mode_leverage = 10.0
  futures_mode_leverage_rebuy_mode = 10.0
  futures_mode_leverage_grind_mode = 10.0

  # ---------------------------------------------------------------------------
  # TP / SL — recalibrated for 10x leverage
  # ---------------------------------------------------------------------------
  # Effective price trigger = pct / leverage. At 10x:
  #   take_profit_pct = 0.50 → price moves +5%  before TP fires
  #   stoploss        = -0.80 → price moves -8% before SL fires
  take_profit_pct = 0.50
  stoploss = -0.80

  # ---------------------------------------------------------------------------
  # Stake sizing — rapid mode uses full slot stake (parent: 0.75)
  # ---------------------------------------------------------------------------
  rapid_mode_stake_multiplier_futures = [1.0]

  # ---------------------------------------------------------------------------
  # Shorts — NFI auto-sets self.can_short=True when trading_mode=futures.
  # Parent only enables 3 short signals by default (501, 502, 542).
  # Aggressive variant enables every short signal NFI ships with.
  # Verified at NostalgiaForInfinityX7.py L23294 — populate_entry_trend
  # iterates this dict and evaluates each enabled signal's logic.
  # ---------------------------------------------------------------------------
  short_entry_signal_params = {
    "short_entry_condition_501_enable": True,
    "short_entry_condition_502_enable": True,
    "short_entry_condition_503_enable": True,
    "short_entry_condition_504_enable": True,
    "short_entry_condition_541_enable": True,
    "short_entry_condition_542_enable": True,
    "short_entry_condition_543_enable": True,
    "short_entry_condition_603_enable": True,
    "short_entry_condition_641_enable": True,
    "short_entry_condition_642_enable": True,
    "short_entry_condition_661_enable": True,
  }
