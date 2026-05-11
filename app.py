from gevent import monkey
monkey.patch_all()

import os, json, uuid, certifi
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, session, Response
from flask_socketio import SocketIO, emit, join_room
from pymongo import MongoClient
from werkzeug.local import LocalProxy

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "admin-power-key")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "1111")
UPLOAD_FOLDER = os.path.join("static", "images")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Сховище для мапінгу Socket ID -> Client ID
online_users = {}

_db_client = None
def get_db():
    global _db_client
    if _db_client is None:
        uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
        client = MongoClient(uri, tlsCAFile=certifi.where(), connect=False)
        _db_client = client["restaurant_db"]
    return _db_client

db = LocalProxy(get_db)

@app.route("/")
def home(): return redirect("/1")

@app.route("/<int:table_id>")
def table(table_id): return render_template("index.html", table_id=table_id)

@app.route("/admin")
def admin():
    if not session.get("admin"): return redirect("/login")
    return render_template("admin.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# API Секція
@app.route("/api/categories")
def get_cats(): return jsonify(list(db.categories.find({}, {"_id": 0})))

@app.route("/api/category/add", methods=["POST"])
def add_cat():
    db.categories.insert_one({"id": int(datetime.now().timestamp()), "name": request.json["name"]})
    socketio.emit("menu_updated"); return jsonify({"success": True})

from gevent import monkey
monkey.patch_all()

import os
import json
import uuid
import certifi
import base64  # Для хранения картинок в базе
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, session, Response
from flask_socketio import SocketIO
from pymongo import MongoClient
from werkzeug.local import LocalProxy

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "softerx-default-key")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "1111")

# --- ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ ---
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

# --- РОУТЫ КЛИЕНТА ---
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

# --- КАТЕГОРИИ И МЕНЮ ---
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
        # Превращаем картинку в строку Base64 для хранения в MongoDB Atlas
        encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
        image_data = f"data:{image_file.mimetype};base64,{encoded_string}"

    db.menu.insert_one({
        "id": int(datetime.now().timestamp()),
        "name": request.form.get("name"),
        "description": request.form.get("description"),
        "price": request.form.get("price"),
        "image": image_data, # Храним саму картинку, а не путь к файлу
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

# --- ЗАКАЗЫ ---
@app.route("/api/order", methods=["POST"])
def create_order():
    data = request.json
    order_id = int(datetime.now().timestamp())
    items_with_meta = []
    
    for i, item in enumerate(data["items"]):
        items_with_meta.append({
            "uid": f"{order_id}_{i}",
            "name": item["name"],
            "price": item["price"],
            "to_go": item.get("to_go", False),
            "status": "В черзі"
        })

    order = {
        "id": order_id,
        "client_id": data.get("client_id", "Unknown"),
        "table": data["table"],
        "items": items_with_meta,
        "comment": data.get("comment", ""),
        "total": data["total"],
        "status": "Активне",
        "created": str(datetime.now().strftime("%H:%M"))
    }
    db.orders.insert_one(order)
    
    db.clients.update_one(
        {"client_id": data.get("client_id")},
        {"$push": {"orders": order_id}},
        upsert=True
    )
    
    del order["_id"]
    socketio.emit("new_order", order)
    socketio.emit("clients_updated")
    return jsonify({"success": True, "order_id": order_id})

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

# --- ОТЗЫВЫ ---
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

# --- КЛИЕНТЫ ---
@socketio.on("sync_client")
def handle_sync_client(data):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    client_id = data.get("client_id")
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

@app.route("/api/clients")
def get_clients():
    if not session.get("admin"): return jsonify([])
    return jsonify(list(db.clients.find({}, {"_id": 0})))

# --- ЭКСПОРТ / ИМПОРТ ---
@app.route("/api/export")
def export_data():
    if not session.get("admin"): return "Unauthorized", 401
    data = {
        "categories": list(db.categories.find({}, {"_id": 0})),
        "menu": list(db.menu.find({}, {"_id": 0})),
        "users": list(db.clients.find({}, {"_id": 0}))
    }
    return Response(json.dumps(data, ensure_ascii=False), mimetype='application/json', headers={'Content-Disposition':'attachment;filename=db_export.json'})

@app.route("/api/import", methods=["POST"])
def import_data():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    file = request.files.get("file")
    if not file: return jsonify({"error": "No file"})
    
    data = json.load(file)
    if "categories" in data:
        db.categories.delete_many({})
        if data["categories"]: db.categories.insert_many(data["categories"])
    if "menu" in data:
        db.menu.delete_many({})
        if data["menu"]: db.menu.insert_many(data["menu"])
        
    socketio.emit("menu_updated")
    return jsonify({"success": True})

# --- ИСПРАВЛЕННЫЙ ЗАПУСК ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
