import datetime as dt
import time
import logging

from optibook.synchronous_client import Exchange
from optibook.common_types import InstrumentType, OptionKind, PriceBook

from math import floor, ceil, sqrt, e
from black_scholes import call_value, put_value, call_delta, put_delta
from libs import calculate_current_time_to_date

exchange = Exchange()
exchange.connect()

logging.getLogger('client').setLevel('ERROR')

def round_down_to_tick(price, tick_size):
    """
    Rounds a price down to the nearest tick, e.g. if the tick size is 0.10, a price of 0.97 will get rounded to 0.90.
    """
    return floor(price / tick_size) * tick_size


def round_up_to_tick(price, tick_size):
    """
    Rounds a price up to the nearest tick, e.g. if the tick size is 0.10, a price of 1.34 will get rounded to 1.40.
    """
    return ceil(price / tick_size) * tick_size


def get_weighted_midpoint_value(instrument_id):
    """
    This function calculates the current weighted midpoint of the order book supplied by the exchange for the instrument
    specified by <instrument_id>, returning None if either side or both sides do not have any orders available.
    """
    order_book = exchange.get_last_price_book(instrument_id=instrument_id)

    # If the instrument doesn't have prices at all or on either side, we cannot calculate a midpoint and return None
    if not (order_book and order_book.bids and order_book.asks):
        return None
    else:
        total_best_volume = (order_book.asks[0].volume + order_book.bids[0].volume)
        midpoint = ((order_book.bids[0].price * order_book.bids[0].volume) + (order_book.asks[0].price * order_book.asks[0].volume)) / total_best_volume
        return midpoint


def calculate_theoretical_option_value(expiry, strike, option_kind, stock_value, interest_rate, volatility):
    """
    This function calculates the current fair call or put value based on Black & Scholes assumptions.

    expiry: dt.date          -  Expiry date of the option
    strike: float            -  Strike price of the option
    option_kind: OptionKind  -  Type of the option
    stock_value:             -  Assumed stock value when calculating the Black-Scholes value
    interest_rate:           -  Assumed interest rate when calculating the Black-Scholes value
    volatility:              -  Assumed volatility of when calculating the Black-Scholes value
    """
    time_to_expiry = calculate_current_time_to_date(expiry)

    if option_kind == OptionKind.CALL:
        option_value = call_value(S=stock_value, K=strike, T=time_to_expiry, r=interest_rate, sigma=volatility)
    elif option_kind == OptionKind.PUT:
        option_value = put_value(S=stock_value, K=strike, T=time_to_expiry, r=interest_rate, sigma=volatility)

    return option_value


def calculate_option_delta(expiry_date, strike, option_kind, stock_value, interest_rate, volatility):
    """
    This function calculates the current option delta based on Black & Scholes assumptions.

    expiry_date: dt.date     -  Expiry date of the option
    strike: float            -  Strike price of the option
    option_kind: OptionKind  -  Type of the option
    stock_value:             -  Assumed stock value when calculating the Black-Scholes value
    interest_rate:           -  Assumed interest rate when calculating the Black-Scholes value
    volatility:              -  Assumed volatility of when calculating the Black-Scholes value
    """
    time_to_expiry = calculate_current_time_to_date(expiry_date)

    if option_kind == OptionKind.CALL:
        option_delta = call_delta(S=stock_value, K=strike, T=time_to_expiry, r=interest_rate, sigma=volatility)
    elif option_kind == OptionKind.PUT:
        option_delta = put_delta(S=stock_value, K=strike, T=time_to_expiry, r=interest_rate, sigma=volatility)
    else:
        raise Exception(f"""Got unexpected value for option_kind argument, should be OptionKind.CALL or OptionKind.PUT but was {option_kind}.""")

    return option_delta


