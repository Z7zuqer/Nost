import logging

from NostalgiaForInfinityX7 import NostalgiaForInfinityX7

logger = logging.getLogger(__name__)


class MyNFI(NostalgiaForInfinityX7):
  def version(self) -> str:
    return super().version() + "-mine"

  # ---------------------------------------------------------------------------
  # Parameter overrides
  # ---------------------------------------------------------------------------

  # Align with FREQTRADE__TRADING_MODE=futures in .env
  is_futures_mode = True

  # 5x leverage across all modes (parent default: 3x). NFI.leverage() picks
  # the rebuy/grind variant for those modes, so all three need to match.
  futures_mode_leverage = 5.0
  futures_mode_leverage_rebuy_mode = 5.0
  futures_mode_leverage_grind_mode = 5.0

  # +30% take-profit on equity (leverage-adjusted to price below).
  take_profit_pct = 0.30

  # -40% hard stop. Delegated to freqtrade's stoploss_on_exchange — it places,
  # tracks, and cancels the SL order automatically across DCA / restarts.
  # (The TP at +30% is placed separately by _place_exchange_tp() below.)
  stoploss = -0.40
  use_custom_stoploss = False
  order_types = {
    "entry": "limit",
    "exit": "limit",
    "stoploss": "market",
    "stoploss_on_exchange": True,
    "stoploss_on_exchange_interval": 15,
  }

  # ---------------------------------------------------------------------------
  # Method overrides — exchange-side TP management
  # ---------------------------------------------------------------------------

  def order_filled(self, pair, trade, order, current_time, **kwargs):
    super().order_filled(pair, trade, order, current_time, **kwargs)
    # Only react to entry fills (initial entry or DCA top-up).
    if order.ft_order_side != trade.entry_side:
      return
    self._place_exchange_tp(pair, trade)

  def confirm_trade_exit(self, pair, trade, order_type, amount, rate,
                         time_in_force, exit_reason, current_time, **kwargs):
    # Cancel exchange-side TP before freqtrade pushes its own exit order,
    # otherwise the leftover TP can fire after the position is already flat.
    self._cancel_exchange_tp(trade)
    return super().confirm_trade_exit(
      pair, trade, order_type, amount, rate, time_in_force,
      exit_reason, current_time, **kwargs,
    )

  def bot_loop_start(self, current_time, **kwargs):
    super().bot_loop_start(current_time, **kwargs)
    if self.config.get("dry_run"):
      return
    # Reconciliation: after restart or if a TP got cancelled externally,
    # ensure every open trade has a live TP on the exchange.
    try:
      from freqtrade.persistence import Trade
      for t in Trade.get_open_trades():
        if not self._exchange_tp_alive(t):
          self._place_exchange_tp(t.pair, t)
    except Exception as e:
      logger.warning(f"TP reconciliation failed: {e}")

  # ---------------------------------------------------------------------------
  # Helpers
  # ---------------------------------------------------------------------------

  def _place_exchange_tp(self, pair, trade):
    if self.config.get("dry_run"):
      return
    if not self.is_futures_mode:
      logger.warning("Exchange TP requires futures mode; skipping for %s", pair)
      return

    # Always cancel any previous TP first — DCA changes the average entry price.
    self._cancel_exchange_tp(trade)

    leverage = max(float(trade.leverage or 1.0), 1.0)
    avg_entry = float(trade.open_rate)
    if not trade.is_short:
      raw_price = avg_entry * (1.0 + self.take_profit_pct / leverage)
      side = "sell"
    else:
      raw_price = avg_entry * (1.0 - self.take_profit_pct / leverage)
      side = "buy"

    tp_price = float(self.dp._exchange.price_to_precision(pair, raw_price))

    try:
      order = self.dp._exchange._api.create_order(
        symbol=pair,
        type="TAKE_PROFIT_MARKET",
        side=side,
        amount=None,
        params={
          "stopPrice": tp_price,
          "closePosition": True,
          "reduceOnly": True,
          "workingType": "MARK_PRICE",
        },
      )
      trade.set_custom_data("tp_order_id", order["id"])
      logger.info(f"Placed TP for {pair}: id={order['id']} stopPrice={tp_price}")
    except Exception as e:
      logger.error(f"Failed to place TP for {pair}: {e}")

  def _cancel_exchange_tp(self, trade):
    if self.config.get("dry_run"):
      return
    order_id = trade.get_custom_data("tp_order_id")
    if not order_id:
      return
    try:
      self.dp._exchange._api.cancel_order(order_id, trade.pair)
    except Exception as e:
      msg = str(e).lower()
      if any(x in msg for x in ("unknown order", "not found", "does not exist")):
        pass  # Already filled or cancelled — nothing to do.
      else:
        logger.warning(f"Failed to cancel TP for {trade.pair}: {e}")
    trade.set_custom_data("tp_order_id", None)

  def _exchange_tp_alive(self, trade):
    order_id = trade.get_custom_data("tp_order_id")
    if not order_id:
      return False
    try:
      o = self.dp._exchange._api.fetch_order(order_id, trade.pair)
      return o.get("status") in ("open", "new")
    except Exception:
      return False
