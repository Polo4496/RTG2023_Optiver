// Copyright 2021 Optiver Asia Pacific Pty. Ltd.
//
// This file is part of Ready Trader Go.
//
//     Ready Trader Go is free software: you can redistribute it and/or
//     modify it under the terms of the GNU Affero General Public License
//     as published by the Free Software Foundation, either version 3 of
//     the License, or (at your option) any later version.
//
//     Ready Trader Go is distributed in the hope that it will be useful,
//     but WITHOUT ANY WARRANTY; without even the implied warranty of
//     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
//     GNU Affero General Public License for more details.
//
//     You should have received a copy of the GNU Affero General Public
//     License along with Ready Trader Go.  If not, see
//     <https://www.gnu.org/licenses/>.
#include <array>

#include <boost/asio/io_context.hpp>

#include <ready_trader_go/logging.h>

#include "autotrader.h"

using namespace ReadyTraderGo;

RTG_INLINE_GLOBAL_LOGGER_WITH_CHANNEL(LG_AT, "AUTO")

constexpr int LOT_SIZE = 10;
constexpr int POSITION_LIMIT = 100;
constexpr int TICK_SIZE_IN_CENTS = 100;
constexpr int MIN_BID_NEARST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) / TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS;
constexpr int MAX_ASK_NEAREST_TICK = MAXIMUM_ASK / TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS;

AutoTrader::AutoTrader(boost::asio::io_context& context) : BaseAutoTrader(context)
{
}

void AutoTrader::DisconnectHandler()
{
    BaseAutoTrader::DisconnectHandler();
}

void AutoTrader::ErrorMessageHandler(unsigned long clientOrderId,
                                     const std::string& errorMessage)
{
    if (clientOrderId != 0 && ((mAsks.count(clientOrderId) == 1) || (mBids.count(clientOrderId) == 1)))
    {
        OrderStatusMessageHandler(clientOrderId, 0, 0, 0);
    }
}

void AutoTrader::HedgeFilledMessageHandler(unsigned long clientOrderId,
                                           unsigned long price,
                                           unsigned long volume)
{
}

void AutoTrader::OrderBookMessageHandler(Instrument instrument,
                                         unsigned long sequenceNumber,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& askPrices,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& askVolumes,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& bidPrices,
                                         const std::array<unsigned long, TOP_LEVEL_COUNT>& bidVolumes)
{
    if (instrument == Instrument::FUTURE)
    {
        mFutureLastAskPrices = askPrices;
        mFutureLastBidPrices = bidPrices;
    } else if(askPrices[0]!=0 && bidPrices[0]!=0) {

        long futureAsk = mFutureLastAskPrices[0];
        long futureBid = mFutureLastBidPrices[0];

        double midPriceFuture = (futureAsk+futureBid)/2.0;

        long etfAsk = askPrices[0];
        long etfBid = bidPrices[0];

        double midPriceETF = (etfBid + etfAsk)/2.0;
        
        double epsilon = 0.8 * TICK_SIZE_IN_CENTS;        
        double gamma = 0.0 * TICK_SIZE_IN_CENTS;

        if(mNumberCross==0) {
            mMu = midPriceETF-etfBid;
        } 

        double delta = gamma + TICK_SIZE_IN_CENTS + mMu;

        if(mBidId!=0) {
            SendCancelOrder(mBidId);
            mBidId=0;
        }
        if(mAskId!=0) {
            SendCancelOrder(mAskId);
            mAskId=0;
        }

        if(futureBid-etfAsk > delta) {
            int volume = POSITION_LIMIT-mPosition;
            if(volume>0) {
                mBidId = mNextMessageId++;
                SendInsertOrder(mBidId, Side::BUY, etfAsk, volume, Lifespan::GOOD_FOR_DAY);
                mBids.emplace(mBidId);
                // RLOG(LG_AT, LogLevel::LL_INFO) << futureBid<<"-"<<etfAsk<<">"<<delta<<" => BUYING "<<volume<<"@"<<etfAsk;
            }
        } else if(etfBid-futureAsk>delta) {
            int volume =  abs(-POSITION_LIMIT-mPosition);
            if(volume>0) {
                mAskId = mNextMessageId++;
                SendInsertOrder(mAskId, Side::SELL, etfBid, volume, Lifespan::GOOD_FOR_DAY);
                mAsks.emplace(mAskId);
                // RLOG(LG_AT, LogLevel::LL_INFO) << etfBid<<"-"<<futureAsk <<"(="<<(etfBid-futureAsk)<<")"<<">"<<delta<<" => SELLING "<<volume<<"@"<<etfBid;
            }
        } else if(futureBid - etfBid - TICK_SIZE_IN_CENTS > delta){
            int volume = POSITION_LIMIT - mPosition;
            if(volume>0){
                mBidId = mNextMessageId++;
                SendInsertOrder(mBidId, Side::BUY, etfBid + TICK_SIZE_IN_CENTS, volume, Lifespan::GOOD_FOR_DAY);
                mBids.emplace(mBidId);
            }
        } else if(etfAsk - futureAsk - TICK_SIZE_IN_CENTS > delta) {
            int volume =  abs(-POSITION_LIMIT-mPosition);
            if(volume>0){
                mAskId = mNextMessageId++;
                SendInsertOrder(mAskId, Side::SELL, etfAsk - TICK_SIZE_IN_CENTS, volume, Lifespan::GOOD_FOR_DAY);
                mAsks.emplace(mAskId);
            }
        }
        
        if(mETFSupF != (midPriceETF > midPriceFuture) && mPosition!=0) {
            mSumMu += midPriceETF-etfBid;
            mNumberCross +=1;
            mMu = mSumMu/mNumberCross;
        }
    }
}

void AutoTrader::OrderFilledMessageHandler(unsigned long clientOrderId,
                                           unsigned long price,
                                           unsigned long volume)
{
    if (mAsks.count(clientOrderId) == 1)
    {
        mPosition -= (long)volume;
        SendHedgeOrder(mNextMessageId++, Side::BUY, MAX_ASK_NEAREST_TICK, volume);
    }
    else if (mBids.count(clientOrderId) == 1)
    {
        mPosition += (long)volume;
        SendHedgeOrder(mNextMessageId++, Side::SELL, MIN_BID_NEARST_TICK, volume);
    }
}

void AutoTrader::OrderStatusMessageHandler(unsigned long clientOrderId,
                                           unsigned long fillVolume,
                                           unsigned long remainingVolume,
                                           signed long fees)
{
    RLOG(LG_AT, LogLevel::LL_INFO) <<"fillVolume: "<<fillVolume<<" remainingVolume: "<<remainingVolume;
    if (remainingVolume == 0)
    {
        if (clientOrderId == mAskId)
        {
            mAskId = 0;
        }
        else if (clientOrderId == mBidId)
        {
            mBidId = 0;
        }

        mAsks.erase(clientOrderId);
        mBids.erase(clientOrderId);
    }
}

void AutoTrader::TradeTicksMessageHandler(Instrument instrument,
                                          unsigned long sequenceNumber,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& askPrices,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& askVolumes,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& bidPrices,
                                          const std::array<unsigned long, TOP_LEVEL_COUNT>& bidVolumes)
{
}
