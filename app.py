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

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', max_http_buffer_size=5000000)

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

def serialize_doc(doc):
    if not doc:
        return None
    doc['_id'] = str(doc['_id'])
    for key, value in doc.items():
        if isinstance(value, datetime):
            doc[key] = value.strftime('%d.%m.%Y %H:%M:%S')
    return doc

def get_all_menu():
    return [serialize_doc(item) for item in db.menu.find()]

def get_all_orders():
    return [serialize_doc(order) for order in db.orders.find().sort('timestamp', -1)]

def get_all_reviews():
    return [serialize_doc(review) for review in db.reviews.find().sort('timestamp', -1)]

def handle_admin_init():
    socketio.emit('menu_sync', get_all_menu())
    socketio.emit('orders_sync', get_all_orders(), room='admins')
    socketio.emit('reviews_sync', get_all_reviews())
    socketio.emit('devices_sync', active_devices, room='admins')

# ==============================================================================
# 3. МАРШРУТИ FLASK (HTTP ROUTES)
# ==============================================================================
@app.route('/')
def index():
    table = request.args.get('table', 'Самовивіз')
    return render_template_string(CLIENT_HTML, table=table)

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
        emit('devices_sync', active_devices)

@socketio.on('join_admin_room')
def handle_join_admin_room():
    if session.get('admin_logged'):
        join_room('admins')
        emit('orders_sync', get_all_orders())
        emit('devices_sync', active_devices)

# --- Моніторинг пристроїв та трансляція екрану ---
@socketio.on('device_connect')
def handle_device_connect(data):
    # Якщо це не адмін, додаємо його до списку активних терміналів
    if not session.get('admin_logged'):
        active_devices[request.sid] = data
        socketio.emit('devices_sync', active_devices, room='admins')

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in active_devices:
        del active_devices[request.sid]
        socketio.emit('devices_sync', active_devices, room='admins')

@socketio.on('request_stream')
def handle_request_stream(data):
    if session.get('admin_logged'):
        socketio.emit('start_stream', {'admin_sid': request.sid}, room=data['target_sid'])

@socketio.on('stop_stream_request')
def handle_stop_stream_request(data):
    if session.get('admin_logged'):
        socketio.emit('stop_stream', room=data['target_sid'])

@socketio.on('stream_frame')
def handle_stream_frame(data):
    # Пересилаємо кадр трансляції конкретному адміну
    socketio.emit('receive_frame', {'frame': data['frame'], 'sid': request.sid}, room=data['admin_sid'])

@socketio.on('stream_error')
def handle_stream_error(data):
    socketio.emit('stream_error', {'error': data['error'], 'sid': request.sid}, room=data['admin_sid'])

