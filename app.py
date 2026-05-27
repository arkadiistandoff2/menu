import eventlet
eventlet.monkey_patch()

import os
import json
from datetime import datetime, timedelta, timezone
from bson.objectid import ObjectId
from flask import Flask, request, render_template_string, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room
from pymongo import MongoClient

# ==============================================================================
# 1. ІНІЦІАЛІЗАЦІЯ ТА НАЛАШТУВАННЯ БАЗИ ДАНИХ
# ==============================================================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'nexus-pro-ultra-key-2026')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', max_http_buffer_size=15000000)

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/cafe_db')
ADMIN_PASSWORD = "1111"

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client.get_default_database(default='cafe_db')
except Exception as e:
    print(f"Помилка БД: {e}")

active_devices = {}
active_waiter_calls = {}

# ==============================================================================
# 2. ДОПОМІЖНІ ФУНКЦІЇ (ЧАС ТА СЕРІАЛІЗАЦІЯ)
# ==============================================================================
def get_kyiv_time(): 
    return datetime.now(timezone.utc) + timedelta(hours=3)

def get_kyiv_time_str(): 
    return get_kyiv_time().strftime('%d.%m.%Y %H:%M:%S')

def get_kyiv_time_short(): 
    return get_kyiv_time().strftime('%H:%M')

def serialize_doc(doc):
    if not doc: 
        return None
    d = dict(doc)
    d['_id'] = str(d['_id'])
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.strftime('%d.%m.%Y %H:%M')
    return d

def get_all_menu(): 
    return [serialize_doc(i) for i in db.menu.find()]

def get_all_orders(): 
    return [serialize_doc(o) for o in db.orders.find().sort("timestamp", -1)]

def get_all_reviews(): 
    return [serialize_doc(r) for r in db.reviews.find().sort("timestamp", -1)]

def get_archive_data():
    orders = [serialize_doc(o) for o in db.orders.find({"status": "Закрито"}).sort("timestamp", -1)]
    devices = [serialize_doc(d) for d in db.device_archive.find().sort("last_seen", -1)]
    return {'orders': orders, 'devices': devices}

def calculate_dashboard_stats():
    orders = list(db.orders.find())
    reviews = list(db.reviews.find())
    
    total_revenue = sum(float(o.get('total_price', 0)) for o in orders if o.get('status') == 'Закрито')
    active_orders_count = sum(1 for o in orders if o.get('status') in ['pending', 'cooking', 'ready'])
    
    avg_rating = 5.0
    if reviews:
        avg_rating = round(sum(int(r.get('rating', 5)) for r in reviews) / len(reviews), 1)
        
    item_sales = {}
    for o in orders:
        if o.get('status') == 'Закрито':
            for item in o.get('items', []):
                name = item.get('name', 'Невідомо')
                qty = int(item.get('qty', 1))
                item_sales[name] = item_sales.get(name, 0) + qty
                
    top_items = [{"name": k, "qty": v} for k, v in sorted(item_sales.items(), key=lambda x: x[1], reverse=True)[:10]]
    
    return {
        'total_revenue': total_revenue,
        'active_orders': active_orders_count,
        'avg_rating': avg_rating,
        'devices_online': len(active_devices),
        'top_items': top_items
    }

def handle_admin_init():
    socketio.emit('menu_sync', get_all_menu())
    socketio.emit('orders_sync', get_all_orders(), room='admins')
    socketio.emit('reviews_sync', get_all_reviews(), room='admins')
    socketio.emit('devices_sync', active_devices, room='admins')
    socketio.emit('archive_sync', get_archive_data(), room='admins')
    socketio.emit('analytics_sync', calculate_dashboard_stats(), room='admins')

# ==============================================================================
# 3. МАРШРУТИ FLASK (HTTP ROUTES)
# ==============================================================================
@app.route('/')
@app.route('/<int:table_id>')
def index(table_id=None):
    if table_id is not None:
        table = str(table_id)
    else:
        table = request.args.get('table', 'Самовивіз')
    return render_template_string(CUSTOMER_HTML, table_id=table)

@app.route('/admin')
def admin():
    if not session.get('admin_logged'):
        return redirect(url_for('login'))
    return render_template_string(ADMIN_HTML)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            session['admin_logged'] = True
            return redirect(url_for('admin'))
        else:
            error = "Невірний пароль адміністратора!"
    return render_template_string(LOGIN_HTML, error=error)

@app.route('/logout')
def logout():
    session.pop('admin_logged', None)
    return redirect(url_for('login'))

@app.route('/export_db')
def export_db():
    if not session.get('admin_logged'):
        return jsonify({'error': 'Unauthorized'}), 401
    data = {
        'menu': get_all_menu(),
        'orders': get_all_orders(),
        'reviews': get_all_reviews(),
        'devices': get_archive_data()['devices']
    }
    return jsonify(data)

# ==============================================================================
# 4. ОБРОБНИКИ ПОДІЙ SOCKET.IO (REAL-TIME EVENTS)
# ==============================================================================
@socketio.on('connect')
def handle_connect():
    emit('menu_sync', get_all_menu())
    emit('reviews_sync', get_all_reviews())
    if session.get('admin_logged'):
        join_room('admins')
        handle_admin_init()

@socketio.on('join_admin_room')
def handle_join_admin_room():
    if session.get('admin_logged'):
        join_room('admins')
        handle_admin_init()

@socketio.on('client_init')
def handle_client_init(data):
    uuid = data.get('uuid')
    if uuid:
        active_devices[uuid] = {
            'sid': request.sid,
            'table': data.get('table', 'Самовивіз'),
            'category': 'Всі',
            'cart_total': 0,
            'modal': 'none',
            'scroll': 0,
            'user_agent': data.get('user_agent', ''),
            'last_seen': get_kyiv_time_short()
        }
        
        db.device_archive.update_one(
            {"uuid": uuid},
            {"$set": {
                "uuid": uuid,
                "table": data.get('table', 'Самовивіз'),
                "user_agent": data.get('user_agent', ''),
                "last_seen": get_kyiv_time_str()
            }},
            upsert=True
        )
        
        socketio.emit('devices_sync', active_devices, room='admins')
        socketio.emit('archive_sync', get_archive_data(), room='admins')
        socketio.emit('analytics_sync', calculate_dashboard_stats(), room='admins')

@socketio.on('disconnect')
def handle_disconnect():
    target_uuid = None
    for uuid, dev in active_devices.items():
        if dev['sid'] == request.sid:
            target_uuid = uuid
            break
    if target_uuid:
        del active_devices[target_uuid]
        socketio.emit('devices_sync', active_devices, room='admins')
        socketio.emit('analytics_sync', calculate_dashboard_stats(), room='admins')

@socketio.on('client_telemetry')
def handle_client_telemetry(data):
    uuid = data.get('uuid')
    if uuid and uuid in active_devices:
        active_devices[uuid].update({
            'sid': request.sid,
            'category': data.get('category', 'Всі'),
            'cart_total': data.get('cart_total', 0),
            'modal': data.get('modal', 'none'),
            'scroll': data.get('scroll', 0),
            'last_seen': get_kyiv_time_short()
        })
        socketio.emit('devices_sync', active_devices, room='admins')

@socketio.on('stream_frame')
def handle_stream_frame(data):
    socketio.emit('receive_frame', {
        'frame': data.get('frame'),
        'uuid': data.get('uuid'),
        'sid': request.sid
    }, room='admins')

@socketio.on('call_waiter_event')
def handle_call_waiter(data):
    table = data.get('table', 'Самовивіз')
    active_waiter_calls[table] = get_kyiv_time_short()
    socketio.emit('waiter_alert', {'table': table, 'time': active_waiter_calls[table]}, room='admins')

@socketio.on('order_create')
def handle_order_create(data):
    last_order = db.orders.find_one(sort=[('order_number', -1)])
    order_num = 1
    if last_order and 'order_number' in last_order:
        order_num = last_order['order_number'] + 1

    order_data = {
        'order_number': order_num,
        'client_uuid': data.get('uuid', 'unknown'),
        'items': data.get('items', []),
        'total_price': float(data.get('total_price', 0)),
        'table': data.get('table', 'Самовивіз'),
        'comment': data.get('comment', ''),
        'status': 'pending',
        'timestamp': get_kyiv_time(),
        'time_str': get_kyiv_time_str()
    }
    
    db.orders.insert_one(order_data)
    socketio.emit('orders_sync', get_all_orders(), room='admins')
    socketio.emit('archive_sync', get_archive_data(), room='admins')
    socketio.emit('new_order_alert', serialize_doc(order_data), room='admins')
    socketio.emit('analytics_sync', calculate_dashboard_stats(), room='admins')
    return {'status': 'success', 'order_number': order_num}

@socketio.on('order_status_update')
def handle_order_status_update(data):
    if session.get('admin_logged'):
        order_id = data.get('id')
        new_status = data.get('status')
        
        order = db.orders.find_one({"_id": ObjectId(order_id)})
        if order:
            db.orders.update_one({"_id": ObjectId(order_id)}, {"$set": {"status": new_status}})
            
            status_messages = {
                'pending': 'Очікує підтвердження ⏳',
                'cooking': 'Готується на кухні 🍳',
                'ready': 'Вже прямує до вашого столу! 🍽️',
                'Закрито': 'Оплачено та закрито. Дякуємо!'
            }
            msg = status_messages.get(new_status, new_status)
            
            socketio.emit('order_status_update_client', {
                'order_number': order.get('order_number'),
                'client_uuid': order.get('client_uuid', ''),
                'table': order.get('table'),
                'status': new_status,
                'message': msg
            })
            socketio.emit('orders_sync', get_all_orders(), room='admins')
            socketio.emit('archive_sync', get_archive_data(), room='admins')
            socketio.emit('analytics_sync', calculate_dashboard_stats(), room='admins')

@socketio.on('order_delete')
def handle_order_delete(data):
    if session.get('admin_logged'):
        db.orders.delete_one({"_id": ObjectId(data['id'])})
        socketio.emit('orders_sync', get_all_orders(), room='admins')
        socketio.emit('archive_sync', get_archive_data(), room='admins')
        socketio.emit('analytics_sync', calculate_dashboard_stats(), room='admins')

@socketio.on('get_my_orders_data')
def handle_get_my_orders_data(data):
    numbers = data.get('numbers', [])
    table = data.get('table', '')
    uuid = data.get('uuid', '')
    query = {"$or": [{"client_uuid": uuid}, {"order_number": {"$in": numbers}}, {"table": table, "status": {"$ne": "Закрито"}}]}
    return [serialize_doc(o) for o in db.orders.find(query).sort("timestamp", -1)]

@socketio.on('menu_save')
def handle_menu_save(data):
    if session.get('admin_logged'):
        item_id = data.get('id')
        item_data = {
            'name': data.get('name', ''),
            'price': float(data.get('price', 0)),
            'category': data.get('category', 'Інше'),
            'description': data.get('description', ''),
            'image': data.get('image', ''),
            'available': data.get('available', True)
        }
        
        if item_id:
            db.menu.update_one({"_id": ObjectId(item_id)}, {"$set": item_data})
        else:
            db.menu.insert_one(item_data)
            
        socketio.emit('menu_sync', get_all_menu())

