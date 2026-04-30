from NostalgiaForInfinityX7 import NostalgiaForInfinityX7


class MyNFI(NostalgiaForInfinityX7):
  def version(self) -> str:
    return super().version() + "-mine"

  # ---------------------------------------------------------------------------
  # Parameter overrides
  # ---------------------------------------------------------------------------
  # Only list values you actually want to differ from upstream.
  # Anything you don't override here will track upstream automatically when
  # nfi-updater pulls a new NostalgiaForInfinityX7.py.
  #
  # Examples (uncomment and edit):
  #
  # stop_threshold_spot = 0.08
  # stop_threshold_doom_spot = 0.15
  # grind_1_stakes_spot = [0.20, 0.22, 0.24]
  # rebuy_mode_min_free_slots = 3
  # is_futures_mode = True
  # futures_mode_leverage = 2.0

  # ---------------------------------------------------------------------------
  # Method overrides
  # ---------------------------------------------------------------------------
  # Override only the methods you want to change. Call super() to keep
  # upstream behavior and only layer your logic on top.
  #
  # Example:
  #
  # def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
  #     decision = super().custom_exit(pair, trade, current_time, current_rate, current_profit, **kwargs)
  #     # your logic here
  #     return decision