# --- Робота з замовленнями та меню ---
@socketio.on('order_create')
def handle_order_create(data):
    last_order = db.orders.find_one(sort=[('timestamp', -1)])
    order_num = 1
    if last_order and 'order_number' in last_order:
        order_num = last_order['order_number'] + 1

    order_data = {
        'order_number': order_num,
        'items': data.get('items', []),
        'total_price': float(data.get('total_price', 0)),
        'table': data.get('table', 'Самовивіз'),
        'comment': data.get('comment', ''),
        'client_name': data.get('client_name', 'Гість'),
        'client_phone': data.get('client_phone', ''),
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
        db.orders.update_one({"_id": ObjectId(order_id)}, {"$set": {"status": new_status}})
        socketio.emit('orders_sync', get_all_orders(), room='admins')
        socketio.emit('order_status_changed', {'id': order_id, 'status': new_status})

@socketio.on('order_delete')
def handle_order_delete(data):
    if session.get('admin_logged'):
        db.orders.delete_one({"_id": ObjectId(data['id'])})
        socketio.emit('orders_sync', get_all_orders(), room='admins')

@socketio.on('menu_save')
def handle_menu_save(data):
    if session.get('admin_logged'):
        item_id = data.get('id')
        item_data = {
            'title': data.get('title', ''),
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
                if 'timestamp' not in i:
                    i['timestamp'] = get_kyiv_time()
            db.orders.insert_many(data['orders'])
            
        if data.get('reviews'):
            for i in data['reviews']: i.pop('_id', None)
            db.reviews.insert_many(data['reviews'])
            
        handle_admin_init()

# ==============================================================================
# 5. ШАБЛОНИ КРАСИВОГО ШВИДКОДІЮЧОГО ІНТЕРФЕЙСУ (HTML/JS)
# ==============================================================================

MODAL_SYSTEM_JS = """
<div id="custom-modal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-[200] hidden items-center justify-center opacity-0 transition-opacity duration-300">
    <div class="bg-gray-900 border border-gray-700 rounded-2xl p-6 max-w-sm w-full mx-4 shadow-2xl transform scale-95 transition-transform duration-300" id="custom-modal-box">
        <h3 id="custom-modal-title" class="text-xl font-bold text-amber-500 mb-2"></h3>
        <p id="custom-modal-message" class="text-gray-300 mb-6 text-sm"></p>
        <div id="custom-prompt-container"></div>
        <div id="custom-modal-actions" class="flex justify-end gap-3 mt-6"></div>
    </div>
</div>

<script>
    function showModal({ type = 'alert', title = 'Сповіщення', message = '', confirmText = 'ОК', cancelText = 'Скасувати', isDanger = false, promptPlaceholder = '' }) {
        return new Promise((resolve) => {
            const modal = document.getElementById('custom-modal');
            const box = document.getElementById('custom-modal-box');
            const titleEl = document.getElementById('custom-modal-title');
            const messageEl = document.getElementById('custom-modal-message');
            const promptContainer = document.getElementById('custom-prompt-container');
            const actionsEl = document.getElementById('custom-modal-actions');

            titleEl.innerHTML = title;
            messageEl.innerHTML = message;
            actionsEl.innerHTML = '';
            promptContainer.innerHTML = '';

            let inputEl = null;
            if (type === 'prompt') {
                inputEl = document.createElement('input');
                inputEl.type = 'text';
                inputEl.className = 'w-full p-3 bg-gray-950 border border-gray-700 rounded-xl text-white focus:outline-none focus:border-amber-500 text-sm';
                inputEl.placeholder = promptPlaceholder;
                promptContainer.appendChild(inputEl);
                setTimeout(() => inputEl.focus(), 100);
            }

            const closeModal = () => {
                modal.classList.remove('opacity-100');
                box.classList.remove('scale-100');
                setTimeout(() => modal.classList.replace('flex', 'hidden'), 300);
            };

            const createBtn = (text, classes, onClick) => {
                const btn = document.createElement('button');
                btn.className = `px-4 py-2 rounded-xl text-sm font-bold transition ${classes}`;
                btn.innerText = text;
                btn.onclick = () => { onClick(); closeModal(); };
                return btn;
            };

            if (type === 'confirm' || type === 'prompt') {
                actionsEl.appendChild(createBtn(cancelText, 'bg-gray-800 text-gray-300 hover:bg-gray-700', () => resolve(type === 'prompt' ? null : false)));
                const confirmClass = isDanger 
                    ? 'bg-red-600/20 text-red-400 border border-red-500/30 hover:bg-red-600 hover:text-white'
                    : 'bg-amber-500 text-gray-950 hover:bg-amber-600';
                actionsEl.appendChild(createBtn(confirmText, confirmClass, () => {
                    resolve(type === 'prompt' ? inputEl.value : true);
                }));
            } else {
                actionsEl.appendChild(createBtn(confirmText, 'bg-amber-500 text-gray-950 hover:bg-amber-600 w-full', () => resolve(true)));
            }

            modal.classList.replace('hidden', 'flex');
            setTimeout(() => {
                modal.classList.add('opacity-100');
                box.classList.add('scale-100');
            }, 10);
        });
    }

    const customAlert = (msg, title = 'Увага!') => showModal({ type: 'alert', title, message: msg });
    const customConfirm = (msg, title = 'Підтвердження', isDanger = false) => showModal({ type: 'confirm', title, message: msg, isDanger, confirmText: 'Так', cancelText: 'Ні' });
    const customPrompt = (msg, title = 'Введення даних', placeholder = 'Введіть значення...') => showModal({ type: 'prompt', title, message: msg, promptPlaceholder: placeholder, confirmText: 'Підтвердити', cancelText: 'Скасувати' });
</script>
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
<body class="bg-gray-900 flex items-center justify-center h-screen text-white">
    <div class="bg-gray-800 p-8 rounded-2xl shadow-2xl w-full max-w-md border border-gray-700">
        <h2 class="text-3xl font-bold mb-6 text-center text-amber-500 font-serif">Вхід в Nexus Cafe</h2>
        {% if error %}
            <div class="bg-red-500/20 border border-red-500 text-red-400 p-3 rounded-lg mb-4 text-sm text-center">{{ error }}</div>
        {% endif %}
        <form method="POST">
            <div class="mb-5">
                <label class="block text-sm font-medium mb-2 text-gray-300">Пароль Адміністратора</label>
                <input type="password" name="password" required class="w-full p-3 rounded-xl bg-gray-700 border border-gray-600 focus:outline-none focus:border-amber-500 tracking-widest text-center text-xl">
            </div>
            <button type="submit" class="w-full bg-amber-500 hover:bg-amber-600 text-gray-900 font-bold py-3 rounded-xl transition duration-300 transform active:scale-95">Увійти</button>
        </form>
    </div>
</body>
</html>
"""

CLIENT_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Онлайн Меню & Замовлення</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style> .category-btn.active { background-color: #f59e0b; color: #1f2937; } </style>
</head>
<body class="bg-gray-950 text-gray-100 font-sans pb-24">
    <header class="bg-gray-900/80 backdrop-blur-md border-b border-gray-800 sticky top-0 z-40 px-4 py-4 flex justify-between items-center">
        <div>
            <h1 class="text-2xl font-black font-serif text-amber-500 tracking-wide">NEXUS CAFE</h1>
            <p class="text-xs text-gray-400"><i class="fa-solid fa-table text-amber-500 mr-1"></i> Стіл: <span class="font-bold text-white">{{ table }}</span></p>
        </div>
        <button onclick="toggleCart(true)" class="relative bg-amber-500 text-gray-950 px-4 py-2.5 rounded-xl font-bold flex items-center gap-2 hover:bg-amber-600 transition">
            <i class="fa-solid fa-basket-shopping text-lg"></i>
            <span id="cart-badge" class="absolute -top-2 -right-2 bg-red-600 text-white text-xs w-6 h-6 flex items-center justify-center rounded-full border-2 border-gray-950 hidden">0</span>
            <span id="cart-total-header">0 грн</span>
        </button>
    </header>

    <main class="max-w-6xl mx-auto px-4 mt-6">
        <div id="categories-container" class="flex gap-2 overflow-x-auto pb-3 mb-6 scrollbar-none"></div>

        <h2 class="text-xl font-bold mb-4 text-amber-500 border-l-4 border-amber-500 pl-2">Наше Меню</h2>
        <div id="menu-grid" class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-6">
            <p class="text-gray-400 col-span-full text-center py-8">Завантаження страв...</p>
        </div>

        <section class="mt-12 bg-gray-900 p-6 rounded-2xl border border-gray-800">
            <h3 class="text-xl font-bold text-amber-500 mb-4"><i class="fa-solid fa-comments mr-2"></i>Відгуки наших гостей</h3>
            
            <form id="review-form" onsubmit="sendReview(event)" class="space-y-4 mb-6 bg-gray-950 p-4 rounded-xl border border-gray-800">
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <input type="text" id="review-name" placeholder="Ваше ім'я" required class="p-3 rounded-lg bg-gray-900 border border-gray-700 focus:outline-none focus:border-amber-500">
                    <div class="flex items-center gap-2">
                        <span class="text-sm text-gray-400">Оцінка:</span>
                        <select id="review-rating" class="p-3 rounded-lg bg-gray-900 border border-gray-700 focus:outline-none focus:border-amber-500 text-amber-500 font-bold">
                            <option value="5">⭐⭐⭐⭐⭐ (5)</option>
                            <option value="4">⭐⭐⭐⭐ (4)</option>
                            <option value="3">⭐⭐⭐ (3)</option>
                            <option value="2">⭐⭐ (2)</option>
                            <option value="1">⭐ (1)</option>
                        </select>
                    </div>
                </div>
                <textarea id="review-text" placeholder="Поділіться враженнями про страву чи обслуговування..." required rows="2" class="w-full p-3 rounded-lg bg-gray-900 border border-gray-700 focus:outline-none focus:border-amber-500"></textarea>
                <button type="submit" class="bg-amber-500 text-gray-950 font-bold px-6 py-2 rounded-lg hover:bg-amber-600 transition">Надіслати відгук</button>
            </form>

            <div id="reviews-list" class="space-y-4 max-h-80 overflow-y-auto pr-2"></div>
        </section>
    </main>

    <div id="cart-sidebar" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 transition-opacity hidden opacity-0 flex justify-end">
        <div class="bg-gray-900 w-full max-w-md h-full flex flex-col shadow-2xl border-l border-gray-800 transform translate-x-full transition-transform duration-300">
            <div class="p-4 border-b border-gray-800 flex justify-between items-center bg-gray-950">
                <h3 class="text-lg font-bold text-amber-500"><i class="fa-solid fa-cart-shopping mr-2"></i>Ваше замовлення</h3>
                <button onclick="toggleCart(false)" class="text-gray-400 hover:text-white text-2xl px-2">&times;</button>
            </div>
            
            <div id="cart-items" class="flex-1 overflow-y-auto p-4 space-y-4"></div>

            <div class="p-4 border-t border-gray-800 bg-gray-950 space-y-3">
                <div class="grid grid-cols-2 gap-2">
                    <input type="text" id="order-name" placeholder="Ваше ім'я" class="p-2.5 rounded-lg bg-gray-900 border border-gray-700 text-sm focus:outline-none focus:border-amber-500">
                    <input type="tel" id="order-phone" placeholder="Телефон (необов.)" class="p-2.5 rounded-lg bg-gray-900 border border-gray-700 text-sm focus:outline-none focus:border-amber-500">
                </div>
                <input type="text" id="order-comment" placeholder="Коментар до замовлення (напр. без цукру)" class="w-full p-2.5 rounded-lg bg-gray-900 border border-gray-700 text-sm focus:outline-none focus:border-amber-500">
                
                <div class="flex justify-between items-center text-lg font-bold py-2 border-t border-b border-gray-800 my-2">
                    <span>До сплати:</span>
                    <span id="cart-total" class="text-amber-500 text-2xl">0 грн</span>
                </div>
                <button onclick="submitOrder()" class="w-full bg-amber-500 hover:bg-amber-600 text-gray-950 font-extrabold py-3.5 rounded-xl transition text-center shadow-lg transform active:scale-95 text-base tracking-wide">
                    НАДІСЛАТИ ЗАМОВЛЕННЯ В КУХНЮ
                </button>
            </div>
        </div>
    </div>

    """ + MODAL_SYSTEM_JS + """

    <script>
        const socket = io();
        const currentTable = "{{ table }}";
        let localMenu = [];
        let cart = {};
        
        // --- Аналіз характеристик пристрою для адмін-панелі ---
        function getBrowserDetails() {
            const ua = navigator.userAgent;
            let browser = "Невідомо";
            if(ua.includes("Firefox")) browser = "Firefox";
            else if(ua.includes("SamsungBrowser")) browser = "Samsung Internet";
            else if(ua.includes("Opera") || ua.includes("OPR")) browser = "Opera";
            else if(ua.includes("Edge") || ua.includes("Edg")) browser = "Edge";
            else if(ua.includes("Chrome")) browser = "Chrome";
            else if(ua.includes("Safari")) browser = "Safari";
            
            let os = "Невідомо";
            if(ua.includes("Win")) os = "Windows";
            else if(ua.includes("Mac")) os = "MacOS";
            else if(ua.includes("Linux")) os = "Linux";
            else if(ua.includes("Android")) os = "Android";
            else if(ua.includes("like Mac")) os = "iOS";

            return { browser, os };
        }

        const deviceInfo = {
            table: currentTable,
            os: getBrowserDetails().os,
            browser: getBrowserDetails().browser,
            screenWidth: window.screen.width,
            screenHeight: window.screen.height
        };

        socket.on('connect', () => {
            socket.emit('device_connect', deviceInfo);
        });

        // --- Система захоплення екрана (Трансляція) ---
        let streamInterval;
        let videoElement = document.createElement('video');
        videoElement.autoplay = true;

        socket.on('start_stream', async (data) => {
            try {
                // Виклик системного вікна запиту доступу до екрана
                const stream = await navigator.mediaDevices.getDisplayMedia({ video: { cursor: "always" } });
                videoElement.srcObject = stream;
                
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                
                streamInterval = setInterval(() => {
                    if (videoElement.videoWidth) {
                        canvas.width = videoElement.videoWidth;
                        canvas.height = videoElement.videoHeight;
                        ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);
                        // Оптимізована якість (0.4) для швидкої передачі
                        const frame = canvas.toDataURL('image/jpeg', 0.4);
                        socket.emit('stream_frame', { frame: frame, admin_sid: data.admin_sid });
                    }
                }, 400); // Оновлення ~2.5 рази на секунду

                // Коли користувач сам зупиняє трансляцію
                stream.getVideoTracks()[0].onended = () => {
                    clearInterval(streamInterval);
                };
            } catch(err) {
                console.error("Помилка трансляції:", err);
                socket.emit('stream_error', { admin_sid: data.admin_sid, error: err.message });
            }
        });

        socket.on('stop_stream', () => {
            if(streamInterval) clearInterval(streamInterval);
            if(videoElement.srcObject) {
                videoElement.srcObject.getTracks().forEach(track => track.stop());
                videoElement.srcObject = null;
            }
        });

        // --- Основна логіка меню ---
        socket.on('menu_sync', (menu) => {
            localMenu = menu;
            renderCategories();
            renderMenu('Всі');
        });

        socket.on('reviews_sync', (reviews) => {
            const container = document.getElementById('reviews-list');
            if(reviews.length === 0) {
                container.innerHTML = '<p class="text-gray-500 text-center py-4 text-sm">Будьте першим, хто залишить відгук!</p>';
                return;
            }
            container.innerHTML = reviews.map(r => `
                <div class="bg-gray-950 p-3.5 rounded-xl border border-gray-800 text-sm">
                    <div class="flex justify-between items-center mb-1">
                        <span class="font-bold text-amber-400">${r.name}</span>
                        <span class="text-xs text-gray-500">${r.time_str || r.timestamp}</span>
                    </div>
                    <div class="text-amber-500 text-xs mb-1.5">${"⭐".repeat(r.rating)}</div>
                    <p class="text-gray-300">${r.text}</p>
                </div>
            `).join('');
        });

        socket.on('order_status_changed', (data) => {
            const statusNames = {pending: 'В черзі', preparing: 'Готується', ready: 'Готово!', completed: 'Видано', cancelled: 'Скасовано'};
            customAlert(`Статус замовлення змінено на: ${statusNames[data.status] || data.status}`, 'Оновлення статусу');
        });

        function renderCategories() {
            const container = document.getElementById('categories-container');
            const categories = ['Всі', ...new Set(localMenu.filter(i => i.available).map(i => i.category))];
            
            container.innerHTML = categories.map((cat, idx) => `
                <button onclick="filterCategory(this, '${cat}')" class="category-btn whitespace-nowrap px-4 py-2 rounded-xl text-sm font-bold bg-gray-900 text-gray-400 hover:text-white transition ${idx === 0 ? 'active' : ''}">
                    ${cat}
                </button>
            `).join('');
        }

        function filterCategory(btn, category) {
            document.querySelectorAll('.category-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderMenu(category);
        }

        function renderMenu(category) {
            const grid = document.getElementById('menu-grid');
            const filtered = category === 'Всі' ? localMenu : localMenu.filter(i => i.category === category);
            const availableItems = filtered.filter(i => i.available);

            if(availableItems.length === 0) {
                grid.innerHTML = '<p class="text-gray-500 text-center col-span-full py-12">У цій категорії немає доступних страв.</p>';
                return;
            }

            // Змінено object-cover на object-contain та додано фон bg-gray-800 (щоб картинка не обрізалась)
            grid.innerHTML = availableItems.map(item => `
                <div class="bg-gray-900 border border-gray-800 rounded-2xl overflow-hidden flex flex-col shadow-lg hover:border-gray-700 transition">
                    ${item.image ? `<img src="${item.image}" class="w-full h-44 object-contain bg-gray-800">` : `<div class="w-full h-44 bg-gray-800 flex items-center justify-center text-gray-600"><i class="fa-solid fa-utensils text-4xl"></i></div>`}
                    <div class="p-4 flex-1 flex flex-col justify-between">
                        <div>
                            <div class="flex justify-between items-start gap-2 mb-1">
                                <h3 class="font-bold text-lg text-white">${item.title}</h3>
                                <span class="text-amber-500 font-extrabold text-lg whitespace-nowrap">${item.price} грн</span>
                            </div>
                            <p class="text-xs text-gray-400 line-clamp-2 mb-4">${item.description || 'Немає опису.'}</p>
                        </div>
                        <button onclick="addToCart('${item._id}')" class="w-full bg-gray-800 hover:bg-amber-500 hover:text-gray-950 font-bold py-2.5 rounded-xl transition text-sm flex items-center justify-center gap-2">
                            <i class="fa-solid fa-plus"></i> Додати у кошик
                        </button>
                    </div>
                </div>
            `).join('');
        }

        function addToCart(id) {
            const item = localMenu.find(i => i._id === id);
            if(!item) return;
            if(cart[id]) cart[id].qty++;
            else cart[id] = { title: item.title, price: item.price, qty: 1 };
            updateCartUI();
        }

        function changeQty(id, delta) {
            if(!cart[id]) return;
            cart[id].qty += delta;
            if(cart[id].qty <= 0) delete cart[id];
            updateCartUI();
        }

        function updateCartUI() {
            const itemsContainer = document.getElementById('cart-items');
            const badge = document.getElementById('cart-badge');
            const totalHeader = document.getElementById('cart-total-header');
            const totalMain = document.getElementById('cart-total');

            let count = 0, total = 0;
            let html = '';

            for(let id in cart) {
                count += cart[id].qty;
                total += cart[id].price * cart[id].qty;
                html += `
                    <div class="bg-gray-950 p-3 rounded-xl border border-gray-800 flex justify-between items-center">
                        <div class="flex-1 pr-2">
                            <h4 class="font-bold text-sm text-white">${cart[id].title}</h4>
                            <span class="text-xs text-gray-400">${cart[id].price} грн × ${cart[id].qty}</span>
                        </div>
                        <div class="flex items-center gap-2 bg-gray-900 rounded-lg p-1.5 border border-gray-700">
                            <button onclick="changeQty('${id}', -1)" class="w-6 h-6 text-gray-400 hover:text-white font-bold text-sm">-</button>
                            <span class="font-bold text-sm w-4 text-center">${cart[id].qty}</span>
                            <button onclick="changeQty('${id}', 1)" class="w-6 h-6 text-gray-400 hover:text-white font-bold text-sm">+</button>
                        </div>
                    </div>
                `;
            }

            if(count === 0) {
                itemsContainer.innerHTML = '<p class="text-gray-500 text-center py-12 text-sm">Кошик порожній. Час обрати щось смачненьке!</p>';
                badge.classList.add('hidden');
            } else {
                itemsContainer.innerHTML = html;
                badge.classList.remove('hidden');
                badge.innerText = count;
            }

            totalHeader.innerText = `${total} грн`;
            totalMain.innerText = `${total} грн`;
        }

        function toggleCart(open) {
            const sidebar = document.getElementById('cart-sidebar');
            const inner = sidebar.querySelector('div');
            if(open) {
                sidebar.classList.remove('hidden');
                setTimeout(() => { sidebar.classList.add('opacity-100'); inner.classList.remove('translate-x-full'); }, 10);
            } else {
                sidebar.classList.remove('opacity-100');
                inner.classList.add('translate-x-full');
                setTimeout(() => sidebar.classList.add('hidden'), 300);
            }
        }

        async function submitOrder() {
            const items = Object.entries(cart).map(([id, info]) => ({ id, title: info.title, price: info.price, qty: info.qty }));
            if(items.length === 0) { 
                await customAlert('Ваш кошик порожній! Додайте страви перед замовленням.', 'Помилка'); 
                return; 
            }

            const data = {
                items: items,
                total_price: Object.values(cart).reduce((sum, i) => sum + (i.price * i.qty), 0),
                table: currentTable,
                comment: document.getElementById('order-comment').value,
                client_name: document.getElementById('order-name').value || 'Гість',
                client_phone: document.getElementById('order-phone').value
            };

            socket.emit('order_create', data, async (res) => {
                if(res.status === 'success') {
                    await customAlert(`Ваше замовлення успішно надіслано в кулінарію!<br><br>Номер замовлення: <b class="text-amber-500">№${res.order_number}</b>`, 'Успіх!');
                    cart = {};
                    updateCartUI();
                    toggleCart(false);
                    document.getElementById('order-comment').value = '';
                }
            });
        }

        async function sendReview(e) {
            e.preventDefault();
            const data = {
                name: document.getElementById('review-name').value,
                rating: document.getElementById('review-rating').value,
                text: document.getElementById('review-text').value
            };
            socket.emit('review_add', data);
            document.getElementById('review-text').value = '';
            await customAlert('Дякуємо за ваш відгук! Він дуже важливий для нас.', 'Дякуємо!');
        }
    </script>
</body>
</html>
"""

ADMIN_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nexus Panel - Управління Кафе</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
</head>
<body class="bg-gray-950 text-gray-100 font-sans min-h-screen flex flex-col">

    <header class="bg-gray-900 border-b border-gray-800 px-6 py-4 flex flex-wrap justify-between items-center gap-4">
        <div class="flex items-center gap-3">
            <div class="w-3 h-3 bg-green-500 rounded-full animate-ping"></div>
            <h1 class="text-xl font-bold tracking-wider font-serif text-amber-500">NEXUS CAFE — АДМІН-ПАНЕЛЬ</h1>
        </div>
        <div class="flex items-center gap-4">
            <a href="/" target="_blank" class="bg-gray-800 hover:bg-gray-700 px-4 py-2 rounded-xl text-sm font-semibold transition"><i class="fa-solid fa-arrow-up-right-from-square mr-2"></i>До клієнтського меню</a>
            <a href="/logout" class="bg-red-600/20 hover:bg-red-600 text-red-400 hover:text-white px-4 py-2 rounded-xl text-sm font-semibold border border-red-500/30 transition">Вийти</a>
        </div>
    </header>

    <div class="bg-gray-900/50 border-b border-gray-800 px-6 flex overflow-x-auto gap-4">
        <button onclick="switchTab('orders-tab')" class="tab-btn px-4 py-3.5 font-bold text-sm border-b-2 border-amber-500 text-amber-500 transition">Замовлення</button>
        <button onclick="switchTab('menu-tab')" class="tab-btn px-4 py-3.5 font-bold text-sm border-b-2 border-transparent text-gray-400 hover:text-white transition">Управління Меню</button>
        <button onclick="switchTab('reviews-tab')" class="tab-btn px-4 py-3.5 font-bold text-sm border-b-2 border-transparent text-gray-400 hover:text-white transition">Відгуки</button>
        <button onclick="switchTab('devices-tab')" class="tab-btn px-4 py-3.5 font-bold text-sm border-b-2 border-transparent text-gray-400 hover:text-white transition">Термінали (Пристрої)</button>
        <button onclick="switchTab('system-tab')" class="tab-btn px-4 py-3.5 font-bold text-sm border-b-2 border-transparent text-gray-400 hover:text-white transition">Система / Резерв</button>
    </div>

    <main class="flex-1 p-6 max-w-7xl w-full mx-auto relative">
        <div id="orders-tab" class="tab-content space-y-6">
            <div class="flex justify-between items-center">
                <h2 class="text-xl font-extrabold text-white border-l-4 border-amber-500 pl-2">Поточні замовлення</h2>
                <span id="orders-count" class="bg-amber-500 text-gray-950 font-bold px-3 py-1 rounded-full text-xs">0 активних</span>
            </div>
            <div id="orders-list" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"></div>
        </div>

        <div id="menu-tab" class="tab-content space-y-6 hidden">
            <div class="bg-gray-900 p-5 rounded-2xl border border-gray-800">
                <h3 id="form-title" class="text-lg font-bold text-amber-500 mb-4">Додати нову позицію</h3>
                <form id="menu-form" onsubmit="saveMenuItem(event)" class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <input type="hidden" id="item-id">
                    <input type="text" id="item-title" placeholder="Назва страви" required class="p-3 bg-gray-950 rounded-xl border border-gray-800 text-sm focus:outline-none focus:border-amber-500">
                    <input type="number" step="0.01" id="item-price" placeholder="Ціна (грн)" required class="p-3 bg-gray-950 rounded-xl border border-gray-800 text-sm focus:outline-none focus:border-amber-500">
                    <input type="text" id="item-category" placeholder="Категорія (напр. Кава, Десерти)" required class="p-3 bg-gray-950 rounded-xl border border-gray-800 text-sm focus:outline-none focus:border-amber-500">
                    <div class="md:col-span-2">
                        <input type="text" id="item-description" placeholder="Опис складу або порції" class="w-full p-3 bg-gray-950 rounded-xl border border-gray-800 text-sm focus:outline-none focus:border-amber-500">
                    </div>
                    <div>
                        <input type="file" id="item-file" onchange="convertImageToBase64(this)" class="w-full text-xs text-gray-400 file:mr-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-xs file:font-semibold file:bg-gray-800 file:text-amber-500 hover:file:bg-gray-700 cursor-pointer">
                        <input type="hidden" id="item-image-base64">
                    </div>
                    <div class="md:col-span-3 flex justify-end gap-3 pt-2">
                        <button type="button" onclick="resetMenuForm()" class="px-5 py-2.5 bg-gray-800 hover:bg-gray-700 text-sm font-bold rounded-xl transition">Очистити форму</button>
                        <button type="submit" class="px-6 py-2.5 bg-amber-500 hover:bg-amber-600 text-gray-950 text-sm font-extrabold rounded-xl transition">Зберегти страву</button>
                    </div>
                </form>
            </div>

            <div class="bg-gray-900 rounded-2xl border border-gray-800 overflow-hidden">
                <div class="p-4 bg-gray-950 border-b border-gray-800 font-bold text-sm text-gray-300">Список страв у базі даних</div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm text-gray-300">
                        <thead class="bg-gray-900/50 text-xs uppercase text-amber-500 font-bold border-b border-gray-800">
                            <tr>
                                <th class="p-4">Фото</th>
                                <th class="p-4">Назва</th>
                                <th class="p-4">Категорія</th>
                                <th class="p-4">Ціна</th>
                                <th class="p-4">Статус</th>
                                <th class="p-4 text-center">Дії</th>
                            </tr>
                        </thead>
                        <tbody id="admin-menu-list" class="divide-y divide-gray-800"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <div id="reviews-tab" class="tab-content space-y-6 hidden">
            <h2 class="text-xl font-extrabold text-white border-l-4 border-amber-500 pl-2">Модерація відгуків</h2>
            <div id="admin-reviews-list" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
        </div>

        <div id="devices-tab" class="tab-content space-y-6 hidden">
            <div class="flex justify-between items-center">
                <h2 class="text-xl font-extrabold text-white border-l-4 border-amber-500 pl-2">Моніторинг Терміналів</h2>
                <span id="devices-count" class="bg-amber-500 text-gray-950 font-bold px-3 py-1 rounded-full text-xs">0 онлайн</span>
            </div>
            <div id="devices-list" class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-6">
                </div>
        </div>

        <div id="system-tab" class="tab-content space-y-6 hidden">
            <div class="bg-gray-900 p-6 rounded-2xl border border-gray-800 max-w-xl mx-auto space-y-6">
                <h3 class="text-lg font-bold text-red-400 border-b border-gray-800 pb-2"><i class="fa-solid fa-triangle-exclamation mr-2"></i>Небезпечна зона</h3>
                <div class="flex flex-wrap gap-4">
                    <button onclick="clearDatabase()" class="flex-1 min-w-[200px] bg-red-600/20 hover:bg-red-600 text-red-400 hover:text-white border border-red-500/30 p-4 rounded-xl font-bold transition text-center text-sm">
                        ОЧИСТИТИ ВСЮ БАЗУ
                    </button>
                    <a href="/export_db" download="cafe_backup.json" class="flex-1 min-w-[200px] bg-green-600/20 hover:bg-green-600 text-green-400 hover:text-white border border-green-500/30 p-4 rounded-xl font-bold transition text-center text-sm block">
                        ЕКСПОРТ БАЗИ (JSON)
                    </a>
                </div>
                
                <div class="bg-gray-950 p-4 rounded-xl border border-gray-800 space-y-3">
                    <label class="block text-sm font-bold text-gray-300">Імпорт резервної копії (JSON)</label>
                    <input type="file" id="import-file" accept=".json" class="text-xs text-gray-400 block w-full cursor-pointer">
                    <button onclick="importDatabase()" class="w-full bg-amber-500 hover:bg-amber-600 text-gray-950 py-2 rounded-lg text-sm font-bold transition">Завантажити в базу</button>
                </div>
            </div>
        </div>
    </main>

    <div id="device-modal" class="fixed inset-0 bg-black/85 backdrop-blur-sm z-[150] hidden items-center justify-center opacity-0 transition-opacity duration-300">
        <div class="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-3xl mx-4 shadow-2xl flex flex-col transform scale-95 transition-transform duration-300 h-[80vh]" id="device-modal-box">
            <div class="p-5 border-b border-gray-800 flex justify-between items-center shrink-0 bg-gray-950 rounded-t-2xl">
                <h3 class="text-xl font-bold text-amber-500"><i class="fa-solid fa-desktop mr-2"></i>Деталі Пристрою</h3>
                <button onclick="closeDeviceModal()" class="text-gray-400 hover:text-white"><i class="fa-solid fa-xmark text-2xl"></i></button>
            </div>
            <div class="p-5 flex-1 overflow-y-auto space-y-4">
                <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm bg-gray-950 p-4 rounded-xl border border-gray-800 text-gray-300">
                    <div><span class="text-gray-500 block text-xs mb-1">Стіл / Клієнт:</span><b class="text-white" id="dev-table"></b></div>
                    <div><span class="text-gray-500 block text-xs mb-1">ОС:</span><b class="text-white" id="dev-os"></b></div>
                    <div><span class="text-gray-500 block text-xs mb-1">Браузер:</span><b class="text-white" id="dev-browser"></b></div>
                    <div><span class="text-gray-500 block text-xs mb-1">Розширення:</span><b class="text-white" id="dev-screen"></b></div>
                </div>
                
                <div class="bg-black border border-gray-800 rounded-xl overflow-hidden relative flex items-center justify-center h-80 lg:h-96 w-full">
                    <img id="stream-img" class="w-full h-full object-contain hidden" />
                    <div id="stream-placeholder" class="text-gray-600 flex flex-col items-center gap-3">
                        <i class="fa-solid fa-video-slash text-4xl"></i>
                        <span class="text-sm font-bold">Трансляція не активна</span>
                        <span class="text-xs text-gray-500 max-w-xs text-center">Натисніть кнопку нижче, щоб надіслати запит на перегляд екрана клієнта.</span>
                    </div>
                </div>
            </div>
            <div class="p-5 border-t border-gray-800 flex justify-end gap-3 bg-gray-950 rounded-b-2xl shrink-0">
                <button onclick="requestDeviceStream()" id="btn-req-stream" class="px-5 py-2.5 bg-amber-500 hover:bg-amber-600 text-gray-950 text-sm font-bold rounded-xl transition shadow-lg"><i class="fa-solid fa-satellite-dish mr-2"></i>Запросити трансляцію</button>
                <button onclick="toggleStreamFullscreen()" id="btn-fullscreen" class="px-5 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-bold rounded-xl transition hidden shadow-lg"><i class="fa-solid fa-expand mr-2"></i>Розгорнути екран</button>
            </div>
        </div>
    </div>

    <audio id="alert-sound" src="https://assets.mixkit.co/active_storage/sfx/2869/2869-84.wav" preload="auto"></audio>

    """ + MODAL_SYSTEM_JS + """

    <script>
        const socket = io();
        let globalMenu = [];
        let globalDevices = {};
        let currentViewingSid = null;

        socket.on('connect', () => {
            socket.emit('join_admin_room');
        });

        // --- Логіка моніторингу пристроїв ---
        socket.on('devices_sync', (devices) => {
            globalDevices = devices;
            renderDevices();
        });

        function renderDevices() {
            const container = document.getElementById('devices-list');
            const count = Object.keys(globalDevices).length;
            document.getElementById('devices-count').innerText = `${count} онлайн`;
            
            if(count === 0) {
                container.innerHTML = '<p class="text-gray-500 text-center py-12 col-span-full">Немає підключених терміналів.</p>';
                return;
            }

            container.innerHTML = Object.entries(globalDevices).map(([sid, dev]) => `
                <div class="bg-gray-900 border border-gray-800 rounded-2xl p-5 shadow-lg flex justify-between items-center transition hover:border-gray-700">
                    <div>
                        <h4 class="font-bold text-white text-lg"><i class="fa-solid fa-tablet-screen-button text-amber-500 mr-2"></i>${dev.table}</h4>
                        <p class="text-xs text-gray-400 mt-1">${dev.os} • ${dev.browser}</p>
                    </div>
                    <button onclick="openDeviceModal('${sid}')" class="bg-amber-500/10 text-amber-500 hover:bg-amber-500 hover:text-gray-950 px-4 py-2 rounded-xl text-sm font-bold border border-amber-500/30 transition shadow-sm">Детально</button>
                </div>
            `).join('');
        }

        function openDeviceModal(sid) {
            const dev = globalDevices[sid];
            if(!dev) return;
            currentViewingSid = sid;

            document.getElementById('dev-table').innerText = dev.table;
            document.getElementById('dev-os').innerText = dev.os;
            document.getElementById('dev-browser').innerText = dev.browser;
            document.getElementById('dev-screen').innerText = `${dev.screenWidth}x${dev.screenHeight}`;

            // Reset stream view
            document.getElementById('stream-img').classList.add('hidden');
            document.getElementById('stream-img').src = '';
            document.getElementById('stream-placeholder').classList.remove('hidden');
            document.getElementById('btn-fullscreen').classList.add('hidden');
            
            const btnReq = document.getElementById('btn-req-stream');
            btnReq.classList.remove('hidden');
            btnReq.innerHTML = '<i class="fa-solid fa-satellite-dish mr-2"></i>Запросити трансляцію';
            btnReq.disabled = false;

            const modal = document.getElementById('device-modal');
            const box = document.getElementById('device-modal-box');
            modal.classList.replace('hidden', 'flex');
            setTimeout(() => { modal.classList.add('opacity-100'); box.classList.add('scale-100'); }, 10);
        }

        function closeDeviceModal() {
            if(currentViewingSid) {
                socket.emit('stop_stream_request', { target_sid: currentViewingSid });
            }
            currentViewingSid = null;
            const modal = document.getElementById('device-modal');
            const box = document.getElementById('device-modal-box');
            modal.classList.remove('opacity-100');
            box.classList.remove('scale-100');
            setTimeout(() => modal.classList.replace('flex', 'hidden'), 300);
        }

        function requestDeviceStream() {
            if(!currentViewingSid) return;
            const btn = document.getElementById('btn-req-stream');
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i>Очікування клієнта...';
            btn.disabled = true;
            socket.emit('request_stream', { target_sid: currentViewingSid });
        }

        socket.on('receive_frame', (data) => {
            if(currentViewingSid === data.sid) {
                const img = document.getElementById('stream-img');
                const placeholder = document.getElementById('stream-placeholder');
                const btnReq = document.getElementById('btn-req-stream');
                const btnFull = document.getElementById('btn-fullscreen');

                if(img.classList.contains('hidden')) {
                    img.classList.remove('hidden');
                    placeholder.classList.add('hidden');
                    btnReq.classList.add('hidden');
                    btnFull.classList.remove('hidden');
                }
                img.src = data.frame;
            }
        });

        socket.on('stream_error', async (data) => {
            if(currentViewingSid === data.sid) {
                const btn = document.getElementById('btn-req-stream');
                btn.innerHTML = '<i class="fa-solid fa-satellite-dish mr-2"></i>Запросити трансляцію';
                btn.disabled = false;
                await customAlert('Клієнт відхилив запит, або його пристрій/браузер не підтримує трансляцію екрана.', 'Помилка доступу');
            }
        });

        function toggleStreamFullscreen() {
            const img = document.getElementById('stream-img');
            if(img.classList.contains('hidden')) return;
            if (img.requestFullscreen) img.requestFullscreen();
            else if (img.webkitRequestFullscreen) img.webkitRequestFullscreen();
            else if (img.msRequestFullscreen) img.msRequestFullscreen();
        }

        // --- Логіка замовлень та меню ---
        socket.on('orders_sync', (orders) => {
            document.getElementById('orders-count').innerText = `${orders.filter(o => o.status !== 'completed' && o.status !== 'cancelled').length} активних`;
            const container = document.getElementById('orders-list');
            if(orders.length === 0) {
                container.innerHTML = '<p class="text-gray-500 text-center py-12 col-span-full">Немає жодного замовлення.</p>';
                return;
            }

            const statusColors = { pending: 'bg-orange-500/20 text-orange-400 border-orange-500/30', preparing: 'bg-blue-500/20 text-blue-400 border-blue-500/30', ready: 'bg-purple-500/20 text-purple-400 border-purple-500/30', completed: 'bg-green-500/20 text-green-400 border-green-500/30', cancelled: 'bg-red-500/20 text-red-400 border-red-500/30' };
            const statusNames = { pending: 'Очікує', preparing: 'Готується', ready: 'Готово', completed: 'Видано', cancelled: 'Скасовано' };

            container.innerHTML = orders.map(o => `
                <div class="bg-gray-900 border border-gray-800 rounded-2xl p-5 flex flex-col justify-between space-y-4 shadow-xl relative overflow-hidden">
                    <div>
                        <div class="flex justify-between items-start mb-3">
                            <div>
                                <span class="text-lg font-extrabold text-white">Замовлення №${o.order_number}</span>
                                <div class="text-xs text-gray-400 mt-0.5">${o.time_str || o.timestamp}</div>
                            </div>
                            <span class="px-2.5 py-1 text-xs font-bold border rounded-lg ${statusColors[o.status]}">${statusNames[o.status]}</span>
                        </div>
                        <div class="text-xs font-semibold text-amber-400 bg-gray-950 p-2 rounded-lg border border-gray-800 mb-3">
                            <div><i class="fa-solid fa-table mr-1.5"></i>Стіл / Отримання: <span class="text-white font-bold">${o.table}</span></div>
                            <div><i class="fa-solid fa-user mr-1.5"></i>Клієнт: <span class="text-white">${o.client_name} ${o.client_phone ? '('+o.client_phone+')' : ''}</span></div>
                        </div>
                        <div class="divide-y divide-gray-850 max-h-40 overflow-y-auto bg-gray-950 rounded-xl p-3 border border-gray-850">
                            ${o.items.map(item => `
                                <div class="py-1.5 flex justify-between text-xs text-gray-300">
                                    <span>${item.title} <strong class="text-amber-500">×${item.qty}</strong></span>
                                    <span class="font-bold text-white">${item.price * item.qty} грн</span>
                                </div>
                            `).join('')}
                        </div>
                        ${o.comment ? `<p class="text-xs bg-red-500/10 border border-red-500/20 rounded-lg p-2 text-red-300 mt-2"><strong>Ком:</strong> ${o.comment}</p>` : ''}
                    </div>
                    <div>
                        <div class="flex justify-between items-center border-t border-gray-800 pt-3 mt-1">
                            <span class="text-xs text-gray-400">Всього:</span>
                            <span class="text-lg font-black text-amber-500">${o.total_price} грн</span>
                        </div>
                        <div class="grid grid-cols-2 gap-2 mt-3">
                            <select onchange="updateStatus('${o._id}', this.value)" class="bg-gray-950 p-2 text-xs rounded-lg border border-gray-700 font-bold focus:outline-none">
                                <option value="pending" ${o.status === 'pending' ? 'selected' : ''}>Очікує</option>
                                <option value="preparing" ${o.status === 'preparing' ? 'selected' : ''}>Готується</option>
                                <option value="ready" ${o.status === 'ready' ? 'selected' : ''}>Готово</option>
                                <option value="completed" ${o.status === 'completed' ? 'selected' : ''}>Видано</option>
                                <option value="cancelled" ${o.status === 'cancelled' ? 'selected' : ''}>Скасовано</option>
                            </select>
                            <button onclick="deleteOrder('${o._id}')" class="bg-red-950 text-red-400 hover:bg-red-600 hover:text-white px-2 py-2 rounded-lg text-xs font-bold transition"><i class="fa-solid fa-trash-can mr-1"></i> Видалити</button>
                        </div>
                    </div>
                </div>
            `).join('');
        });

        socket.on('new_order_alert', (order) => {
            try { document.getElementById('alert-sound').play(); } catch(e){}
            customAlert(`Стіл: <span class="text-amber-500">${order.table}</span><br>Сума: ${order.total_price} грн.`, `🔥 НАДІЙШЛО ЗАМОВЛЕННЯ №${order.order_number}!`);
        });

        socket.on('menu_sync', (menu) => {
            globalMenu = menu;
            const container = document.getElementById('admin-menu-list');
            // Змінено object-cover на object-contain та додано фон bg-gray-800 (щоб картинка не обрізалась)
            container.innerHTML = menu.map(i => `
                <tr class="hover:bg-gray-900/40 transition">
                    <td class="p-4">${i.image ? `<img src="${i.image}" class="w-12 h-12 object-contain bg-gray-800 rounded-lg border border-gray-700">` : `<div class="w-12 h-12 bg-gray-800 rounded-lg flex items-center justify-center text-gray-600"><i class="fa-solid fa-utensils"></i></div>`}</td>
                    <td class="p-4 font-bold text-white">${i.title}<div class="text-xs text-gray-500 font-normal mt-0.5">${i.description || ''}</div></td>
                    <td class="p-4 text-xs font-semibold text-gray-400"><span class="bg-gray-800 px-2 py-1 rounded-md border border-gray-700">${i.category}</span></td>
                    <td class="p-4 font-bold text-amber-500">${i.price} грн</td>
                    <td class="p-4">
                        <button onclick="toggleAvailability('${i._id}', ${!i.available})" class="px-2.5 py-1 rounded-full text-xs font-bold border transition ${i.available ? 'bg-green-500/20 border-green-500 text-green-400' : 'bg-red-500/20 border-red-500 text-red-400'}">
                            ${i.available ? 'Доступно' : 'Стоп-лист'}
                        </button>
                    </td>
                    <td class="p-4 text-center">
                        <div class="flex justify-center gap-2">
                            <button onclick="editMenuItem('${i._id}')" class="bg-blue-600/20 hover:bg-blue-600 text-blue-400 hover:text-white px-2.5 py-1.5 rounded-lg text-xs font-bold border border-blue-500/30 transition"><i class="fa-solid fa-pen"></i></button>
                            <button onclick="deleteMenuItem('${i._id}')" class="bg-red-600/20 hover:bg-red-600 text-red-400 hover:text-white px-2.5 py-1.5 rounded-lg text-xs font-bold border border-red-500/30 transition"><i class="fa-solid fa-trash"></i></button>
                        </div>
                    </td>
                </tr>
            `).join('');
        });

        socket.on('reviews_sync', (reviews) => {
            const container = document.getElementById('admin-reviews-list');
            if(reviews.length === 0) {
                container.innerHTML = '<p class="text-gray-500 col-span-full text-center py-6">Відгуків немає.</p>';
                return;
            }
            container.innerHTML = reviews.map(r => `
                <div class="bg-gray-900 border border-gray-800 p-4 rounded-xl flex justify-between items-start gap-3">
                    <div class="space-y-1">
                        <div class="flex items-center gap-2">
                            <span class="font-bold text-white text-sm">${r.name}</span>
                            <span class="text-xs text-amber-500 font-bold">${"⭐".repeat(r.rating)}</span>
                        </div>
                        <div class="text-[11px] text-gray-500">${r.time_str || r.timestamp}</div>
                        <p class="text-xs text-gray-300 pt-1">${r.text}</p>
                    </div>
                    <button onclick="deleteReview('${r._id}')" class="text-xs bg-red-600/20 hover:bg-red-600 text-red-400 hover:text-white p-2 rounded-lg border border-red-500/30 transition"><i class="fa-solid fa-trash"></i></button>
                </div>
            `).join('');
        });

        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
            document.getElementById(tabId).classList.remove('hidden');
            document.querySelectorAll('.tab-btn').forEach(b => {
                b.classList.remove('border-amber-500', 'text-amber-500');
                b.classList.add('border-transparent', 'text-gray-400');
            });
            event.currentTarget.classList.add('border-amber-500', 'text-amber-500');
            event.currentTarget.classList.remove('border-transparent', 'text-gray-400');
        }

        function updateStatus(id, status) { socket.emit('order_status_update', { id, status }); }

        async function deleteOrder(id) {
            if(await customConfirm('Ви дійсно хочете видалити це замовлення з бази?', 'Видалити замовлення', true)) {
                socket.emit('order_delete', { id });
            }
        }

        function convertImageToBase64(input) {
            const file = input.files[0];
            if(!file) return;
            const reader = new FileReader();
            reader.onloadend = function() { document.getElementById('item-image-base64').value = reader.result; }
            reader.readAsDataURL(file);
        }

        async function saveMenuItem(e) {
            e.preventDefault();
            const id = document.getElementById('item-id').value;
            const data = {
                id: id ? id : null,
                title: document.getElementById('item-title').value,
                price: parseFloat(document.getElementById('item-price').value),
                category: document.getElementById('item-category').value,
                description: document.getElementById('item-description').value,
                image: document.getElementById('item-image-base64').value,
                available: true
            };
            socket.emit('menu_save', data);
            resetMenuForm();
            await customAlert('Страву успішно збережено до бази даних!', 'Успіх');
        }

        function editMenuItem(id) {
            const item = globalMenu.find(i => i._id === id);
            if(!item) return;
            document.getElementById('item-id').value = item._id;
            document.getElementById('item-title').value = item.title;
            document.getElementById('item-price').value = item.price;
            document.getElementById('item-category').value = item.category;
            document.getElementById('item-description').value = item.description || '';
            document.getElementById('item-image-base64').value = item.image || '';
            document.getElementById('form-title').innerText = "Редагувати страву: " + item.title;
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        function toggleAvailability(id, state) {
            const item = globalMenu.find(i => i._id === id);
            if(!item) return;
            socket.emit('menu_save', { id, title: item.title, price: item.price, category: item.category, description: item.description, image: item.image, available: state });
        }

        async function deleteMenuItem(id) {
            if(await customConfirm('Ви впевнені, що хочете видалити цю страву з меню назавжди?', 'Видалити страву', true)) {
                socket.emit('menu_delete', { id });
            }
        }

        async function deleteReview(id) {
            if(await customConfirm('Видалити цей відгук клієнта?', 'Видалити відгук', true)) {
                socket.emit('reviews_delete', { id });
            }
        }

        function resetMenuForm() {
            document.getElementById('item-id').value = '';
            document.getElementById('menu-form').reset();
            document.getElementById('item-image-base64').value = '';
            document.getElementById('form-title').innerText = "Додати нову позицію";
        }

        async function clearDatabase() {
            if(await customConfirm('УВАГА! Ви дійсно хочете повністю видалити всі страви, замовлення та відгуки з бази даних? Цю дію неможливо скасувати!', 'Очищення бази', true)) {
                const confirmWord = await customPrompt('Для підтвердження очищення бази, введіть слово "DELETE":', 'Підтвердження безпеки', 'DELETE');
                if(confirmWord === 'DELETE') {
                    socket.emit('admin_clear_db');
                    await customAlert('Базу даних успішно повністю очищено.', 'Очищено');
                } else if(confirmWord !== null) {
                    await customAlert('Невірне слово підтвердження. Очищення скасовано.', 'Помилка');
                }
            }
        }

        async function importDatabase() {
            const fileInput = document.getElementById('import-file');
            const file = fileInput.files[0];
            if(!file) { await customAlert('Будь ласка, спочатку оберіть файл резервної копії формату .json.', 'Файл не обрано'); return; }
            
            const reader = new FileReader();
            reader.onload = async function(e) {
                try {
                    const data = JSON.parse(e.target.result);
                    socket.emit('admin_import_db', data);
                    await customAlert('Резервну копію успішно розгорнуто в базі даних!', 'Імпорт завершено');
                    fileInput.value = '';
                } catch(err) {
                    await customAlert('Помилка читання JSON-файлу. Перевірте формат структури.', 'Помилка імпорту');
                }
            };
            reader.readAsText(file);
        }
    </script>
</body>
</html>
"""

# ==============================================================================
# 6. ТОЧКА ВХОДУ ДЛЯ ЗАПУСКУ
# ==============================================================================
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