def update_quotes(instrument_id, theoretical_price, credit, optimal_ask_volume, optimal_bid_volume, position_limit, tick_size, update_ask_price, ask_order_id, update_bid_price, bid_order_id, update_ask_volume, update_bid_volume):
    """
    This function updates the quotes specified by <option_id>. We take the following actions in sequence:
        - pull (remove) any current oustanding orders
        - add credit to theoretical price and round to nearest tick size to create a set of bid/ask quotes
        - calculate max volumes to insert as to not pass the position_limit
        - reinsert limit orders on those levels

    Arguments:
        instrument_id: str           -  Exchange Instrument ID of the option to trade
        theoretical_price: float -  Price to quote around
        credit: float            -  Difference to subtract from/add to theoretical price to come to final bid/ask price
        volume:                  -  Volume (# lots) of the inserted orders (given they do not breach position limits)
        position_limit: int      -  Position limit (long/short) to avoid crossing
        tick_size: float         -  Tick size of the quoted instrument
    """
    outstanding_ask_id = ask_order_id
    outstanding_bid_id = bid_order_id

    # Pull (remove) all existing outstanding orders
    orders = exchange.get_outstanding_orders(instrument_id=instrument_id)
    for order_id, order in orders.items():
        
        if (update_ask_price and order.order_id == outstanding_ask_id) or (update_bid_price and order.order_id == outstanding_bid_id):
            print(f'- Deleting old {order.side} order in {instrument_id} for {order.volume} @ {order.price:8.2f}.')
            exchange.delete_order(instrument_id=instrument_id, order_id=order_id)
         

    # Calculate bid and ask price
    bid_price = round_down_to_tick(theoretical_price - credit, tick_size)
    ask_price = round_up_to_tick(theoretical_price + credit, tick_size)
    
    position = exchange.get_positions()[instrument_id]
    max_volume_to_buy = position_limit - position
    max_volume_to_sell = position_limit + position
    bid_volume = min(optimal_bid_volume, max_volume_to_buy)
    ask_volume = min(optimal_ask_volume, max_volume_to_sell)
    
    # Insert new limit orders
    if bid_volume > 0 and update_bid_price:
        print(f'- Inserting bid limit order in {instrument_id} for {bid_volume} @ {bid_price:8.2f}.')
        exchange.insert_order(
            instrument_id=instrument_id,
            price=bid_price,
            volume=bid_volume,
            side='bid',
            order_type='limit')
            
    elif bid_volume > 0 and (not update_bid_price) and update_bid_volume:
        print(f'- Amending bid limit order in {instrument_id} for {bid_volume} @ {bid_price:8.2f}.')
        exchange.amend_order(instrument_id=instrument_id, order_id=outstanding_bid_id, volume = bid_volume)
        
    else:
        print(f'- Maintained bid limit order.')
        
    if ask_volume > 0 and update_ask_price:
        print(f'- Inserting ask limit order in {instrument_id} for {ask_volume} @ {ask_price:8.2f}.')
        exchange.insert_order(
            instrument_id=instrument_id,
            price=ask_price,
            volume=ask_volume,
            side='ask',
            order_type='limit')
            
    elif ask_volume > 0 and (not update_ask_price) and update_ask_volume:
        print(f'- Amending ask limit order in {instrument_id} for {ask_volume} @ {ask_price:8.2f}.')
        exchange.amend_order(instrument_id=instrument_id, order_id=outstanding_ask_id, volume = ask_volume)
        
    else:
        print(f'- Maintained ask limit order.')
        
    print(" ")


