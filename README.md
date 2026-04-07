# MetisEdge 🔱

> Prediction market mispricing scanner — Kalshi vs Sharp Books

## Strategy Modules
1. **Arb Scanner** — Kalshi vs Pinnacle fair value divergence
2. **Correlation Engine** — Manual parlay construction across correlated Kalshi contracts
3. **Stale Price Detector** — Sharp line moves not yet reflected in Kalshi

## Stack
- Python 3.11+
- FastAPI (signal API server)
- SQLite (signal log)
- The Odds API (sharp book lines)
- Kalshi REST API (prediction market prices)
- Discord Webhook (alerts)

## Project Structure
```
MetisEdge/
├── core/
│   ├── fair_value.py       # Vig removal + implied probability engine
│   ├── kelly.py            # Kelly criterion stake sizing
│   └── models.py           # Signal + position dataclasses
├── scanners/
│   ├── arb_scanner.py      # Kalshi vs Pinnacle divergence
│   ├── correlation.py      # Correlated leg combo builder
│   └── stale_price.py      # Sharp move → Kalshi lag detector
├── data/
│   ├── kalshi_client.py    # Kalshi API wrapper
│   ├── odds_client.py      # The Odds API wrapper
│   └── db.py               # SQLite signal logging
├── alerts/
│   └── discord.py          # Discord webhook alert formatter
├── api/
│   └── server.py           # FastAPI signal endpoint
├── config/
│   └── settings.py         # API keys, thresholds, config
├── main.py                 # Orchestrator / scheduler
└── requirements.txt
```

## Quickstart
```bash
pip install -r requirements.txt
cp config/settings.example.py config/settings.py
# Add your API keys to settings.py
python main.py
```
