
import os
import math
import secrets
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Literal
from urllib.parse import quote, urlencode
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from backend.strategy import calculate_atr, generate_signal

# ============================================================
# APP CONFIGURATION
# ============================================================

app = FastAPI(title="AlgoPilot India API")

# CORS: allow the AlgoPilot Expo Codespaces frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://symmetrical-meme-55qpjvggv5pcp7qj-8081.app.github.dev",
        "http://localhost:8081",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
)


# ============================================================
# PAPER TRADING SAFETY
# ============================================================

# No broker order placement is implemented or enabled.
LIVE_ORDERS_ENABLED = False
BROKER_ORDER_EXECUTION_ENABLED = False

IST = ZoneInfo("Asia/Kolkata")


def is_nse_market_open(now=None):
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    return (
        now.weekday() < 5
        and datetime.strptime("09:15", "%H:%M").time()
        <= now.time()
        <= datetime.strptime("15:30", "%H:%M").time()
    )


PAPER_ACCOUNT = {
    "starting_balance": 100000.0,
    "cash": 100000.0,
    "realized_pnl": 0.0,
    "open_positions": [],
    "orders": [],
}

RISK_PER_TRADE = 0.005       # 0.5%
MAX_OPEN_POSITIONS = 5
MAX_TOTAL_OPEN_RISK = (
    PAPER_ACCOUNT["starting_balance"] * 0.025
)  # 2.5% = ₹2,500


# ============================================================
# UPSTOX OAUTH CONFIGURATION
# ============================================================

_pending_states = {}
STATE_TTL_SECONDS = 600

# Token is held in backend memory only.
# Restarting the backend clears it.
_upstox_access_token = None
_upstox_token_received_at = None

# Populated only from successful, server-side Upstox instrument searches.
_instrument_cache = {}
INSTRUMENT_CACHE_TTL = 12 * 60 * 60


def oauth_config():
    client_id = os.getenv("UPSTOX_CLIENT_ID")
    client_secret = os.getenv("UPSTOX_CLIENT_SECRET")
    redirect_uri = os.getenv("UPSTOX_REDIRECT_URI")

    if not all([client_id, client_secret, redirect_uri]):
        return None

    return client_id, client_secret, redirect_uri


# ============================================================
# REQUEST MODELS
# ============================================================

class PaperTradeRequest(BaseModel):
    instrument_key: str = Field(min_length=3, max_length=100)
    side: Literal["BUY", "SELL"]
    entry_price: float = Field(gt=0, allow_inf_nan=False)
    stop_loss: float = Field(gt=0, allow_inf_nan=False)
    take_profit: float = Field(gt=0, allow_inf_nan=False)


class CloseTradeRequest(BaseModel):
    exit_price: float = Field(gt=0, allow_inf_nan=False)


class MarkTradeRequest(BaseModel):
    current_price: float = Field(gt=0, allow_inf_nan=False)


# ============================================================
# PAPER P&L CALCULATION
# ============================================================