def hedge_delta_position(stock_id, options, futures, stock_value):
    """
    This function (once finished) hedges the outstanding delta position by trading in the stock.

    That is:
        - It calculates how sensitive the total position value is to changes in the underlying by summing up all
          individual delta component.
        - And then trades stocks which have the opposite exposure, to remain, roughly, flat delta exposure

    Arguments:
        stock_id: str         -  Exchange Instrument ID of the stock to hedge with
        options: List[dict]   -  List of options with details to calculate and sum up delta positions for
        stock_value: float    -  The stock value to assume when making delta calculations using Black-Scholes
    """

    # A2: Calculate the delta position here 
    
    if stock_id == "NVDA":
        
        options_deltas = {}  
        aggregate_position_delta = 0 

        for option_id, option in options.items():
            positions = exchange.get_positions()
            options_deltas[option_id] = calculate_option_delta(expiry_date=options[option_id].expiry,
                                                           strike=options[option_id].strike,
                                                           option_kind=options[option_id].option_kind,
                                                           stock_value=stock_value,
                                                           interest_rate=0.03,
                                                           volatility=3.0)
        
            aggregate_position_delta += options_deltas[option_id]*positions[option_id]
            #print(f"- The current position in option {option_id} is {position}. Computed delta: {options_deltas[option_id]}")
        
        for future_id, future in futures.items():
            aggregate_position_delta += positions[future_id]
        

        stock_position = positions[stock_id]
        aggregate_position_delta += stock_position
        
        stock_dual_position = positions["NVDA_DUAL"]
        aggregate_position_delta += stock_dual_position
        
        underlying_order_book = exchange.get_last_price_book("NVDA")
    
        while underlying_order_book.bids == [] or underlying_order_book.asks == []:
            underlying_order_book = exchange.get_last_price_book("NVDA")
        
        max_volume_to_buy = 100 - stock_position
        max_volume_to_sell = 100 + stock_position
    
        bid_volume = min(abs(round(aggregate_position_delta)), max_volume_to_buy)
        ask_volume = min(abs(round(aggregate_position_delta)), max_volume_to_sell)
    

        if (aggregate_position_delta > 35 and ask_volume > 0):
            exchange.insert_order(instrument_id="NVDA", price = underlying_order_book.bids[0].price, volume = ask_volume, side = "ask", order_type="ioc")
        elif (aggregate_position_delta < -35 and bid_volume > 0):
            exchange.insert_order(instrument_id="NVDA", price=underlying_order_book.asks[0].price, volume = bid_volume , side = "bid", order_type="ioc")
    
    elif stock_id == "SAN":
        
        positions = exchange.get_positions() 
        aggregate_position_delta = 0
        
        stock_position = positions[stock_id]
        aggregate_position_delta += stock_position
        
        stock_dual_position = positions["SAN_DUAL"]
        aggregate_position_delta += stock_dual_position
        
        underlying_order_book = exchange.get_last_price_book("SAN")
    
        while underlying_order_book.bids == [] or underlying_order_book.asks == []:
            underlying_order_book = exchange.get_last_price_book("SAN")
        
        max_volume_to_buy = 100 - stock_position
        max_volume_to_sell = 100 + stock_position
    
        bid_volume = min(abs(round(aggregate_position_delta)), max_volume_to_buy)
        ask_volume = min(abs(round(aggregate_position_delta)), max_volume_to_sell)
    

        if (aggregate_position_delta > 1 and ask_volume > 0):
            exchange.insert_order(instrument_id="SAN", price = underlying_order_book.bids[0].price, volume = ask_volume, side = "ask", order_type="ioc")
        elif (aggregate_position_delta < -1 and bid_volume > 0):
            exchange.insert_order(instrument_id="SAN", price=underlying_order_book.asks[0].price, volume = bid_volume , side = "bid", order_type="ioc")
        
 
    
def load_instruments_for_underlying(underlying_stock_id):
    all_instruments = exchange.get_instruments()
    stock = all_instruments[underlying_stock_id]
    options = {instrument_id: instrument
               for instrument_id, instrument in all_instruments.items()
               if instrument.instrument_type == InstrumentType.STOCK_OPTION
               and instrument.base_instrument_id == underlying_stock_id}
               
    futures = {instrument_id: instrument
               for instrument_id, instrument in all_instruments.items()
               if instrument.instrument_type == InstrumentType.STOCK_FUTURE
               and instrument.base_instrument_id == underlying_stock_id}
    
    return stock, options, futures

