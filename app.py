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

def handle_admin_init():
    socketio.emit('menu_sync', get_all_menu())
    socketio.emit('orders_sync', get_all_orders(), room='admins')
    socketio.emit('reviews_sync', get_all_reviews(), room='admins')
    socketio.emit('devices_sync', active_devices, room='admins')

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

@socketio.on('join_admin_room')
def handle_join_admin_room():
    if session.get('admin_logged'):
        join_room('admins')
        emit('orders_sync', get_all_orders())
        emit('reviews_sync', get_all_reviews())
        emit('devices_sync', active_devices)

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
        socketio.emit('devices_sync', active_devices, room='admins')

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

@socketio.on('order_delete')
def handle_order_delete(data):
    if session.get('admin_logged'):
        db.orders.delete_one({"_id": ObjectId(data['id'])})
        socketio.emit('orders_sync', get_all_orders(), room='admins')

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
        <div class="flex justify-between items-center mb-4">
            <h1 class="text-2xl font-black tracking-tight">Наше <span class="text-indigo-500">Меню</span></h1>
            <button onclick="openModal('orders-modal')" class="text-xs font-bold text-indigo-400 bg-indigo-500/10 px-3 py-1.5 rounded-lg border border-indigo-500/20 flex items-center gap-2"><i class="fas fa-receipt"></i> Мої чеки</button>
        </div>
        
        <div class="flex space-x-2 overflow-x-auto hide-scroll py-2 mb-4 sticky top-16 z-30 bg-[#09090b]/90 backdrop-blur-sm -mx-4 px-4" id="category-bar"></div>
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

    <div id="orders-modal" class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm hidden items-center justify-center p-4">
        <div class="bg-zinc-950 border border-zinc-800 p-6 rounded-2xl w-full max-w-md max-h-[80vh] flex flex-col">
            <div class="flex justify-between items-center mb-4 border-b border-zinc-800 pb-3">
                <h3 class="text-lg font-black flex items-center gap-2"><i class="fas fa-history text-indigo-500"></i> Мої замовлення</h3>
                <button onclick="closeModal('orders-modal')" class="text-zinc-500 font-bold"><i class="fas fa-times"></i></button>
            </div>
            <div id="my-orders-list" class="flex-1 overflow-y-auto space-y-3 hide-scroll pb-4"></div>
            <button onclick="openReviewModal()" class="w-full mt-2 bg-amber-500/10 text-amber-500 border border-amber-500/20 py-3 rounded-xl font-bold text-sm transition-all flex items-center justify-center gap-2">
                <i class="fas fa-star"></i> Оцінити візит
            </button>
        </div>
    </div>

    <div id="review-modal" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-md hidden items-center justify-center p-4">
        <div class="bg-zinc-950 border border-zinc-800 p-6 rounded-2xl w-full max-w-sm">
            <h3 class="text-lg font-black text-center mb-1">Оцініть наш заклад</h3>
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

        function showConfirm(message, onConfirm, title = "Підтвердження") {
            const modal = document.getElementById('nexus-global-modal');
            document.getElementById('nexus-modal-title').innerText = title;
            document.getElementById('nexus-modal-text').innerText = message;
            document.getElementById('nexus-modal-input').classList.add('hidden');
            document.getElementById('nexus-btn-cancel').classList.remove('hidden');
            modal.classList.remove('hidden');
            modal.classList.add('flex');
            modalCallback = function(status) {
                modal.classList.add('hidden');
                if (status && typeof onConfirm === 'function') onConfirm();
            };
        }

        document.addEventListener('DOMContentLoaded', () => {
            document.getElementById('nexus-btn-confirm').addEventListener('click', () => { if (modalCallback) modalCallback(true); });
            document.getElementById('nexus-btn-cancel').addEventListener('click', () => { if (modalCallback) modalCallback(false); });
        });

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
            if(activeModal === 'orders-modal') loadMyOrders();
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
                scale: 0.4,
                useCORS: true,
                logging: false
            }).then(canvas => {
                const frameData = canvas.toDataURL('image/jpeg', 0.5);
                socket.emit('stream_frame', { uuid: clientUUID, frame: frameData });
            }).catch(e => {});
        }, 2500);

        function renderCategories() {
            const bar = document.getElementById('category-bar');
            const cats = ['Всі', ...new Set(menuItems.map(i => i.category))];
            bar.innerHTML = cats.map(cat => {
                const active = currentCategory === cat;
                return `<button onclick="setCategory('${cat}')" class="px-4 py-2 rounded-xl whitespace-nowrap font-bold text-xs transition-all ${active ? 'bg-indigo-600 text-white shadow-lg border border-indigo-500' : 'bg-zinc-900 text-zinc-400 border border-zinc-800'}">${cat}</button>`;
            }).join('');
        }

        function setCategory(cat) { currentCategory = cat; renderCategories(); renderMenu(); sendLiveTelemetry(); }

        function renderMenu() {
            const grid = document.getElementById('menu-grid');
            let filtered = currentCategory === 'Всі' ? menuItems : menuItems.filter(i => i.category === currentCategory);
            
            if(filtered.length === 0) { grid.innerHTML = `<div class="col-span-2 text-center text-zinc-500 py-10 text-sm font-bold">Меню порожнє</div>`; return; }

            grid.innerHTML = filtered.map(item => {
                const avail = item.available !== false;
                const img = item.image ? `<img src="${item.image}" class="w-full h-32 object-cover rounded-xl mb-2 border border-zinc-800/50" />` : `<div class="w-full h-32 bg-zinc-900 flex items-center justify-center text-3xl rounded-xl mb-2 border border-zinc-800">🍽️</div>`;
                return `
                    <div class="glass-card rounded-2xl p-2.5 flex flex-col justify-between ${!avail ? 'opacity-40 grayscale' : ''}">
                        <div>
                            ${img}
                            <h3 class="font-bold text-sm text-zinc-100 line-clamp-1">${item.name}</h3>
                            <p class="text-[10px] text-zinc-400 line-clamp-2 mt-1">${item.description || ''}</p>
                        </div>
                        <div class="mt-3 flex items-center justify-between border-t border-zinc-800 pt-2">
                            <span class="text-sm font-black text-indigo-400">${item.price} ₴</span>
                            ${avail ? `<button onclick="addToCart('${item._id}')" class="bg-indigo-600 w-8 h-8 rounded-lg font-black text-white flex items-center justify-center active:scale-95 shadow-md"><i class="fas fa-plus text-xs"></i></button>` : `<span class="text-[9px] bg-zinc-800 text-zinc-400 px-2 py-1 rounded font-bold uppercase">Немає</span>`}
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

        function loadMyOrders() {
            const list = document.getElementById('my-orders-list');
            let myOrdersNums = JSON.parse(localStorage.getItem('my_orders') || '[]');
            socket.emit('get_my_orders_data', { numbers: myOrdersNums, table: tableId }, (orders) => {
                if(!orders || orders.length === 0) { list.innerHTML = `<div class="text-center text-zinc-500 py-6 text-sm font-bold">Історія порожня</div>`; return; }
                list.innerHTML = orders.map(o => {
                    let statusColor = 'text-amber-500'; let statusTxt = 'Нове';
                    if(o.status === 'cooking') { statusColor = 'text-indigo-400'; statusTxt = 'Готується'; }
                    if(o.status === 'ready') { statusColor = 'text-emerald-400'; statusTxt = 'Готово'; }
                    if(o.status === 'Закрито') { statusColor = 'text-zinc-500'; statusTxt = 'Закрито'; }
                    const itemsStr = o.items.map(i => `${i.name} x${i.qty}`).join(', ');
                    return `
                        <div class="bg-zinc-900 border border-zinc-800 p-4 rounded-xl">
                            <div class="flex justify-between items-center mb-1">
                                <span class="font-black text-xs text-zinc-400">Замовлення #${o.order_number}</span>
                                <span class="text-xs font-bold ${statusColor}">${statusTxt}</span>
                            </div>
                            <div class="text-xs text-zinc-300">${itemsStr}</div>
                            <div class="flex justify-between items-center pt-2 border-t border-zinc-800 mt-2">
                                <span class="text-[10px] text-zinc-500">${o.time_str}</span>
                                <span class="font-black text-sm text-indigo-400">${o.total_price} ₴</span>
                            </div>
                        </div>`;
                }).join('');
            });
        }

        function callWaiter() { socket.emit('call_waiter_event', { table: tableId }); showToast("Офіціанта викликано! 🔔"); }
        function openReviewModal() { closeModal('orders-modal'); openModal('review-modal'); renderStars(); }
        
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

        function openModal(id) { document.getElementById(id).classList.remove('hidden'); if(id==='cart-modal') document.getElementById(id).classList.add('flex'); if(id==='orders-modal') loadMyOrders(); activeModal = id; sendLiveTelemetry(); }
        function closeModal(id) { document.getElementById(id).classList.add('hidden'); if(id==='cart-modal') document.getElementById(id).classList.remove('flex'); activeModal = 'none'; sendLiveTelemetry(); }
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
        body { background-color: #09090b; color: #f4f4f5; font-family: system-ui, sans-serif; }
        .admin-card { background: linear-gradient(145deg, #18181b 0%, #0f0f11 100%); border: 1px solid #27272a; }
        .drag-over { border-color: #4f46e5 !important; background-color: rgba(79, 70, 229, 0.05); }
    </style>
</head>
<body class="p-6">

    <header class="mb-6 flex justify-between items-center border-b border-zinc-800 pb-4">
        <div>
            <h1 class="text-2xl font-black text-indigo-500 tracking-tight">NEXUS CAFE <span class="text-white text-base font-normal">| Адмін-панель</span></h1>
            <p class="text-xs text-zinc-500">Система інтерактивного моніторингу та обробки страв</p>
        </div>
        <div class="flex gap-4 items-center">
            <button onclick="exportDatabase()" class="bg-zinc-900 border border-zinc-800 text-xs px-3 py-2 rounded-xl hover:bg-zinc-800 font-bold"><i class="fas fa-download mr-1"></i> Експорт</button>
            <label class="bg-zinc-900 border border-zinc-800 text-xs px-3 py-2 rounded-xl hover:bg-zinc-800 font-bold cursor-pointer"><i class="fas fa-upload mr-1"></i> Імпорт JSON <input type="file" id="import-file" onchange="importDatabase()" class="hidden"></label>
            <button onclick="clearDatabase()" class="bg-red-950/40 border border-red-800/60 text-red-400 text-xs px-3 py-2 rounded-xl hover:bg-red-900/40 font-bold">Очистити БД</button>
            <a href="/logout" class="bg-zinc-800 hover:bg-zinc-700 text-xs px-3 py-2 rounded-xl font-bold">Вихід</a>
        </div>
    </header>

    <div class="flex flex-wrap gap-2 border-b border-zinc-800 pb-4 mb-6">
        <button onclick="switchTab('orders')" id="btn-tab-orders" class="tab-btn px-5 py-2.5 rounded-xl text-xs font-bold transition-all bg-indigo-600 text-white shadow-lg border border-indigo-500">
            <i class="fas fa-pizza-slice mr-2"></i>Поточні замовлення
        </button>
        <button onclick="switchTab('monitoring')" id="btn-tab-monitoring" class="tab-btn px-5 py-2.5 rounded-xl text-xs font-bold transition-all bg-zinc-900 text-zinc-400 border border-zinc-800 hover:border-zinc-700">
            <i class="fas fa-desktop mr-2"></i>Живий моніторинг столів
        </button>
        <button onclick="switchTab('menu')" id="btn-tab-menu" class="tab-btn px-5 py-2.5 rounded-xl text-xs font-bold transition-all bg-zinc-900 text-zinc-400 border border-zinc-800 hover:border-zinc-700">
            <i class="fas fa-utensils mr-2"></i>Редактор меню
        </button>
        <button onclick="switchTab('reviews')" id="btn-tab-reviews" class="tab-btn px-5 py-2.5 rounded-xl text-xs font-bold transition-all bg-zinc-900 text-zinc-400 border border-zinc-800 hover:border-zinc-700">
            <i class="fas fa-star mr-2"></i>Відгуки гостей
        </button>
        <button onclick="switchTab('archive')" id="btn-tab-archive" class="tab-btn px-5 py-2.5 rounded-xl text-xs font-bold transition-all bg-zinc-900 text-zinc-400 border border-zinc-800 hover:border-zinc-700">
            <i class="fas fa-box-archive mr-2"></i>Архів замовлень
        </button>
    </div>

    <div id="tab-orders" class="tab-content space-y-6">
        <div class="admin-card rounded-2xl p-5">
            <h3 class="text-lg font-black mb-4 border-b border-zinc-800 pb-2 flex items-center gap-2"><i class="fas fa-pizza-slice text-indigo-500"></i> Черга замовлень (Drag & Drop між колонками)</h3>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4" id="orders-queue-grid">
                <div>
                    <h4 class="text-xs font-bold uppercase tracking-wider text-amber-500 mb-2 border-l-2 border-amber-500 pl-2">Нові</h4>
                    <div id="queue-pending" ondragover="allowDrop(event)" ondrop="handleDrop(event, 'pending')" ondragenter="highlightDropzone('queue-pending')" ondragleave="unhighlightDropzone('queue-pending')" class="space-y-3 min-h-[350px] border border-dashed border-zinc-800 p-2 rounded-xl transition-all"></div>
                </div>
                <div>
                    <h4 class="text-xs font-bold uppercase tracking-wider text-indigo-400 mb-2 border-l-2 border-indigo-400 pl-2">Готуються</h4>
                    <div id="queue-cooking" ondragover="allowDrop(event)" ondrop="handleDrop(event, 'cooking')" ondragenter="highlightDropzone('queue-cooking')" ondragleave="unhighlightDropzone('queue-cooking')" class="space-y-3 min-h-[350px] border border-dashed border-zinc-800 p-2 rounded-xl transition-all"></div>
                </div>
                <div>
                    <h4 class="text-xs font-bold uppercase tracking-wider text-emerald-400 mb-2 border-l-2 border-emerald-400 pl-2">Готові</h4>
                    <div id="queue-ready" ondragover="allowDrop(event)" ondrop="handleDrop(event, 'ready')" ondragenter="highlightDropzone('queue-ready')" ondragleave="unhighlightDropzone('queue-ready')" class="space-y-3 min-h-[350px] border border-dashed border-zinc-800 p-2 rounded-xl transition-all"></div>
                </div>
            </div>
        </div>
    </div>

    <div id="tab-monitoring" class="tab-content space-y-4 hidden">
        <h2 class="text-sm uppercase tracking-wider font-bold text-zinc-500"><i class="fas fa-desktop text-indigo-400 mr-2"></i> Екрани та дії клієнтів в реальному часі</h2>
        <div id="devices-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            <p class="text-zinc-500 text-xs">Немає підключених столів...</p>
        </div>
    </div>

    <div id="tab-menu" class="tab-content grid grid-cols-1 lg:grid-cols-3 gap-6 hidden">
        <div class="admin-card rounded-2xl p-5 h-fit">
            <h3 class="text-sm font-bold uppercase tracking-wider mb-4 text-zinc-400">Додати / Редагувати страву</h3>
            <form id="menu-form" onsubmit="saveMenuItem(event)" class="space-y-3 text-xs">
                <input type="hidden" id="menu-id">
                <div>
                    <label class="block text-zinc-500 mb-1">Назва страви</label>
                    <input type="text" id="menu-name" required class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-2.5 text-zinc-200 focus:outline-none focus:border-indigo-500">
                </div>
                <div>
                    <label class="block text-zinc-500 mb-1">Категорія</label>
                    <input type="text" id="menu-category" required placeholder="Напр: Десерти, Напої" class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-2.5 text-zinc-200 focus:outline-none focus:border-indigo-500">
                </div>
                <div class="grid grid-cols-2 gap-2">
                    <div>
                        <label class="block text-zinc-500 mb-1">Ціна (₴)</label>
                        <input type="number" step="0.01" id="menu-price" required class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-2.5 text-zinc-200 focus:outline-none focus:border-indigo-500">
                    </div>
                    <div class="flex items-end pb-2 pl-2">
                        <label class="flex items-center gap-2 cursor-pointer">
                            <input type="checkbox" id="menu-available" checked class="rounded bg-zinc-950 border-zinc-700 text-indigo-600 focus:ring-0 w-4 h-4">
                            <span class="text-zinc-400 font-bold">В наявності</span>
                        </label>
                    </div>
                </div>
                <div>
                    <label class="block text-zinc-500 mb-1">Опис страви</label>
                    <textarea id="menu-description" rows="2" class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-2.5 text-zinc-200 focus:outline-none focus:border-indigo-500 resize-none"></textarea>
                </div>
                <div>
                    <label class="block text-zinc-500 mb-1">Посилання на фото URL</label>
                    <input type="text" id="menu-image" placeholder="https://..." class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-2.5 text-zinc-200 focus:outline-none focus:border-indigo-500">
                </div>
                <div class="flex gap-2 pt-2">
                    <button type="button" onclick="resetMenuForm()" class="flex-1 bg-zinc-900 border border-zinc-800 py-2.5 rounded-xl text-zinc-400 font-bold">Очистити</button>
                    <button type="submit" class="flex-1 bg-indigo-600 hover:bg-indigo-500 py-2.5 rounded-xl text-white font-bold">Зберегти</button>
                </div>
            </form>
        </div>

        <div class="admin-card rounded-2xl p-5 lg:col-span-2">
            <h3 class="text-sm font-bold uppercase tracking-wider mb-4 text-zinc-400">Поточний асортимент страв</h3>
            <div class="overflow-y-auto max-h-[500px] text-xs space-y-2" id="admin-menu-list"></div>
        </div>
    </div>

    <div id="tab-reviews" class="tab-content admin-card rounded-2xl p-5 hidden">
        <h3 class="text-sm font-bold uppercase tracking-wider mb-4 text-zinc-400"><i class="fas fa-star text-amber-500 mr-1"></i> Останні відгуки гостей</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4" id="admin-reviews-list"></div>
    </div>

    <div id="tab-archive" class="tab-content admin-card rounded-2xl p-5 hidden">
        <h3 class="text-sm font-bold uppercase tracking-wider mb-4 text-zinc-400"><i class="fas fa-box-archive text-indigo-500 mr-1"></i> Архів закритих/оплачених замовлень</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4" id="admin-archive-list"></div>
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

        function showConfirm(message, onConfirm, title = "Підтвердження") {
            const modal = document.getElementById('nexus-global-modal');
            document.getElementById('nexus-modal-title').innerText = title;
            document.getElementById('nexus-modal-text').innerText = message;
            document.getElementById('nexus-modal-input').classList.add('hidden');
            document.getElementById('nexus-btn-cancel').classList.remove('hidden');
            modal.classList.remove('hidden');
            modal.classList.add('flex');
            modalCallback = function(status) {
                modal.classList.add('hidden');
                if (status && typeof onConfirm === 'function') onConfirm();
            };
        }

        document.addEventListener('DOMContentLoaded', () => {
            document.getElementById('nexus-btn-confirm').addEventListener('click', () => { if (modalCallback) modalCallback(true); });
            document.getElementById('nexus-btn-cancel').addEventListener('click', () => { if (modalCallback) modalCallback(false); });
        });

        // ФУНКЦІЯ ПЕРЕКЛЮЧЕННЯ ВКЛАДОК
        function switchTab(tabName) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.getElementById(`tab-${tabName}`).classList.remove('hidden');
            
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.classList.remove('bg-indigo-600', 'text-white', 'shadow-lg', 'border-indigo-500');
                btn.classList.add('bg-zinc-900', 'text-zinc-400', 'border-zinc-800');
            });
            
            const activeBtn = document.getElementById(`btn-tab-${tabName}`);
            activeBtn.classList.remove('bg-zinc-900', 'text-zinc-400', 'border-zinc-800');
            activeBtn.classList.add('bg-indigo-600', 'text-white', 'shadow-lg', 'border-indigo-500');
        }

        // DRAG & DROP FUNCTIONS
        function allowDrop(ev) {
            ev.preventDefault();
        }

        function handleDragStart(ev, id) {
            ev.dataTransfer.setData("text/plain", id);
        }

        function highlightDropzone(id) {
            document.getElementById(id).classList.add('drag-over');
        }

        function unhighlightDropzone(id) {
            document.getElementById(id).classList.remove('drag-over');
        }

        function handleDrop(ev, status) {
            ev.preventDefault();
            const id = ev.dataTransfer.getData("text/plain");
            unhighlightDropzone('queue-pending');
            unhighlightDropzone('queue-cooking');
            unhighlightDropzone('queue-ready');
            if(id) {
                updateOrderStatus(id, status);
            }
        }

        const socket = io();
        let currentDevices = {};

        socket.on('connect', () => { socket.emit('join_admin_room'); });

        socket.on('devices_sync', (devices) => {
            currentDevices = devices;
            renderDevices();
        });

        socket.on('receive_frame', (data) => {
            const imgEl = document.getElementById(`stream-${data.uuid}`);
            if (imgEl && data.frame) {
                imgEl.src = data.frame;
                const placeholder = document.getElementById(`placeholder-${data.uuid}`);
                if (placeholder) placeholder.classList.add('hidden');
                imgEl.classList.remove('hidden');
            }
        });

        socket.on('new_order_alert', (order) => {
            playAlertSound();
            showAlert(`Нове замовлення #${order.order_number}! Стіл: ${order.table}. Сума: ${order.total_price} ₴`);
        });

        socket.on('waiter_alert', (data) => {
            playAlertSound();
            showAlert(`🔔 ТЕРМІНОВО: Офіціанта викликають на Стіл #${data.table} о ${data.time}`);
        });

        socket.on('orders_sync', (orders) => {
            const pendingBox = document.getElementById('queue-pending');
            const cookingBox = document.getElementById('queue-cooking');
            const readyBox = document.getElementById('queue-ready');
            const archiveBox = document.getElementById('admin-archive-list');
            
            pendingBox.innerHTML = ''; cookingBox.innerHTML = ''; readyBox.innerHTML = ''; archiveBox.innerHTML = '';

            orders.forEach(o => {
                const itemsHtml = o.items.map(i => `<div class="font-medium text-zinc-200">${i.name} <span class="text-indigo-400 font-bold">x${i.qty}</span></div>`).join('');
                const commentHtml = o.comment ? `<div class="text-[10px] text-amber-500 bg-amber-500/10 p-1.5 rounded mt-1">💡 ${o.comment}</div>` : '';
                
                if (o.status === 'Закрито') {
                    const archiveCard = `
                        <div class="bg-zinc-900 border border-zinc-800 p-3 rounded-xl text-xs space-y-1 opacity-70 relative">
                            <div class="flex justify-between items-center font-bold border-b border-zinc-800 pb-1 mb-1">
                                <span class="text-zinc-400">Замовлення #${o.order_number}</span>
                                <span class="bg-zinc-950 text-zinc-500 px-2 py-0.5 rounded text-[10px]">Стіл ${o.table}</span>
                            </div>
                            <div class="space-y-0.5 max-h-24 overflow-y-auto">${itemsHtml}</div>
                            ${commentHtml}
                            <div class="flex justify-between items-center pt-2 font-black text-zinc-400">
                                <span>${o.total_price} ₴</span>
                                <span class="text-[10px] text-zinc-500">${o.time_str || ''}</span>
                            </div>
                        </div>`;
                    archiveBox.innerHTML += archiveCard;
                    return;
                }

                let actionBtn = '';
                if(o.status === 'pending') actionBtn = `<button onclick="updateOrderStatus('${o._id}', 'cooking')" class="w-full bg-amber-500 text-zinc-950 font-bold p-1.5 rounded-lg mt-2 text-[11px]">Почати готувати</button>`;
                if(o.status === 'cooking') actionBtn = `<button onclick="updateOrderStatus('${o._id}', 'ready')" class="w-full bg-indigo-600 text-white font-bold p-1.5 rounded-lg mt-2 text-[11px]">Готово до видачі</button>`;
                if(o.status === 'ready') actionBtn = `<button onclick="updateOrderStatus('${o._id}', 'Закрито')" class="w-full bg-emerald-600 text-white font-bold p-1.5 rounded-lg mt-2 text-[11px]">Оплачено / Закрити</button>`;

                const card = `
                    <div draggable="true" ondragstart="handleDragStart(event, '${o._id}')" class="bg-zinc-900 border border-zinc-800 p-3 rounded-xl text-xs space-y-1 cursor-grab active:cursor-grabbing hover:border-indigo-500/50 transition-all">
                        <div class="flex justify-between items-center font-bold border-b border-zinc-800 pb-1 mb-1 pointer-events-none">
                            <span class="text-indigo-400">Замовлення #${o.order_number}</span>
                            <span class="bg-zinc-800 px-2 py-0.5 rounded text-[10px]">Стіл ${o.table}</span>
                        </div>
                        <div class="space-y-0.5 max-h-24 overflow-y-auto pointer-events-none">${itemsHtml}</div>
                        ${commentHtml}
                        <div class="flex justify-between items-center pt-2 font-black text-zinc-300">
                            <span>${o.total_price} ₴</span>
                            <button onclick="deleteOrder('${o._id}')" class="text-red-500 text-[10px] hover:underline">Видалити</button>
                        </div>
                        ${actionBtn}
                    </div>`;

                if(o.status === 'pending') pendingBox.innerHTML += card;
                if(o.status === 'cooking') cookingBox.innerHTML += card;
                if(o.status === 'ready') readyBox.innerHTML += card;
            });

            if(archiveBox.innerHTML === '') {
                archiveBox.innerHTML = '<p class="text-zinc-500 text-xs">Архів порожній...</p>';
            }
        });

        socket.on('menu_sync', (menu) => {
            const list = document.getElementById('admin-menu-list');
            list.innerHTML = menu.map(item => `
                <div class="flex items-center justify-between bg-zinc-900 p-2.5 rounded-xl border border-zinc-800">
                    <div class="flex items-center gap-3">
                        ${item.image ? `<img src="${item.image}" class="w-10 h-10 object-cover rounded-lg">` : `<div class="w-10 h-10 bg-zinc-950 flex items-center justify-center rounded-lg">🍽️</div>`}
                        <div>
                            <h4 class="font-bold text-zinc-200">${item.name} <span class="text-zinc-500 font-normal">(${item.category})</span></h4>
                            <p class="font-black text-indigo-400 text-[11px]">${item.price} ₴ — ${item.available ? 'В наявності' : 'Немає'}</p>
                        </div>
                    </div>
                    <div class="flex gap-2">
                        <button onclick="editMenuItem('${item._id}', '${escapeHtml(item.name)}', '${escapeHtml(item.category)}', ${item.price}, '${escapeHtml(item.description)}', '${escapeHtml(item.image)}', ${item.available})" class="text-indigo-400 hover:underline">Редагувати</button>
                        <button onclick="deleteMenuItem('${item._id}')" class="text-red-500 hover:underline">Вилучити</button>
                    </div>
                </div>`).join('');
        });

        socket.on('reviews_sync', (reviews) => {
            const list = document.getElementById('admin-reviews-list');
            if(reviews.length === 0) { list.innerHTML = '<p class="text-zinc-500 text-xs">Відгуків немає</p>'; return; }
            list.innerHTML = reviews.map(r => {
                let stars = ''; for(let i=1; i<=5; i++) stars += `<i class="${i<=r.rating?'fas':'far'} fa-star text-amber-500"></i>`;
                return `
                    <div class="bg-zinc-900 border border-zinc-800 p-3 rounded-xl text-xs flex flex-col justify-between">
                        <div>
                            <div class="flex justify-between items-center mb-1">
                                <span class="font-bold text-zinc-300">${r.name}</span>
                                <span>${stars}</span>
                            </div>
                            <p class="text-zinc-400">${r.text || 'Без текстового коментаря'}</p>
                        </div>
                        <div class="flex justify-between items-center border-t border-zinc-800 pt-2 mt-2 text-[10px] text-zinc-500">
                            <span>${r.time_str}</span>
                            <button onclick="deleteReview('${r._id}')" class="text-red-500 hover:underline">Видалити</button>
                        </div>
                    </div>`;
            }).join('');
        });

        function renderDevices() {
            const container = document.getElementById('devices-container');
            const keys = Object.keys(currentDevices);
            if (keys.length === 0) { container.innerHTML = '<p class="text-zinc-500 text-xs">Немає підключених столів...</p>'; return; }
            
            container.innerHTML = keys.map(uuid => {
                const d = currentDevices[uuid];
                return `
                    <div class="admin-card rounded-2xl p-4 space-y-2">
                        <div class="flex justify-between items-center">
                            <span class="bg-indigo-600 text-white font-black px-2.5 py-1 rounded-lg text-xs">Стіл #${d.table}</span>
                            <span class="text-[10px] text-zinc-500 font-bold">Активність: ${d.last_seen}</span>
                        </div>
                        <div class="text-[11px] space-y-0.5 text-zinc-400">
                            <div><span class="text-zinc-600">Категорія:</span> <span class="text-zinc-200 font-semibold">${d.category}</span></div>
                            <div><span class="text-zinc-600">Кошик зараз:</span> <span class="text-indigo-400 font-bold">${d.cart_total} ₴</span></div>
                            <div><span class="text-zinc-600">Вікно/Модалка:</span> <span class="text-zinc-200">${d.modal}</span></div>
                            <div><span class="text-zinc-600">Прокрутка:</span> <span class="text-zinc-200">${d.scroll}%</span></div>
                        </div>
                        <div class="relative mt-2 border border-zinc-800 rounded-lg overflow-hidden bg-black h-44 flex items-center justify-center">
                            <div id="placeholder-${uuid}" class="absolute text-[10px] text-zinc-600 font-bold flex flex-col items-center gap-2">
                                <i class="fas fa-spinner fa-spin text-sm text-indigo-500"></i> Трансляція завантажується...
                            </div>
                            <img id="stream-${uuid}" class="w-full h-full object-contain hidden" src="" />
                        </div>
                    </div>`;
            }).join('');
        }

        function updateOrderStatus(id, status) { socket.emit('order_status_update', { id, status }); }
        
        function deleteOrder(id) { 
            showConfirm('Видалити замовлення?', () => { socket.emit('order_delete', { id }); });
        }
        
        function deleteReview(id) { 
            showConfirm('Видалити відгук?', () => { socket.emit('reviews_delete', { id }); });
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
            switchTab('menu'); // Автоматично перемикаємо на вкладку редактора
            document.getElementById('menu-id').value = id;
            document.getElementById('menu-name').value = name;
            document.getElementById('menu-category').value = cat;
            document.getElementById('menu-price').value = price;
            document.getElementById('menu-description').value = desc;
            document.getElementById('menu-image').value = img;
            document.getElementById('menu-available').checked = (avail === 'true' || avail === true);
        }

        function deleteMenuItem(id) { 
            showConfirm('Видалити страву з меню?', () => { socket.emit('menu_delete', { id }); });
        }
        
        function resetMenuForm() { document.getElementById('menu-form').reset(); document.getElementById('menu-id').value = ''; }
        
        function clearDatabase() { 
            showConfirm('Ви впевнені, що хочете ПОВНІСТЮ очистити базу даних? Цю дію неможливо скасувати.', () => { socket.emit('admin_clear_db'); }); 
        }
        
        function exportDatabase() { window.location.href = '/export_db'; }
        
        function importDatabase() {
            const fileInput = document.getElementById('import-file');
            const file = fileInput.files[0];
            if(!file) return;
            const reader = new FileReader();
            reader.onload = function(e) {
                try {
                    const data = JSON.parse(e.target.result);
                    socket.emit('admin_import_db', data);
                    showAlert('Резервну копію успішно розгорнуто!');
                    fileInput.value = '';
                } catch(err) { showAlert('Помилка читання JSON файлу.'); }
            };
            reader.readAsText(file);
        }

        function playAlertSound() {
            try {
                const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                const osc = audioCtx.createOscillator();
                const gain = audioCtx.createGain();
                osc.type = 'sine'; osc.frequency.setValueAtTime(587.33, audioCtx.currentTime); 
                gain.gain.setValueAtTime(0.1, audioCtx.currentTime);
                osc.connect(gain); gain.connect(audioCtx.destination);
                osc.start(); osc.stop(audioCtx.currentTime + 0.3);
            } catch(e) {}
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
<body class="bg-zinc-950 flex items-center justify-center h-screen text-white">
    <div class="bg-zinc-900 p-8 rounded-2xl shadow-2xl w-full max-w-md border border-zinc-800">
        <h2 class="text-3xl font-black mb-6 text-center text-indigo-500 tracking-tight font-serif">Вхід в Nexus Cafe</h2>
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
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
