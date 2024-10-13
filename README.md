Project Overview

This repository contains the Python-based market-making algorithm developed for the Optiver Algorithmic Trading Coding Competition hosted by LSE, where I was awarded the "Most Outstanding Individual" by Optiver's CEO, Jan Boomaars, among 114 participants.

The algorithm implements market-making strategies for stocks, options, and futures, with features such as delta hedging and inventory management. The project achieved the 4th highest profitability as an individual contestant among 23 teams.

Features
Market-Making Strategy: Implements dynamic bid and ask pricing based on the Black-Scholes model and real-time market data.
Delta Hedging: Adjusts the portfolio to maintain a balanced risk exposure.
Inventory Management: Manages position limits by updating orders and adjusting volumes to prevent overexposure.

How It Works
Order Book Analysis: Continuously evaluates the order book for each instrument to calculate weighted midpoints and determine optimal bid and ask prices.
Theoretical Pricing: Calculates fair values using the Black-Scholes model for options.
Quote Updates: Dynamically updates market-making quotes with volume adjustments based on position limits and strategy parameters.
Hedging: Executes delta-neutral strategies by trading stock to hedge the position's risk exposure.
