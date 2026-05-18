from gevent import monkey
monkey.patch_all()

import os, json, uuid, certifi, base64
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, session, Response
from flask_socketio import SocketIO, emit
from pymongo import MongoClient
from werkzeug.local import LocalProxy

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "super-secure-key-zlata")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Zlata")

_db_client = None
def get_db():
    global _db_client
    if _db_client is None:
        uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
        client = MongoClient(uri, tlsCAFile=certifi.where(), connect=False)
        _db_client = client["restaurant_db"]
    return _db_client

db = LocalProxy(get_db)

# --- МАРШРУТИ ЗАСТОСУНКУ ---
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

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
        return "<script>alert('Невірний пароль'); window.location='/login';</script>"
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# --- API: КАТЕГОРІЇ ТА МЕНЮ ---
@app.route("/api/categories")
def get_categories(): 
    return jsonify(list(db.categories.find({}, {"_id": 0})))

@app.route("/api/categories/add", methods=["POST"])
def add_category():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    name = request.json.get("name")
    if name:
        db.categories.update_one({"name": name}, {"$set": {"name": name}}, upsert=True)
        socketio.emit("menu_updated")
    return jsonify({"success": True})

@app.route("/api/menu")
def get_menu(): 
    return jsonify(list(db.menu.find({}, {"_id": 0})))

@app.route("/api/menu/add", methods=["POST"])
def add_menu():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    
    img = request.files.get("image")
    img_base64 = ""
    if img:
        img_base64 = f"data:{img.mimetype};base64,{base64.b64encode(img.read()).decode('utf-8')}"
        
    item_id = str(uuid.uuid4())[:8]
    db.menu.insert_one({
        "id": item_id,
        "name": request.form.get("name"),
        "price": float(request.form.get("price", 0)),
        "description": request.form.get("description", ""),
        "category": request.form.get("category"),
        "image": img_base64
    })
    socketio.emit("menu_updated")
    return jsonify({"success": True})

@app.route("/api/menu/edit", methods=["POST"])
def edit_menu():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    
    item_id = request.form.get("id")
    update_data = {
        "name": request.form.get("name"),
        "price": float(request.form.get("price", 0)),
        "description": request.form.get("description", ""),
        "category": request.form.get("category")
    }
    
    img = request.files.get("image")
    if img:
        update_data["image"] = f"data:{img.mimetype};base64,{base64.b64encode(img.read()).decode('utf-8')}"
        
    db.menu.update_one({"id": item_id}, {"$set": update_data})
    socketio.emit("menu_updated")
    return jsonify({"success": True})

@app.route("/api/menu/delete/<item_id>", methods=["POST"])
def delete_menu(item_id):
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    db.menu.delete_one({"id": item_id})
    socketio.emit("menu_updated")
    return jsonify({"success": True})

# --- API: ЗАМОВЛЕННЯ ---
@app.route("/api/orders/active")
def get_active_orders():
    if not session.get("admin"): return jsonify([])
    return jsonify(list(db.orders.find({"status": {"$ne": "Завершено"}}, {"_id": 0})))

@app.route("/api/order", methods=["POST"])
def create_order():
    data = request.json
    order_id = str(uuid.uuid4())[:6].upper()
    
    order = {
        "id": order_id,
        "table": data["table"],
        "items": data["items"],
        "total": float(data["total"]),
        "status": "Нове",
        "time": datetime.now().strftime("%H:%M:%S")
    }
    db.orders.insert_one(order)
    order.pop("_id", None)
    
    socketio.emit("new_order", order)
    return jsonify({"success": True, "order": order})

# --- API: ІМПОРТ / ЕКСПОРТ ---
@app.route("/api/export")
def export_data():
    if not session.get("admin"): return "Unauthorized", 401
    data = {
        "categories": list(db.categories.find({}, {"_id": 0})),
        "menu": list(db.menu.find({}, {"_id": 0}))
    }
    return Response(
        json.dumps(data, ensure_ascii=False, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment;filename=restaurant_backup.json'}
    )

@app.route("/api/import", methods=["POST"])
def import_data():
    if not session.get("admin"): return jsonify({"error": "Unauthorized"}), 401
    file = request.files.get("file")
    if not file: return jsonify({"error": "Файл не знайдено"}), 400
    
    try:
        data = json.load(file)
        if "categories" in data:
            db.categories.delete_many({})
            if data["categories"]: db.categories.insert_many(data["categories"])
        if "menu" in data:
            db.menu.delete_many({})
            if data["menu"]: db.menu.insert_many(data["menu"])
            
        socketio.emit("menu_updated")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- SOCKET.IO: РЕАЛЬНИЙ ЧАС ТА СТРІМ СЕСІЙ ---
@socketio.on("sync_client")
def handle_sync_client(data):
    client_id = data.get("client_id")
    if not client_id: return
    
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    client_data = {
        "client_id": client_id,
        "table": data.get("table"),
        "ip": ip,
        "user_agent": request.headers.get("User-Agent", "Unknown"),
        "cart": data.get("cart", []),
        "current_view": data.get("current_view", "Все"),
        "last_seen": datetime.now().strftime("%H:%M:%S")
    }
    db.clients.update_one({"client_id": client_id}, {"$set": client_data}, upsert=True)
    
    # Стрімінг активності в адмін панель в прямому ефірі
    socketio.emit("live_client_update", client_data)

@socketio.on("call_waiter")
def handle_waiter_call(data):
    emit("waiter_called_admin", {"table": data["table"], "time": datetime.now().strftime("%H:%M:%S")}, broadcast=True)

@socketio.on("change_order_status")
def handle_status_change(data):
    db.orders.update_one({"id": data["id"]}, {"$set": {"status": data["status"]}})
    socketio.emit("order_status_updated", data)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
