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

  # Take-profit and stop-loss expressed as equity-ratio (freqtrade convention).
  # Effective price trigger = pct / leverage. At 5x leverage:
  #   take_profit_pct = 0.50 → price moves +10% before TP fires
  #   stoploss        = -0.60 → price moves -12% before SL fires
  take_profit_pct = 0.50

  # SL delegated to freqtrade's stoploss_on_exchange — it places, tracks, and
  # cancels the SL order automatically across DCA / restarts.
  # (TP is placed separately by _place_exchange_tp() below.)
  stoploss = -0.60
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

  # One-time flag: cleanup orphan TPs from previous broken runs at first cycle
  _startup_cleanup_done = False

  def bot_loop_start(self, current_time, **kwargs):
    super().bot_loop_start(current_time, **kwargs)
    if self.config.get("dry_run"):
      return
    try:
      from freqtrade.persistence import Trade
      open_trades = Trade.get_open_trades()
      # First-cycle cleanup: cancel ALL exchange-side TPs and reset stored IDs.
      # Existing trades from a prior run may have many orphan TPs accumulated.
      if not self._startup_cleanup_done:
        for t in open_trades:
          self._cleanup_all_pair_tps(t)
          t.set_custom_data(key="tp_order_id", value="")
        self._startup_cleanup_done = True
      # Reconciliation: place one TP per trade that has no stored ID. Treat the
      # literal string "null" as empty too — freqtrade locks cd_type at first
      # write, so a later None gets serialized to the literal string "null".
      for t in open_trades:
        stored = t.get_custom_data(key="tp_order_id") or ""
        if not stored or stored == "null":
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
    tp_amount = float(self.dp._exchange.amount_to_precision(pair, abs(trade.amount)))

    try:
      # Use reduceOnly + explicit quantity (NOT closePosition) — freqtrade's
      # stoploss_on_exchange already placed a STOP_MARKET with closePosition=True,
      # and Binance Futures only allows one closePosition order per direction
      # (error -4130). Quantity is reset on every order_filled so DCA stays in sync.
      order = self.dp._exchange._api.create_order(
        symbol=pair,
        type="TAKE_PROFIT_MARKET",
        side=side,
        amount=tp_amount,
        params={
          "stopPrice": tp_price,
          "reduceOnly": True,
          "workingType": "MARK_PRICE",
        },
      )
      trade.set_custom_data(key="tp_order_id", value=order["id"])
      logger.info(f"Placed TP for {pair}: id={order['id']} stopPrice={tp_price}")
    except Exception as e:
      logger.error(f"Failed to place TP for {pair}: {e}")

  def _cancel_exchange_tp(self, trade):
    if self.config.get("dry_run"):
      return
    order_id = trade.get_custom_data(key="tp_order_id")
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
    # Use "" (not None) — freqtrade locks cd_type at first write; if we wrote
    # a string ID first, a None value gets serialized as the literal string "null".
    trade.set_custom_data(key="tp_order_id", value="")

  def _cleanup_all_pair_tps(self, trade):
    """Nuke every open order on this pair (orphan recovery at startup).

    Binance Futures' fetch_open_orders sometimes excludes stop/TP-type orders,
    so a selective cancel can leave orphans behind. cancel_all_orders is the
    only way to guarantee a clean slate. Side effects accepted:
      - freqtrade's stoploss_on_exchange re-places SL within ~15s
      - this strategy's order_filled / bot_loop_start re-places TP within ~5s
      - any pending entry orders get re-issued by NFI's normal flow
    """
    try:
      result = self.dp._exchange._api.cancel_all_orders(trade.pair)
      logger.info(f"Startup cleanup: cancel_all_orders({trade.pair}) → {result}")
    except Exception as e:
      logger.warning(f"Startup cleanup failed for {trade.pair}: {e}")
