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

@app.route("/api/category/delete/<int:id>", methods=["POST"])
def del_cat(id):
    db.categories.delete_one({"id": id})
    socketio.emit("menu_updated"); return jsonify({"success": True})

@app.route("/api/menu")
def get_menu(): return jsonify(list(db.menu.find({}, {"_id": 0})))

@app.route("/api/menu/add", methods=["POST"])
def add_menu():
    img = request.files.get("image")
    img_name = f"{uuid.uuid4().hex}.{img.filename.split('.')[-1]}" if img else ""
    if img: img.save(os.path.join(app.config["UPLOAD_FOLDER"], img_name))
    
    db.menu.insert_one({
        "id": int(datetime.now().timestamp()),
        "name": request.form.get("name"),
        "description": request.form.get("description"),
        "price": request.form.get("price"),
        "image": img_name,
        "category": request.form.get("category")
    })
    socketio.emit("menu_updated"); return jsonify({"success": True})

@app.route("/api/menu/delete/<int:id>", methods=["POST"])
def del_menu(id):
    db.menu.delete_one({"id": id})
    socketio.emit("menu_updated"); return jsonify({"success": True})

@app.route("/api/order", methods=["POST"])
def create_order():
    data = request.json
    order_id = int(datetime.now().timestamp())
    items = [{**i, "status": "В черзі"} for i in data["items"]]
    order = {
        "id": order_id, "table": data["table"], "items": items,
        "total": data["total"], "client_id": data["client_id"],
        "comment": data.get("comment", ""), "status": "Активне",
        "created": datetime.now().strftime("%H:%M")
    }
    db.orders.insert_one(order)
    socketio.emit("new_order")
    return jsonify({"success": True, "order_id": order_id})

@app.route("/api/orders")
def get_orders():
    return jsonify(list(db.orders.find({"status": "Активне"}, {"_id": 0})))

@app.route("/api/order/all_status", methods=["POST"])
def update_status():
    d = request.json
    db.orders.update_one({"id": d["order_id"]}, {"$set": {"status": d["status"], "items.$[].status": d["status"]}})
    socketio.emit("order_updated")
    socketio.emit("notify_client", {"order_id": d["order_id"], "status": d["status"]})
    return jsonify({"success": True})

@app.route("/api/clients")
def get_clients():
    clients = list(db.clients.find({}, {"_id": 0}))
    return jsonify(clients)

@app.route("/api/review", methods=["POST"])
def add_review():
    db.reviews.insert_one({**request.json, "date": datetime.now().strftime("%d.%m %H:%M")})
    socketio.emit("reviews_updated"); return jsonify({"success": True})

@app.route("/api/reviews")
def get_reviews():
    revs = list(db.reviews.find({}, {"_id": 0}))
    for r in revs: r["order"] = db.orders.find_one({"id": r["order_id"]}, {"_id": 0, "items": 1, "table": 1})
    return jsonify(revs)

# Socket Logic
@socketio.on("sync_client")
def sync(data):
    cid = data.get("client_id")
    if not cid: return
    online_users[request.sid] = cid
    join_room(cid) # Кожен клієнт у своїй "кімнаті" для приватних повідомлень
    
    ua = data.get("user_agent", "")
    ua_short = ua.split('(')[1].split(')')[0] if '(' in ua else "Unknown Device"
    
    db.clients.update_one(
        {"client_id": cid},
        {"$set": {
            "ip": request.headers.get('X-Forwarded-For', request.remote_addr),
            "user_agent": ua,
            "user_agent_short": ua_short,
            "cart": data.get("cart", []),
            "notif_permission": data.get("notif_permission"),
            "last_seen": datetime.now().strftime("%d.%m %H:%M:%S"),
            "last_seen_raw": datetime.now().isoformat()
        }, "$addToSet": {"tables_visited": data.get("table_id")}},
        upsert=True
    )
    socketio.emit("clients_updated")

@socketio.on("disconnect")
def disc():
    if request.sid in online_users: del online_users[request.sid]
    socketio.emit("clients_updated")

@socketio.on("admin_send_message")
def admin_msg(data):
    # Відправка конкретному клієнту в його кімнату
    socketio.emit("admin_message", {"msg": data["msg"]}, room=data["client_id"])

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
ost="0.0.0.0", port=port)