def resolve_paper_instrument(instrument_key):
    cached = _instrument_cache.get(instrument_key)

    if not cached:
        raise ValueError(
            "Instrument not found in backend cache. Search for it again."
        )

    if time.time() - cached["cached_at"] > INSTRUMENT_CACHE_TTL:
        _instrument_cache.pop(instrument_key, None)
        raise ValueError("Instrument data expired. Search for it again.")

    item = cached["metadata"]

    if item.get("instrument_key") != instrument_key:
        raise ValueError("Instrument key validation failed.")

    expiry = item.get("expiry")
    if expiry:
        try:
            expiry_date = datetime.strptime(
                str(expiry), "%Y-%m-%d"
            ).date()
        except ValueError:
            raise ValueError("Unsupported contract expiry format.")

        if expiry_date < datetime.now(timezone.utc).date():
            raise ValueError("This contract has expired.")

    exchange = item.get("exchange")
    segment = item.get("segment")
    instrument_type = item.get("instrument_type")
    underlying = item.get("underlying_symbol")
    lot_size = item.get("lot_size")
    qty_multiplier = item.get("qty_multiplier")

    try:
        freeze_quantity = float(item.get("freeze_quantity"))
    except (TypeError, ValueError):
        raise ValueError("Instrument freeze quantity is missing or invalid.")

    if not math.isfinite(freeze_quantity) or freeze_quantity <= 0:
        raise ValueError("Instrument freeze quantity is invalid.")

    # NSE cash equities: one share, one rupee of P&L per rupee move.
    if (
        exchange == "NSE"
        and segment == "NSE_EQ"
        and instrument_type == "EQ"
        and lot_size == 1
        and float(qty_multiplier or 0) == 1.0
    ):
        return {
            "symbol": item.get("trading_symbol") or item.get("name"),
            "exchange": exchange,
            "lot_size": 1,
            "pnl_multiplier": 1.0,
            "quantity_unit": "shares",
            "freeze_quantity": freeze_quantity,
        }

    # Explicitly supported MCX contracts.
    # Both the contract identity and returned metadata must match.
    mcx_specs = {
        "GOLD": ("GRMS", 1, 100),
        "GOLDM": ("GRMS", 100, 10),
        "GOLDTEN": ("GRMS", 10, 1),
        "GOLDPETAL": ("GRMS", 1, 1),
        "CRUDEOIL": ("BBL", 100, 100),
        "CRUDEOILM": ("BBL", 10, 10),
    }

    if (
        exchange == "MCX"
        and segment == "MCX_FO"
        and instrument_type == "FUT"
        and underlying in mcx_specs
    ):
        expected_unit, expected_lot, expected_multiplier = (
            mcx_specs[underlying]
        )

        if (
            item.get("price_quote_unit") != expected_unit
            or lot_size != expected_lot
            or float(qty_multiplier or 0) != expected_multiplier
        ):
            raise ValueError(
                "Contract metadata does not match the approved specification."
            )

        return {
            "symbol": item.get("trading_symbol"),
            "exchange": exchange,
            "lot_size": expected_lot,
            "pnl_multiplier": float(expected_multiplier),
            "quantity_unit": "lots",
            "freeze_quantity": freeze_quantity,
        }

    raise ValueError(
        "This instrument is not yet approved for paper trading."
    )


def calculate_pnl(position, price):
    direction = 1 if position["side"] == "BUY" else -1
    multiplier = position.get("pnl_multiplier", 1.0)

    return round(
        (price - position["entry_price"])
        * position["quantity"]
        * multiplier
        * direction,
        2,
    )


def get_unrealized_pnl():
    return round(
        sum(
            calculate_pnl(position, position["current_price"])
            for position in PAPER_ACCOUNT["open_positions"]
        ),
        2,
    )


def get_open_risk():
    return round(
        sum(
            position["risk_amount"]
            for position in PAPER_ACCOUNT["open_positions"]
        ),
        2,
    )


# ============================================================
# BASIC HEALTH ROUTES
# ============================================================

@app.get("/")
def home():
    return {
        "app": "AlgoPilot India",
        "status": "running",
        "mode": "PAPER_ONLY",
        "live_orders_enabled": False,
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "mode": "PAPER_ONLY",
    }


# ============================================================
# PAPER ACCOUNT
# ============================================================

@app.get("/api/paper/account")
def paper_account():
    unrealized = get_unrealized_pnl()
    open_risk = get_open_risk()

    return {
        "mode": "PAPER_ONLY",
        "starting_balance": PAPER_ACCOUNT["starting_balance"],
        "cash": round(PAPER_ACCOUNT["cash"], 2),
        "realized_pnl": round(PAPER_ACCOUNT["realized_pnl"], 2),
        "unrealized_pnl": unrealized,
        "equity": round(
            PAPER_ACCOUNT["cash"] + unrealized,
            2,
        ),
        "risk_per_trade_percent": RISK_PER_TRADE * 100,
        "maximum_risk_per_trade": round(
            PAPER_ACCOUNT["starting_balance"] * RISK_PER_TRADE,
            2,
        ),
        "open_risk": open_risk,
        "maximum_total_open_risk": round(
            MAX_TOTAL_OPEN_RISK,
            2,
        ),
        "open_positions_count": len(
            PAPER_ACCOUNT["open_positions"]
        ),
        "live_orders_enabled": False,
    }


@app.get("/api/paper/orders")
def paper_orders():
    return PAPER_ACCOUNT["orders"]


@app.get("/api/paper/positions")
def paper_positions():
    return PAPER_ACCOUNT["open_positions"]


# ============================================================
# CREATE PAPER TRADE
# ============================================================