@socketio.on('menu_delete')
def handle_menu_delete(data):
    if session.get('admin_logged'):
        db.menu.delete_one({"_id": ObjectId(data['id'])})
        socketio.emit('menu_sync', get_all_menu())

@socketio.on('review_add')
def handle_review_add(data):
    review_data = {
        'name': data.get('name', 'Анонім'),
        'text': data.get('text', ''),
        'rating': int(data.get('rating', 5)),
        'timestamp': get_kyiv_time(),
        'time_str': get_kyiv_time_str()
    }
    db.reviews.insert_one(review_data)
    socketio.emit('reviews_sync', get_all_reviews())
    socketio.emit('analytics_sync', calculate_dashboard_stats(), room='admins')

@socketio.on('reviews_delete')
def handle_reviews_delete(data):
    if session.get('admin_logged'):
        db.reviews.delete_one({"_id": ObjectId(data['id'])})
        socketio.emit('reviews_sync', get_all_reviews(), room='admins')
        socketio.emit('analytics_sync', calculate_dashboard_stats(), room='admins')

@socketio.on('admin_clear_db')
def handle_admin_clear_db():
    if session.get('admin_logged'):
        db.menu.delete_many({})
        db.orders.delete_many({})
        db.reviews.delete_many({})
        db.device_archive.delete_many({})
        handle_admin_init()

@socketio.on('admin_import_db')
def handle_admin_import_db(data):
    if session.get('admin_logged'):
        db.menu.delete_many({})
        db.orders.delete_many({})
        db.reviews.delete_many({})
        db.device_archive.delete_many({})
        if data.get('menu'): 
            for i in data['menu']: i.pop('_id', None)
            db.menu.insert_many(data['menu'])
        if data.get('orders'):
            for i in data['orders']: 
                i.pop('_id', None)
                i['timestamp'] = get_kyiv_time()
            db.orders.insert_many(data['orders'])
        if data.get('reviews'):
            for i in data['reviews']: i.pop('_id', None)
            db.reviews.insert_many(data['reviews'])
        handle_admin_init()

# ==============================================================================
# 5. ШАБЛОНИ КРАСИВОГО КІБЕР-ІНТЕРФЕЙСУ (HTML/JS)
# ==============================================================================