def get_quantified_data(underlying, instrument):
    
    """
    underlying type: string
    instrument type: instrument object (option/future)
    
    If dual listing is taking place, treat the liquid asset as the underlying and the illiquid asset as the instrument.
    
    """
    
    if (instrument.instrument_type == InstrumentType.STOCK):

        if(underlying == "NVDA"):
            all_instruments = exchange.get_instruments()
            instrument = all_instruments["NVDA_DUAL"]
            data_instrument = exchange.get_trade_tick_history(instrument.instrument_id)
            data_underlying = exchange.get_trade_tick_history("NVDA")
            
        else:
            all_instruments = exchange.get_instruments()
            instrument = all_instruments["SAN_DUAL"]
            data_instrument = exchange.get_trade_tick_history(instrument.instrument_id)
            data_underlying = exchange.get_trade_tick_history("SAN")
    else:
        data_instrument = exchange.get_trade_tick_history(instrument.instrument_id)
        data_underlying = exchange.get_trade_tick_history(underlying)
        
    total_ask_volume=0
    total_bid_volume=0
    aggregate_squared_deviation=0 #aggregate deviation of traded prices from theoritical value
    
    for line in data_instrument:
        if line.aggressor_side=="bid":
            total_bid_volume+=line.volume
            traded_price_instrument=line.price
            timestamp_instrument = line.timestamp
            
            #finds the price at which the underlying was trading at during "timestamp_underlying"
            first_loop = True
            
            for line in data_underlying:
                if first_loop: #initiates minimum
                    minimum = abs(timestamp_instrument - line.timestamp)
                    traded_price_underlying = line.price
                    first_loop = False
                    
                elif minimum > abs(timestamp_instrument - line.timestamp):
                    minimum = timestamp_instrument - line.timestamp
                    traded_price_underlying = line.price
            
            if (instrument.instrument_type == InstrumentType.STOCK_OPTION):
                theoretical_value = calculate_theoretical_option_value(expiry=instrument.expiry,
                                                                   strike=instrument.strike,
                                                                   option_kind=instrument.option_kind,
                                                                   stock_value=traded_price_underlying,
                                                                   interest_rate=0.03,
                                                                   volatility=3.0)
                                                                   
            elif (instrument.instrument_type == InstrumentType.STOCK_FUTURE):
                theoretical_value = traded_price_underlying * pow(e , (0.03 * calculate_current_time_to_date(instrument.expiry)))
            
            elif (instrument.instrument_type == InstrumentType.STOCK):
                theoretical_value = traded_price_underlying
                
                
            aggregate_squared_deviation += pow(theoretical_value - traded_price_instrument, 2)
                    
        else:
            
            total_ask_volume+=line.volume
            traded_price_instrument=line.price
            timestamp_instrument = line.timestamp
            
            #finds the price at which the underlying was trading at during "timestamp_underlying"
            first_loop = True
            
            for line in data_underlying:
                if first_loop: #initiates minimum
                    minimum = abs(timestamp_instrument - line.timestamp)
                    traded_price_underlying = line.price
                    first_loop = False
                    
                elif minimum > abs(timestamp_instrument - line.timestamp):
                    minimum = timestamp_instrument - line.timestamp
                    traded_price_underlying = line.price
            
            if (instrument.instrument_type == InstrumentType.STOCK_OPTION):
                theoretical_value = calculate_theoretical_option_value(expiry=instrument.expiry,
                                                                   strike=instrument.strike,
                                                                   option_kind=instrument.option_kind,
                                                                   stock_value=traded_price_underlying,
                                                                   interest_rate=0.03,
                                                                   volatility=3.0)
                                                                   
            elif (instrument.instrument_type == InstrumentType.STOCK_FUTURE):
                theoretical_value = traded_price_underlying * pow(e , (0.03 * calculate_current_time_to_date(instrument.expiry)))
                
            elif (instrument.instrument_type == InstrumentType.STOCK):
                theoretical_value = traded_price_underlying
                
            aggregate_squared_deviation += pow(theoretical_value - traded_price_instrument , 2) 
            
    #print(minimum)
    average_squared_deviation = aggregate_squared_deviation / len(data_instrument) 
    average_volume_ask = total_ask_volume / len(data_instrument)
    average_volume_bid = total_bid_volume / len(data_instrument)

    return average_squared_deviation, average_volume_ask, average_volume_bid
    