@app.post("/api/paper/trades")
def create_paper_trade(trade: PaperTradeRequest):

    try:
        instrument = resolve_paper_instrument(trade.instrument_key)
    except ValueError as exc:
        return JSONResponse(
            {"error": str(exc), "mode": "PAPER_ONLY"},
            status_code=400,
        )

    entry = trade.entry_price
    stop = trade.stop_loss
    target = trade.take_profit

    if trade.side == "BUY":
        if not stop < entry < target:
            return JSONResponse(
                {"error": "BUY requires stop_loss < entry_price < take_profit"},
                status_code=400,
            )
    else:
        if not target < entry < stop:
            return JSONResponse(
                {"error": "SELL requires take_profit < entry_price < stop_loss"},
                status_code=400,
            )

    # For MCX, quantity means lots. For NSE equity, it means shares.
    pnl_multiplier = instrument["pnl_multiplier"]
    risk_per_unit = abs(entry - stop) * pnl_multiplier

    max_risk = PAPER_ACCOUNT["starting_balance"] * RISK_PER_TRADE
    quantity = int(max_risk // risk_per_unit)

    if quantity < 1:
        return JSONResponse(
            {"error": "Stop-loss distance is too large for the risk limit"},
            status_code=400,
        )

    total_instrument_quantity = quantity * instrument["lot_size"]

    if total_instrument_quantity > instrument["freeze_quantity"]:
        return JSONResponse(
            {
                "error": "Calculated quantity exceeds the instrument freeze limit",
                "calculated_quantity": total_instrument_quantity,
                "freeze_quantity": instrument["freeze_quantity"],
            },
            status_code=400,
        )

    risk_amount = round(quantity * risk_per_unit, 2)

    if len(PAPER_ACCOUNT["open_positions"]) >= MAX_OPEN_POSITIONS:
        return JSONResponse(
            {"error": "Maximum open paper positions reached"},
            status_code=400,
        )

    current_open_risk = get_open_risk()

    if current_open_risk + risk_amount > MAX_TOTAL_OPEN_RISK:
        return JSONResponse(
            {"error": "Total open-risk limit reached"},
            status_code=400,
        )

    position = {
        "trade_id": str(uuid4()),
        "instrument_key": trade.instrument_key,
        "symbol": instrument["symbol"],
        "exchange": instrument["exchange"],
        "quantity_unit": instrument["quantity_unit"],
        "lot_size": instrument["lot_size"],
        "pnl_multiplier": pnl_multiplier,
        "side": trade.side,
        "entry_price": entry,
        "current_price": entry,
        "stop_loss": stop,
        "take_profit": target,
        "quantity": quantity,
        "risk_amount": risk_amount,
        "status": "OPEN",
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }

    PAPER_ACCOUNT["open_positions"].append(position)

    PAPER_ACCOUNT["orders"].append({
        **position,
        "type": "PAPER_ENTRY",
    })

    return {
        "message": "Paper trade created",
        "trade": position,
        "broker_order_sent": False,
        "mode": "PAPER_ONLY",
    }


# ============================================================
# MARK PAPER POSITION
# ============================================================

@app.post("/api/paper/positions/{trade_id}/mark")
def mark_paper_trade(
    trade_id: str,
    mark: MarkTradeRequest,
):

    for position in PAPER_ACCOUNT["open_positions"]:

        if position["trade_id"] == trade_id:

            position["current_price"] = mark.current_price

            return {
                "trade_id": trade_id,
                "current_price": mark.current_price,
                "unrealized_pnl": calculate_pnl(
                    position,
                    mark.current_price,
                ),
                "mode": "PAPER_ONLY",
                "broker_order_sent": False,
            }

    return JSONResponse(
        {"error": "Open trade not found"},
        status_code=404,
    )


# ============================================================
# CLOSE PAPER POSITION
# ============================================================

@app.post("/api/paper/positions/{trade_id}/close")
def close_paper_trade(
    trade_id: str,
    close: CloseTradeRequest,
):

    for index, position in enumerate(
        PAPER_ACCOUNT["open_positions"]
    ):

        if position["trade_id"] == trade_id:

            pnl = calculate_pnl(
                position,
                close.exit_price,
            )

            PAPER_ACCOUNT["cash"] = round(
                PAPER_ACCOUNT["cash"] + pnl,
                2,
            )

            PAPER_ACCOUNT["realized_pnl"] = round(
                PAPER_ACCOUNT["realized_pnl"] + pnl,
                2,
            )

            closed = {
                **position,
                "exit_price": close.exit_price,
                "realized_pnl": pnl,
                "status": "CLOSED",
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }

            PAPER_ACCOUNT["open_positions"].pop(index)

            PAPER_ACCOUNT["orders"].append({
                **closed,
                "type": "PAPER_EXIT",
            })

            return {
                "message": "Paper trade closed",
                "trade": closed,
                "broker_order_sent": False,
                "mode": "PAPER_ONLY",
            }

    return JSONResponse(
        {"error": "Open trade not found"},
        status_code=404,
    )


# ============================================================
# UPSTOX LOGIN
# ============================================================

@app.get("/auth/upstox/login")
async def upstox_login():

    config = oauth_config()

    if not config:
        return JSONResponse(
            {
                "error": (
                    "Upstox OAuth environment "
                    "is not configured"
                )
            },
            status_code=503,
        )

    client_id, _, redirect_uri = config

    # Remove expired OAuth states.
    now = time.time()

    for key, created_at in list(_pending_states.items()):
        if now - created_at > STATE_TTL_SECONDS:
            _pending_states.pop(key, None)

    # Create one-time state.
    state = secrets.token_urlsafe(32)
    _pending_states[state] = now

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }

    url = (
        "https://api.upstox.com/v2/login/authorization/dialog?"
        + urlencode(params)
    )

    return RedirectResponse(
        url,
        status_code=302,
    )


