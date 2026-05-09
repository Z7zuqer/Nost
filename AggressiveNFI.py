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
  # Leverage — 10x across all modes (parent default 3x)
  # ---------------------------------------------------------------------------
  futures_mode_leverage = 10.0
  futures_mode_leverage_rebuy_mode = 10.0
  futures_mode_leverage_grind_mode = 10.0

  # ---------------------------------------------------------------------------
  # TP / SL — calibrated for 10x leverage
  # ---------------------------------------------------------------------------
  # Effective price trigger = pct / leverage. At 10x:
  #   take_profit_pct = 0.50 → price moves +5% before TP fires
  #   stoploss        = -0.80 → price moves -8% before SL fires (~2% buffer to liq)
  take_profit_pct = 0.50
  stoploss = -0.80

  # ---------------------------------------------------------------------------
  # Stake sizing — rapid mode uses full slot stake (parent: 0.75)
  # ---------------------------------------------------------------------------
  rapid_mode_stake_multiplier_futures = [1.0]

  # ---------------------------------------------------------------------------
  # Shorts — DISABLED.
  # Empty dict so populate_entry_trend's loop has nothing to evaluate.
  # __init__ override below also flips can_short back to False after NFI
  # auto-sets it (L991 sets True whenever trading_mode=futures).
  # ---------------------------------------------------------------------------
  short_entry_signal_params = {}

  def __init__(self, config):
    super().__init__(config)
    self.can_short = False