def operational_optimazation(instrument_id, outstanding_orders, suggested_volume, new_bid_price, new_ask_price):
    
    optimal_ask_volume = suggested_volume
    optimal_bid_volume = suggested_volume
    update_ask_price = True
    update_bid_price = True
    update_ask_volume = True
    update_bid_volume = True
    ask_order_id = None
    bid_order_id = None
    
    for order in outstanding_orders:
        if(order.side == "ask"):
            existing_ask_price = order.price
        else:
            existing_bid_price = order.price
                
    positions = exchange.get_positions()
    inventory_instrument_id = positions[instrument_id]
        
    if inventory_instrument_id > 0:
        optimal_ask_volume += inventory_instrument_id
    elif inventory_instrument_id < 0:
        optimal_bid_volume += abs(inventory_instrument_id)
        
        
    for order in outstanding_orders:
            
        if(order.side == "ask"):
            ask_order_id = order.order_id
            if order.price == new_ask_price:
                update_ask_price = False
            if order.volume == optimal_ask_volume:
                update_ask_volume = False
                    
        if(order.side == "bid"):
            bid_order_id = order.order_id
            if order.price == new_bid_price:
                update_bid_price = False
            if order.volume == optimal_bid_volume:
                update_bid_volume = False
        
    #check if new prices would lead to a self-trade scenario
    if update_ask_price and update_bid_price == False:
        if new_ask_price == existing_bid_price:
            update_ask_price = False
                
    elif update_bid_price and update_ask_price == False:
        if new_bid_price == existing_ask_price:
            update_bid_price = False
    
    return [optimal_ask_volume, optimal_bid_volume, update_ask_price, update_bid_price, ask_order_id, bid_order_id, update_ask_volume, update_bid_volume, positions]
    
    
def run_market_making_strategy_for_options(option_id, option, stock_value):
    theoretical_price = calculate_theoretical_option_value(expiry=option.expiry,
                                                               strike=option.strike,
                                                               option_kind=option.option_kind,
                                                               stock_value=stock_value,
                                                               interest_rate=0.03,
                                                               volatility=3.0)
        
    average_squared_deviation, average_volume_ask, average_volume_bid = get_quantified_data("NVDA", option)
    average_traded_volume = round(min(average_volume_ask,average_volume_bid))
    current_order_book = exchange.get_last_price_book(instrument_id = option_id)
    
    if current_order_book.bids != []:
        current_bid_volume = current_order_book.bids[0].volume
    else:
        current_bid_volume = 0 
    
    if current_order_book.asks != []:
        current_ask_volume = current_order_book.asks[0].volume
    else:
        current_ask_volume = 0 
        
    excess_volume = min(current_ask_volume , current_bid_volume) - average_traded_volume
        
    if excess_volume > 0:
        optimal_volume = round(average_traded_volume + 0.15 * excess_volume)
    else:
        optimal_volume = round(average_traded_volume) 
        
    if optimal_volume > 45:
        optimal_volume = 45

    optimal_credit = sqrt(average_squared_deviation) - (sqrt(average_squared_deviation) % 0.1)
            
        
    new_bid_price = round_down_to_tick(theoretical_price - optimal_credit, 0.1)
    new_ask_price = round_up_to_tick(theoretical_price + optimal_credit, 0.1)

    positions = exchange.get_positions()
    instrument_position = positions[option_id]
    
    #emergency situations
    if instrument_position > 65:
        new_bid_price = round_down_to_tick(theoretical_price - 1.5*optimal_credit, 0.1)
        new_ask_price = round_up_to_tick(theoretical_price + 0.5*optimal_credit , 0.1)
        
    elif instrument_position < -65:
        new_bid_price = round_down_to_tick(theoretical_price - 0.5*optimal_credit, 0.1)
        new_ask_price = round_up_to_tick(theoretical_price + 1.5*optimal_credit, 0.1)
        

    outstanding_orders = list(exchange.get_outstanding_orders(option_id).values())
    print(f'- outstanding orders: {outstanding_orders} ')
    print(" ")
            
    arguments = operational_optimazation(instrument_id = option_id, 
                                           outstanding_orders = outstanding_orders, 
                                           suggested_volume = optimal_volume, 
                                           new_bid_price = new_bid_price , 
                                           new_ask_price = new_ask_price )
            
    update_quotes(instrument_id=option_id,
                    theoretical_price=theoretical_price,
                    credit=optimal_credit,
                    position_limit=100,
                    tick_size=0.10,
                    optimal_ask_volume = arguments[0],
                    optimal_bid_volume = arguments[1],
                    update_ask_price = arguments[2],
                    update_bid_price = arguments[3],
                    ask_order_id = arguments[4],
                    bid_order_id = arguments[5],
                    update_ask_volume = arguments[6],
                    update_bid_volume = arguments[7])
    
    time.sleep(0.2)
                    
    
