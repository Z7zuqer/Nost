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

  # First-cycle diagnostic: log what the API returns for each pair so we
  # can debug why orphans aren't being detected. NO auto-cancellation,
  # NO auto-placement — just observation.
  _startup_cleanup_done = False

  def bot_loop_start(self, current_time, **kwargs):
    super().bot_loop_start(current_time, **kwargs)
    if self.config.get("dry_run"):
      return
    try:
      from freqtrade.persistence import Trade
      open_trades = Trade.get_open_trades()
      # Once-per-process cleanup: cancel orphan conditional orders that aren't
      # tracked by either freqtrade (SL) or this strategy (TP).
      if not self._startup_cleanup_done:
        for t in open_trades:
          self._cleanup_orphan_conditional_orders(t)
        self._startup_cleanup_done = True
      # Reconciliation: only place TP if there is genuinely NO stored ID.
      # Don't reset/wipe IDs on restart — trust prior placement. If stored ID
      # exists but exchange cancelled it externally, user must manually re-place
      # or clear DB. This prevents orphan accumulation across restarts.
      for t in open_trades:
        stored = t.get_custom_data(key="tp_order_id") or ""
        if not stored or stored == "null":
          logger.info(f"[{t.pair}] no stored TP id — placing fresh")
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
      # stop=True is required for binance futures conditional-order namespace
      self.dp._exchange._api.cancel_order(order_id, trade.pair, params={"stop": True})
    except Exception as e:
      msg = str(e).lower()
      if any(x in msg for x in ("unknown order", "not found", "does not exist")):
        pass  # Already filled or cancelled — nothing to do.
      else:
        logger.warning(f"Failed to cancel TP for {trade.pair}: {e}")
    # Use "" (not None) — freqtrade locks cd_type at first write; if we wrote
    # a string ID first, a None value gets serialized as the literal string "null".
    trade.set_custom_data(key="tp_order_id", value="")

  def _cleanup_orphan_conditional_orders(self, trade):
    """Cancel orphan TP/SL orders on this pair that aren't tracked by anyone.

    On Binance Futures, conditional orders (stop / take-profit) live in a
    separate API namespace from regular limit orders — fetch_open_orders
    without params only returns regular orders, returning empty for stops.
    Use params={"stop": True} to enumerate, and params={"stop": True} on
    cancel_order to actually cancel them.

    "Tracked" = freqtrade's stoploss_order_id (per trade) OR our tp_order_id
    (per trade in custom_data). Anything else is an orphan and gets cancelled.
    """
    api = self.dp._exchange._api
    pair = trade.pair

    try:
      stops = api.fetch_open_orders(pair, params={"stop": True})
    except Exception as e:
      logger.warning(f"Cleanup [{pair}]: fetch_open_orders failed: {e}")
      return

    tracked_tp = str(trade.get_custom_data(key="tp_order_id") or "")
    sl_ids = {
      str(o.order_id) for o in trade.orders
      if o.ft_order_side == "stoploss" and o.ft_is_open
    }
    keep_ids = ({tracked_tp} | sl_ids) - {"", "null", "None"}

    cancelled = 0
    for o in stops:
      oid = str(o.get("id"))
      if oid in keep_ids:
        continue
      try:
        api.cancel_order(oid, pair, params={"stop": True})
        cancelled += 1
      except Exception as e:
        logger.warning(f"Cleanup [{pair}]: cancel orphan {oid} failed: {e}")
    logger.info(f"Cleanup [{pair}]: kept {len(keep_ids)} tracked, cancelled {cancelled} orphan(s)")
