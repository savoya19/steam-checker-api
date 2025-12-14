from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from main import get_inventory, add_prices_to_items, parse_price_to_float, APP_ID, CURRENCY

app = Flask(__name__)
CORS(app)  # разрешаем запросы с любых сайтов

@app.route("/check", methods=["POST"])
def check():
    data = request.get_json()
    profile_url = data.get("link")
    if not profile_url:
        return jsonify({"error": "No link"}), 400

    try:
        items = get_inventory(profile_url)
        items = add_prices_to_items(items, app_id=APP_ID, currency=CURRENCY)

        total = 0.0
        for item in items:
            price_info = item.get("price") or {}
            value = parse_price_to_float(price_info.get("lowest_price"))
            if value is not None:
                total += value

        return jsonify({"total_value": total})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