def run_market_making_strategy_for_futures(future_id, future, stock_value):
    
    theoretical_price = stock_value * pow(e , (0.03 * calculate_current_time_to_date(future.expiry)))
    
    average_squared_deviation, average_volume_ask, average_volume_bid = get_quantified_data("NVDA", future)
    average_traded_volume = round(min(average_volume_ask,average_volume_bid))
    current_order_book = exchange.get_last_price_book(instrument_id = future_id)
    
    
    if current_order_book.bids != []:
        current_bid_volume = current_order_book.bids[0].volume
    else:
        current_bid_volume = 0 
    
    if current_order_book.asks != []:
        current_ask_volume = current_order_book.asks[0].volume
    else:
        current_ask_volume = 0 
        
     
    excess_volume = min(current_ask_volume , current_bid_volume) - average_traded_volume
        
    if excess_volume > 0:
        optimal_volume = round(average_traded_volume + 0.15 * excess_volume)
    else:
        optimal_volume = round(average_traded_volume) 
        
    if optimal_volume > 45:
        optimal_volume = 45
        
    #current_implied_credit = min(abs(current_ask_price-theoretical_price), abs(current_bid_price-theoretical_price))
    #excess_credit = current_implied_credit - sqrt(average_squared_deviation)
        
    #if excess_credit > 0:
      #  optimal_credit = sqrt(average_squared_deviation) + 0.3*excess_credit - ((sqrt(average_squared_deviation) + 0.3*excess_credit) % 0.1)
    #else:
    optimal_credit = sqrt(average_squared_deviation) - (sqrt(average_squared_deviation) % 0.1)
        
    new_bid_price = round_down_to_tick(theoretical_price - optimal_credit, 0.1)
    new_ask_price = round_up_to_tick(theoretical_price + optimal_credit, 0.1)
    
    positions = exchange.get_positions()
    instrument_position = positions[future_id]
    
    #emergency situations
    if instrument_position > 65:
        new_bid_price = round_down_to_tick(theoretical_price - 1.5*optimal_credit, 0.1)
        new_ask_price = round_up_to_tick(theoretical_price + 0.5*optimal_credit , 0.1)
        
    elif instrument_position < -65:
        new_bid_price = round_down_to_tick(theoretical_price - 0.5 * optimal_credit, 0.1)
        new_ask_price = round_up_to_tick(theoretical_price + 1.5*optimal_credit, 0.1)

        
    outstanding_orders = list(exchange.get_outstanding_orders(future_id).values())
    print(f'- outstanding orders: {outstanding_orders} ')
    print(" ")
    
    arguments = operational_optimazation(instrument_id = future_id, 
                                           outstanding_orders = outstanding_orders, 
                                           suggested_volume = optimal_volume, 
                                           new_bid_price = new_bid_price , 
                                           new_ask_price = new_ask_price )
            
    update_quotes(instrument_id=future_id,
                    theoretical_price=theoretical_price,
                    credit=optimal_credit,
                    position_limit=100,
                    tick_size=0.10,
                    optimal_ask_volume = arguments[0],
                    optimal_bid_volume = arguments[1],
                    update_ask_price = arguments[2],
                    update_bid_price = arguments[3],
                    ask_order_id = arguments[4],
                    bid_order_id = arguments[5],
                    update_ask_volume = arguments[6],
                    update_bid_volume = arguments[7])
    
    time.sleep(0.2)
    