# ============================================================
# UPSTOX OAUTH CALLBACK
# ============================================================

@app.get(
    "/auth/upstox/callback",
    response_class=HTMLResponse,
)
async def upstox_callback(request: Request):

    global _upstox_access_token
    global _upstox_token_received_at

    error = request.query_params.get("error")

    if error:
        return HTMLResponse(
            "<h2>Upstox authorization was not completed.</h2>"
            "<p>Return to AlgoPilot and try again.</p>",
            status_code=400,
        )

    code = request.query_params.get("code")
    returned_state = request.query_params.get("state")

    if not code or not returned_state:
        return HTMLResponse(
            "<h2>Missing authorization response.</h2>"
            "<p>Start again from the AlgoPilot login page.</p>",
            status_code=400,
        )

    # Validate and consume the one-time state.
    created_at = _pending_states.pop(
        returned_state,
        None,
    )

    if (
        created_at is None
        or time.time() - created_at > STATE_TTL_SECONDS
    ):
        return HTMLResponse(
            "<h2>Authorization state invalid or expired.</h2>"
            "<p>Please restart Upstox login.</p>",
            status_code=400,
        )

    config = oauth_config()

    if not config:
        return HTMLResponse(
            "<h2>Server OAuth configuration missing.</h2>",
            status_code=503,
        )

    client_id, client_secret, redirect_uri = config

    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    try:

        async with httpx.AsyncClient(timeout=20.0) as client:

            response = await client.post(
                "https://api.upstox.com/v2/login/authorization/token",
                data=payload,
                headers={
                    "accept": "application/json",
                    "Content-Type": (
                        "application/x-www-form-urlencoded"
                    ),
                },
            )

        if response.status_code != 200:

            print(
                "Upstox HTTP status:",
                response.status_code,
            )

            try:
                error_data = response.json()
                errors = error_data.get("errors", [])

                if isinstance(errors, list):

                    for item in errors:

                        if isinstance(item, dict):

                            print(
                                "Upstox error code:",
                                item.get("errorCode")
                                or item.get("error_code"),
                            )

                            print(
                                "Upstox error message:",
                                item.get("message"),
                            )

            except ValueError:
                print(
                    "Upstox error response was not JSON"
                )

            return HTMLResponse(
                "<h2>Upstox token exchange failed.</h2>"
                "<p>Check backend logs for safe diagnostics.</p>",
                status_code=502,
            )

        token_data = response.json()
        access_token = token_data.get("access_token")

        if not access_token:
            return HTMLResponse(
                "<h2>Upstox did not return an access token.</h2>",
                status_code=502,
            )

        _upstox_access_token = access_token

        _upstox_token_received_at = datetime.now(
            timezone.utc
        )

        return HTMLResponse(
            "<h2>Upstox connected successfully.</h2>"
            "<p>Authentication is held in backend process memory.</p>"
            "<p>AlgoPilot remains in PAPER TRADING ONLY mode.</p>"
            "<p>You may close this page.</p>"
        )

    except httpx.TimeoutException:

        return HTMLResponse(
            "<h2>Upstox request timed out.</h2>"
            "<p>Start a fresh login attempt.</p>",
            status_code=504,
        )

    except Exception:

        return HTMLResponse(
            "<h2>Unexpected authentication error.</h2>"
            "<p>No credentials or tokens have been displayed.</p>",
            status_code=500,
        )


# ============================================================
# UPSTOX AUTHENTICATION STATUS
# ============================================================

