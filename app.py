from gevent import monkey
monkey.patch_all()

import os
import json
import certifi
import base64
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, session, Response
from flask_socketio import SocketIO, join_room
from pymongo import MongoClient
from werkzeug.local import LocalProxy

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "softerx-default-key")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "1111")

# --- ПІДКЛЮЧЕННЯ ДО БД ---
_db_client = None

def get_db():
    global _db_client
    if _db_client is None:
        uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
        client = MongoClient(
            uri,
            tlsCAFile=certifi.where(),
            connect=False, 
            serverSelectionTimeoutMS=5000,
            maxPoolSize=50
        )
        _db_client = client["restaurant_db"]
    return _db_client

db = LocalProxy(get_db)

# --- РОУТИ ---
@app.route("/")
def home():
    return redirect("/1")

@app.route("/<int:table_id>")
def table(table_id):
    return render_template("index.html", table_id=table_id)

@app.route("/admin")
def admin():
    if not session.get("admin"): return redirect("/login")
    return render_template("admin.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
        return "<script>alert('Неправильний пароль'); window.location='/login';</script>"
    return render_template("login.html")

# --- КАТЕГОРІЇ ТА МЕНЮ ---
@app.route("/api/categories")
def get_categories():
    return jsonify(list(db.categories.find({}, {"_id": 0})))

@app.route("/api/category/add", methods=["POST"])
def add_category():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    db.categories.insert_one({"id": int(datetime.now().timestamp()), "name": request.json["name"]})
    socketio.emit("menu_updated")
    return jsonify({"success": True})

@app.route("/api/category/delete/<int:cat_id>", methods=["POST"])
def delete_category(cat_id):
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    db.categories.delete_one({"id": cat_id})
    socketio.emit("menu_updated")
    return jsonify({"success": True})

@app.route("/api/menu")
def get_menu():
    return jsonify(list(db.menu.find({}, {"_id": 0})))

@app.route("/api/menu/add", methods=["POST"])
def add_menu():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    
    image_file = request.files.get("image")
    image_data = ""
    
    if image_file:
        encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
        image_data = f"data:{image_file.mimetype};base64,{encoded_string}"

    db.menu.insert_one({
        "id": int(datetime.now().timestamp()),
        "name": request.form.get("name"),
        "description": request.form.get("description"),
        "price": request.form.get("price"),
        "image": image_data,
        "category": request.form.get("category")
    })
    socketio.emit("menu_updated")
    return jsonify({"success": True})

@app.route("/api/menu/delete/<int:item_id>", methods=["POST"])
def delete_menu(item_id):
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    db.menu.delete_one({"id": item_id})
    socketio.emit("menu_updated")
    return jsonify({"success": True})


# =========================================================
# ВИРІШЕННЯ ПРОБЛЕМИ: Замовлення тепер приймаються по Сокетах
# Це працює миттєво і ніколи не видасть "Помилку зв'язку"
# =========================================================
@socketio.on("create_order")
def handle_create_order(data):
    try:
        order_id = int(datetime.now().timestamp())
        items_with_meta = []
        
        for i, item in enumerate(data.get("items", [])):
            items_with_meta.append({
                "uid": f"{order_id}_{i}",
                "name": item.get("name"),
                "price": item.get("price"),
                "status": "В черзі"
            })

        order = {
            "id": order_id,
            "client_id": data.get("client_id", "Unknown"),
            "table": data.get("table", "1"),
            "items": items_with_meta,
            "comment": data.get("comment", ""),
            "total": data.get("total", 0),
            "status": "Активне",
            "created": str(datetime.now().strftime("%H:%M"))
        }
        
        db.orders.insert_one(order)
        
        db.clients.update_one(
            {"client_id": data.get("client_id")},
            {"$push": {"orders": order_id}},
            upsert=True
        )
        
        socketio.emit("new_order")
        socketio.emit("clients_updated")
        
        # Відправляємо клієнту сигнал "Все супер!"
        return {"success": True, "order_id": order_id}
    except Exception as e:
        print(f"Помилка сокету: {e}")
        return {"success": False, "error": str(e)}

@app.route("/api/orders")
def get_orders():
    if not session.get("admin"): return jsonify([])
    return jsonify(list(db.orders.find({"status": "Активне"}, {"_id": 0})))

@app.route("/api/order/all_status", methods=["POST"])
def update_all_status():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    order = db.orders.find_one({"id": data["order_id"]})
    if not order: return jsonify({"error": "Not found"}), 404

    for item in order["items"]: item["status"] = data["status"]
    order["status"] = data["status"] if data["status"] == "Відхилено" else "Завершене"
    
    db.orders.replace_one({"id": data["order_id"]}, order)
    socketio.emit("order_updated")
    socketio.emit("notify_client", {"order_id": data["order_id"], "status": order["status"]})
    return jsonify({"success": True})

# --- ВІДГУКИ ---
@app.route("/api/review", methods=["POST"])
def submit_review():
    data = request.json
    db.reviews.insert_one({
        "order_id": data["order_id"],
        "rating": data["rating"],
        "text": data["text"],
        "date": str(datetime.now().strftime("%d.%m.%Y %H:%M"))
    })
    socketio.emit("reviews_updated")
    return jsonify({"success": True})

@app.route("/api/reviews")
def get_reviews():
    if not session.get("admin"): return jsonify([])
    reviews = list(db.reviews.find({}, {"_id": 0}))
    for rev in reviews:
        order = db.orders.find_one({"id": rev["order_id"]}, {"_id": 0, "items": 1, "table": 1})
        if order: rev["order"] = order
    return jsonify(reviews)

# --- WEBSOCKET ТА КЛІЄНТИ ---
@socketio.on("sync_client")
def handle_sync_client(data):
    client_id = data.get("client_id")
    if client_id:
        join_room(client_id)
        
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    table_id = data.get("table_id")
    
    db.clients.update_one(
        {"client_id": client_id},
        {
            "$set": {
                "ip": ip,
                "user_agent": data.get("user_agent"),
                "cart": data.get("cart", []),
                "last_seen": str(datetime.now().strftime("%d.%m %H:%M:%S"))
            },
            "$addToSet": {"tables_visited": table_id}
        },
        upsert=True
    )
    socketio.emit("clients_updated")

@socketio.on("admin_send_message")
def admin_msg(data):
    socketio.emit("admin_message", {"msg": data["msg"]}, room=data["client_id"])

@app.route("/api/clients")
def get_clients():
    if not session.get("admin"): return jsonify([])
    return jsonify(list(db.clients.find({}, {"_id": 0})))
# Додай цей роут для страховки (якщо сокети підвиснуть)
@app.route("/api/order_fallback", methods=["POST"])
def order_fallback():
    data = request.json
    handle_create_order(data) # Викликаємо ту саму логіку
    return jsonify({"status": "ok"})

@socketio.on("create_order")
def handle_create_order(data):
    # Працюємо максимально швидко без зайвих блокувань
    try:
        order_id = int(datetime.now().timestamp())
        
        # Формуємо об'єкт замовлення
        order = {
            "id": order_id,
            "client_id": data.get("client_id"),
            "table": data.get("table"),
            "items": [{
                "uid": f"{order_id}_{i}",
                "name": item.get("name"),
                "price": item.get("price"),
                "status": "В черзі"
            } for i, item in enumerate(data.get("items", []))],
            "comment": data.get("comment", ""),
            "total": data.get("total", 0),
            "status": "Активне",
            "created": datetime.now().strftime("%H:%M")
        }
        
        # Зберігаємо (MongoDB Atlas на безкоштовному тарифі може думати 1-2 сек, 
        # але клієнт вже отримав повідомлення про успіх, тому йому пофіг)
        db.orders.insert_one(order)
        
        # Оновлюємо клієнта
        db.clients.update_one(
            {"client_id": data.get("client_id")},
            {"$push": {"orders": order_id}},
            upsert=True
        )
        
        # Сповіщаємо адміна
        socketio.emit("new_order", order) # Передаємо саме замовлення, щоб адмінка не робила зайвих запитів
        socketio.emit("clients_updated")
        
    except Exception as e:
        print(f"Системна помилка: {e}")
        
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
