# app.py

from flask import Flask, render_template, request, jsonify, redirect, session
from flask_socketio import SocketIO
import json
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = "softerx"

socketio = SocketIO(app, cors_allowed_origins="*")

DB = "db.json"

ADMIN_PASSWORD = "1111"


# =========================
# DATABASE
# =========================

def load_db():

    if not os.path.exists(DB):

        data = {
            "settings": {
                "tables": 5
            },
            "menu": [],
            "orders": [],
            "reviews": []
        }

        with open(DB, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    with open(DB, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db(data):

    with open(DB, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# =========================
# ROUTES
# =========================

@app.route("/")
def home():
    return redirect("/1")


@app.route("/<int:table_id>")
def table(table_id):
    return render_template("index.html", table_id=table_id)


@app.route("/admin")
def admin():

    if not session.get("admin"):
        return redirect("/login")

    return render_template("admin.html")


@app.route("/logout")
def logout():

    session.clear()

    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():

    error = ""

    if request.method == "POST":

        password = request.form.get("password")

        if password == ADMIN_PASSWORD:

            session["admin"] = True

            return redirect("/admin")

        else:
            error = "Неправильний пароль"

    return f"""
    <html>

    <head>

    <script src="https://cdn.tailwindcss.com"></script>

    </head>

    <body class="bg-black flex justify-center items-center h-screen">

        <form method="POST"
        class="bg-zinc-900 p-10 rounded-3xl w-[400px]">

            <h1 class="text-white text-4xl font-bold mb-5">
                Admin Login
            </h1>

            <input
            type="password"
            name="password"
            placeholder="Пароль"
            class="w-full p-4 rounded-xl bg-zinc-800 text-white mb-4">

            <button
            class="w-full bg-green-500 py-4 rounded-xl text-white font-bold">
                Увійти
            </button>

            <p class="text-red-500 mt-4">
                {error}
            </p>

        </form>

    </body>

    </html>
    """


# =========================
# MENU API
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
        "description": data["description"],
        "price": data["price"],
        "image": data["image"],
        "category": data["category"]
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


@app.route("/api/menu/edit/<int:item_id>", methods=["POST"])
def edit_menu(item_id):

    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"})

    db = load_db()

    data = request.json

    for item in db["menu"]:

        if item["id"] == item_id:

            item["name"] = data["name"]
            item["description"] = data["description"]
            item["price"] = data["price"]
            item["image"] = data["image"]
            item["category"] = data["category"]

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
        "total": data["total"],
        "status": "В черзі",
        "created": str(datetime.now())
    }

    db["orders"].append(order)

    save_db(db)

    socketio.emit("new_order", order)

    return jsonify({"success": True})


@app.route("/api/orders")
def get_orders():

    if not session.get("admin"):
        return jsonify([])

    db = load_db()

    return jsonify(db["orders"])


@app.route("/api/order/status", methods=["POST"])
def order_status():

    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"})

    db = load_db()

    data = request.json

    for order in db["orders"]:

        if order["id"] == data["id"]:

            order["status"] = data["status"]

    save_db(db)

    socketio.emit("order_updated", data)

    return jsonify({"success": True})


# =========================
# SOCKET
# =========================

@socketio.on("connect")
def connect():
    print("Connected")


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