@app.get("/api/auth/upstox/status")
def upstox_status():

    return {
        "configured": oauth_config() is not None,
        "authenticated": (
            _upstox_access_token is not None
        ),
        "token_storage": "backend_memory_only",
        "mode": "PAPER_ONLY",
        "live_orders_enabled": False,
        "received_at": (
            _upstox_token_received_at.isoformat()
            if _upstox_token_received_at
            else None
        ),
    }


# ============================================================
# SYSTEM SAFETY STATUS
# ============================================================

@app.get("/api/system/safety")
def safety_status():

    return {
        "paper_trading_only": True,
        "live_order_execution": False,
        "broker_order_api_enabled": False,
    }


# ============================================================
# SYSTEM EXECUTION STATUS
# ============================================================

@app.get("/api/system/execution")
def execution_status():

    return {
        "mode": "PAPER_ONLY",
        "paper_engine_enabled": True,
        "broker_order_execution": False,
        "live_orders_enabled": False,
        "upstox_order_placement": False,
    }
@app.post("/api/strategy/signal")
async def strategy_signal(payload: dict):
    candles = payload.get("candles", [])

    if not isinstance(candles, list):
        return {
            "error": "candles must be a list",
            "mode": "PAPER_ONLY"
        }

    try:
        equity = float(payload.get("equity", 100000))
        open_risk = float(payload.get("open_risk", 0))

        if equity <= 0 or open_risk < 0:
            raise ValueError("Invalid risk values")

        result = generate_signal(
            candles=candles,
            equity=equity,
            open_risk=open_risk
        )

        return result

    except (TypeError, ValueError) as exc:
        return {
            "error": str(exc),
            "mode": "PAPER_ONLY"
        }

# ============================================================
# UPSTOX READ-ONLY MARKET DATA
# ============================================================

UPSTOX_MARKET_DATA_URL = (
    "https://api.upstox.com/v2/market-quote/quotes"
)


@app.get("/api/market/quote")
async def upstox_market_quote(instrument_key: str):

    token = os.getenv("UPSTOX_ANALYTICS_TOKEN")

    if not token:
        return JSONResponse(
            {"error": "Upstox Analytics Token is not configured"},
            status_code=503,
        )

    if (
        not instrument_key
        or len(instrument_key) > 100
        or "|" not in instrument_key
        or any(char in instrument_key for char in "\r\n")
    ):
        return JSONResponse(
            {"error": "Invalid instrument key"},
            status_code=400,
        )

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                UPSTOX_MARKET_DATA_URL,
                headers=headers,
                params={"instrument_key": instrument_key},
            )

        if response.status_code == 401:
            return JSONResponse(
                {"error": "Upstox rejected the Analytics Token"},
                status_code=401,
            )

        if response.status_code == 403:
            return JSONResponse(
                {"error": "Market-data permission denied by Upstox"},
                status_code=403,
            )

        if response.status_code != 200:
            return JSONResponse(
                {
                    "error": "Upstox market-data request failed",
                    "upstox_status": response.status_code,
                },
                status_code=502,
            )

        data = response.json()

        return {
            "provider": "UPSTOX",
            "instrument_key": instrument_key,
            "market_data": data.get("data", {}),
            "mode": "PAPER_ONLY",
            "read_only": True,
            "broker_order_sent": False,
        }

    except httpx.TimeoutException:
        return JSONResponse(
            {"error": "Upstox market-data request timed out"},
            status_code=504,
        )

    except (httpx.RequestError, ValueError):
        return JSONResponse(
            {"error": "Unable to retrieve valid Upstox market data"},
            status_code=502,
        )


# ============================================================
# UPSTOX INSTRUMENT SEARCH - READ ONLY
# ============================================================

