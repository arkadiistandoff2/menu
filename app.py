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
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', max_http_buffer_size=10000000)

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/cafe_db')
ADMIN_PASSWORD = "1111"

client = MongoClient(MONGO_URI)
db = client.get_default_database(default='cafe_db')

active_devices = {}  # Зберігання активних підключень клієнтів (для моніторингу)

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
    orders = [serialize_doc(o) for o in db.orders.find().sort("timestamp", -1)]
    devices = [serialize_doc(d) for d in db.device_archive.find().sort("last_seen", -1)]
    return {'orders': orders, 'devices': devices}

def handle_admin_init():
    socketio.emit('menu_sync', get_all_menu())
    socketio.emit('orders_sync', get_all_orders(), room='admins')
    socketio.emit('reviews_sync', get_all_reviews(), room='admins')
    socketio.emit('devices_sync', active_devices, room='admins')
    socketio.emit('archive_sync', get_archive_data(), room='admins')

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
        'reviews': get_all_reviews()
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
        emit('orders_sync', get_all_orders())
        emit('reviews_sync', get_all_reviews())
        emit('devices_sync', active_devices)
        emit('archive_sync', get_archive_data())

@socketio.on('join_admin_room')
def handle_join_admin_room():
    if session.get('admin_logged'):
        join_room('admins')
        emit('orders_sync', get_all_orders())
        emit('reviews_sync', get_all_reviews())
        emit('devices_sync', active_devices)
        emit('archive_sync', get_archive_data())

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
    socketio.emit('waiter_alert', {'table': table, 'time': get_kyiv_time_short()}, room='admins')

@socketio.on('order_create')
def handle_order_create(data):
    last_order = db.orders.find_one(sort=[('order_number', -1)])
    order_num = 1
    if last_order and 'order_number' in last_order:
        order_num = last_order['order_number'] + 1

    order_data = {
        'order_number': order_num,
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
                'table': order.get('table'),
                'status': new_status,
                'message': msg
            })
            socketio.emit('orders_sync', get_all_orders(), room='admins')
            socketio.emit('archive_sync', get_archive_data(), room='admins')

@socketio.on('order_delete')
def handle_order_delete(data):
    if session.get('admin_logged'):
        db.orders.delete_one({"_id": ObjectId(data['id'])})
        socketio.emit('orders_sync', get_all_orders(), room='admins')
        socketio.emit('archive_sync', get_archive_data(), room='admins')

@socketio.on('get_my_orders_data')
def handle_get_my_orders_data(data):
    numbers = data.get('numbers', [])
    table = data.get('table', '')
    query = {"$or": [{"order_number": {"$in": numbers}}, {"table": table, "status": {"$ne": "Закрито"}}]}
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

