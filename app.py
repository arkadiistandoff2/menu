import os
import json
import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, session
from flask_socketio import SocketIO
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "softerx-default-key")

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

DB = "db.json"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "1111")
UPLOAD_FOLDER = os.path.join("static", "images")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# =========================
# DATABASE
# =========================

def load_db():
    if not os.path.exists(DB):
        data = {
            "settings": {"tables": 5},
            "categories": [{"id": 1, "name": "Основні страви"}, {"id": 2, "name": "Напої"}],
            "menu": [],
            "orders": []
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
    <head><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-black flex justify-center items-center h-screen font-sans">
        <form method="POST" class="bg-zinc-900 p-10 rounded-3xl w-[400px]">
            <h1 class="text-white text-4xl font-bold mb-5">Вхід для адміна</h1>
            <input type="password" name="password" placeholder="Пароль" class="w-full p-4 rounded-xl bg-zinc-800 text-white mb-4 outline-none border border-zinc-700">
            <button class="w-full bg-blue-600 hover:bg-blue-500 py-4 rounded-xl text-white font-bold transition">Увійти</button>
            <p class="text-red-500 mt-4 text-center">{error}</p>
        </form>
    </body>
    </html>
    """

# =========================
# CATEGORY API
# =========================

@app.route("/api/categories")
def get_categories():
    db = load_db()
    return jsonify(db.get("categories", []))

@app.route("/api/category/add", methods=["POST"])
def add_category():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    db = load_db()
    data = request.json
    cat = {"id": int(datetime.now().timestamp()), "name": data["name"]}
    if "categories" not in db:
        db["categories"] = []
    db["categories"].append(cat)
    save_db(db)
    socketio.emit("menu_updated")
    return jsonify({"success": True})

@app.route("/api/category/delete/<int:cat_id>", methods=["POST"])
def delete_category(cat_id):
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    db = load_db()
    db["categories"] = [x for x in db.get("categories", []) if x["id"] != cat_id]
    save_db(db)
    socketio.emit("menu_updated")
    return jsonify({"success": True})

@app.route("/api/category/edit/<int:cat_id>", methods=["POST"])
def edit_category(cat_id):
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    db = load_db()
    data = request.json
    for cat in db.get("categories", []):
        if cat["id"] == cat_id:
            cat["name"] = data["name"]
    save_db(db)
    socketio.emit("menu_updated")
    return jsonify({"success": True})

# =========================
# MENU API
# =========================

@app.route("/api/menu")
def get_menu():
    db = load_db()
    return jsonify(db["menu"])

@app.route("/api/menu/add", methods=["POST"])
def add_menu():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    db = load_db()
    
    name = request.form.get("name")
    description = request.form.get("description")
    price = request.form.get("price")
    category = request.form.get("category")
    image_file = request.files.get("image")
    
    image_name = ""
    if image_file:
        ext = image_file.filename.split('.')[-1]
        image_name = f"{uuid.uuid4().hex}.{ext}"
        image_file.save(os.path.join(app.config["UPLOAD_FOLDER"], image_name))

    item = {
        "id": int(datetime.now().timestamp()),
        "name": name,
        "description": description,
        "price": price,
        "image": image_name,
        "category": category
    }

    db["menu"].append(item)
    save_db(db)
    socketio.emit("menu_updated")
    return jsonify({"success": True})

@app.route("/api/menu/delete/<int:item_id>", methods=["POST"])
def delete_menu(item_id):
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    db = load_db()
    db["menu"] = [x for x in db["menu"] if x["id"] != item_id]
    save_db(db)
    socketio.emit("menu_updated")
    return jsonify({"success": True})

# =========================
# ORDERS API
# =========================

@app.route("/api/order", methods=["POST"])
def create_order():
    db = load_db()
    data = request.json
    
    order_id = int(datetime.now().timestamp())
    items_with_ids = []
    for i, item in enumerate(data["items"]):
        items_with_ids.append({
            "uid": f"{order_id}_{i}",
            "name": item["name"],
            "price": item["price"],
            "status": "В черзі"
        })

    order = {
        "id": order_id,
        "table": data["table"],
        "items": items_with_ids,
        "total": data["total"],
        "status": "Активне",
        "created": str(datetime.now().strftime("%H:%M"))
    }

    db["orders"].append(order)
    save_db(db)
    socketio.emit("new_order", order)
    return jsonify({"success": True, "order_id": order_id})

@app.route("/api/orders")
def get_orders():
    if not session.get("admin"): return jsonify([])
    db = load_db()
    active_orders = [o for o in db["orders"] if o["status"] == "Активне"]
    return jsonify(active_orders)

@app.route("/api/client_orders", methods=["POST"])
def get_client_orders():
    # Роут для клієнта, щоб перевіряти свої замовлення за ID
    db = load_db()
    data = request.json
    my_ids = data.get("ids", [])
    my_orders = [o for o in db["orders"] if o["id"] in my_ids]
    return jsonify(my_orders)

@app.route("/api/order/item_status", methods=["POST"])
def update_item_status():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    db = load_db()
    data = request.json
    
    updated_item_name = ""
    for order in db["orders"]:
        if order["id"] == data["order_id"]:
            for item in order["items"]:
                if item["uid"] == data["item_uid"]:
                    item["status"] = data["status"]
                    updated_item_name = item["name"]
            
            all_done = all(i["status"] in ["Готово", "Відхилено"] for i in order["items"])
            if all_done:
                order["status"] = "Завершене"

    save_db(db)
    socketio.emit("order_updated")
    # Надсилаємо спец-івент для пуш-сповіщень клієнта
    socketio.emit("notify_client", {
        "order_id": data["order_id"],
        "title": "Оновлення замовлення",
        "body": f"Ваша позиція '{updated_item_name}' - {data['status'].lower()}!"
    })
    return jsonify({"success": True})

@app.route("/api/order/all_status", methods=["POST"])
def update_all_status():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    db = load_db()
    data = request.json
    
    for order in db["orders"]:
        if order["id"] == data["order_id"]:
            for item in order["items"]:
                item["status"] = data["status"]
            order["status"] = "Завершене"

    save_db(db)
    socketio.emit("order_updated")
    socketio.emit("notify_client", {
        "order_id": data["order_id"],
        "title": "Оновлення замовлення",
        "body": f"Усе ваше замовлення - {data['status'].lower()}!"
    })
    return jsonify({"success": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=True)