@app.get("/api/market/instruments/search")
async def search_market_instruments(
    query: str,
    exchange: str = "MCX",
    segment: str | None = None,
    instrument_type: str = "FUT",
):
    token = os.getenv("UPSTOX_ANALYTICS_TOKEN")

    if not token:
        return JSONResponse(
            {"error": "Upstox Analytics Token is not configured"},
            status_code=503,
        )

    if not query or len(query) > 50:
        return JSONResponse(
            {"error": "Query must contain 1 to 50 characters"},
            status_code=400,
        )

    if exchange not in {"MCX", "NSE", "BSE"}:
        return JSONResponse(
            {"error": "Unsupported exchange"},
            status_code=400,
        )

    if segment is None:
        segment = "COMM" if exchange == "MCX" else "FO"

    if segment not in {"EQ", "FO", "COMM", "CURR", "INDEX"}:
        return JSONResponse(
            {"error": "Unsupported segment"},
            status_code=400,
        )

    if instrument_type not in {"FUT", "CE", "PE", "EQ", "INDEX"}:
        return JSONResponse(
            {"error": "Unsupported instrument type"},
            status_code=400,
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.upstox.com/v2/instruments/search",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                params={
                    "query": query,
                    "exchanges": exchange,
                    "segments": segment,
                    "instrument_types": instrument_type,
                    "page_number": 1,
                    "records": 20,
                },
            )

        if response.status_code != 200:
            return JSONResponse(
                {
                    "error": "Upstox instrument search failed",
                    "upstox_status": response.status_code,
                },
                status_code=502,
            )

        result = response.json()

        for item in result.get("data", []):
            key = item.get("instrument_key")
            if key:
                _instrument_cache[key] = {
                    "metadata": item,
                    "cached_at": time.time(),
                }

        return {
            "provider": "UPSTOX",
            "exchange": exchange,
            "query": query,
            "instruments": result.get("data", []),
            "mode": "PAPER_ONLY",
            "read_only": True,
            "broker_order_sent": False,
        }

    except (httpx.RequestError, ValueError):
        return JSONResponse(
            {"error": "Unable to search Upstox instruments"},
            status_code=502,
        )


# ============================================================
# UPSTOX HISTORICAL CANDLES V3 - READ ONLY
# ============================================================

@app.get("/api/market/candles")
async def historical_market_candles(
    instrument_key: str,
    from_date: str,
    to_date: str,
    interval: int = 5,
):
    token = os.getenv("UPSTOX_ANALYTICS_TOKEN")

    if not token:
        return JSONResponse(
            {"error": "Upstox Analytics Token is not configured"},
            status_code=503,
        )

    if (
        not instrument_key
        or len(instrument_key) > 100
        or "|" not in instrument_key
        or any(char in instrument_key for char in "\r\n")
    ):
        return JSONResponse(
            {"error": "Invalid instrument key"},
            status_code=400,
        )

    if interval not in {1, 5, 15}:
        return JSONResponse(
            {"error": "Interval must be 1, 5, or 15 minutes"},
            status_code=400,
        )

    try:
        start = datetime.strptime(from_date, "%Y-%m-%d").date()
        end = datetime.strptime(to_date, "%Y-%m-%d").date()

        if start > end:
            raise ValueError("from_date must not exceed to_date")

        if (end - start).days > 30:
            raise ValueError("Maximum date range is 31 days")

    except ValueError as exc:
        return JSONResponse(
            {"error": str(exc)},
            status_code=400,
        )

    encoded_key = quote(instrument_key, safe="")

    url = (
        "https://api.upstox.com/v3/historical-candle/"
        f"{encoded_key}/minutes/{interval}/{to_date}/{from_date}"
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )

        if response.status_code in {401, 403}:
            return JSONResponse(
                {"error": "Upstox market-data authorization failed"},
                status_code=response.status_code,
            )

        if response.status_code != 200:
            return JSONResponse(
                {
                    "error": "Upstox historical candle request failed",
                    "upstox_status": response.status_code,
                },
                status_code=502,
            )

        payload = response.json()
        raw_candles = payload.get("data", {}).get("candles", [])

        candles = []

        for item in raw_candles:
            if not isinstance(item, list) or len(item) < 6:
                continue

            try:
                timestamp = str(item[0])
                open_price = float(item[1])
                high = float(item[2])
                low = float(item[3])
                close = float(item[4])
                volume = float(item[5])

                values = [
                    open_price, high, low, close, volume
                ]

                if not all(math.isfinite(v) for v in values):
                    continue

                if (
                    min(open_price, high, low, close) <= 0
                    or volume < 0
                    or high < low
                    or high < max(open_price, close)
                    or low > min(open_price, close)
                ):
                    continue

                candles.append({
                    "timestamp": timestamp,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                })

            except (TypeError, ValueError):
                continue

        candles.sort(key=lambda candle: candle["timestamp"])

        return {
            "provider": "UPSTOX",
            "instrument_key": instrument_key,
            "interval_minutes": interval,
            "count": len(candles),
            "candles": candles,
            "mode": "PAPER_ONLY",
            "read_only": True,
            "broker_order_sent": False,
        }

    except httpx.TimeoutException:
        return JSONResponse(
            {"error": "Historical candle request timed out"},
            status_code=504,
        )

    except (httpx.RequestError, ValueError):
        return JSONResponse(
            {"error": "Unable to retrieve historical candles"},
            status_code=502,
        )


