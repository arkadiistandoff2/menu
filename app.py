# app.py

from flask import Flask, render_template, request, jsonify, session, redirect
from flask_socketio import SocketIO, emit
import json
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = "softerx_secret_key"

socketio = SocketIO(app, cors_allowed_origins="*")

DB_FILE = "db.json"
ADMIN_PASSWORD = "1111"


# =========================
# DATABASE
# =========================

def load_db():
    if not os.path.exists(DB_FILE):
        default_db = {
            "settings": {
                "tables": 5
            },
            "menu": [],
            "orders": [],
            "reviews": []
        }

        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(default_db, f, indent=4, ensure_ascii=False)

    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# =========================
# ROUTES
# =========================

@app.route("/")
def home():
    return redirect("/1")


@app.route("/<int:table_id>")
def table(table_id):
    db = load_db()

    if table_id > db["settings"]["tables"]:
        return "Столик не існує"

    return render_template("index.html", table_id=table_id)


@app.route("/admin")
def admin():
    if not session.get("admin"):
        return redirect("/login")

    return render_template("admin.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password")

        if password == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")

    return """
    <html>
    <body style="background:#111;color:white;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;">
        <form method="POST">
            <input type="password" name="password" placeholder="Пароль"
            style="padding:15px;border:none;border-radius:10px;">
            <button style="padding:15px;border:none;border-radius:10px;background:lime;">
                Увійти
            </button>
        </form>
    </body>
    </html>
    """


# =========================
# API MENU
# =========================

@app.route("/api/menu")
def get_menu():
    db = load_db()
    return jsonify(db["menu"])


@app.route("/api/menu/add", methods=["POST"])
def add_menu():
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"})

    db = load_db()
    data = request.json

    item = {
        "id": int(datetime.now().timestamp()),
        "name": data["name"],
        "price": data["price"],
        "category": data["category"],
        "image": data["image"]
    }

    db["menu"].append(item)
    save_db(db)

    socketio.emit("menu_updated")

    return jsonify({"success": True})


@app.route("/api/menu/delete/<int:item_id>", methods=["POST"])
def delete_menu(item_id):
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"})

    db = load_db()

    db["menu"] = [x for x in db["menu"] if x["id"] != item_id]

    save_db(db)

    socketio.emit("menu_updated")

    return jsonify({"success": True})


# =========================
# ORDERS
# =========================

@app.route("/api/order", methods=["POST"])
def create_order():
    db = load_db()

    data = request.json

    order = {
        "id": int(datetime.now().timestamp()),
        "table": data["table"],
        "items": data["items"],
        "status": "В черзі",
        "created": str(datetime.now()),
        "comment": ""
    }

    db["orders"].append(order)

    save_db(db)

    socketio.emit("new_order", order)

    return jsonify({"success": True})


@app.route("/api/orders")
def get_orders():
    db = load_db()
    return jsonify(db["orders"])


@app.route("/api/order/status", methods=["POST"])
def update_order():
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"})

    db = load_db()

    data = request.json

    for order in db["orders"]:
        if order["id"] == data["id"]:
            order["status"] = data["status"]

            if "comment" in data:
                order["comment"] = data["comment"]

    save_db(db)

    socketio.emit("order_updated", data)

    return jsonify({"success": True})


# =========================
# REVIEWS
# =========================

@app.route("/api/review", methods=["POST"])
def add_review():
    db = load_db()

    data = request.json

    review = {
        "name": data["name"],
        "rating": data["rating"],
        "comment": data["comment"]
    }

    db["reviews"].append(review)

    save_db(db)

    socketio.emit("review_added", review)

    return jsonify({"success": True})


@app.route("/api/reviews")
def get_reviews():
    db = load_db()
    return jsonify(db["reviews"])


# =========================
# SOCKETS
# =========================

@socketio.on("connect")
def connect():
    print("User connected")


# =========================
# START
# =========================

if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=True
    )