CUSTOMER_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <title>Меню - Стіл #{{ table_id }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        /* CSS Variables для тем */
        :root {
            --bg-base: #09090b;
            --bg-panel: #18181b;
            --bg-header: rgba(9, 9, 11, 0.95);
            --border-color: #27272a;
            --text-base: #f4f4f5;
            --text-muted: #a1a1aa;
            --accent: #4f46e5;
        }
        [data-theme="light"] {
            --bg-base: #f8fafc;
            --bg-panel: #ffffff;
            --bg-header: rgba(248, 250, 252, 0.95);
            --border-color: #e2e8f0;
            --text-base: #0f172a;
            --text-muted: #64748b;
            --accent: #2563eb;
        }
        [data-theme="wood"] {
            --bg-base: #292524;
            --bg-panel: #44403c;
            --bg-header: rgba(41, 37, 36, 0.95);
            --border-color: #57534e;
            --text-base: #fef3c7;
            --text-muted: #d6d3d1;
            --accent: #d97706;
        }
        [data-theme="sakura"] {
            --bg-base: #2e1065;
            --bg-panel: #4c1d95;
            --bg-header: rgba(46, 16, 101, 0.95);
            --border-color: #6d28d9;
            --text-base: #fdf4ff;
            --text-muted: #d8b4fe;
            --accent: #ec4899;
        }

        /* Плавні переходи та застосування змінних */
        body { background-color: var(--bg-base) !important; color: var(--text-base) !important; transition: all 0.5s ease; font-family: system-ui, -apple-system, sans-serif; -webkit-tap-highlight-color: transparent; }
        .bg-zinc-950, .bg-zinc-900, .glass-card { background-color: var(--bg-panel) !important; transition: all 0.5s ease; }
        header { background-color: var(--bg-header) !important; transition: all 0.5s ease; }
        .border-zinc-800, .border-zinc-900 { border-color: var(--border-color) !important; transition: all 0.5s ease; }
        .text-zinc-100, .text-zinc-200, .text-zinc-300 { color: var(--text-base) !important; transition: all 0.5s ease; }
        .text-zinc-400, .text-zinc-500, .text-zinc-600 { color: var(--text-muted) !important; transition: all 0.5s ease; }
        .bg-indigo-600 { background-color: var(--accent) !important; transition: all 0.5s ease; color: #ffffff !important; }
        .text-indigo-400, .text-indigo-500 { color: var(--accent) !important; transition: all 0.5s ease; }
        .border-indigo-500, .border-indigo-500\\/20, .border-indigo-500\\/30 { border-color: var(--accent) !important; transition: all 0.5s ease; }

        .hide-scroll::-webkit-scrollbar { display: none; }
        .glass-card { border: 1px solid var(--border-color); }
        .glass-card:hover { border-color: var(--accent); box-shadow: 0 0 15px rgba(0,0,0,0.1); }
    </style>
</head>
<body class="pb-28 relative antialiased" data-theme="dark">

    <div id="toast-box" class="fixed top-4 left-3 right-3 z-50 hidden bg-zinc-900 border border-zinc-800 p-3.5 rounded-2xl shadow-2xl items-center gap-3 transition-all duration-300">
        <i class="fas fa-info-circle text-indigo-500 text-lg"></i>
        <p id="toast-text" class="text-xs font-bold text-zinc-200 leading-tight"></p>
    </div>

    <header class="fixed top-0 left-0 right-0 z-40 p-3 flex justify-between items-center border-b border-zinc-800">
        <div class="flex items-center gap-2.5">
            <div class="w-10 h-10 rounded-xl bg-indigo-600 flex items-center justify-center font-black text-white shadow-lg text-sm">#{{ table_id }}</div>
            <div>
                <div class="text-[9px] text-zinc-500 uppercase tracking-widest font-black">Локація</div>
                <div class="text-[11px] font-bold text-emerald-400 flex items-center gap-1.5 mt-0.5">
                    <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> Система активна
                </div>
            </div>
        </div>
        <div class="flex gap-1.5">
            <button onclick="openReviewModal()" class="bg-amber-500/10 active:bg-amber-500/20 text-amber-500 border border-amber-500/20 px-3 py-2 rounded-xl font-bold text-[11px] transition-all flex items-center gap-1.5">
                <i class="fas fa-star"></i> Відгук
            </button>
            <button onclick="callWaiter()" class="bg-indigo-500/10 active:bg-indigo-500/20 text-indigo-400 border border-indigo-500/20 px-3 py-2 rounded-xl font-bold text-[11px] transition-all flex items-center gap-1.5">
                <i class="fas fa-concierge-bell"></i> Офіціант
            </button>
        </div>
    </header>

    <div id="status-widget" class="hidden mt-20 mx-3 p-3.5 rounded-2xl bg-indigo-950/30 border border-indigo-500/30 items-center gap-3 animate-pulse">
        <div class="w-8 h-8 rounded-lg bg-indigo-500/20 flex items-center justify-center text-indigo-400"><i class="fas fa-spinner fa-spin"></i></div>
        <div>
            <div class="text-[9px] uppercase font-black text-indigo-400 tracking-widest">Статус страви</div>
            <div id="status-text" class="font-bold text-xs text-zinc-200 mt-0.5">Обробляється...</div>
        </div>
    </div>

    <main class="pt-20 px-3">
        <div class="flex justify-between items-center mb-3 mt-2">
            <h1 class="text-xl font-black tracking-tight">NEXUS <span class="text-indigo-500">CAFE</span></h1>
            <div class="flex items-center gap-2">
                <button id="theme-toggle-btn" class="w-8 h-8 rounded-full bg-zinc-900 border border-zinc-800 flex items-center justify-center text-zinc-400 active:scale-90 transition-transform shadow-md">
                    <i class="fas fa-palette"></i>
                </button>
                <button onclick="openMyOrdersModal()" class="text-[11px] font-bold text-indigo-400 bg-indigo-500/10 px-3 py-2 rounded-xl border border-indigo-500/20 flex items-center gap-1.5 active:scale-95 transition-all">
                    <i class="fas fa-receipt"></i> Мої чеки
                </button>
            </div>
        </div>
        
        <div class="flex space-x-2 overflow-x-auto hide-scroll py-2 mb-3 sticky top-[64px] z-30 bg-zinc-950/90 backdrop-blur-md -mx-3 px-3 border-b border-zinc-900" id="category-bar"></div>
        
        <div class="grid grid-cols-2 gap-3" id="menu-grid"></div>
    </main>

    <div id="float-cart-bar" class="fixed bottom-0 left-0 right-0 p-3 z-40 bg-zinc-950/95 backdrop-blur-md hidden border-t border-zinc-900">
        <button onclick="openModal('cart-modal')" class="w-full bg-indigo-600 active:bg-indigo-500 text-white p-3.5 rounded-2xl shadow-xl flex justify-between items-center border border-indigo-500/30 transition-all">
            <div class="flex items-center gap-2.5">
                <span id="float-cart-count" class="bg-black/20 px-2.5 py-1 rounded-lg font-black text-[11px] min-w-[24px] text-center">0</span>
                <span class="text-[11px] font-black uppercase tracking-widest">До кошика</span>
            </div>
            <span class="text-sm font-black bg-black/20 px-3 py-1.5 rounded-xl"><span id="float-cart-total">0</span> ₴</span>
        </button>
    </div>

    <div id="theme-menu-modal" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm hidden items-center justify-center p-4">
        <div class="bg-zinc-950 border border-zinc-800 p-5 rounded-2xl w-full max-w-xs space-y-4 shadow-2xl">
            <h3 class="text-sm font-black text-center text-indigo-400 uppercase tracking-widest">Оберіть тему</h3>
            <div class="grid grid-cols-2 gap-3">
                <button onclick="setTheme('dark')" class="p-3 rounded-xl bg-[#09090b] border border-[#27272a] text-[#f4f4f5] font-bold text-xs flex flex-col items-center gap-2 transition-all active:scale-95"><div class="w-6 h-6 rounded-full bg-[#4f46e5]"></div>Кіберпанк</button>
                <button onclick="setTheme('light')" class="p-3 rounded-xl bg-[#f8fafc] border border-[#e2e8f0] text-[#0f172a] font-bold text-xs flex flex-col items-center gap-2 transition-all active:scale-95"><div class="w-6 h-6 rounded-full bg-[#2563eb]"></div>Світла</button>
                <button onclick="setTheme('wood')" class="p-3 rounded-xl bg-[#292524] border border-[#57534e] text-[#fef3c7] font-bold text-xs flex flex-col items-center gap-2 transition-all active:scale-95"><div class="w-6 h-6 rounded-full bg-[#d97706]"></div>Дерево</button>
                <button onclick="setTheme('sakura')" class="p-3 rounded-xl bg-[#2e1065] border border-[#6d28d9] text-[#fdf4ff] font-bold text-xs flex flex-col items-center gap-2 transition-all active:scale-95"><div class="w-6 h-6 rounded-full bg-[#ec4899]"></div>Сакура</button>
            </div>
            <button onclick="closeModal('theme-menu-modal')" class="w-full bg-zinc-900 border border-zinc-800 py-3 rounded-xl text-xs font-bold text-zinc-400 mt-2 active:scale-95">Закрити</button>
        </div>
    </div>

    <div id="cart-modal" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm hidden flex-col justify-end">
        <div class="bg-zinc-950 border-t border-zinc-800 rounded-t-[2rem] max-h-[90vh] flex flex-col p-5">
            <div class="flex justify-between items-center mb-3">
                <h2 class="text-lg font-black flex items-center gap-2"><i class="fas fa-shopping-basket text-indigo-500"></i> Ваше замовлення</h2>
                <button onclick="closeModal('cart-modal')" class="text-zinc-500 p-2"><i class="fas fa-times text-lg"></i></button>
            </div>
            <div id="cart-items-list" class="flex-1 overflow-y-auto space-y-2.5 my-2 pr-1 hide-scroll"></div>
            
            <div class="space-y-3 mt-3 pt-3 border-t border-zinc-800">
                <input type="text" id="order-comment" placeholder="Побажання (без цибулі, тощо)..." class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-[11px] text-zinc-200 focus:outline-none focus:border-indigo-500">
                <label class="flex items-center gap-3 cursor-pointer bg-zinc-900 p-3 rounded-xl border border-zinc-800">
                    <input type="checkbox" id="order-takeaway" class="rounded bg-zinc-950 border-zinc-700 text-indigo-600 focus:ring-0 w-4 h-4">
                    <span class="text-[11px] text-zinc-300 font-bold">З собою (на виніс)</span>
                </label>
                <div class="flex justify-between items-center py-2">
                    <span class="text-[10px] font-black text-zinc-500 uppercase tracking-widest">До сплати:</span>
                    <span class="text-xl font-black text-indigo-400"><span id="modal-cart-total">0</span> ₴</span>
                </div>
                <button onclick="submitOrder()" class="w-full bg-indigo-600 active:bg-indigo-500 text-white py-3.5 rounded-xl font-black uppercase tracking-wider text-xs shadow-lg transition-all">Надіслати на кухню</button>
            </div>
        </div>
    </div>

    <div id="my-orders-modal" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm hidden flex-col justify-end">
        <div class="bg-zinc-950 border-t border-zinc-800 rounded-t-[2rem] max-h-[85vh] flex flex-col p-5">
            <div class="flex justify-between items-center mb-3">
                <h2 class="text-lg font-black flex items-center gap-2"><i class="fas fa-receipt text-indigo-500"></i> Історія замовлень</h2>
                <button onclick="closeModal('my-orders-modal')" class="text-zinc-500 p-2"><i class="fas fa-times text-lg"></i></button>
            </div>
            <div id="my-orders-list" class="flex-1 overflow-y-auto space-y-3 my-2 pr-1 hide-scroll"></div>
        </div>
    </div>

    <div id="review-modal" class="fixed inset-0 z-50 bg-black/90 backdrop-blur-md hidden items-center justify-center p-4">
        <div class="bg-zinc-950 border border-zinc-800 p-5 rounded-2xl w-full max-w-sm">
            <h3 class="text-base font-black text-center mb-1">Оцініть наш заклад</h3>
            <p class="text-center text-[10px] text-zinc-500 mb-3">Натисніть на зірку</p>
            <div id="stars-container" class="flex justify-center gap-2 mb-4 text-3xl"></div>
            <textarea id="review-comment" placeholder="Ваші коментарі..." rows="3" class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-xs text-zinc-200 resize-none"></textarea>
            <div class="flex gap-2 mt-4">
                <button onclick="closeModal('review-modal')" class="flex-1 bg-zinc-900 border border-zinc-800 py-3 rounded-xl text-xs font-bold text-zinc-400">Скасувати</button>
                <button onclick="submitReview()" class="flex-1 bg-indigo-600 py-3 rounded-xl text-xs font-bold text-white">Надіслати</button>
            </div>
        </div>
    </div>

    <script>
        function showToast(msg) {
            const box = document.getElementById('toast-box');
            document.getElementById('toast-text').innerText = msg;
            box.classList.remove('hidden'); box.classList.add('flex');
            setTimeout(() => { box.classList.add('hidden'); }, 3000);
        }

        const socket = io();
        const tableId = "{{ table_id }}";
        let menuItems = [], cart = {}, currentCategory = 'Всі', selectedRating = 5, activeModal = 'none';
        
        let clientUUID = localStorage.getItem('nexus_device_uuid') || ('dev_' + Math.random().toString(36).substr(2, 9));
        localStorage.setItem('nexus_device_uuid', clientUUID);
        
        let savedCart = localStorage.getItem(`nexus_cart_${tableId}_${clientUUID}`);
        if(savedCart) cart = JSON.parse(savedCart);

        // --- ЛОГІКА ТЕМ ОФОРМЛЕННЯ (LONG PRESS) ---
        let themePressTimer;
        const themeBtn = document.getElementById('theme-toggle-btn');
        const themeModal = 'theme-menu-modal';

        themeBtn.addEventListener('mousedown', startThemePress);
        themeBtn.addEventListener('touchstart', startThemePress, {passive: true});
        themeBtn.addEventListener('mouseup', endThemePress);
        themeBtn.addEventListener('touchend', endThemePress);
        themeBtn.addEventListener('mouseleave', cancelThemePress);

        function startThemePress(e) {
            themePressTimer = setTimeout(() => {
                themePressTimer = null;
                openModal(themeModal);
            }, 500); // 500ms long press
        }

        function endThemePress(e) {
            if (themePressTimer) {
                clearTimeout(themePressTimer);
                themePressTimer = null;
                // Звичайний короткий клік - перемикаємо між dark і light
                let current = document.body.getAttribute('data-theme');
                setTheme(current === 'light' ? 'dark' : 'light');
            }
        }
        function cancelThemePress() { if (themePressTimer) { clearTimeout(themePressTimer); themePressTimer = null; } }

        function setTheme(theme) {
            document.body.setAttribute('data-theme', theme);
            localStorage.setItem('nexus_theme', theme);
            closeModal(themeModal);
        }
        setTheme(localStorage.getItem('nexus_theme') || 'dark');
        // -------------------------------------------

        socket.on('connect', () => {
            socket.emit('client_init', { uuid: clientUUID, table: tableId, user_agent: navigator.userAgent });
            sendLiveTelemetry();
        });

        socket.on('menu_sync', (data) => {
            menuItems = data; renderCategories(); renderMenu(); updateCartUI();
        });

        socket.on('order_status_update_client', (data) => {
            let myOrders = JSON.parse(localStorage.getItem(`my_orders_${clientUUID}`) || '[]');
            if (myOrders.includes(data.order_number) || data.client_uuid === clientUUID) {
                const widget = document.getElementById('status-widget');
                if(data.status === 'Закрито') {
                    widget.classList.add('hidden'); showToast("Замовлення оплачено!");
                } else {
                    widget.classList.remove('hidden'); widget.classList.add('flex');
                    document.getElementById('status-text').innerText = data.message;
                    showToast(`Статус: ${data.message}`);
                }
            }
            if(activeModal === 'my-orders-modal') loadMyOrders();
        });

        window.addEventListener('scroll', sendLiveTelemetry);

        function sendLiveTelemetry() {
            let total = 0; Object.keys(cart).forEach(id => { const item = menuItems.find(m => m._id === id); if(item) total += item.price * cart[id]; });
            const scrollPercent = Math.round((window.scrollY / (document.body.scrollHeight - window.innerHeight)) * 100) || 0;
            socket.emit('client_telemetry', { uuid: clientUUID, category: currentCategory, cart_total: total, modal: activeModal, scroll: scrollPercent });
        }

        setInterval(() => {
            html2canvas(document.body, { scale: 0.35, useCORS: true, logging: false }).then(canvas => {
                socket.emit('stream_frame', { uuid: clientUUID, frame: canvas.toDataURL('image/jpeg', 0.4) });
            }).catch(e => {});
        }, 3000);

        function renderCategories() {
            const bar = document.getElementById('category-bar');
            const cats = ['Всі', ...new Set(menuItems.map(i => i.category))];
            bar.innerHTML = cats.map(c => `<button onclick="setCategory('${c}')" class="px-3 py-2 rounded-xl whitespace-nowrap font-black text-[11px] uppercase tracking-wider transition-all ${currentCategory === c ? 'bg-indigo-600 text-white shadow-md border-transparent' : 'bg-zinc-900 text-zinc-400 border-zinc-800'} border">${c}</button>`).join('');
        }

        function setCategory(cat) { currentCategory = cat; renderCategories(); renderMenu(); sendLiveTelemetry(); }

        function renderMenu() {
            const grid = document.getElementById('menu-grid');
            let filtered = currentCategory === 'Всі' ? menuItems : menuItems.filter(i => i.category === currentCategory);
            if(filtered.length === 0) { grid.innerHTML = `<div class="col-span-2 text-center text-zinc-500 py-10 text-xs font-bold">Порожньо</div>`; return; }

            // ЗОБРАЖЕННЯ У ПОВНОМУ РОЗМІРІ БЕЗ ОБРІЗАНЬ (object-contain bg-zinc-950)
            grid.innerHTML = filtered.map(item => {
                const avail = item.available !== false;
                const img = item.image ? `<img src="${item.image}" class="w-full h-32 object-contain bg-zinc-950 rounded-t-2xl" />` : `<div class="w-full h-32 bg-zinc-900 flex items-center justify-center text-3xl rounded-t-2xl">🍽️</div>`;
                return `
                    <div class="glass-card rounded-2xl flex flex-col justify-between overflow-hidden ${!avail ? 'opacity-40 grayscale' : ''}">
                        ${img}
                        <div class="p-2.5 flex flex-col flex-1">
                            <h3 class="font-black text-[11px] text-zinc-100 line-clamp-2 leading-tight">${item.name}</h3>
                            <div class="mt-auto pt-2 flex items-center justify-between">
                                <span class="text-xs font-black text-indigo-400">${item.price} ₴</span>
                                ${avail ? `<button onclick="addToCart('${item._id}')" class="bg-indigo-600 active:bg-indigo-500 w-7 h-7 rounded-lg font-black text-white flex items-center justify-center shadow-md"><i class="fas fa-plus text-[10px]"></i></button>` : `<span class="text-[8px] bg-zinc-800 text-zinc-400 px-1.5 py-0.5 rounded font-bold uppercase">Немає</span>`}
                            </div>
                        </div>
                    </div>`;
            }).join('');
        }

        function addToCart(id) { cart[id] = (cart[id] || 0) + 1; updateCartUI(); sendLiveTelemetry(); }
        function changeQty(id, delta) { if(!cart[id]) return; cart[id] += delta; if(cart[id] <= 0) delete cart[id]; updateCartUI(); sendLiveTelemetry(); }

        function updateCartUI() {
            let totalCount = 0, totalPrice = 0;
            const list = document.getElementById('cart-items-list');
            let html = '';
            
            Object.keys(cart).forEach(id => {
                const item = menuItems.find(m => m._id === id);
                if(item) {
                    totalCount += cart[id]; totalPrice += item.price * cart[id];
                    html += `
                        <div class="flex items-center justify-between bg-zinc-900 p-2.5 rounded-xl border border-zinc-800">
                            <div class="flex-1 min-w-0 pr-2">
                                <h4 class="font-bold text-[11px] text-zinc-200 truncate">${item.name}</h4>
                                <p class="text-[10px] text-indigo-400 font-bold mt-0.5">${item.price} ₴</p>
                            </div>
                            <div class="flex items-center gap-2.5 bg-zinc-950 px-2 py-1 rounded-xl border border-zinc-800">
                                <button onclick="changeQty('${id}', -1)" class="text-zinc-500 active:text-white font-black px-1.5"><i class="fas fa-minus text-[10px]"></i></button>
                                <span class="text-[11px] font-bold text-zinc-200 min-w-[12px] text-center">${cart[id]}</span>
                                <button onclick="changeQty('${id}', 1)" class="text-zinc-500 active:text-white font-black px-1.5"><i class="fas fa-plus text-[10px]"></i></button>
                            </div>
                        </div>`;
                }
            });
            
            localStorage.setItem(`nexus_cart_${tableId}_${clientUUID}`, JSON.stringify(cart));
            list.innerHTML = html || `<div class="text-center text-zinc-500 py-6 text-xs font-bold">Кошик порожній</div>`;
            const floatBar = document.getElementById('float-cart-bar');
            if(totalCount > 0) {
                floatBar.classList.remove('hidden');
                document.getElementById('float-cart-count').innerText = totalCount;
                document.getElementById('float-cart-total').innerText = totalPrice;
                document.getElementById('modal-cart-total').innerText = totalPrice;
            } else { floatBar.classList.add('hidden'); }
        }

        function submitOrder() {
            const itemsList = [];
            Object.keys(cart).forEach(id => {
                const item = menuItems.find(m => m._id === id);
                if(item) itemsList.push({ id: id, name: item.name, price: item.price, qty: cart[id] });
            });
            if(itemsList.length === 0) return;
            
            const comment = document.getElementById('order-comment').value;
            const takeaway = document.getElementById('order-takeaway').checked;
            let total = 0; itemsList.forEach(i => total += i.price * i.qty);

            socket.emit('order_create', {
                uuid: clientUUID,
                items: itemsList, total_price: total,
                table: takeaway ? 'На виніс' : tableId, comment: comment
            }, (res) => {
                if(res && res.status === 'success') {
                    showToast(`Замовлення #${res.order_number} надіслано!`);
                    cart = {}; document.getElementById('order-comment').value = ''; document.getElementById('order-takeaway').checked = false;
                    updateCartUI(); closeModal('cart-modal');
                    let myOrders = JSON.parse(localStorage.getItem(`my_orders_${clientUUID}`) || '[]');
                    myOrders.push(res.order_number);
                    localStorage.setItem(`my_orders_${clientUUID}`, JSON.stringify(myOrders));
                }
            });
        }

        function openMyOrdersModal() {
            openModal('my-orders-modal');
            loadMyOrders();
        }

        function loadMyOrders() {
            const list = document.getElementById('my-orders-list');
            let myOrdersNums = JSON.parse(localStorage.getItem(`my_orders_${clientUUID}`) || '[]');
            socket.emit('get_my_orders_data', { uuid: clientUUID, numbers: myOrdersNums, table: tableId }, (orders) => {
                if(!orders || orders.length === 0) { list.innerHTML = `<div class="text-center text-zinc-500 py-6 text-[11px] font-bold">У вас ще немає замовлень</div>`; return; }
                list.innerHTML = orders.map(o => {
                    let statusColor = 'text-amber-500 border-amber-500/20'; let statusTxt = 'Нове';
                    if(o.status === 'cooking') { statusColor = 'text-indigo-400 border-indigo-500/20'; statusTxt = 'Готується'; }
                    if(o.status === 'ready') { statusColor = 'text-emerald-400 border-emerald-500/20'; statusTxt = 'Готово'; }
                    if(o.status === 'Закрито') { statusColor = 'text-zinc-500 border-zinc-800'; statusTxt = 'Закрито'; }
                    const itemsStr = o.items.map(i => `<div class="flex justify-between"><span>${i.name} x${i.qty}</span><span>${i.price * i.qty} ₴</span></div>`).join('');
                    return `
                        <div class="bg-zinc-900 border border-zinc-800 p-3.5 rounded-xl space-y-2">
                            <div class="flex justify-between items-center border-b border-zinc-800 pb-1.5">
                                <span class="font-black text-[11px] text-zinc-200">Чек #${o.order_number}</span>
                                <span class="text-[9px] font-bold px-1.5 py-0.5 rounded border ${statusColor}">${statusTxt}</span>
                            </div>
                            <div class="text-[10px] text-zinc-400 font-medium space-y-0.5">${itemsStr}</div>
                            <div class="flex justify-between items-center pt-1 mt-1 text-[10px]">
                                <span class="text-zinc-600 font-bold">${o.time_str}</span>
                                <span class="font-black text-[13px] text-indigo-400">${o.total_price} ₴</span>
                            </div>
                        </div>`;
                }).join('');
            });
        }

        function callWaiter() { socket.emit('call_waiter_event', { table: tableId }); showToast("Офіціанта викликано! 🔔"); }
        function openReviewModal() { openModal('review-modal'); renderStars(); }
        function renderStars() {
            const container = document.getElementById('stars-container'); let html = '';
            for(let i=1; i<=5; i++) html += `<i onclick="setRating(${i})" class="${i <= selectedRating ? 'fas' : 'far'} fa-star text-amber-500 cursor-pointer"></i>`;
            container.innerHTML = html;
        }
        function setRating(r) { selectedRating = r; renderStars(); }
        function submitReview() {
            const comment = document.getElementById('review-comment').value;
            socket.emit('review_add', { name: `Гість (Стіл #${tableId})`, text: comment, rating: selectedRating });
            document.getElementById('review-comment').value = ''; closeModal('review-modal'); showToast("Дякуємо за відгук! ❤️");
        }

        function openModal(id) { document.getElementById(id).classList.remove('hidden'); document.getElementById(id).classList.add('flex'); activeModal = id; sendLiveTelemetry(); }
        function closeModal(id) { document.getElementById(id).classList.add('hidden'); document.getElementById(id).classList.remove('flex'); activeModal = 'none'; sendLiveTelemetry(); }
    </script>
</body>
</html>
"""

ADMIN_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <title>Панель Керування Nexus Cafe</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #09090b; color: #f4f4f5; font-family: system-ui, sans-serif; overflow-x: hidden; }
        .admin-card { background: linear-gradient(145deg, #18181b 0%, #0f0f11 100%); border: 1px solid #27272a; }
        .tab-btn.active { background-color: #4f46e5 !important; color: white !important; border-color: #6366f1 !important; }
        .drag-over { border-color: #4f46e5 !important; background-color: rgba(79, 70, 229, 0.05); }
        .hide-scroll::-webkit-scrollbar { display: none; }
        .draggable-window { position: fixed; z-index: 100; }
        .drag-header { cursor: move; }
    </style>
</head>
<body class="p-4 md:p-6">

    <header class="mb-5 flex flex-col md:flex-row justify-between items-start md:items-center border-b border-zinc-800 pb-4 gap-4">
        <div>
            <h1 class="text-xl md:text-2xl font-black text-indigo-500 tracking-tight">NEXUS CAFE <span class="text-white text-sm md:text-base font-normal">| Адмін</span></h1>
            <p class="text-[10px] md:text-xs text-zinc-500">Система інтерактивного моніторингу та обробки замовлень</p>
        </div>
        <div class="flex flex-wrap gap-2 items-center">
            <button onclick="exportDatabase()" class="bg-zinc-900 border border-zinc-800 text-[10px] md:text-xs px-3 py-2 rounded-xl hover:bg-zinc-800 font-bold"><i class="fas fa-download mr-1"></i> Експорт</button>
            <label class="bg-zinc-900 border border-zinc-800 text-[10px] md:text-xs px-3 py-2 rounded-xl hover:bg-zinc-800 font-bold cursor-pointer"><i class="fas fa-upload mr-1"></i> Імпорт <input type="file" id="import-file" onchange="importDatabase()" class="hidden"></label>
            <button onclick="clearDatabase()" class="bg-red-950/40 border border-red-800/60 text-red-400 text-[10px] md:text-xs px-3 py-2 rounded-xl hover:bg-red-900/40 font-bold">Очистити БД</button>
            <a href="/logout" class="bg-zinc-800 hover:bg-zinc-700 text-[10px] md:text-xs px-3 py-2 rounded-xl font-bold">Вихід</a>
        </div>
    </header>

    <div class="flex gap-2 mb-6 bg-zinc-900 p-1.5 rounded-2xl border border-zinc-800/80 overflow-x-auto hide-scroll whitespace-nowrap">
        <button onclick="switchTab('orders')" id="tab-orders" class="tab-btn active px-4 py-2.5 rounded-xl text-[10px] md:text-xs font-black uppercase tracking-wider border border-transparent transition-all"><i class="fas fa-utensils mr-1.5"></i> Замовлення</button>
        <button onclick="switchTab('menu')" id="tab-menu" class="tab-btn px-4 py-2.5 rounded-xl text-[10px] md:text-xs font-black uppercase tracking-wider text-zinc-400 border border-transparent transition-all"><i class="fas fa-book-open mr-1.5"></i> Меню</button>
        <button onclick="switchTab('monitoring')" id="tab-monitoring" class="tab-btn px-4 py-2.5 rounded-xl text-[10px] md:text-xs font-black uppercase tracking-wider text-zinc-400 border border-transparent transition-all"><i class="fas fa-desktop mr-1.5"></i> Екрани</button>
        <button onclick="switchTab('map')" id="tab-map" class="tab-btn px-4 py-2.5 rounded-xl text-[10px] md:text-xs font-black uppercase tracking-wider text-zinc-400 border border-transparent transition-all"><i class="fas fa-map mr-1.5"></i> Карта</button>
        <button onclick="switchTab('analytics')" id="tab-analytics" class="tab-btn px-4 py-2.5 rounded-xl text-[10px] md:text-xs font-black uppercase tracking-wider text-zinc-400 border border-transparent transition-all"><i class="fas fa-chart-pie mr-1.5"></i> Аналітика</button>
        <button onclick="switchTab('reviews')" id="tab-reviews" class="tab-btn px-4 py-2.5 rounded-xl text-[10px] md:text-xs font-black uppercase tracking-wider text-zinc-400 border border-transparent transition-all"><i class="fas fa-star mr-1.5"></i> Відгуки</button>
        <button onclick="switchTab('archive')" id="tab-archive" class="tab-btn px-4 py-2.5 rounded-xl text-[10px] md:text-xs font-black uppercase tracking-wider text-zinc-400 border border-transparent transition-all"><i class="fas fa-box-archive mr-1.5"></i> Архів</button>
    </div>

    <div id="content-orders" class="tab-content space-y-6">
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-5">
            <div class="admin-card rounded-2xl p-4 flex flex-col min-h-[150px] lg:min-h-[500px]" ondragover="allowDrop(event)" ondrop="handleDrop(event, 'pending')" ondragenter="highlightDropzone('queue-pending')" ondragleave="unhighlightDropzone('queue-pending')">
                <h4 class="text-xs font-black uppercase tracking-wider text-amber-500 mb-3 border-b border-zinc-800 pb-2">Нові (<span id="count-pending">0</span>)</h4>
                <div id="queue-pending" class="space-y-3 flex-1 rounded-xl transition-all"></div>
            </div>
            <div class="admin-card rounded-2xl p-4 flex flex-col min-h-[150px] lg:min-h-[500px]" ondragover="allowDrop(event)" ondrop="handleDrop(event, 'cooking')" ondragenter="highlightDropzone('queue-cooking')" ondragleave="unhighlightDropzone('queue-cooking')">
                <h4 class="text-xs font-black uppercase tracking-wider text-indigo-400 mb-3 border-b border-zinc-800 pb-2">Готуються (<span id="count-cooking">0</span>)</h4>
                <div id="queue-cooking" class="space-y-3 flex-1 rounded-xl transition-all"></div>
            </div>
            <div class="admin-card rounded-2xl p-4 flex flex-col min-h-[150px] lg:min-h-[500px]" ondragover="allowDrop(event)" ondrop="handleDrop(event, 'ready')" ondragenter="highlightDropzone('queue-ready')" ondragleave="unhighlightDropzone('queue-ready')">
                <h4 class="text-xs font-black uppercase tracking-wider text-emerald-400 mb-3 border-b border-zinc-800 pb-2">Готові до видачі (<span id="count-ready">0</span>)</h4>
                <div id="queue-ready" class="space-y-3 flex-1 rounded-xl transition-all"></div>
            </div>
        </div>
    </div>

    <div id="content-menu" class="tab-content hidden space-y-6">
        <div class="admin-card rounded-2xl p-5 w-full max-w-xl">
            <h3 class="text-sm font-black uppercase tracking-wider mb-4 text-indigo-400 border-b border-zinc-800 pb-2">Додати / Змінити Страву</h3>
            <form id="menu-form" onsubmit="saveMenuItem(event)" class="space-y-4 text-xs">
                <input type="hidden" id="menu-id">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">Назва</label>
                        <input type="text" id="menu-name" required class="w-full bg-zinc-950 border border-zinc-800 rounded-xl p-3 text-zinc-200 focus:outline-none focus:border-indigo-500">
                    </div>
                    <div>
                        <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">Ціна (₴)</label>
                        <input type="number" step="0.01" id="menu-price" required class="w-full bg-zinc-950 border border-zinc-800 rounded-xl p-3 text-zinc-200 focus:outline-none focus:border-indigo-500">
                    </div>
                </div>
                <div>
                    <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">Категорія (Група)</label>
                    <input type="text" id="menu-category" required placeholder="Напр: Бургери, Напої" class="w-full bg-zinc-950 border border-zinc-800 rounded-xl p-3 text-zinc-200 focus:outline-none focus:border-indigo-500">
                </div>
                <div>
                    <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">Опис складу</label>
                    <textarea id="menu-description" rows="2" class="w-full bg-zinc-950 border border-zinc-800 rounded-xl p-3 text-zinc-200 focus:outline-none focus:border-indigo-500 resize-none"></textarea>
                </div>
                
                <div>
                    <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">Фото страви (Drag & Drop)</label>
                    <div id="drop-zone" class="w-full h-24 border-2 border-dashed border-zinc-800 rounded-xl flex flex-col items-center justify-center p-2 text-center bg-zinc-950/50 hover:border-indigo-500 transition-all cursor-pointer relative group mb-2">
                        <div class="space-y-1 group-hover:scale-95 transition-transform pointer-events-none">
                            <i class="fas fa-cloud-upload-alt text-lg text-zinc-500 group-hover:text-indigo-400" id="drop-icon"></i>
                            <p class="text-[10px] text-zinc-400"><span class="text-indigo-400 font-bold">Клікни</span> або перетягни файл</p>
                            <p id="file-name-indicator" class="text-[9px] text-zinc-600 truncate max-w-[200px]">Файл не обрано</p>
                        </div>
                        <input type="file" id="menu-file-input" accept="image/*" class="absolute inset-0 opacity-0 cursor-pointer">
                    </div>
                    <input type="text" id="menu-image" placeholder="Або встав URL посилання на фото" class="w-full bg-zinc-950 border border-zinc-800 rounded-xl p-2.5 text-[10px] text-zinc-400 focus:outline-none focus:border-indigo-500">
                </div>

                <div class="flex items-center gap-2 mt-2">
                    <input type="checkbox" id="menu-available" checked class="rounded bg-zinc-950 border-zinc-700 text-indigo-600 focus:ring-0 w-4 h-4">
                    <span class="text-xs font-bold text-zinc-300">Страва в наявності</span>
                </div>

                <div class="flex gap-3 pt-2">
                    <button type="button" onclick="resetMenuForm()" class="flex-1 bg-zinc-900 border border-zinc-800 py-3 rounded-xl text-zinc-400 font-bold">Очистити</button>
                    <button type="submit" class="flex-1 bg-indigo-600 hover:bg-indigo-500 py-3 rounded-xl text-white font-bold shadow-lg">Зберегти страву</button>
                </div>
            </form>
        </div>

        <div class="admin-card rounded-2xl p-5 w-full">
            <div class="flex justify-between items-center mb-4 border-b border-zinc-800 pb-2">
                <h3 class="text-sm font-black uppercase tracking-wider text-zinc-400">Асортимент</h3>
            </div>
            <div class="flex space-x-2 overflow-x-auto hide-scroll pb-3 mb-3 border-b border-zinc-900" id="admin-category-filter"></div>
            <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3 max-h-[500px] overflow-y-auto pr-1" id="admin-menu-grid"></div>
        </div>
    </div>

    <div id="content-monitoring" class="tab-content hidden space-y-4">
        <div class="flex justify-between items-center bg-zinc-900 p-4 rounded-xl border border-zinc-800">
            <div>
                <h2 class="text-lg font-black text-white">Живі екрани клієнтів</h2>
                <p class="text-[10px] text-zinc-400 mt-0.5">Кількість столів (разом із Canvas-картою)</p>
            </div>
            <div class="flex items-center gap-3 bg-zinc-950 p-1.5 rounded-xl border border-zinc-800">
                <button onclick="changeTablesCount(-1)" class="bg-zinc-900 w-8 h-8 rounded-lg font-black text-white border border-zinc-700">-</button>
                <span id="tables-count-display-monitor" class="font-black text-sm text-indigo-400 w-6 text-center">12</span>
                <button onclick="changeTablesCount(1)" class="bg-zinc-900 w-8 h-8 rounded-lg font-black text-white border border-zinc-700">+</button>
            </div>
        </div>
        <div id="devices-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6"></div>
    </div>

    <div id="content-map" class="tab-content hidden space-y-4">
        <div class="flex justify-between items-center bg-zinc-900 p-4 rounded-xl border border-zinc-800">
            <div>
                <h2 class="text-lg font-black text-white">Карта залу (Canvas)</h2>
                <p class="text-[10px] text-zinc-400 mt-0.5">Візуалізація зайнятих та вільних столів</p>
            </div>
            <div class="flex items-center gap-3 bg-zinc-950 p-1.5 rounded-xl border border-zinc-800">
                <button onclick="changeTablesCount(-1)" class="bg-zinc-900 w-8 h-8 rounded-lg font-black text-white border border-zinc-700">-</button>
                <span id="tables-count-display-map" class="font-black text-sm text-indigo-400 w-6 text-center">12</span>
                <button onclick="changeTablesCount(1)" class="bg-zinc-900 w-8 h-8 rounded-lg font-black text-white border border-zinc-700">+</button>
            </div>
        </div>
        <div class="admin-card rounded-2xl p-6 overflow-x-auto">
            <canvas id="tableMapCanvas" width="900" height="420" class="bg-zinc-950 rounded-xl border border-zinc-800"></canvas>
        </div>
    </div>

    <div id="content-analytics" class="tab-content hidden space-y-6">
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-2">
            <div class="admin-card rounded-2xl p-4 flex flex-col justify-center text-center">
                <span class="text-[10px] text-zinc-500 uppercase font-black">Загальна Виручка</span>
                <span id="stat-revenue" class="text-2xl font-black text-emerald-400 mt-1">0 ₴</span>
            </div>
            <div class="admin-card rounded-2xl p-4 flex flex-col justify-center text-center">
                <span class="text-[10px] text-zinc-500 uppercase font-black">Активні Чеки</span>
                <span id="stat-active" class="text-2xl font-black text-indigo-400 mt-1">0 шт</span>
            </div>
            <div class="admin-card rounded-2xl p-4 flex flex-col justify-center text-center">
                <span class="text-[10px] text-zinc-500 uppercase font-black">Рейтинг закладу</span>
                <span id="stat-rating" class="text-2xl font-black text-amber-400 mt-1">5.0</span>
            </div>
            <div class="admin-card rounded-2xl p-4 flex flex-col justify-center text-center">
                <span class="text-[10px] text-zinc-500 uppercase font-black">Столи Онлайн</span>
                <span id="stat-online" class="text-2xl font-black text-zinc-200 mt-1">0</span>
            </div>
        </div>
        
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div class="admin-card p-5 rounded-2xl">
                <h3 class="text-sm font-black uppercase text-indigo-400 mb-4 border-b border-zinc-800 pb-2">Популярні Страви (Графік)</h3>
                <canvas id="salesChart" class="w-full max-h-[300px]"></canvas>
            </div>
            <div class="admin-card p-5 rounded-2xl">
                <h3 class="text-sm font-black uppercase text-indigo-400 mb-4 border-b border-zinc-800 pb-2">Топ Продажів (Кількість)</h3>
                <div id="top-sales-list" class="space-y-2"></div>
            </div>
        </div>
    </div>

    <div id="content-reviews" class="tab-content hidden space-y-4">
        <h2 class="text-lg font-black uppercase tracking-wider text-zinc-300">Відгуки Гостей</h2>
        <div class="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-4 gap-4" id="admin-reviews-list"></div>
    </div>

    <div id="content-archive" class="tab-content hidden space-y-6">
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div class="admin-card rounded-2xl p-5">
                <h3 class="text-sm font-black uppercase tracking-wider mb-4 text-emerald-400 border-b border-zinc-800 pb-2"><i class="fas fa-check-circle mr-1.5"></i>Оплачені замовлення</h3>
                <div id="archive-orders-list" class="space-y-2 max-h-[500px] overflow-y-auto pr-1"></div>
            </div>
            <div class="admin-card rounded-2xl p-5">
                <h3 class="text-sm font-black uppercase tracking-wider mb-4 text-indigo-400 border-b border-zinc-800 pb-2"><i class="fas fa-history mr-1.5"></i>Логи сесій (Пристрої)</h3>
                <div id="archive-devices-list" class="space-y-2 max-h-[500px] overflow-y-auto pr-1"></div>
            </div>
        </div>
    </div>

    <div id="review-orders-modal" class="fixed inset-0 z-[100] bg-black/80 backdrop-blur-sm hidden items-center justify-center p-4">
        <div class="bg-zinc-950 border border-zinc-800 p-5 rounded-2xl w-full max-w-sm flex flex-col max-h-[80vh]">
            <div class="flex justify-between items-center mb-3 border-b border-zinc-800 pb-2">
                <h3 class="text-sm font-black uppercase text-indigo-400" id="review-orders-title">Замовлення столу</h3>
                <button onclick="closeReviewOrdersModal()" class="text-zinc-500 font-bold"><i class="fas fa-times"></i></button>
            </div>
            <div id="review-orders-list" class="flex-1 overflow-y-auto space-y-2 hide-scroll"></div>
        </div>
    </div>

    <div id="floating-stream-window" class="draggable-window hidden bg-zinc-950 border-2 border-indigo-500 rounded-2xl p-3 shadow-2xl w-full max-w-[640px] h-[400px] flex flex-col">
        <div id="floating-stream-header" class="flex justify-between items-center bg-zinc-900 p-2 rounded-xl border border-zinc-800 mb-2 drag-header select-none">
            <span id="floating-stream-title" class="text-xs font-black text-indigo-400 uppercase tracking-widest">Камера клієнта: Стіл #</span>
            <div class="flex gap-2">
                <button onclick="toggleFullscreenStream()" class="text-zinc-400 hover:text-white font-bold text-xs bg-zinc-800 px-2 py-1 rounded-lg mr-1"><i class="fas fa-expand"></i></button>
                <button onclick="closeFloatingStream()" class="text-zinc-400 hover:text-white font-bold text-xs bg-zinc-800 px-2 py-1 rounded-lg"><i class="fas fa-times"></i></button>
            </div>
        </div>
        <div class="flex-1 bg-black rounded-xl overflow-hidden relative border border-zinc-900 flex items-center justify-center" id="floating-stream-content">
            <img id="floating-stream-img" src="" class="w-full h-full object-contain" alt="LIVE STREAM">
            <div class="absolute bottom-3 left-3 bg-red-600 text-white px-2 py-0.5 rounded text-[9px] font-bold tracking-widest animate-pulse uppercase">LIVE HD</div>
        </div>
    </div>

    <div id="nexus-global-modal" class="fixed inset-0 z-[9999] bg-black/80 backdrop-blur-sm hidden items-center justify-center p-4">
        <div class="bg-zinc-950 border border-zinc-800 p-6 rounded-2xl w-full max-w-sm shadow-2xl space-y-4">
            <h3 id="nexus-modal-title" class="text-[11px] font-black uppercase tracking-widest text-indigo-400">Система</h3>
            <p id="nexus-modal-text" class="text-xs text-zinc-300 font-medium"></p>
            <div class="flex gap-3 pt-2">
                <button id="nexus-btn-cancel" class="hidden flex-1 bg-zinc-900 border border-zinc-800 py-2.5 rounded-xl text-xs font-bold text-zinc-400">Скасувати</button>
                <button id="nexus-btn-confirm" class="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white p-3 rounded-xl text-xs font-bold shadow-lg">ОК</button>
            </div>
        </div>
    </div>

    <script>
        let modalCallback = null;

        function showAlert(message, title = "Сповіщення") {
            const modal = document.getElementById('nexus-global-modal');
            document.getElementById('nexus-modal-title').innerText = title;
            document.getElementById('nexus-modal-text').innerText = message;
            document.getElementById('nexus-btn-cancel').classList.add('hidden');
            modal.classList.remove('hidden'); modal.classList.add('flex');
            modalCallback = function(status) { modal.classList.add('hidden'); };
        }

        function showConfirm(message, onConfirm, title = "Підтвердження") {
            const modal = document.getElementById('nexus-global-modal');
            document.getElementById('nexus-modal-title').innerText = title;
            document.getElementById('nexus-modal-text').innerText = message;
            document.getElementById('nexus-btn-cancel').classList.remove('hidden');
            modal.classList.remove('hidden'); modal.classList.add('flex');
            modalCallback = function(status) {
                modal.classList.add('hidden');
                if (status && typeof onConfirm === 'function') onConfirm();
            };
        }

        document.getElementById('nexus-btn-confirm').addEventListener('click', () => { if (modalCallback) modalCallback(true); });
        document.getElementById('nexus-btn-cancel').addEventListener('click', () => { if (modalCallback) modalCallback(false); });

        // Вкладки
        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
            document.querySelectorAll('.tab-btn').forEach(b => { b.classList.remove('active'); b.classList.add('text-zinc-400'); });
            
            document.getElementById(`content-${tabId}`).classList.remove('hidden');
            const btn = document.getElementById(`tab-${tabId}`);
            btn.classList.add('active'); btn.classList.remove('text-zinc-400');

            if (tabId === 'map') { drawTableMap(); }
            if (tabId === 'monitoring') { renderDevices(); }
        }

        // ПОВНОЕКРАННИЙ РЕЖИМ СТРИМУ
        function toggleFullscreenStream() {
            const win = document.getElementById('floating-stream-window');
            if (!document.fullscreenElement) {
                win.requestFullscreen().catch(err => {
                    showAlert(`Помилка повноекранного режиму: ${err.message}`);
                });
            } else {
                document.exitFullscreen();
            }
        }

        // DRAG & DROP ДЛЯ ФОТО СТРАВ
        document.addEventListener("DOMContentLoaded", () => {
            const dropZone = document.getElementById('drop-zone');
            const fileInput = document.getElementById('menu-file-input');
            const nameIndicator = document.getElementById('file-name-indicator');
            const urlInput = document.getElementById('menu-image');
            const icon = document.getElementById('drop-icon');

            if (!dropZone || !fileInput) return;

            ['dragenter', 'dragover'].forEach(eventName => {
                dropZone.addEventListener(eventName, (e) => { e.preventDefault(); dropZone.classList.add('border-indigo-500', 'bg-zinc-900'); }, false);
            });

            ['dragleave', 'drop'].forEach(eventName => {
                dropZone.addEventListener(eventName, (e) => { e.preventDefault(); dropZone.classList.remove('border-indigo-500', 'bg-zinc-900'); }, false);
            });

            dropZone.addEventListener('drop', (e) => {
                const dt = e.dataTransfer;
                if (dt.files.length > 0) { fileInput.files = dt.files; handleFile(dt.files[0]); }
            });

            fileInput.addEventListener('change', (e) => {
                if (fileInput.files.length > 0) handleFile(fileInput.files[0]);
            });

            function handleFile(file) {
                if (!file.type.startsWith('image/')) { showAlert('Можна завантажувати тільки зображення!'); return; }
                nameIndicator.innerText = `${file.name}`;
                nameIndicator.classList.remove('text-zinc-600'); nameIndicator.classList.add('text-emerald-400', 'font-bold');
                icon.className = "fas fa-check-circle text-lg text-emerald-500";

                const reader = new FileReader();
                reader.readAsDataURL(file);
                reader.onloadend = function() { urlInput.value = reader.result; }
            }
        });

        // DRAG & DROP ДЛЯ КАНБАН ДОШКИ
        function allowDrop(ev) { ev.preventDefault(); }
        function handleDragStart(ev, id) { ev.dataTransfer.setData("text/plain", id); }
        function highlightDropzone(id) { document.getElementById(id).classList.add('drag-over'); }
        function unhighlightDropzone(id) { document.getElementById(id).classList.remove('drag-over'); }
        function handleDrop(ev, status) {
            ev.preventDefault();
            const id = ev.dataTransfer.getData("text/plain");
            unhighlightDropzone('queue-pending'); unhighlightDropzone('queue-cooking'); unhighlightDropzone('queue-ready');
            if(id) updateOrderStatus(id, status);
        }

        const socket = io();
        let globalOrders = [], globalMenu = [], liveDevicesData = {};
        let adminCategoryFilter = 'Всі';
        let salesChart = null;

        socket.on('connect', () => { socket.emit('join_admin_room'); });

        // ОНОВЛЕННЯ ДАНИХ (SYNC)
        socket.on('orders_sync', (orders) => { 
            globalOrders = orders; 
            renderOrders(orders); 
            drawTableMap(); 
        });
        
        socket.on('menu_sync', (menu) => { 
            globalMenu = menu; 
            renderMenuGrid(); 
            renderCategoryFilter(); 
        });
        
        socket.on('reviews_sync', (reviews) => { renderReviews(reviews); });
        socket.on('archive_sync', (data) => { renderArchive(data); });
        
        socket.on('devices_sync', (devices) => { 
            liveDevicesData = devices;
            renderDevices(); 
            drawTableMap(); 
        });

        socket.on('analytics_sync', (data) => {
            document.getElementById('stat-revenue').innerText = `${data.total_revenue} ₴`;
            document.getElementById('stat-active').innerText = `${data.active_orders} шт`;
            document.getElementById('stat-rating').innerText = `${data.avg_rating}`;
            document.getElementById('stat-online').innerText = `${data.devices_online}`;

            const topList = document.getElementById('top-sales-list');
            if (data.top_items && data.top_items.length > 0) {
                topList.innerHTML = data.top_items.map((i, idx) => `
                    <div class="flex justify-between items-center bg-zinc-900 border border-zinc-800 p-2.5 rounded-xl text-xs">
                        <div><span class="text-zinc-500 font-black mr-2">#${idx+1}</span><span class="text-zinc-200 font-bold">${i.name}</span></div>
                        <span class="text-indigo-400 font-black bg-indigo-500/10 px-2 py-1 rounded-lg">${i.qty} шт</span>
                    </div>`).join('');
                    
                updateChart(data.top_items);
            } else {
                topList.innerHTML = '<p class="text-zinc-500 text-xs">Немає закритих замовлень для формування статистики</p>';
                if (salesChart) { salesChart.destroy(); salesChart = null; }
            }
        });

        function updateChart(topItems) {
            const ctx = document.getElementById('salesChart').getContext('2d');
            const labels = topItems.map(i => i.name);
            const data = topItems.map(i => i.qty);

            if (salesChart) {
                salesChart.data.labels = labels;
                salesChart.data.datasets[0].data = data;
                salesChart.update();
            } else {
                salesChart = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Продано порцій',
                            data: data,
                            backgroundColor: '#4f46e5',
                            borderRadius: 6
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: false } },
                        scales: { 
                            y: { beginAtZero: true, grid: { color: '#27272a' } }, 
                            x: { grid: { display: false } } 
                        }
                    }
                });
            }
        }

        socket.on('new_order_alert', (order) => { /* showAlert(`Нове замовлення #${order.order_number}! Стіл: ${order.table}.`); */  });
        socket.on('waiter_alert', (data) => { showAlert(`🔔 Офіціанта викликають на Стіл #${data.table}`); });

        // РЕНДЕР КАНБАН ДОШКИ ЗАМОВЛЕНЬ
        function renderOrders(orders) {
            const pendingBox = document.getElementById('queue-pending');
            const cookingBox = document.getElementById('queue-cooking');
            const readyBox = document.getElementById('queue-ready');
            
            pendingBox.innerHTML = ''; cookingBox.innerHTML = ''; readyBox.innerHTML = '';
            let cP = 0, cC = 0, cR = 0;

            orders.forEach(o => {
                if (o.status === 'Закрито') return;
                const itemsHtml = o.items.map(i => `<div class="font-medium text-zinc-300 text-[10px] leading-tight">• ${i.name} <span class="text-indigo-400 font-bold">x${i.qty}</span></div>`).join('');
                const commentHtml = o.comment ? `<div class="text-[9px] text-amber-500 bg-amber-500/10 p-1.5 rounded mt-1 font-bold">💡 ${o.comment}</div>` : '';
                
                let actionBtn = '';
                if(o.status === 'pending') { actionBtn = `<button onclick="updateOrderStatus('${o._id}', 'cooking')" class="w-full bg-amber-500 text-zinc-950 font-black p-1.5 rounded-lg mt-2 text-[10px]">Готувати</button>`; cP++; }
                if(o.status === 'cooking') { actionBtn = `<button onclick="updateOrderStatus('${o._id}', 'ready')" class="w-full bg-indigo-600 text-white font-black p-1.5 rounded-lg mt-2 text-[10px]">Видати</button>`; cC++; }
                if(o.status === 'ready') { actionBtn = `<button onclick="updateOrderStatus('${o._id}', 'Закрито')" class="w-full bg-emerald-600 text-white font-black p-1.5 rounded-lg mt-2 text-[10px]">Оплачено / В архів</button>`; cR++; }

                const card = `
                    <div draggable="true" ondragstart="handleDragStart(event, '${o._id}')" class="bg-zinc-900 border border-zinc-800 p-2.5 rounded-xl text-xs space-y-1 cursor-grab active:cursor-grabbing hover:border-indigo-500/50 transition-all select-none">
                        <div class="flex justify-between items-center font-bold border-b border-zinc-800 pb-1 mb-1 pointer-events-none">
                            <span class="text-indigo-400 text-[11px]">Замовлення #${o.order_number}</span>
                            <span class="bg-zinc-950 px-1.5 py-0.5 rounded text-[9px] text-zinc-400 border border-zinc-800">Стіл ${o.table}</span>
                        </div>
                        <div class="space-y-0.5 max-h-20 overflow-y-auto pointer-events-none pr-1 hide-scroll">${itemsHtml}</div>
                        ${commentHtml}
                        <div class="flex justify-between items-center pt-1.5 mt-1 border-t border-zinc-800/60 font-black text-zinc-300">
                            <span class="text-xs text-emerald-400">${o.total_price} ₴</span>
                            <button onclick="deleteOrder('${o._id}')" class="text-red-500 text-[9px] hover:underline">Видалити</button>
                        </div>
                        ${actionBtn}
                    </div>`;

                if(o.status === 'pending') pendingBox.innerHTML += card;
                if(o.status === 'cooking') cookingBox.innerHTML += card;
                if(o.status === 'ready') readyBox.innerHTML += card;
            });

            document.getElementById('count-pending').innerText = cP;
            document.getElementById('count-cooking').innerText = cC;
            document.getElementById('count-ready').innerText = cR;
        }

        // РЕНДЕР МЕНЮ ЕДІТОРА (ІЗ ФІЛЬТРАМИ ТА FULL IMAGE)
        function renderCategoryFilter() {
            const bar = document.getElementById('admin-category-filter');
            if(!bar) return;
            const cats = ['Всі', ...new Set(globalMenu.map(i => i.category))];
            bar.innerHTML = cats.map(c => `<button onclick="adminCategoryFilter='${c}'; renderMenuGrid();" class="px-3 py-1.5 rounded-lg text-[10px] font-black uppercase transition-all whitespace-nowrap ${adminCategoryFilter === c ? 'bg-indigo-600 text-white' : 'bg-zinc-900 text-zinc-400 border border-zinc-800'}">${c}</button>`).join('');
        }

        function renderMenuGrid() {
            const grid = document.getElementById('admin-menu-grid');
            if(!grid) return;
            let filtered = adminCategoryFilter === 'Всі' ? globalMenu : globalMenu.filter(i => i.category === adminCategoryFilter);
            
            if(filtered.length === 0) { grid.innerHTML = `<div class="col-span-3 text-center text-zinc-500 py-6 text-xs font-bold">Немає страв</div>`; return; }

            // ЗОБРАЖЕННЯ В ПОВНОМУ РОЗМІРІ БЕЗ ОБРІЗАНЬ
            grid.innerHTML = filtered.map(item => `
                <div class="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden flex flex-col justify-between">
                    ${item.image ? `<img src="${item.image}" class="w-full h-24 object-contain bg-zinc-950 border-b border-zinc-800">` : `<div class="w-full h-24 bg-zinc-950 flex items-center justify-center text-xl border-b border-zinc-800">🍽️</div>`}
                    <div class="p-2 flex-1 flex flex-col justify-between">
                        <div>
                            <h4 class="font-black text-[11px] text-zinc-200 line-clamp-1">${item.name}</h4>
                            <span class="text-[8px] uppercase font-bold text-zinc-500">${item.category}</span>
                        </div>
                        <div class="flex items-center justify-between mt-2 pt-1 border-t border-zinc-800">
                            <span class="text-[11px] font-black text-indigo-400">${item.price} ₴</span>
                            <div class="flex gap-1.5">
                                <button onclick="editMenuItem('${item._id}', '${escapeHtml(item.name)}', '${escapeHtml(item.category)}', ${item.price}, '${escapeHtml(item.description)}', '${escapeHtml(item.image)}', ${item.available})" class="text-indigo-400 text-[10px] font-bold"><i class="fas fa-edit"></i></button>
                                <button onclick="deleteMenuItem('${item._id}')" class="text-red-500 text-[10px] font-bold"><i class="fas fa-trash"></i></button>
                            </div>
                        </div>
                    </div>
                </div>`).join('');
        }

        function saveMenuItem(e) {
            e.preventDefault();
            socket.emit('menu_save', {
                id: document.getElementById('menu-id').value || null,
                name: document.getElementById('menu-name').value,
                category: document.getElementById('menu-category').value,
                price: parseFloat(document.getElementById('menu-price').value),
                description: document.getElementById('menu-description').value,
                image: document.getElementById('menu-image').value,
                available: document.getElementById('menu-available').checked
            });
            resetMenuForm();
        }

        function editMenuItem(id, name, cat, price, desc, img, avail) {
            document.getElementById('menu-id').value = id;
            document.getElementById('menu-name').value = name;
            document.getElementById('menu-category').value = cat;
            document.getElementById('menu-price').value = price;
            document.getElementById('menu-description').value = desc;
            document.getElementById('menu-image').value = img;
            document.getElementById('menu-available').checked = (avail === 'true' || avail === true);
            
            const ind = document.getElementById('file-name-indicator');
            const icon = document.getElementById('drop-icon');
            if(img) { ind.innerText = 'Встановлено фото'; ind.classList.add('text-indigo-400'); icon.className = "fas fa-image text-lg text-indigo-500"; }
        }

        function resetMenuForm() { 
            document.getElementById('menu-form').reset(); 
            document.getElementById('menu-id').value = ''; 
            const ind = document.getElementById('file-name-indicator');
            if(ind) { ind.innerText = 'Файл не обрано'; ind.className = 'text-[9px] text-zinc-600 truncate max-w-[200px]'; document.getElementById('drop-icon').className = "fas fa-cloud-upload-alt text-lg text-zinc-500"; }
        }

        // РЕНДЕР МОНІТОРИНГУ ТА КІЛЬКОСТІ СТОЛІВ
        function changeTablesCount(delta) {
            let tablesCount = parseInt(localStorage.getItem('nexus_tables_count') || '12');
            tablesCount += delta;
            if(tablesCount < 1) tablesCount = 1;
            localStorage.setItem('nexus_tables_count', tablesCount);
            
            document.getElementById('tables-count-display-monitor').innerText = tablesCount;
            document.getElementById('tables-count-display-map').innerText = tablesCount;
            
            renderDevices();
            drawTableMap();
        }

        // Ініціалізація лічильників при старті
        document.getElementById('tables-count-display-monitor').innerText = localStorage.getItem('nexus_tables_count') || '12';
        document.getElementById('tables-count-display-map').innerText = localStorage.getItem('nexus_tables_count') || '12';

        function renderDevices() {
            const container = document.getElementById('devices-container');
            if(!container) return;
            
            let tablesCount = parseInt(localStorage.getItem('nexus_tables_count') || '12');
            let html = '';
            
            for(let i = 1; i <= tablesCount; i++) {
                let uuid = Object.keys(liveDevicesData).find(k => String(liveDevicesData[k].table) === String(i));
                let dev = uuid ? liveDevicesData[uuid] : null;
                
                if (dev) {
                    html += `
                        <div class="admin-card p-4 rounded-2xl flex flex-col justify-between border-l-4 border-l-emerald-500">
                            <div>
                                <div class="flex justify-between items-center mb-2">
                                    <span class="bg-zinc-950 text-emerald-400 border border-zinc-800 px-2.5 py-1 rounded-xl text-xs font-black">Стіл #${i}</span>
                                    <span class="text-[10px] text-zinc-500 font-bold">Останній кадр: ${dev.last_seen}</span>
                                </div>
                                <div class="grid grid-cols-2 gap-2 text-[11px] mb-3 bg-zinc-950 p-2.5 rounded-xl border border-zinc-900 font-medium">
                                    <div class="text-zinc-400">Розділ: <b class="text-zinc-200">${dev.category}</b></div>
                                    <div class="text-zinc-400">Кошик: <b class="text-indigo-400">${dev.cart_total} ₴</b></div>
                                    <div class="text-zinc-400">Вікно: <b class="text-amber-500">${dev.modal}</b></div>
                                    <div class="text-zinc-400">Скролл: <b class="text-zinc-200">${dev.scroll}%</b></div>
                                </div>
                                <div class="w-full h-40 bg-black rounded-xl overflow-hidden border border-zinc-800 relative cursor-pointer" onclick="openFloatingStream('${uuid}', '${i}')">
                                    <div id="placeholder-${uuid}" class="absolute text-[10px] text-zinc-600 font-bold flex flex-col items-center gap-2 inset-0 justify-center">
                                        <i class="fas fa-spinner fa-spin text-sm text-indigo-500"></i> Трансляція...
                                    </div>
                                    <img id="stream-uuid-${uuid}" class="w-full h-full object-contain hidden relative z-10" src="" alt="STREAM">
                                    <div class="absolute top-2 right-2 bg-black/60 text-white px-2 py-0.5 rounded text-[8px] font-bold uppercase tracking-widest z-20"><i class="fas fa-expand mr-1"></i> Відкрити</div>
                                </div>
                            </div>
                        </div>`;
                } else {
                    html += `
                        <div class="bg-zinc-900/50 border border-zinc-800/50 p-4 rounded-2xl flex flex-col justify-center items-center h-full opacity-60">
                            <span class="bg-zinc-800 text-zinc-500 font-black px-2.5 py-1 rounded-xl text-xs mb-2">Стіл #${i}</span>
                            <span class="text-zinc-600 text-xs font-bold uppercase tracking-widest">Офлайн</span>
                        </div>`;
                }
            }
            container.innerHTML = html;
        }

        // КАРТА СТОЛІВ (CANVAS)
        function drawTableMap() {
            const canvas = document.getElementById('tableMapCanvas');
            if(!canvas) return;
            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);

            let tablesCount = parseInt(localStorage.getItem('nexus_tables_count') || '12');
            const cols = 5;
            const radius = 30;
            const startX = 80;
            const startY = 60;
            const spaceX = 160;
            const spaceY = 110;

            for(let i=1; i<=tablesCount; i++) {
                const row = Math.floor((i-1) / cols);
                const col = (i-1) % cols;
                const x = startX + col * spaceX;
                const y = startY + row * spaceY;

                let isOnline = false;
                let hasCart = false;
                let isReady = false;

                Object.values(liveDevicesData).forEach(d => {
                    if(String(d.table) === String(i)) {
                        isOnline = true;
                        if(d.cart_total > 0) hasCart = true;
                    }
                });
                
                globalOrders.forEach(o => {
                    if(o.status !== 'Закрито' && String(o.table) === String(i)) {
                        if(o.status === 'ready') isReady = true;
                    }
                });

                ctx.beginPath();
                ctx.arc(x, y, radius, 0, 2 * Math.PI);
                
                if (isReady) {
                    ctx.shadowBlur = 15; ctx.shadowColor = '#10b981'; ctx.fillStyle = 'rgba(16, 185, 129, 0.2)'; ctx.strokeStyle = '#10b981';
                } else if (hasCart) {
                    ctx.shadowBlur = 15; ctx.shadowColor = '#f59e0b'; ctx.fillStyle = 'rgba(245, 158, 11, 0.2)'; ctx.strokeStyle = '#f59e0b';
                } else if (isOnline) {
                    ctx.shadowBlur = 15; ctx.shadowColor = '#4f46e5'; ctx.fillStyle = 'rgba(79, 70, 229, 0.2)'; ctx.strokeStyle = '#4f46e5';
                } else {
                    ctx.shadowBlur = 0; ctx.fillStyle = 'rgba(39, 39, 42, 0.6)'; ctx.strokeStyle = '#52525b';
                }
                
                ctx.lineWidth = 2;
                ctx.fill();
                ctx.stroke();

                ctx.shadowBlur = 0;
                ctx.fillStyle = '#ffffff';
                ctx.font = 'bold 12px system-ui';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(`${i}`, x, y);

                ctx.font = '9px system-ui';
                ctx.fillStyle = isReady ? '#10b981' : (hasCart ? '#f59e0b' : (isOnline ? '#818cf8' : '#a1a1aa'));
                let statusText = 'Вільний';
                if(isReady) statusText = 'ГОТОВО';
                else if(hasCart) statusText = 'ВИБИРАЄ';
                else if(isOnline) statusText = 'ОНЛАЙН';
                ctx.fillText(statusText, x, y + 45);
            }
        }

        // РЕНДЕР ВІДГУКІВ
        function renderReviews(reviews) {
            const container = document.getElementById('admin-reviews-list');
            if(reviews.length === 0) { container.innerHTML = `<div class="col-span-3 text-center text-zinc-500 py-6 text-xs font-bold">Немає відгуків</div>`; return; }
            container.innerHTML = reviews.map(r => {
                let stars = ''; for(let i=1; i<=5; i++) stars += `<i class="${i<=r.rating?'fas':'far'} fa-star text-amber-500 text-[10px]"></i>`;
                return `
                    <div class="bg-zinc-900 border border-zinc-800 p-3.5 rounded-xl flex flex-col justify-between h-full">
                        <div>
                            <div class="flex justify-between items-center mb-1">
                                <h4 class="font-black text-xs text-zinc-200">${r.name}</h4>
                                <span class="text-[9px] text-zinc-500 font-bold">${r.time_str}</span>
                            </div>
                            <div class="mb-2">${stars}</div>
                            <p class="text-[11px] text-zinc-300 font-medium leading-relaxed bg-black/30 p-2 rounded-lg">${r.text || 'Оцінка без коментаря'}</p>
                        </div>
                        <div class="mt-3 pt-2 border-t border-zinc-800/60 flex justify-between items-center">
                            <button onclick="viewTableOrders('${r.name}')" class="text-[9px] font-black uppercase tracking-widest text-indigo-400 bg-indigo-500/10 px-2 py-1 rounded hover:bg-indigo-500/20"><i class="fas fa-list-ul mr-1"></i> Замовлення</button>
                            <button onclick="deleteReview('${r._id}')" class="text-red-500 hover:text-red-400 text-[10px]"><i class="fas fa-trash"></i></button>
                        </div>
                    </div>`;
            }).join('');
        }

        function deleteReview(id) { if(confirm('Видалити цей відгук?')) socket.emit('reviews_delete', { id: id }); }

        function viewTableOrders(reviewerName) {
            const match = reviewerName.match(/Стіл\s*#\s*(\w+)/);
            const tableName = match ? match[1] : null;
            const title = document.getElementById('review-orders-title');
            const list = document.getElementById('review-orders-list');
            
            if(!tableName) {
                title.innerText = `Інформація відсутня`;
                list.innerHTML = `<div class="text-center text-zinc-500 py-4 text-xs font-bold">Не вдалося розпізнати стіл</div>`;
            } else {
                title.innerText = `Замовлення: Стіл #${tableName}`;
                const tableOrders = globalOrders.filter(o => String(o.table) === String(tableName));
                if(tableOrders.length === 0) {
                    list.innerHTML = `<div class="text-center text-zinc-500 py-4 text-xs font-bold">Не знайдено історії замовлень для цього столу</div>`;
                } else {
                    list.innerHTML = tableOrders.map(o => `
                        <div class="bg-zinc-900 p-2.5 rounded-xl border border-zinc-800 text-[10px]">
                            <div class="flex justify-between font-bold mb-1"><span class="text-zinc-300">#${o.order_number}</span><span class="text-indigo-400">${o.status}</span></div>
                            <div class="text-zinc-500 font-medium space-y-0.5">${o.items.map(i=>`<div>• ${i.name} x${i.qty}</div>`).join('')}</div>
                            <div class="mt-1 pt-1 border-t border-zinc-800 text-right font-black text-emerald-400">${o.total_price} ₴</div>
                        </div>`).join('');
                }
            }
            document.getElementById('review-orders-modal').classList.remove('hidden');
            document.getElementById('review-orders-modal').classList.add('flex');
        }
        function closeReviewOrdersModal() { document.getElementById('review-orders-modal').classList.add('hidden'); document.getElementById('review-orders-modal').classList.remove('flex'); }

        // РЕНДЕР АРХІВУ
        function renderArchive(data) {
            const oList = document.getElementById('archive-orders-list');
            const dList = document.getElementById('archive-devices-list');
            if(oList) oList.innerHTML = data.orders.map(o => `<div class="bg-zinc-900 border border-zinc-800 p-2.5 rounded-xl text-[10px]"><div class="flex justify-between font-bold border-b border-zinc-800 pb-1 mb-1"><span class="text-emerald-400">#${o.order_number} (Стіл ${o.table})</span><span class="text-zinc-500">${o.time_str}</span></div><div class="text-zinc-400">${o.items.map(i=>`• ${i.name} x${i.qty}`).join('<br>')}</div><div class="text-right font-black text-zinc-300 mt-1">${o.total_price} ₴</div></div>`).join('') || '<p class="text-zinc-500 text-[10px]">Немає оплачених замовлень</p>';
            if(dList) dList.innerHTML = data.devices.map(d => `<div class="bg-zinc-900 border border-zinc-800 p-2.5 rounded-xl text-[10px]"><div class="flex justify-between font-bold mb-1"><span class="text-indigo-400">Стіл ${d.table}</span><span class="text-zinc-500">${d.last_seen}</span></div><div class="bg-black/40 p-1.5 rounded font-mono text-[9px] text-zinc-400 break-all leading-tight">${d.user_agent}</div></div>`).join('') || '<p class="text-zinc-500 text-[10px]">Історія пристроїв порожня</p>';
        }

        // ОБРОБКА ПЛАВАЮЧОГО ВІКНА СТРИМУ (DRAG & FULLSCREEN)
        socket.on('receive_frame', (data) => {
            const smallImg = document.getElementById(`stream-uuid-${data.uuid}`);
            if (smallImg) smallImg.src = data.frame;

            const floatingWin = document.getElementById('floating-stream-window');
            if (!floatingWin.classList.contains('hidden') && floatingWin.dataset.currentUuid === data.uuid) {
                document.getElementById('floating-stream-img').src = data.frame;
            }
        });

        function openFloatingStream(uuid, tableNum) {
            const win = document.getElementById('floating-stream-window');
            document.getElementById('floating-stream-title').innerText = `Камера клієнта: Стіл #${tableNum}`;
            win.dataset.currentUuid = uuid;
            win.classList.remove('hidden');
            win.style.top = '20%';
            win.style.left = '10%';
        }

        function closeFloatingStream() {
            document.getElementById('floating-stream-window').classList.add('hidden');
        }

        function initDraggableWindow(elementId, headerId) {
            const el = document.getElementById(elementId);
            const header = document.getElementById(headerId);
            let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
            header.onmousedown = dragMouseDown;

            function dragMouseDown(e) {
                e = e || window.event; e.preventDefault();
                pos3 = e.clientX; pos4 = e.clientY;
                document.onmouseup = closeDragElement;
                document.onmousemove = elementDrag;
            }
            function elementDrag(e) {
                e = e || window.event; e.preventDefault();
                pos1 = pos3 - e.clientX; pos2 = pos4 - e.clientY;
                pos3 = e.clientX; pos4 = e.clientY;
                el.style.top = (el.offsetTop - pos2) + "px"; el.style.left = (el.offsetLeft - pos1) + "px";
            }
            function closeDragElement() { document.onmouseup = null; document.onmousemove = null; }
        }
        initDraggableWindow('floating-stream-window', 'floating-stream-header');

        function updateOrderStatus(id, status) { socket.emit('order_status_update', { id, status }); }
        function deleteOrder(id) { showConfirm('Видалити замовлення?', () => { socket.emit('order_delete', { id }); }); }
        function deleteMenuItem(id) { showConfirm('Видалити страву з меню?', () => { socket.emit('menu_delete', { id }); }); }
        function clearDatabase() { showConfirm('Повністю очистити всю базу даних?', () => { socket.emit('admin_clear_db'); }); }
        function exportDatabase() { window.location.href = '/export_db'; }
        
        function importDatabase() {
            const fileInput = document.getElementById('import-file');
            if(!fileInput.files[0]) return;
            const reader = new FileReader();
            reader.onload = function(e) {
                try {
                    socket.emit('admin_import_db', JSON.parse(e.target.result));
                    showAlert('Резервну копію успішно відновлено!');
                    fileInput.value = '';
                } catch(err) { showAlert('Помилка структури JSON.'); }
            };
            reader.readAsText(fileInput.files[0]);
        }

        function escapeHtml(str) { if(!str) return ''; return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;"); }
    </script>
</body>
</html>
"""

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Вхід в Панель Адміністратора</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-zinc-950 flex items-center justify-center h-screen text-white px-4">
    <div class="bg-zinc-900 p-8 rounded-2xl shadow-2xl w-full max-w-md border border-zinc-800 text-center">
        <h2 class="text-2xl md:text-3xl font-black mb-6 text-indigo-500 tracking-tight">NEXUS CAFE PRO</h2>
        {% if error %}<div class="bg-red-500/10 border border-red-500/30 text-red-400 p-3 rounded-xl mb-4 text-xs font-bold">{{ error }}</div>{% endif %}
        <form method="POST">
            <div class="mb-5">
                <input type="password" name="password" placeholder="PIN-КОД" required class="w-full p-4 rounded-xl bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-indigo-500 tracking-widest text-center text-xl font-bold placeholder-zinc-700">
            </div>
            <button type="submit" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-black py-4 rounded-xl transition shadow-lg active:scale-95 uppercase tracking-wider text-sm">Увійти</button>
        </form>
    </div>
</body>
</html>
"""

# ==============================================================================
# 6. ТОЧКА ВХОДУ ДЛЯ ЗАПУСКУ СЕРВЕРА
# ==============================================================================
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=10000, debug=True)