# ============================================================
# AUTOMATED MARKET SIGNAL - PAPER ONLY
# ============================================================

@app.get("/api/strategy/market-signal")
async def automated_market_signal(
    instrument_key: str,
    from_date: str,
    to_date: str,
    interval: int = 5,
):
    if not instrument_key.startswith("NSE_EQ|"):
        return JSONResponse(
            {"error": "Automated signals currently support NSE equities only"},
            status_code=400,
        )

    candle_response = await historical_market_candles(
        instrument_key=instrument_key,
        from_date=from_date,
        to_date=to_date,
        interval=interval,
    )

    if isinstance(candle_response, JSONResponse):
        return candle_response

    candles = candle_response.get("candles", [])

    if len(candles) < 50:
        return {
            "instrument_key": instrument_key,
            "signal": "HOLD",
            "reason": "At least 50 valid candles are required",
            "candle_count": len(candles),
            "mode": "PAPER_ONLY",
            "broker_order_sent": False,
        }

    try:
        equity = float(PAPER_ACCOUNT["starting_balance"])
        open_risk = float(get_open_risk())

        result = generate_signal(
            candles=candles,
            equity=equity,
            open_risk=open_risk,
        )

        return {
            "provider": "UPSTOX",
            "instrument_key": instrument_key,
            "interval_minutes": interval,
            "candle_count": len(candles),
            "latest_candle": candles[-1]["timestamp"],
            **result,
            "mode": "PAPER_ONLY",
            "read_only": True,
            "broker_order_sent": False,
        }

    except (TypeError, ValueError, KeyError):
        return JSONResponse(
            {"error": "Unable to calculate market signal"},
            status_code=500,
        )


# ============================================================
# EXPLICIT PAPER EXECUTION - NSE EQUITIES ONLY
# ============================================================

