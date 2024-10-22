# Copyright 2021 Optiver Asia Pacific Pty. Ltd.
#
# This file is part of Ready Trader Go.
#
#     Ready Trader Go is free software: you can redistribute it and/or
#     modify it under the terms of the GNU Affero General Public License
#     as published by the Free Software Foundation, either version 3 of
#     the License, or (at your option) any later version.
#
#     Ready Trader Go is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU Affero General Public License for more details.
#
#     You should have received a copy of the GNU Affero General Public
#     License along with Ready Trader Go.  If not, see
#     <https://www.gnu.org/licenses/>.
import asyncio
import itertools
import numpy as np

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side

LOT_SIZE = 10
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS


class AutoTrader(BaseAutoTrader):
    """Example Auto-trader.

    When it starts this auto-trader places ten-lot bid and ask orders at the
    current best-bid and best-ask prices respectively. Thereafter, if it has
    a long position (it has bought more lots than it has sold) it reduces its
    bid and ask prices. Conversely, if it has a short position (it has sold
    more lots than it has bought) then it increases its bid and ask prices.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.future_last_ask_prices = []
        self.future_last_bid_prices = []
        self.ask_id = self.ask_price = self.bid_id = self.bid_price = self.position = 0

        # The attributes used in the computation of \mu
        self.ETF_sup_F = False
        self.sum_mu = 0
        self.mu = 0
        self.number_cross = 0  # the number of crosses that happened

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error.

        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your hedge orders is filled.

        The price is the average price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """

        if instrument == Instrument.FUTURE:
            self.future_last_ask_prices = ask_prices
            self.future_last_bid_prices = bid_prices

        if instrument == Instrument.ETF and ask_prices[0] != 0 and bid_prices[0] != 0:

            future_ask = self.future_last_ask_prices[0]
            future_bid = self.future_last_bid_prices[0]
            mid_price_future = (future_ask + future_bid) / 2
            etf_ask = ask_prices[0]
            etf_bid = bid_prices[0]
            mid_price_etf = (etf_bid + etf_ask) / 2
            epsilon = 0.8 * TICK_SIZE_IN_CENTS
            gamma = 0 * TICK_SIZE_IN_CENTS
            self.mu = mid_price_etf - etf_bid if self.number_cross == 0 else self.mu
            delta = gamma + TICK_SIZE_IN_CENTS + self.mu
            # Delete active orders
            if self.bid_id != 0:
                self.send_cancel_order(self.bid_id)
                self.bid_id = 0
            if self.ask_id != 0:
                self.send_cancel_order(self.ask_id)
                self.ask_id = 0

            # Check delta spread when ETF > F or F > ETF
            if future_bid - etf_ask > delta:
                volume = POSITION_LIMIT-self.position
                self.bid_id = next(self.order_ids)
                self.send_insert_order(self.bid_id, Side.BUY, etf_ask, volume, Lifespan.GOOD_FOR_DAY)
                self.bids.add(self.bid_id)
            elif etf_bid - future_ask > delta:
                volume = abs(-POSITION_LIMIT-self.position)
                self.ask_id = next(self.order_ids)
                self.send_insert_order(self.ask_id, Side.SELL, etf_bid, volume, Lifespan.GOOD_FOR_DAY)
                self.asks.add(self.ask_id)

            # Check delta spread with limit order (when F and ETF are crossed)
            elif future_bid - etf_bid - TICK_SIZE_IN_CENTS > delta:
                volume = POSITION_LIMIT-self.position
                self.bid_id = next(self.order_ids)
                self.send_insert_order(self.bid_id, Side.BUY, etf_bid + TICK_SIZE_IN_CENTS, volume, Lifespan.GOOD_FOR_DAY)
                self.bids.add(self.bid_id)
            elif etf_ask - future_ask - TICK_SIZE_IN_CENTS > delta:
                volume = abs(-POSITION_LIMIT-self.position)
                self.ask_id = next(self.order_ids)
                self.send_insert_order(self.ask_id, Side.SELL, etf_ask - TICK_SIZE_IN_CENTS, volume, Lifespan.GOOD_FOR_DAY)
                self.asks.add(self.ask_id)

            # Close positions if > epsilon
            # elif etf_bid - future_ask > epsilon and self.position > 0:
            #     self.ask_id = next(self.order_ids)
            #     self.send_insert_order(self.ask_id, Side.SELL, MIN_BID_NEAREST_TICK, 3 * LOT_SIZE,
            #                            Lifespan.GOOD_FOR_DAY)
            #     self.asks.add(self.ask_id)
            # elif future_bid - etf_ask > epsilon and self.position < 0:
            #     self.bid_id = next(self.order_ids)
            #     self.send_insert_order(self.bid_id, Side.BUY, MAX_ASK_NEAREST_TICK, 3 * LOT_SIZE,
            #                            Lifespan.GOOD_FOR_DAY)
            #     self.bids.add(self.bid_id)

            # Estimate mu
            if self.ETF_sup_F != (mid_price_etf > mid_price_future) and self.position != 0:
                self.sum_mu += mid_price_etf - etf_bid
                self.number_cross += 1
                self.mu = self.sum_mu / self.number_cross

    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        if client_order_id in self.bids:
            self.position += volume
            self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, volume)
        elif client_order_id in self.asks:
            self.position -= volume
            self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, volume)

    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Called when the status of one of your orders changes.

        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.

        If an order is cancelled its remaining volume will be zero.
        """
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0

            # It could be either a bid or an ask
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically when there is trading activity on the market.

        The five best ask (i.e. sell) and bid (i.e. buy) prices at which there
        has been trading activity are reported along with the aggregated volume
        traded at each of those price levels.

        If there are less than five prices on a side, then zeros will appear at
        the end of both the prices and volumes arrays.
        """
        pass
