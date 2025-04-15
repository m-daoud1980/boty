from flask import Flask, render_template, request, jsonify, Response
import time, hmac, hashlib, requests, threading
from decimal import Decimal
import queue

app = Flask(__name__)

API_KEY = "QAMgelwkaqIWoXzAn2"
API_SECRET = "oXy9pVlu9pEPHCi2DZc4PGYEYlYSaRrII5nE"
BYBIT_API_URL = "https://api.bybit.com"

logs = {}

def log(symbol, message):
    if symbol not in logs:
        logs[symbol] = queue.Queue()
    timestamp = time.strftime('%H:%M:%S')
    logs[symbol].put(f"{timestamp} | {message}")

def safe_request(method, url, **kwargs):
    for _ in range(3):
        try:
            response = requests.request(method, url, timeout=5, **kwargs)
            return response.json()
        except:
            time.sleep(1)
    return {}

def generate_signature(secret, params):
    sorted_params = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hmac.new(secret.encode(), sorted_params.encode(), hashlib.sha256).hexdigest()

def get_avg_entry_price(symbol, pos_idx):
    params = {
        "category": "linear",
        "symbol": symbol,
        "api_key": API_KEY,
        "timestamp": str(int(time.time()*1000))
    }
    params["sign"] = generate_signature(API_SECRET, params)
    r = safe_request("GET", f"{BYBIT_API_URL}/v5/position/list", params=params)
    for p in r.get("result", {}).get("list", []):
        if int(p["positionIdx"]) == pos_idx:
            return float(p["avgPrice"])
    return None

def update_tp_sl(symbol, tp, sl, pos_idx):
    params = {
        "category": "linear",
        "symbol": symbol,
        "positionIdx": pos_idx,
        "api_key": API_KEY,
        "timestamp": str(int(time.time()*1000))
    }
    if tp: params["takeProfit"] = str(tp)
    if sl: params["stopLoss"] = str(sl)
    params["sign"] = generate_signature(API_SECRET, params)
    safe_request("POST", f"{BYBIT_API_URL}/v5/position/trading-stop", json=params)

def is_long_open(symbol):
    params = {
        "category": "linear",
        "symbol": symbol,
        "api_key": API_KEY,
        "timestamp": str(int(time.time()*1000))
    }
    params["sign"] = generate_signature(API_SECRET, params)
    r = safe_request("GET", f"{BYBIT_API_URL}/v5/position/list", params=params)
    for p in r.get("result", {}).get("list", []):
        if int(p["positionIdx"]) == 1:
            return float(p["size"]) > 0
    return False

def is_short_closed(symbol):
    params = {
        "category": "linear",
        "symbol": symbol,
        "api_key": API_KEY,
        "timestamp": str(int(time.time()*1000))
    }
    params["sign"] = generate_signature(API_SECRET, params)
    r = safe_request("GET", f"{BYBIT_API_URL}/v5/position/list", params=params)
    for p in r.get("result", {}).get("list", []):
        if int(p["positionIdx"]) == 2:
            return float(p["size"]) == 0
    return False

def place_limit_short(symbol, qty, price, order_id):
    params = {
        "api_key": API_KEY,
        "category": "linear",
        "symbol": symbol,
        "side": "Sell",
        "orderType": "Limit",
        "price": str(price),
        "qty": str(qty),
        "timeInForce": "GTC",
        "positionIdx": 2,
        "orderLinkId": order_id,
        "timestamp": str(int(time.time()*1000))
    }
    params["sign"] = generate_signature(API_SECRET, params)
    safe_request("POST", f"{BYBIT_API_URL}/v5/order/create", json=params)
    log(symbol, f"🟠 وضع أمر شورت Limit عند {price}")

    while True:
        avg_price = get_avg_entry_price(symbol, 2)
        if avg_price:
            log(symbol, f"✅ تم تنفيذ أمر الشورت عند {avg_price}")
            return avg_price
        time.sleep(0.3)