def run_market_making_strategy_for_dual(stock_id, instrument, stock_value):
    
    theoretical_price = stock_value
    average_squared_deviation, average_volume_ask, average_volume_bid = get_quantified_data(stock_id, instrument)
    average_traded_volume = round(min(average_volume_ask,average_volume_bid))
    
    if stock_id == 'NVDA':
        stock_dual_id = "NVDA_DUAL"
    else:
        stock_dual_id = "SAN_DUAL"
        
    current_order_book = exchange.get_last_price_book(instrument_id = stock_dual_id)
    
    if current_order_book.bids != []:
        current_bid_volume = current_order_book.bids[0].volume
    else:
        current_bid_volume = 0 
    
    if current_order_book.asks != []:
        current_ask_volume = current_order_book.asks[0].volume
    else:
        current_ask_volume = 0 
        
    excess_volume = min(current_ask_volume , current_bid_volume) - average_traded_volume
        
    if excess_volume > 0:
        optimal_volume = round(average_traded_volume + 0.15 * excess_volume)
    else:
        optimal_volume = round(average_traded_volume) 
    
    
    if optimal_volume > 45:
        optimal_volume = 45
        
    optimal_credit = sqrt(average_squared_deviation) - (sqrt(average_squared_deviation) % 0.1)
    new_bid_price = round_down_to_tick(theoretical_price - optimal_credit, 0.1)
    new_ask_price = round_up_to_tick(theoretical_price + optimal_credit, 0.1)

    positions = exchange.get_positions()
    instrument_position = positions[stock_dual_id]
    
    #emergency situations
    if instrument_position > 65:
        new_bid_price = round_down_to_tick(theoretical_price - 1.5*optimal_credit, 0.1)
        new_ask_price = round_up_to_tick(theoretical_price + 0.5*optimal_credit, 0.1)
        
    elif instrument_position < -65:
        new_bid_price = round_down_to_tick(theoretical_price - 0.5*optimal_credit, 0.1)
        new_ask_price = round_up_to_tick(theoretical_price + 1.5*optimal_credit, 0.1)
        
    outstanding_orders = list(exchange.get_outstanding_orders(stock_dual_id).values())
    print(f'- outstanding orders: {outstanding_orders} ')
    print(" ")
    
    arguments = operational_optimazation(instrument_id = stock_dual_id, 
                                           outstanding_orders = outstanding_orders, 
                                           suggested_volume = optimal_volume, 
                                           new_bid_price = new_bid_price , 
                                           new_ask_price = new_ask_price )
            
    update_quotes(instrument_id=stock_dual_id,
                    theoretical_price=theoretical_price,
                    credit=optimal_credit,
                    position_limit=100,
                    tick_size=0.10,
                    optimal_ask_volume = arguments[0],
                    optimal_bid_volume = arguments[1],
                    update_ask_price = arguments[2],
                    update_bid_price = arguments[3],
                    ask_order_id = arguments[4],
                    bid_order_id = arguments[5],
                    update_ask_volume = arguments[6],
                    update_bid_volume = arguments[7])
        
    
time.sleep(45)

stock_NVDA, options, futures = load_instruments_for_underlying("NVDA")
all_instruments = exchange.get_instruments()
stock_SAN = all_instruments["SAN"]


while True:
    print(f'')
    print(f'-----------------------------------------------------------------')
    print(f'TRADE LOOP ITERATION ENTERED AT {str(dt.datetime.now()):18s} UTC.')
    print(f'-----------------------------------------------------------------')

    stock_value = get_weighted_midpoint_value("NVDA")
    
    if stock_value is None:
        print('Empty stock order book on bid or ask-side, or both, unable to update option prices.')
        time.sleep(1)
        continue
    
    for option_id, option in options.items():
        run_market_making_strategy_for_options(option_id, option, stock_value)
    
    for future_id, future in futures.items():
       run_market_making_strategy_for_futures(future_id, future, stock_value)
        
    run_market_making_strategy_for_dual("NVDA",stock_NVDA,stock_value)
    
    print(f'\nHedging delta position NVDA')
    hedge_delta_position("NVDA", options, futures, stock_value)
    
    stock_value = get_weighted_midpoint_value("SAN")
    
    if stock_value is None:
        print('Empty stock order book on bid or ask-side, or both, unable to update option prices.')
        time.sleep(1)
        continue
    
    run_market_making_strategy_for_dual("SAN", stock_SAN, stock_value)
    print(f'\nHedging delta position SAN')
    hedge_delta_position("SAN", options == None, futures == None, stock_value)


    print(f'\nSleeping for 0.150 seconds.')
    time.sleep(0.150)