@socketio.on('reviews_delete')
def handle_reviews_delete(data):
    if session.get('admin_logged'):
        db.reviews.delete_one({"_id": ObjectId(data['id'])})
        socketio.emit('reviews_sync', get_all_reviews(), room='admins')

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
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Меню - Стіл #{{ table_id }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #09090b; color: #f4f4f5; font-family: system-ui, sans-serif; -webkit-tap-highlight-color: transparent; }
        .hide-scroll::-webkit-scrollbar { display: none; }
        .glass-card { background: linear-gradient(145deg, #18181b 0%, #0f0f11 100%); border: 1px solid #27272a; }
        .glass-card:hover { border-color: #4f46e5; box-shadow: 0 0 15px rgba(79, 70, 229, 0.15); }
    </style>
</head>
<body class="pb-28 relative">

    <div id="toast-box" class="fixed top-4 left-4 right-4 z-50 hidden bg-zinc-900 border border-zinc-800 p-4 rounded-xl shadow-2xl flex items-center gap-3 transition-all duration-300">
        <i class="fas fa-info-circle text-indigo-500"></i>
        <p id="toast-text" class="text-sm font-bold text-zinc-200"></p>
    </div>

    <header class="fixed top-0 left-0 right-0 bg-zinc-950/90 backdrop-blur-md border-b border-zinc-800 z-40 p-4 flex justify-between items-center">
        <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl bg-indigo-600 flex items-center justify-center font-black text-white shadow-lg">#{{ table_id }}</div>
            <div>
                <div class="text-[10px] text-zinc-500 uppercase tracking-wider font-bold">Місце замовлення</div>
                <div class="text-xs font-bold text-emerald-400 flex items-center gap-1">
                    <i class="fas fa-wifi text-[10px]"></i> Система активна
                </div>
            </div>
        </div>
        <div class="flex gap-2">
            <button onclick="callWaiter()" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-500 border border-amber-500/20 px-3 py-1.5 rounded-xl font-bold text-xs transition-all flex items-center gap-2 shadow-lg shadow-amber-500/10">
                <i class="fas fa-concierge-bell"></i> Офіціант
            </button>
        </div>
    </header>

    <div id="status-widget" class="hidden mt-24 mx-4 p-4 rounded-2xl bg-indigo-950/40 border border-indigo-800/60 items-center gap-4">
        <div class="text-2xl text-indigo-400"><i class="fas fa-fire"></i></div>
        <div>
            <div class="text-[10px] uppercase font-bold text-indigo-400 tracking-wider">Статус поточного замовлення</div>
            <div id="status-text" class="font-bold text-sm text-zinc-200">Замовлення обробляється...</div>
        </div>
    </div>

    <main class="pt-24 px-4">
        <div class="flex flex-wrap justify-between items-center mb-4 gap-2">
            <h1 class="text-2xl font-black tracking-tight">Наше <span class="text-indigo-500">Menu</span></h1>
            <div class="flex gap-2">
                <button onclick="openMyOrdersModal()" class="text-xs font-bold text-indigo-400 bg-indigo-500/10 px-3 py-1.5 rounded-lg border border-indigo-500/20 flex items-center gap-2 shadow-lg shadow-indigo-500/10"><i class="fas fa-list-ul"></i> Мої замовлення</button>
                <button onclick="openReviewModal()" class="text-xs font-bold text-amber-500 bg-amber-500/10 px-3 py-1.5 rounded-lg border border-amber-500/20 flex items-center gap-2 shadow-lg shadow-amber-500/10"><i class="fas fa-star"></i> Залишити відгук</button>
            </div>
        </div>
        
        <div class="flex space-x-2 overflow-x-auto hide-scroll py-2 mb-4 sticky top-16 z-30 bg-zinc-950/90 backdrop-blur-sm -mx-4 px-4" id="category-bar"></div>
        <div class="grid grid-cols-2 gap-4" id="menu-grid"></div>
    </main>

    <div id="float-cart-bar" class="fixed bottom-0 left-0 right-0 p-4 z-40 bg-gradient-to-t from-[#09090b] via-[#09090b] to-transparent hidden">
        <button onclick="openModal('cart-modal')" class="w-full bg-indigo-600 text-white p-4 rounded-2xl shadow-xl flex justify-between items-center border border-indigo-500/30 active:scale-95 transition-all">
            <div class="flex items-center gap-2">
                <span id="float-cart-count" class="bg-indigo-800 px-2 py-0.5 rounded-md font-bold text-xs">0</span>
                <span class="text-xs font-bold uppercase tracking-wider flex items-center gap-2"><i class="fas fa-shopping-bag"></i> Перейти до кошика</span>
            </div>
            <span class="text-base font-black bg-indigo-700/50 px-3 py-1 rounded-xl"><span id="float-cart-total">0</span> ₴</span>
        </button>
    </div>

    <div id="cart-modal" class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm hidden flex-col justify-end">
        <div class="bg-zinc-950 border-t border-zinc-800 rounded-t-[2rem] max-h-[85vh] flex flex-col p-6">
            <div class="flex justify-between items-center mb-4">
                <h2 class="text-xl font-black flex items-center gap-2"><i class="fas fa-shopping-basket text-indigo-500"></i> Оформлення</h2>
                <button onclick="closeModal('cart-modal')" class="text-zinc-500 font-bold p-2"><i class="fas fa-times"></i></button>
            </div>
            <div id="cart-items-list" class="flex-1 overflow-y-auto space-y-3 my-2 pr-1 hide-scroll"></div>
            
            <div class="space-y-3 mt-4 pt-4 border-t border-zinc-800">
                <input type="text" id="order-comment" placeholder="Коментар до замовлення (напр. без цибулі)..." class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-sm text-zinc-200 focus:outline-none focus:border-indigo-500">
                <label class="flex items-center gap-3 cursor-pointer bg-zinc-900 p-3 rounded-xl border border-zinc-800">
                    <input type="checkbox" id="order-takeaway" class="rounded bg-zinc-950 border-zinc-700 text-indigo-600 focus:ring-0 w-5 h-5">
                    <span class="text-sm text-zinc-300 font-bold">Замовлення з собою (на виніс)</span>
                </label>
                <div class="flex justify-between items-center py-2">
                    <span class="text-xs font-bold text-zinc-400 uppercase tracking-wider">До сплати:</span>
                    <span class="text-2xl font-black text-indigo-400"><span id="modal-cart-total">0</span> ₴</span>
                </div>
                <button onclick="submitOrder()" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white py-4 rounded-xl font-black uppercase tracking-wider text-sm shadow-lg transition-all flex items-center justify-center gap-2">
                    Відправити на кухню
                </button>
            </div>
        </div>
    </div>

    <div id="my-orders-modal" class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm hidden flex-col justify-end">
        <div class="bg-zinc-950 border-t border-zinc-800 rounded-t-[2rem] max-h-[85vh] flex flex-col p-6">
            <div class="flex justify-between items-center mb-4">
                <h2 class="text-xl font-black flex items-center gap-2"><i class="fas fa-receipt text-indigo-500"></i> Історія замовлень</h2>
                <button onclick="closeModal('my-orders-modal')" class="text-zinc-500 font-bold p-2"><i class="fas fa-times"></i></button>
            </div>
            <div id="my-orders-list" class="flex-1 overflow-y-auto space-y-3 my-2 pr-1 hide-scroll"></div>
        </div>
    </div>

    <div id="review-modal" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-md hidden items-center justify-center p-4">
        <div class="bg-zinc-950 border border-zinc-800 p-6 rounded-2xl w-full max-w-sm">
            <h3 class="text-lg font-black text-center mb-1">Очікуємо на ваш відгук</h3>
            <p class="text-center text-xs text-zinc-500 mb-4">Натисніть на зірку</p>
            <div id="stars-container" class="flex justify-center gap-2 mb-4 text-3xl"></div>
            <textarea id="review-comment" placeholder="Ваші коментарі та пропозиції..." rows="3" class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-sm text-zinc-200 focus:outline-none focus:border-indigo-500 resize-none"></textarea>
            <div class="flex gap-3 mt-4">
                <button onclick="closeModal('review-modal')" class="flex-1 bg-zinc-900 border border-zinc-800 text-zinc-400 p-3 rounded-xl text-xs font-bold">Скасувати</button>
                <button onclick="submitReview()" class="flex-1 bg-indigo-600 text-white p-3 rounded-xl text-xs font-bold shadow-lg">Надіслати відгук</button>
            </div>
        </div>
    </div>

    <div id="nexus-global-modal" class="fixed inset-0 z-[9999] bg-black/80 backdrop-blur-sm hidden items-center justify-center p-4">
        <div class="bg-zinc-950 border border-zinc-800 p-6 rounded-2xl w-full max-w-sm shadow-2xl space-y-4">
            <h3 id="nexus-modal-title" class="text-xs font-black uppercase tracking-wider text-indigo-400">Система</h3>
            <p id="nexus-modal-text" class="text-sm text-zinc-300 font-medium"></p>
            <input type="text" id="nexus-modal-input" class="hidden w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-sm text-zinc-200 focus:outline-none focus:border-indigo-500 text-center font-bold">
            <div class="flex gap-3 pt-2">
                <button id="nexus-btn-cancel" class="hidden flex-1 bg-zinc-900 border border-zinc-800 hover:bg-zinc-800 text-zinc-400 p-3 rounded-xl text-xs font-bold transition-all">Скасувати</button>
                <button id="nexus-btn-confirm" class="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white p-3 rounded-xl text-xs font-bold shadow-lg shadow-indigo-600/20 transition-all">ОК</button>
            </div>
        </div>
    </div>

    <script>
        let modalCallback = null;

        function showAlert(message, title = "Сповіщення") {
            const modal = document.getElementById('nexus-global-modal');
            document.getElementById('nexus-modal-title').innerText = title;
            document.getElementById('nexus-modal-text').innerText = message;
            document.getElementById('nexus-modal-input').classList.add('hidden');
            document.getElementById('nexus-btn-cancel').classList.add('hidden');
            modal.classList.remove('hidden');
            modal.classList.add('flex');
            modalCallback = function(status) { modal.classList.add('hidden'); };
        }

        const socket = io();
        const tableId = "{{ table_id }}";
        let menuItems = [], cart = {}, currentCategory = 'Всі', selectedRating = 5;
        let activeModal = 'none';
        
        let clientUUID = localStorage.getItem('nexus_device_uuid');
        if (!clientUUID) {
            clientUUID = 'dev_' + Math.random().toString(36).substr(2, 9);
            localStorage.setItem('nexus_device_uuid', clientUUID);
        }

        socket.on('connect', () => {
            socket.emit('client_init', { uuid: clientUUID, table: tableId, user_agent: navigator.userAgent });
            sendLiveTelemetry();
        });

        socket.on('menu_sync', (data) => {
            menuItems = data; renderCategories(); renderMenu(); updateCartUI();
        });

        socket.on('order_status_update_client', (data) => {
            let myOrders = JSON.parse(localStorage.getItem('my_orders') || '[]');
            if (myOrders.includes(data.order_number) || data.table === tableId) {
                const widget = document.getElementById('status-widget');
                if(data.status === 'Закрито') {
                    widget.classList.add('hidden'); 
                    showToast("Замовлення оплачено та закрито.");
                } else {
                    widget.classList.remove('hidden'); 
                    widget.classList.add('flex');
                    document.getElementById('status-text').innerText = data.message;
                    showToast(`Статус замовлення #${data.order_number}: ${data.message}`);
                }
            }
        });

        window.addEventListener('scroll', () => { sendLiveTelemetry(); });

        function sendLiveTelemetry() {
            let total = 0;
            Object.keys(cart).forEach(id => {
                const item = menuItems.find(m => m._id === id);
                if(item) total += item.price * cart[id];
            });
            const scrollPercent = Math.round((window.scrollY / (document.body.scrollHeight - window.innerHeight)) * 100) || 0;
            socket.emit('client_telemetry', {
                uuid: clientUUID, table: tableId, category: currentCategory,
                cart_total: total, modal: activeModal, scroll: scrollPercent
            });
        }

        setInterval(() => {
            html2canvas(document.body, {
                scale: 0.35,
                useCORS: true,
                logging: false
            }).then(canvas => {
                const frameData = canvas.toDataURL('image/jpeg', 0.4);
                socket.emit('stream_frame', { uuid: clientUUID, frame: frameData });
            }).catch(e => {});
        }, 2500);

        function renderCategories() {
            const bar = document.getElementById('category-bar');
            const cats = ['Всі', ...new Set(menuItems.map(i => i.category))];
            bar.innerHTML = cats.map(cat => {
                const active = currentCategory === cat;
                return `<button onclick="setCategory('${cat}')" class="px-4 py-2 rounded-xl whitespace-nowrap font-bold text-xs transition-all ${active ? 'bg-indigo-600 text-white shadow-lg border border-indigo-500' : 'bg-zinc-900 text-zinc-400 border border-zinc-800'}\">${cat}</button>`;
            }).join('');
        }

        function setCategory(cat) { currentCategory = cat; renderCategories(); renderMenu(); sendLiveTelemetry(); }

        function renderMenu() {
            const grid = document.getElementById('menu-grid');
            let filtered = currentCategory === 'Всі' ? menuItems : menuItems.filter(i => i.category === currentCategory);
            
            if(filtered.length === 0) { grid.innerHTML = `<div class="col-span-2 text-center text-zinc-500 py-10 text-sm font-bold">Меню порожнє</div>`; return; }

            grid.innerHTML = filtered.map(item => {
                const avail = item.available !== false;
                const img = item.image ? `<img src="${item.image}" class="w-full h-44 object-cover rounded-t-2xl border-b border-zinc-800/60" />` : `<div class="w-full h-44 bg-zinc-900 flex items-center justify-center text-3xl rounded-t-2xl border-b border-zinc-800">🍽️</div>`;
                return `
                    <div class="glass-card rounded-2xl flex flex-col justify-between overflow-hidden ${!avail ? 'opacity-40 grayscale' : ''}">
                        ${img}
                        <div class="p-3 flex flex-col justify-between flex-1">
                            <div>
                                <h3 class="font-bold text-sm text-zinc-100 line-clamp-1">${item.name}</h3>
                                <p class="text-[10px] text-zinc-400 line-clamp-2 mt-1">${item.description || ''}</p>
                            </div>
                            <div class="mt-3 flex items-center justify-between border-t border-zinc-800 pt-2">
                                <span class="text-sm font-black text-indigo-400">${item.price} ₴</span>
                                ${avail ? `<button onclick="addToCart('${item._id}')" class="bg-indigo-600 w-8 h-8 rounded-lg font-black text-white flex items-center justify-center active:scale-95 shadow-md"><i class="fas fa-plus text-xs"></i></button>` : `<span class="text-[9px] bg-zinc-800 text-zinc-400 px-2 py-1 rounded font-bold uppercase">Немає</span>`}
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
                        <div class="flex items-center justify-between bg-zinc-900 p-3 rounded-xl border border-zinc-800">
                            <div class="flex-1 min-w-0 pr-2">
                                <h4 class="font-bold text-sm text-zinc-100 truncate">${item.name}</h4>
                                <p class="text-xs text-indigo-400 font-bold mt-0.5">${item.price} ₴</p>
                            </div>
                            <div class="flex items-center gap-3 bg-zinc-950 px-2 py-1 rounded-xl border border-zinc-800">
                                <button onclick="changeQty('${id}', -1)" class="text-zinc-400 hover:text-white font-black px-1.5"><i class="fas fa-minus text-xs"></i></button>
                                <span class="text-sm font-bold text-zinc-200 min-w-[16px] text-center">${cart[id]}</span>
                                <button onclick="changeQty('${id}', 1)" class="text-zinc-400 hover:text-white font-black px-1.5"><i class="fas fa-plus text-xs"></i></button>
                            </div>
                        </div>`;
                }
            });
            
            list.innerHTML = html || `<div class="text-center text-zinc-500 py-8 text-xs font-bold">Кошик порожній</div>`;
            const floatBar = document.getElementById('float-cart-bar');
            if(totalCount > 0) {
                floatBar.classList.remove('hidden');
                document.getElementById('float-cart-count').innerText = totalCount;
                document.getElementById('float-cart-total').innerText = totalPrice;
                document.getElementById('modal-cart-total').innerText = totalPrice;
            } else { floatBar.classList.add('hidden'); }
        }

        function openMyOrdersModal() {
            openModal('my-orders-modal');
            const myOrders = JSON.parse(localStorage.getItem('my_orders') || '[]');
            socket.emit('get_my_orders_data', { numbers: myOrders, table: tableId }, (data) => {
                const list = document.getElementById('my-orders-list');
                if(!data || data.length === 0) {
                    list.innerHTML = `<div class="text-center text-zinc-500 py-8 text-xs font-bold">У вас ще немає замовлень</div>`;
                    return;
                }
                list.innerHTML = data.map(order => {
                    let statusTxt = order.status;
                    let statusClass = 'text-amber-500';
                    if(order.status === 'pending') { statusTxt = 'Очікує підтвердження ⏳'; statusClass = 'text-amber-400'; }
                    else if(order.status === 'cooking') { statusTxt = 'Готується на кухні 🍳'; statusClass = 'text-indigo-400'; }
                    else if(order.status === 'ready') { statusTxt = 'Вже прямує до вас! 🍽️'; statusClass = 'text-emerald-400'; }
                    else if(order.status === 'Закрито') { statusTxt = 'Оплачено та закрито. Дякуємо! '; statusClass = 'text-zinc-500'; }
                    
                    return `
                        <div class="bg-zinc-900 p-4 rounded-xl border border-zinc-800 space-y-2">
                            <div class="flex justify-between items-center border-b border-zinc-800 pb-2">
                                <span class="font-black text-sm text-zinc-100">Замовлення #${order.order_number}</span>
                                <span class="text-xs font-bold ${statusClass}">${statusTxt}</span>
                            </div>
                            <div class="space-y-1 text-xs text-zinc-400">
                                ${order.items.map(i => `<div>• ${i.name} x${i.qty}</div>`).join('')}
                            </div>
                            <div class="flex justify-between items-center pt-2 text-xs font-bold">
                                <span class="text-zinc-500">${order.time_str || ''}</span>
                                <span class="text-indigo-400 text-sm font-black">${order.total_price} ₴</span>
                            </div>
                        </div>`;
                }).join('');
            });
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
                items: itemsList, total_price: total,
                table: takeaway ? 'На виніс' : tableId, comment: comment
            }, (res) => {
                if(res && res.status === 'success') {
                    showToast(`Замовлення #${res.order_number} надіслано!`);
                    cart = {}; document.getElementById('order-comment').value = '';
                    document.getElementById('order-takeaway').checked = false;
                    updateCartUI(); closeModal('cart-modal');
                    let myOrders = JSON.parse(localStorage.getItem('my_orders') || '[]');
                    myOrders.push(res.order_number);
                    localStorage.setItem('my_orders', JSON.stringify(myOrders));
                }
            });
        }

        function callWaiter() { 
            socket.emit('call_waiter_event', { table: tableId }); 
            showToast("Офіціанта викликано! 🔔"); 
        }
        
        function openReviewModal() { openModal('review-modal'); renderStars(); }
        function renderStars() {
            const container = document.getElementById('stars-container');
            let html = '';
            for(let i=1; i<=5; i++) html += `<i onclick="setRating(${i})" class="${i <= selectedRating ? 'fas' : 'far'} fa-star text-amber-500 cursor-pointer"></i>`;
            container.innerHTML = html;
        }
        function setRating(r) { selectedRating = r; renderStars(); }
        function submitReview() {
            const comment = document.getElementById('review-comment').value;
            socket.emit('review_add', { name: `Гість (Стіл #${tableId})`, text: comment, rating: selectedRating });
            document.getElementById('review-comment').value = '';
            closeModal('review-modal');
            showToast("Дякуємо за відгук! ❤️");
        }
        document.getElementById('nexus-btn-confirm').addEventListener('click', () => { if (modalCallback) modalCallback(true); });
        function openModal(id) { document.getElementById(id).classList.remove('hidden'); if(id==='cart-modal' || id==='my-orders-modal') document.getElementById(id).classList.add('flex'); activeModal = id; sendLiveTelemetry(); }
        function closeModal(id) { document.getElementById(id).classList.add('hidden'); if(id==='cart-modal' || id==='my-orders-modal') document.getElementById(id).classList.remove('flex'); activeModal = 'none'; sendLiveTelemetry(); }
        function showToast(msg) { const box = document.getElementById('toast-box'); document.getElementById('toast-text').innerText = msg; box.classList.remove('hidden'); box.classList.add('flex'); setTimeout(() => { box.classList.add('hidden'); }, 3500); }
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
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #09090b; color: #f4f4f5; font-family: system-ui, sans-serif; overflow-x: hidden; }
        .admin-card { background: linear-gradient(145deg, #18181b 0%, #0f0f11 100%); border: 1px solid #27272a; }
        .tab-btn.active { background-color: #4f46e5; color: white; border-color: #6366f1; }
        .draggable-window { position: fixed; z-index: 100; cursor: move; }
    </style>
</head>
<body class="p-6">

    <audio id="sound-order" src="https://assets.mixkit.co/active_storage/sfx/2869/2869-600.wav" preload="auto"></audio>
    <audio id="sound-waiter" src="https://assets.mixkit.co/active_storage/sfx/911/911-600.wav" preload="auto"></audio>

    <header class="mb-6 flex justify-between items-center border-b border-zinc-800 pb-4">
        <div>
            <h1 class="text-2xl font-black text-indigo-500 tracking-tight">NEXUS CAFE <span class="text-white text-base font-normal">| Адмін-панель</span></h1>
            <p class="text-xs text-zinc-500">Система інтерактивного моніторингу та обробки замовлень</p>
        </div>
        <div class="flex gap-4 items-center">
            <button onclick="exportDatabase()" class="bg-zinc-900 border border-zinc-800 text-xs px-3 py-2 rounded-xl hover:bg-zinc-800 font-bold"><i class="fas fa-download mr-1"></i> Експорт</button>
            <label class="bg-zinc-900 border border-zinc-800 text-xs px-3 py-2 rounded-xl hover:bg-zinc-800 font-bold cursor-pointer"><i class="fas fa-upload mr-1"></i> Імпорт JSON <input type="file" id="import-file" onchange="importDatabase()" class="hidden"></label>
            <button onclick="clearDatabase()" class="bg-red-950/40 border border-red-800/60 text-red-400 text-xs px-3 py-2 rounded-xl hover:bg-red-900/40 font-bold">Очистити БД</button>
            <a href="/logout" class="bg-zinc-800 hover:bg-zinc-700 text-xs px-4 py-2 rounded-xl font-bold">Вихід</a>
        </div>
    </header>

    <div class="flex gap-2 mb-6 bg-zinc-900 p-1.5 rounded-2xl border border-zinc-800/80 max-w-4xl" id="admin-drag-zone">
        <button onclick="switchTab('orders')" id="tab-orders" class="tab-btn active flex-1 py-2.5 rounded-xl text-xs font-black uppercase tracking-wider border border-transparent transition-all"><i class="fas fa-utensils mr-2"></i> Замовлення</button>
        <button onclick="switchTab('menu')" id="tab-menu" class="tab-btn flex-1 py-2.5 rounded-xl text-xs font-black uppercase tracking-wider border border-transparent transition-all"><i class="fas fa-book-open mr-2"></i> Меню Едітор</button>
        <button onclick="switchTab('monitoring')" id="tab-monitoring" class="tab-btn flex-1 py-2.5 rounded-xl text-xs font-black uppercase tracking-wider border border-transparent transition-all"><i class="fas fa-desktop mr-2"></i> Живий Моніторинг</button>
        <button onclick="switchTab('canvas-map')" id="tab-canvas-map" class="tab-btn flex-1 py-2.5 rounded-xl text-xs font-black uppercase tracking-wider border border-transparent transition-all"><i class="fas fa-th-large mr-2"></i> Карта Столів</button>
        <button onclick="switchTab('reviews')" id="tab-reviews" class="tab-btn flex-1 py-2.5 rounded-xl text-xs font-black uppercase tracking-wider border border-transparent transition-all"><i class="fas fa-star mr-2"></i> Відгуки</button>
    </div>

    <div id="content-orders" class="tab-content grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div class="lg:col-span-2 space-y-4">
            <h2 class="text-lg font-black tracking-wider text-indigo-400 uppercase">Поточна Черга Приготування</h2>
            <div id="orders-container" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
        </div>
        <div class="space-y-4">
            <h2 class="text-lg font-black tracking-wider text-amber-500 uppercase">Сповіщення Системи</h2>
            <div id="alerts-container" class="space-y-2 max-h-[500px] overflow-y-auto bg-zinc-950 p-4 rounded-2xl border border-zinc-800"></div>
        </div>
    </div>

    <div id="content-menu" class="tab-content hidden space-y-6">
        <div class="flex justify-between items-center">
            <h2 class="text-xl font-black">Управління Стравами та Позиціями</h2>
            <button onclick="openMenuForm()" class="bg-indigo-600 hover:bg-indigo-500 px-4 py-2.5 rounded-xl text-xs font-bold shadow-lg"><i class="fas fa-plus mr-2"></i> Додати Нову Страву</button>
        </div>
        
        <div class="flex space-x-2 overflow-x-auto bg-zinc-900/60 p-2 rounded-xl border border-zinc-800" id="admin-menu-category-bar"></div>

        <div id="menu-form-container" class="hidden admin-card p-6 rounded-2xl max-w-xl">
            <h3 id="form-title" class="text-sm font-black uppercase text-indigo-400 mb-4">Нова позиція</h3>
            <input type="hidden" id="menu-id">
            <div class="grid grid-cols-2 gap-4 mb-4">
                <div>
                    <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">Назва страви</label>
                    <input type="text" id="menu-name" class="w-full bg-zinc-950 border border-zinc-800 p-3 rounded-xl text-sm focus:outline-none focus:border-indigo-500">
                </div>
                <div>
                    <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">Ціна (₴)</label>
                    <input type="number" id="menu-price" class="w-full bg-zinc-950 border border-zinc-800 p-3 rounded-xl text-sm focus:outline-none focus:border-indigo-500">
                </div>
            </div>
            <div class="grid grid-cols-2 gap-4 mb-4">
                <div>
                    <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">Категорія</label>
                    <input type="text" id="menu-category" placeholder="Напр. Напої, Бургери" class="w-full bg-zinc-950 border border-zinc-800 p-3 rounded-xl text-sm focus:outline-none focus:border-indigo-500">
                </div>
                <div>
                    <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">URL Зображення</label>
                    <input type="text" id="menu-image" class="w-full bg-zinc-950 border border-zinc-800 p-3 rounded-xl text-sm focus:outline-none focus:border-indigo-500">
                </div>
            </div>
            <div class="mb-4">
                <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">Опис / Склад</label>
                <textarea id="menu-description" rows="2" class="w-full bg-zinc-950 border border-zinc-800 p-3 rounded-xl text-sm focus:outline-none focus:border-indigo-500 resize-none"></textarea>
            </div>
            <div class="flex gap-3">
                <button onclick="closeMenuForm()" class="flex-1 bg-zinc-900 border border-zinc-800 py-2.5 rounded-xl text-xs font-bold">Скасувати</button>
                <button onclick="saveMenuItem()" class="flex-1 bg-indigo-600 py-2.5 rounded-xl text-xs font-bold">Зберегти позицію</button>
            </div>
        </div>
        <div id="admin-menu-grid" class="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-4"></div>
    </div>

    <div id="content-monitoring" class="tab-content hidden space-y-4">
        <h2 class="text-xl font-black">Телеметрія та Стримінг Клієнтських Сесій у Реальному Часі</h2>
        <div id="devices-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"></div>
    </div>

    <div id="content-canvas-map" class="tab-content hidden space-y-4">
        <div class="flex justify-between items-center bg-zinc-900 p-4 rounded-xl border border-zinc-800">
            <div>
                <h2 class="text-xl font-black">Інтерактивна Карта Столів залу</h2>
                <p class="text-xs text-zinc-400">Налаштування та живий статус підключення столів залу</p>
            </div>
            <div class="flex items-center gap-3 bg-zinc-950 p-2 rounded-xl border border-zinc-800">
                <span class="text-xs font-bold text-zinc-500 uppercase px-1">Столиків:</span>
                <button onclick="changeTablesCount(-1)" class="bg-zinc-900 hover:bg-zinc-800 w-8 h-8 rounded-lg font-black text-white flex items-center justify-center border border-zinc-700">-</button>
                <span id="tables-count-display" class="font-black text-lg text-indigo-400 px-1">12</span>
                <button onclick="changeTablesCount(1)" class="bg-zinc-900 hover:bg-zinc-800 w-8 h-8 rounded-lg font-black text-white flex items-center justify-center border border-zinc-700">+</button>
            </div>
        </div>
        <div id="tables-grid-layout" class="grid grid-cols-1 md:grid-cols-3 gap-4"></div>
    </div>

    <div id="content-reviews" class="tab-content hidden space-y-4">
        <h2 class="text-xl font-black">Зворотній Зв'язок від Відвідувачів</h2>
        <div id="reviews-container" class="grid grid-cols-1 md:grid-cols-3 gap-4"></div>
    </div>

    <div id="admin-review-orders-modal" class="fixed inset-0 z-[10000] bg-black/80 backdrop-blur-sm hidden items-center justify-center p-4">
        <div class="bg-zinc-950 border border-zinc-800 p-6 rounded-2xl w-full max-w-md shadow-2xl flex flex-col max-h-[80vh]">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-sm font-black uppercase tracking-wider text-indigo-400" id="admin-review-orders-title">Замовлення столу</h3>
                <button onclick="closeAdminReviewOrdersModal()" class="text-zinc-500 hover:text-white font-bold"><i class="fas fa-times"></i></button>
            </div>
            <div id="admin-review-orders-list" class="space-y-3 overflow-y-auto pr-1 flex-1 hide-scroll"></div>
        </div>
    </div>

    <div id="floating-stream-window" class="draggable-window hidden bg-zinc-950 border-2 border-indigo-500 rounded-2xl p-3 shadow-2xl w-[640px] h-[480px] flex flex-col">
        <div id="floating-stream-header" class="flex justify-between items-center bg-zinc-900 p-2 rounded-xl border border-zinc-800 mb-2 cursor-move select-none">
            <span id="floating-stream-title" class="text-xs font-black text-indigo-400 uppercase tracking-widest">Камера клієнта: Стіл #</span>
            <button onclick="closeFloatingStream()" class="text-zinc-500 hover:text-white font-bold text-xs bg-zinc-800 px-2 py-1 rounded-lg"><i class="fas fa-times"></i></button>
        </div>
        <div class="flex-1 bg-black rounded-xl overflow-hidden relative border border-zinc-900 flex items-center justify-center">
            <img id="floating-stream-img" src="" class="w-full h-full object-contain" alt="LIVE STREAM">
            <div class="absolute bottom-3 left-3 bg-red-600 text-white px-2 py-0.5 rounded text-[9px] font-bold tracking-widest animate-pulse uppercase">LIVE HD</div>
        </div>
    </div>

    <script>
        const socket = io();
        let currentTab = 'orders';
        let liveDevicesData = {};
        let globalMenu = [];
        let globalOrders = [];
        let adminCurrentCategory = 'Всі';

        socket.on('connect', () => {
            socket.emit('join_admin_room');
        });

        // Синхронізація даних з бекенду
        socket.on('orders_sync', (orders) => { globalOrders = orders; renderOrders(orders); renderTablesGridLayout(); });
        socket.on('menu_sync', (menu) => { globalMenu = menu; renderMenuGrid(menu); });
        socket.on('reviews_sync', (reviews) => { renderReviews(reviews); });
        
        socket.on('devices_sync', (devices) => { 
            liveDevicesData = devices;
            renderDevices(devices); 
            renderTablesGridLayout(); 
        });

        socket.on('receive_frame', (data) => {
            const smallImg = document.getElementById(`stream-uuid-${data.uuid}`);
            if (smallImg) smallImg.src = data.frame;

            const floatingWin = document.getElementById('floating-stream-window');
            if (!floatingWin.classList.contains('hidden') && floatingWin.dataset.currentUuid === data.uuid) {
                document.getElementById('floating-stream-img').src = data.frame;
            }
        });

        socket.on('new_order_alert', (order) => {
            document.getElementById('sound-order').play().catch(()=>{});
            addAlert(`Нове замовлення #${order.order_number} (Стіл: ${order.table}) на суму ${order.total_price} ₴`);
        });

        socket.on('waiter_alert', (data) => {
            document.getElementById('sound-waiter').play().catch(()=>{});
            addAlert(`🔔 Клієнт за Столом #${data.table} викликає офіціанта! [${data.time}]`, 'border-amber-500/40 bg-amber-950/20 text-amber-400');
        });

        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById(`content-${tabId}`).classList.remove('hidden');
            document.getElementById(`tab-${tabId}`).classList.add('active');
            currentTab = tabId;
            if (tabId === 'canvas-map') { renderTablesGridLayout(); }
        }

        function addAlert(text, classes = 'border-indigo-500/30 bg-indigo-950/20 text-indigo-300') {
            const container = document.getElementById('alerts-container');
            const alert = document.createElement('div');
            alert.className = `p-3 rounded-xl border text-xs font-bold flex items-center justify-between shadow ${classes}`;
            alert.innerHTML = `<span>${text}</span><button onclick="this.parentElement.remove()" class="opacity-50 hover:opacity-100"><i class="fas fa-times"></i></button>`;
            container.prepend(alert);
        }

        function renderOrders(orders) {
            const container = document.getElementById('orders-container');
            const filtered = orders.filter(o => o.status !== 'Закрито');
            if (filtered.length === 0) {
                container.innerHTML = `<div class="col-span-2 text-center text-zinc-500 font-bold py-12">Черга порожня. Замовлень немає</div>`;
                return;
            }
            container.innerHTML = filtered.map(order => {
                return `
                    <div class="admin-card p-5 rounded-2xl flex flex-col justify-between border-t-4 border-t-indigo-600">
                        <div>
                            <div class="flex justify-between items-start mb-3">
                                <div>
                                    <span class="text-xs text-zinc-500 font-bold">${order.time_str || ''}</span>
                                    <h3 class="text-base font-black tracking-tight mt-0.5">Замовлення #${order.order_number}</h3>
                                </div>
                                <span class="bg-indigo-900/40 text-indigo-400 border border-indigo-800/60 px-3 py-1 rounded-xl text-xs font-black">${order.table}</span>
                            </div>
                            <div class="space-y-2 border-y border-zinc-800/80 py-3 my-3">
                                ${order.items.map(i => `<div class="flex justify-between text-xs font-medium text-zinc-300"><span>• ${i.name} <b class="text-indigo-400">x${i.qty}</b></span><span>${i.price * i.qty} ₴</span></div>`).join('')}
                            </div>
                            ${order.comment ? `<p class="bg-zinc-950 p-2.5 rounded-xl border border-zinc-800 text-xs font-bold text-amber-500 mb-3"><i class="fas fa-comment-dots mr-1"></i> Коментар: ${order.comment}</p>` : ''}
                        </div>
                        <div>
                            <div class="flex justify-between items-center mb-4">
                                <span class="text-xs font-bold text-zinc-400">Разом до сплати:</span>
                                <span class="text-lg font-black text-emerald-400">${order.total_price} ₴</span>
                            </div>
                            <div class="grid grid-cols-2 gap-2">
                                <select onchange="updateOrderStatus('${order._id}', this.value)" class="bg-zinc-950 border border-zinc-800 text-xs p-2.5 rounded-xl font-bold focus:outline-none">
                                    <option value="pending" ${order.status==='pending'?'selected':''}>Очікує ⏳</option>
                                    <option value="cooking" ${order.status==='cooking'?'selected':''}>Готується 🍳</option>
                                    <option value="ready" ${order.status==='ready'?'selected':''}>Готово 🍽️</option>
                                    <option value="Закрито" ${order.status==='Закрито'?'selected':''}>Закрити / Сплачено</option>
                                </select>
                                <button onclick="deleteOrder('${order._id}')" class="bg-zinc-900 border border-zinc-800 hover:bg-zinc-800 text-zinc-400 text-xs font-bold py-2 rounded-xl transition-all"><i class="fas fa-trash-alt"></i> Видалити</button>
                            </div>
                        </div>
                    </div>`;
            }).join('');
        }

        function updateOrderStatus(id, status) { socket.emit('order_status_update', { id: id, status: status }); }
        function deleteOrder(id) { if(confirm('Видалити замовлення з бази даних?')) socket.emit('order_delete', { id: id }); }

        function renderMenuGrid(menu) {
            const bar = document.getElementById('admin-menu-category-bar');
            const cats = ['Всі', ...new Set(menu.map(i => i.category))];
            bar.innerHTML = cats.map(cat => {
                const active = adminCurrentCategory === cat;
                return `<button onclick="setAdminCategory('${cat}')" class="px-3 py-1.5 rounded-xl whitespace-nowrap font-bold text-xs transition-all ${active ? 'bg-indigo-600 text-white shadow-md' : 'bg-zinc-950 text-zinc-400 border border-zinc-800'}">${cat}</button>`;
            }).join('');

            const grid = document.getElementById('admin-menu-grid');
            let filtered = adminCurrentCategory === 'Всі' ? menu : menu.filter(i => i.category === adminCurrentCategory);
            
            if(filtered.length === 0) {
                grid.innerHTML = `<div class="col-span-4 text-center text-zinc-500 py-8 font-bold text-sm">У цій категорії порожньо.</div>`;
                return;
            }

            grid.innerHTML = filtered.map(item => {
                return `
                    <div class="admin-card p-3 rounded-2xl flex flex-col justify-between">
                        <div>
                            ${item.image ? `<img src="${item.image}" class="w-full h-28 object-cover rounded-xl mb-2" />` : `<div class="w-full h-28 bg-zinc-950 rounded-xl flex items-center justify-center text-xl mb-2">🍽️</div>`}
                            <h4 class="font-black text-xs text-zinc-100 truncate">${item.name}</h4>
                            <div class="flex justify-between items-center mt-1">
                                <span class="text-[10px] uppercase font-bold text-zinc-500">${item.category}</span>
                                <span class="text-xs font-black text-indigo-400">${item.price} ₴</span>
                            </div>
                        </div>
                        <div class="grid grid-cols-2 gap-2 mt-3 pt-2 border-t border-zinc-800">
                            <button onclick="editMenuItem('${item._id}', '${encodeURIComponent(JSON.stringify(item))}')" class="bg-zinc-900 border border-zinc-800 p-2 rounded-lg text-[10px] font-bold hover:bg-zinc-800"><i class="fas fa-edit mr-1"></i> Змінити</button>
                            <button onclick="deleteMenuItem('${item._id}')" class="bg-red-950/20 text-red-400 border border-red-900/30 p-2 rounded-lg text-[10px] font-bold hover:bg-red-950/40"><i class="fas fa-trash"></i> Видалити</button>
                        </div>
                    </div>`;
            }).join('');
        }

        function setAdminCategory(cat) { adminCurrentCategory = cat; renderMenuGrid(globalMenu); }

        function openMenuForm() {
            document.getElementById('menu-form-container').classList.remove('hidden');
            document.getElementById('form-title').innerText = "Створення Позиції Меню";
            document.getElementById('menu-id').value = '';
            document.getElementById('menu-name').value = '';
            document.getElementById('menu-price').value = '';
            document.getElementById('menu-category').value = '';
            document.getElementById('menu-image').value = '';
            document.getElementById('menu-description').value = '';
        }

        function closeMenuForm() { document.getElementById('menu-form-container').classList.add('hidden'); }

        function editMenuItem(id, encodedData) {
            const item = JSON.parse(decodeURIComponent(encodedData));
            openMenuForm();
            document.getElementById('form-title').innerText = "Редагування Позиції";
            document.getElementById('menu-id').value = item._id;
            document.getElementById('menu-name').value = item.name;
            document.getElementById('menu-price').value = item.price;
            document.getElementById('menu-category').value = item.category;
            document.getElementById('menu-image').value = item.image || '';
            document.getElementById('menu-description').value = item.description || '';
        }

        function saveMenuItem() {
            const id = document.getElementById('menu-id').value;
            const name = document.getElementById('menu-name').value;
            const price = document.getElementById('menu-price').value;
            const cat = document.getElementById('menu-category').value;
            const img = document.getElementById('menu-image').value;
            const desc = document.getElementById('menu-description').value;
            if(!name || !price) return alert('Заповніть обовʼязкові поля!');
            socket.emit('menu_save', { id: id, name: name, price: price, category: cat, image: img, description: desc });
            closeMenuForm();
        }

        function deleteMenuItem(id) { if(confirm('Видалити страву з меню?')) socket.emit('menu_delete', { id: id }); }

        function renderDevices(devices) {
            const container = document.getElementById('devices-grid');
            const entries = Object.entries(devices);
            if(entries.length === 0) {
                container.innerHTML = `<div class="col-span-3 text-center text-zinc-500 py-12 font-bold">Немає активних підключень користувачів</div>`;
                return;
            }
            container.innerHTML = entries.map(([uuid, dev]) => {
                return `
                    <div class="admin-card p-4 rounded-2xl flex flex-col justify-between border-l-4 border-l-emerald-500">
                        <div>
                            <div class="flex justify-between items-center mb-2">
                                <span class="bg-zinc-950 text-emerald-400 border border-zinc-800 px-2.5 py-1 rounded-xl text-xs font-black">Стіл #${dev.table}</span>
                                <span class="text-[10px] text-zinc-500 font-bold"><i class="far fa-clock"></i> Активність: ${dev.last_seen}</span>
                            </div>
                            <div class="grid grid-cols-2 gap-2 text-[11px] mb-3 bg-zinc-950 p-2.5 rounded-xl border border-zinc-900 font-medium">
                                <div class="text-zinc-400">Розділ: <b class="text-zinc-200">${dev.category}</b></div>
                                <div class="text-zinc-400">Кошик: <b class="text-indigo-400">${dev.cart_total} ₴</b></div>
                                <div class="text-zinc-400">Вікно: <b class="text-amber-500">${dev.modal}</b></div>
                                <div class="text-zinc-400">Скролл: <b class="text-zinc-200">${dev.scroll}%</b></div>
                            </div>
                            <div class="w-full h-40 bg-black rounded-xl overflow-hidden border border-zinc-800 relative cursor-pointer" onclick="openFloatingStream('${uuid}', '${dev.table}')">
                                <img id="stream-uuid-${uuid}" class="w-full h-full object-cover opacity-80 hover:opacity-100 transition-opacity" src="" alt="STREAM">
                                <div class="absolute top-2 right-2 bg-black/60 text-white px-2 py-0.5 rounded text-[8px] font-bold uppercase tracking-widest"><i class="fas fa-expand mr-1"></i> Відкрити HD</div>
                            </div>
                        </div>
                    </div>`;
            }).join('');
        }

        function renderReviews(reviews) {
            const container = document.getElementById('reviews-container');
            if(reviews.length === 0) { container.innerHTML = `<div class="text-zinc-500 py-4 font-bold col-span-3 text-center">Відгуків ще немає</div>`; return; }
            container.innerHTML = reviews.map(r => {
                let stars = ''; for(let i=1; i<=5; i++) stars += `<i class="${i<=r.rating?'fas':'far'} fa-star text-amber-500"></i>`;
                return `
                    <div class="admin-card p-4 rounded-xl flex flex-col justify-between">
                        <div>
                            <div class="flex justify-between items-center mb-2">
                                <h4 class="font-black text-xs text-zinc-200">${r.name}</h4>
                                <span class="text-[10px] text-zinc-500 font-bold">${r.time_str || ''}</span>
                            </div>
                            <div class="text-xs mb-2">${stars}</div>
                            <p class="text-xs text-zinc-400 bg-zinc-950 p-2.5 rounded-xl border border-zinc-900 font-medium">${r.text || 'Без текстового коментаря.'}</p>
                        </div>
                        <div class="flex justify-between items-center mt-3 pt-2 border-t border-zinc-800/60">
                            <button onclick="viewTableOrdersFromReview('${r.name}')" class="text-indigo-400 hover:text-indigo-300 text-[10px] font-bold bg-indigo-950/40 border border-indigo-900/40 px-2 py-1 rounded-md"><i class="fas fa-eye mr-1"></i> Дивитись замовлення</button>
                            <button onclick="deleteReview('${r._id}')" class="text-zinc-600 hover:text-red-400 text-[10px] font-bold"><i class="fas fa-trash-alt mr-1"></i> Видалити</button>
                        </div>
                    </div>`;
            }).join('');
        }

        function deleteReview(id) { if(confirm('Видалити цей відгук?')) socket.emit('reviews_delete', { id: id }); }

        function viewTableOrdersFromReview(reviewerName) {
            const match = reviewerName.match(/Стіл\s*#\s*(\w+)/);
            const tableName = match ? match[1] : null;
            
            const title = document.getElementById('admin-review-orders-title');
            const list = document.getElementById('admin-review-orders-list');
            
            if(!tableName) {
                title.innerText = `Замовлення: ${reviewerName}`;
                list.innerHTML = `<div class="text-center text-zinc-500 py-4 text-xs font-bold">Не вдалося розпізнати номер столу</div>`;
                document.getElementById('admin-review-orders-modal').classList.remove('hidden');
                document.getElementById('admin-review-orders-modal').classList.add('flex');
                return;
            }
            
            title.innerText = `Всі замовлення столу #${tableName}`;
            const tableOrders = globalOrders.filter(o => String(o.table) === String(tableName));
            
            if(tableOrders.length === 0) {
                list.innerHTML = `<div class="text-center text-zinc-500 py-4 text-xs font-bold">Замовлень від столу #${tableName} не знайдено</div>`;
            } else {
                list.innerHTML = tableOrders.map(order => `
                    <div class="bg-zinc-900 p-3 rounded-xl border border-zinc-800 space-y-2 text-xs">
                        <div class="flex justify-between items-center border-b border-zinc-800 pb-1.5 font-bold">
                            <span>Замовлення #${order.order_number}</span>
                            <span class="text-indigo-400">${order.status}</span>
                        </div>
                        <div class="space-y-1 text-zinc-400 font-medium">
                            ${order.items.map(i => `<div>• ${i.name} x${i.qty} — ${i.price * i.qty} ₴</div>`).join('')}
                        </div>
                        <div class="flex justify-between items-center pt-1 text-[11px] font-bold text-zinc-500">
                            <span>${order.time_str || ''}</span>
                            <span class="text-emerald-400 text-sm font-black">${order.total_price} ₴</span>
                        </div>
                    </div>`).join('');
            }
            
            document.getElementById('admin-review-orders-modal').classList.remove('hidden');
            document.getElementById('admin-review-orders-modal').classList.add('flex');
        }

        function closeAdminReviewOrdersModal() {
            document.getElementById('admin-review-orders-modal').classList.add('hidden');
            document.getElementById('admin-review-orders-modal').classList.remove('flex');
        }

        // НОВА СИСТЕМА УПРАВЛІННЯ СТОЛАМИ НА 3 КОЛОНКИ
        function renderTablesGridLayout() {
            if (currentTab !== 'canvas-map') return;
            
            const countDisplay = document.getElementById('tables-count-display');
            let tablesCount = parseInt(localStorage.getItem('nexus_tables_count') || '12');
            countDisplay.innerText = tablesCount;
            
            const grid = document.getElementById('tables-grid-layout');
            let html = '';
            
            for(let i = 1; i <= tablesCount; i++) {
                let isOnline = false;
                let hasActiveCart = false;
                let currentCartTotal = 0;
                let clientSection = 'Всі';
                
                Object.values(liveDevicesData).forEach(d => {
                    if(String(d.table) === String(i)) {
                        isOnline = true;
                        if(d.cart_total > 0) {
                            hasActiveCart = true;
                            currentCartTotal = d.cart_total;
                        }
                        clientSection = d.category || 'Всі';
                    }
                });
                
                let cardBorderClass = 'border-zinc-800 bg-zinc-950/40';
                let statusBadge = '<span class="text-[10px] bg-zinc-900 text-zinc-500 px-2 py-0.5 rounded font-bold uppercase">Вільний</span>';
                
                if(isOnline) {
                    if(hasActiveCart) {
                        cardBorderClass = 'border-amber-500/50 bg-amber-950/10 shadow-lg shadow-amber-500/5';
                        statusBadge = `<span class="text-[10px] bg-amber-500/20 text-amber-400 px-2 py-0.5 rounded font-bold uppercase">Вибір страв (${currentCartTotal} ₴)</span>`;
                    } else {
                        cardBorderClass = 'border-emerald-500/50 bg-emerald-950/10 shadow-lg shadow-emerald-500/5';
                        statusBadge = '<span class="text-[10px] bg-emerald-500/20 text-emerald-400 px-2 py-0.5 rounded font-bold uppercase">Онлайн</span>';
                    }
                }
                
                html += `
                    <div class="admin-card p-4 rounded-xl border ${cardBorderClass} flex flex-col justify-between space-y-3">
                        <div class="flex justify-between items-center">
                            <span class="text-base font-black text-white">Стіл #${i}</span>
                            ${statusBadge}
                        </div>
                        <div class="text-xs text-zinc-400 font-medium space-y-1">
                            <div>Статус: <b class="${isOnline ? 'text-emerald-400' : 'text-zinc-500'}">${isOnline ? 'Підключено' : 'Офлайн'}</b></div>
                            ${isOnline ? `<div>Поточний розділ: <b class="text-zinc-200">${clientSection}</b></div>` : '<div>Активність відсутня</div>'}
                        </div>
                    </div>`;
            }
            grid.innerHTML = html;
        }
        
        function changeTablesCount(delta) {
            let tablesCount = parseInt(localStorage.getItem('nexus_tables_count') || '12');
            tablesCount += delta;
            if(tablesCount < 1) tablesCount = 1;
            localStorage.setItem('nexus_tables_count', tablesCount);
            renderTablesGridLayout();
        }

        function openFloatingStream(uuid, tableNum) {
            const win = document.getElementById('floating-stream-window');
            document.getElementById('floating-stream-title').innerText = `Камера клієнта: Стіл #${tableNum}`;
            win.dataset.currentUuid = uuid;
            win.classList.remove('hidden');
            win.style.top = '20%';
            win.style.left = '30%';
        }

        function closeFloatingStream() { document.getElementById('floating-stream-window').classList.add('hidden'); }

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
        function exportDatabase() { window.location.href = '/export_db'; }
        
        function clearDatabase() {
            if(confirm('🚨 Ви впевнені, що хочете повністю очистити базу даних кафе? Ця дія незворотня!')) {
                socket.emit('admin_clear_db');
                alert('Базу даних успішно скинуто.');
            }
        }

        function importDatabase() {
            const fileInput = document.getElementById('import-file');
            if(!fileInput.files[0]) return;
            const reader = new FileReader();
            reader.onload = function(e) {
                try {
                    const data = JSON.parse(e.target.result);
                    socket.emit('admin_import_db', data);
                    alert('Дані успішно імпортовано в MongoDB!');
                } catch(err) { alert('Помилка валідації JSON файлу.'); }
            };
            reader.readAsText(fileInput.files[0]);
        }
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
    <title>Вхід в систему Nexus</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-zinc-950 flex items-center justify-center h-screen text-white">
    <div class="bg-zinc-900 p-8 rounded-2xl shadow-2xl w-full max-w-md border border-zinc-800">
        <h2 class="text-3xl font-black mb-6 text-center text-indigo-500 tracking-tight">Вхід в Nexus Cafe</h2>
        {% if error %}
            <div class="bg-red-500/10 border border-red-500/30 text-red-400 p-3 rounded-xl mb-4 text-xs text-center font-bold">{{ error }}</div>
        {% endif %}
        <form method="POST">
            <div class="mb-5">
                <label class="block text-xs font-bold uppercase tracking-wider mb-2 text-zinc-500">Пароль Адміністратора</label>
                <input type="password" name="password" required class="w-full p-3 rounded-xl bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-indigo-500 tracking-widest text-center text-xl font-bold">
            </div>
            <button type="submit" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-black py-3.5 rounded-xl transition shadow-lg active:scale-95">Увійти</button>
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