def place_pending_long(symbol, qty, price, order_id):
    params = {
        "category": "linear",
        "symbol": symbol,
        "side": "Buy",
        "orderType": "Market",
        "triggerPrice": str(price),
        "triggerDirection": 1,
        "qty": str(qty),
        "positionIdx": 1,
        "orderLinkId": order_id,
        "api_key": API_KEY,
        "timestamp": str(int(time.time()*1000))
    }
    params["sign"] = generate_signature(API_SECRET, params)
    safe_request("POST", f"{BYBIT_API_URL}/v5/order/create", json=params)
    log(symbol, f"🟡 وضع أمر لونغ معلق عند {price}")

def place_limit_long(symbol, qty, price, order_id):
    params = {
        "api_key": API_KEY,
        "category": "linear",
        "symbol": symbol,
        "side": "Buy",
        "orderType": "Limit",
        "price": str(price),
        "qty": str(qty),
        "timeInForce": "GTC",
        "positionIdx": 1,
        "orderLinkId": order_id,
        "timestamp": str(int(time.time()*1000))
    }
    params["sign"] = generate_signature(API_SECRET, params)
    safe_request("POST", f"{BYBIT_API_URL}/v5/order/create", json=params)
    log(symbol, f"🔵 تعزيز لونغ Limit عند {price}")

def cancel_order(symbol, order_id):
    params = {
        "category": "linear",
        "symbol": symbol,
        "orderLinkId": order_id,
        "api_key": API_KEY,
        "timestamp": str(int(time.time()*1000))
    }
    params["sign"] = generate_signature(API_SECRET, params)
    safe_request("POST", f"{BYBIT_API_URL}/v5/order/cancel", json=params)
    log(symbol, f"🗑️ تم إلغاء الأمر: {order_id}")

def run_bot(symbol, qty, short_price):
    short_qty = round(Decimal(qty) * 14, 4)
    long_qty = round(Decimal(qty) * 7, 4)

    short_id = f"SHORT_{symbol}_{int(time.time())}"
    entry = place_limit_short(symbol, short_qty, short_price, short_id)

    tp_short = round(Decimal(entry) * Decimal("0.995"), 6)
    update_tp_sl(symbol, tp=tp_short, sl=None, pos_idx=2)
    log(symbol, f"🎯 تعيين TP للشورت عند {tp_short}")

    long_entry = round(Decimal(entry) * Decimal("1.005"), 6)
    long_id = f"LONG_{symbol}_{int(time.time())}"
    place_pending_long(symbol, long_qty, long_entry, long_id)

    reinforce_id = None
    long_triggered = False

    while True:
        time.sleep(1)

        if is_long_open(symbol) and not long_triggered:
            log(symbol, f"✅ تم فتح صفقة اللونغ عند {long_entry}")
            tp_l = round(Decimal(long_entry) * Decimal("1.005"), 6)
            sl_l = tp_short
            update_tp_sl(symbol, tp=tp_l, sl=sl_l, pos_idx=1)
            update_tp_sl(symbol, tp=None, sl=tp_l, pos_idx=2)
            reinforce_id = f"REINFORCE_{symbol}_{int(time.time())}"
            place_limit_long(symbol, long_qty, entry, reinforce_id)
            long_triggered = True

        if is_short_closed(symbol):
            log(symbol, f"✅ تم إغلاق الشورت، إلغاء الأوامر المعلقة")
            cancel_order(symbol, long_id)
            if reinforce_id:
                cancel_order(symbol, reinforce_id)
            break

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/start-bot", methods=["POST"])
def start_bot():
    data = request.json
    symbol = data.get("symbol").upper()
    qty = float(data.get("qty"))
    short_price = float(data.get("short_price"))

    thread = threading.Thread(target=run_bot, args=(symbol, qty, short_price))
    thread.start()

    return jsonify({"message": f"✅ بدأ البوت لزوج {symbol}"})

@app.route("/log-stream/<symbol>")
def log_stream(symbol):
    def event_stream():
        while True:
            if symbol in logs:
                try:
                    msg = logs[symbol].get(timeout=1)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    continue
            else:
                time.sleep(1)
    return Response(event_stream(), mimetype="text/event-stream")

if __name__ == "__main__":
    app.run(debug=True)