@app.post("/api/strategy/paper-execute")
async def execute_strategy_paper_trade(instrument_key: str):

    if not instrument_key.startswith("NSE_EQ|"):
        return JSONResponse(
            {"error": "Paper execution supports NSE equities only"},
            status_code=400,
        )

    if not is_nse_market_open():
        return JSONResponse(
            {
                "error": "NSE market is closed",
                "mode": "PAPER_ONLY",
            },
            status_code=409,
        )

    # Prevent duplicate positions in the same instrument.
    if any(
        position.get("instrument_key") == instrument_key
        and position.get("status") == "OPEN"
        for position in PAPER_ACCOUNT["open_positions"]
    ):
        return JSONResponse(
            {"error": "An open paper position already exists for this instrument"},
            status_code=409,
        )

    # Fetch the latest read-only market quote.
    quote_response = await upstox_market_quote(instrument_key)

    if isinstance(quote_response, JSONResponse):
        return quote_response

    market_data = quote_response.get("market_data", {})

    quote_item = next(
        (
            item for item in market_data.values()
            if isinstance(item, dict)
            and item.get("instrument_token") == instrument_key
        ),
        None,
    )

    if not quote_item:
        return JSONResponse(
            {"error": "No matching instrument quote returned by Upstox"},
            status_code=502,
        )

    try:
        entry = float(quote_item["last_price"])
        quote_time = datetime.fromisoformat(str(quote_item["timestamp"]))

        if not math.isfinite(entry) or entry <= 0:
            raise ValueError("Invalid quote price")

        if quote_time.tzinfo is None:
            raise ValueError("Quote timestamp has no timezone")

    except (KeyError, TypeError, ValueError):
        return JSONResponse(
            {"error": "Invalid quote price or timestamp"},
            status_code=502,
        )

    now = datetime.now(IST)
    quote_age = now - quote_time.astimezone(IST)

    if quote_age.total_seconds() < -5 or quote_age > timedelta(seconds=30):
        return JSONResponse(
            {
                "error": "Quote is stale or has an invalid future timestamp",
                "quote_timestamp": quote_item.get("timestamp"),
            },
            status_code=409,
        )

    # Fetch today's 5-minute historical candles.
    today = now.date().isoformat()

    candle_response = await historical_market_candles(
        instrument_key=instrument_key,
        from_date=today,
        to_date=today,
        interval=5,
    )

    if isinstance(candle_response, JSONResponse):
        return candle_response

    # Keep completed candles only. Reject timezone-naive timestamps.
    completed_candles = []

    now = datetime.now(IST)

    if not is_nse_market_open(now):
        return JSONResponse(
            {"error": "NSE market closed while fetching market data"},
            status_code=409,
        )

    for candle in candle_response.get("candles", []):
        try:
            candle_time = datetime.fromisoformat(candle["timestamp"])

            if candle_time.tzinfo is None:
                continue

            candle_end = candle_time.astimezone(IST) + timedelta(minutes=5)

            if candle_end <= now:
                completed_candles.append(candle)

        except (KeyError, TypeError, ValueError):
            continue

    completed_candles.sort(key=lambda item: item["timestamp"])

    if len(completed_candles) < 50:
        return JSONResponse(
            {
                "error": "At least 50 completed 5-minute candles are required",
                "completed_candles": len(completed_candles),
                "mode": "PAPER_ONLY",
            },
            status_code=409,
        )

    latest_candle_time = datetime.fromisoformat(
        completed_candles[-1]["timestamp"]
    ).astimezone(IST)

    latest_candle_end = latest_candle_time + timedelta(minutes=5)
    candle_age = now - latest_candle_end

    if candle_age < timedelta(0) or candle_age > timedelta(minutes=10):
        return JSONResponse(
            {
                "error": "Latest completed candle is stale",
                "latest_candle": completed_candles[-1]["timestamp"],
            },
            status_code=409,
        )

    # Generate signal using completed candles only.
    try:
        signal_result = generate_signal(
            candles=completed_candles,
            equity=float(PAPER_ACCOUNT["starting_balance"]),
            open_risk=float(get_open_risk()),
        )

        side = signal_result.get("signal")

        if side not in {"BUY", "SELL"}:
            return {
                "message": "No paper trade created",
                "signal": signal_result,
                "reason": "Strategy did not generate a BUY or SELL signal",
                "mode": "PAPER_ONLY",
                "broker_order_sent": False,
            }

        atr = calculate_atr(completed_candles)

        if atr is None or not math.isfinite(atr) or atr <= 0:
            return JSONResponse(
                {"error": "Unable to calculate a valid ATR"},
                status_code=422,
            )

        stop_distance = atr * 1.5

        if side == "BUY":
            stop_loss = entry - stop_distance
            take_profit = entry + (stop_distance * 2)
        else:
            stop_loss = entry + stop_distance
            take_profit = entry - (stop_distance * 2)

        if stop_loss <= 0 or take_profit <= 0:
            return JSONResponse(
                {"error": "Calculated stop-loss or target is invalid"},
                status_code=422,
            )

        # Recheck freshness immediately before creating the paper trade.
        execution_now = datetime.now(IST)
        quote_age = execution_now - quote_time.astimezone(IST)

        if not is_nse_market_open(execution_now):
            return JSONResponse(
                {"error": "NSE market closed before paper trade creation"},
                status_code=409,
            )

        if quote_age.total_seconds() < -5 or quote_age > timedelta(seconds=30):
            return JSONResponse(
                {"error": "Quote became stale before paper trade creation"},
                status_code=409,
            )

        latest_candle_time = datetime.fromisoformat(
            completed_candles[-1]["timestamp"]
        ).astimezone(IST)
        candle_age = execution_now - (
            latest_candle_time + timedelta(minutes=5)
        )

        if candle_age < timedelta(0) or candle_age > timedelta(minutes=10):
            return JSONResponse(
                {"error": "Latest completed candle became stale"},
                status_code=409,
            )

        # Reuse the existing paper trade engine and its risk controls.

        trade_request = PaperTradeRequest(
            instrument_key=instrument_key,
            side=side,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        trade_result = create_paper_trade(trade_request)

        if isinstance(trade_result, JSONResponse):
            return trade_result

        return {
            **trade_result,
            "signal": signal_result,
            "quote_timestamp": quote_item["timestamp"],
            "quote_age_seconds": round(quote_age.total_seconds(), 2),
            "latest_completed_candle": completed_candles[-1]["timestamp"],
            "completed_candle_count": len(completed_candles),
            "atr_14": round(atr, 4),
            "mode": "PAPER_ONLY",
            "broker_order_sent": False,
        }

    except (TypeError, ValueError, KeyError, OverflowError):
        return JSONResponse(
            {"error": "Unable to safely calculate or create paper trade"},
            status_code=500,
        )


# ============================================================
# EXPLICIT PAPER EXECUTION - NSE EQUITIES ONLY
# ============================================================
