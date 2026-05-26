import eventlet
eventlet.monkey_patch()

import os
import json
import logging
from datetime import datetime, timedelta, timezone
from bson.objectid import ObjectId
from flask import Flask, request, render_template_string, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from pymongo import MongoClient

# ==============================================================================
# 1. НАЛАШТУВАННЯ ЛОГУВАННЯ ТА ІНІЦІАЛІЗАЦІЯ СЕРВЕРА
# ==============================================================================
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s')
logger = logging.getLogger("NexusCafe")

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'nexus-pro-ultra-key-2026-secure-string-xyz')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', max_http_buffer_size=15000000)

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/cafe_db')
ADMIN_PASSWORD = "1111"

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client.get_default_database(default='cafe_db')
    # Перевірка підключення
    client.server_info()
    logger.info("Успішно підключено до бази даних MongoDB.")
except Exception as e:
    logger.error(f"Помилка підключення до MongoDB: {e}")

# Словник для збереження активних телеметричних даних пристроїв клієнтів
active_devices = {}
# Кількість адміністраторів, які зараз дивляться вкладку моніторингу
active_monitoring_admins = 0

# ==============================================================================
# 2. ДОПОМІЖНІ ФУНКЦІЇ ДЛЯ РОБОТИ З ДАНИМИ ТА ЧАСОМ
# ==============================================================================
def get_kyiv_time():
    """Повертає поточний час у часовому поясі Києва (UTC+3)"""
    return datetime.now(timezone.utc) + timedelta(hours=3)

def get_kyiv_time_str():
    """Форматований рядок дати та часу для чеків"""
    return get_kyiv_time().strftime('%d.%m.%Y %H:%M:%S')

def get_kyiv_time_short():
    """Короткий час для відмітки останньої активності телеметрії"""
    return get_kyiv_time().strftime('%H:%M:%S')

def serialize_doc(doc):
    """Конвертує ObjectId та datetime об'єкти MongoDB у зрозумілий для JSON формат"""
    if not doc:
        return None
    d = dict(doc)
    d['_id'] = str(d['_id'])
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.strftime('%d.%m.%Y %H:%M')
    return d

def get_all_menu():
    """Отримання всього списку страв з сортуванням за категоріями"""
    return [serialize_doc(i) for i in db.menu.find().sort("category", 1)]

def get_all_orders():
    """Отримання списку замовлень, відсортованих за часом надходження (спочатку нові)"""
    return [serialize_doc(o) for o in db.orders.find().sort("timestamp", -1)]

def get_all_reviews():
    """Отримання відгуків користувачів"""
    return [serialize_doc(r) for r in db.reviews.find().sort("timestamp", -1)]

def handle_admin_init_sync():
    """Комплексна синхронізація всіх блоків адмін-панелі"""
    socketio.emit('menu_sync', get_all_menu())
    socketio.emit('orders_sync', get_all_orders(), room='admins')
    socketio.emit('reviews_sync', get_all_reviews(), room='admins')
    socketio.emit('devices_sync', active_devices, room='admins')
    # Розрахунок аналітики та відправка клієнту
    socketio.emit('analytics_sync', calculate_dashboard_stats(), room='admins')

def calculate_dashboard_stats():
    """Розраховує фінансові та кількісні метрики для головного екрана адміна"""
    orders = list(db.orders.find())
    reviews = list(db.reviews.find())
    
    total_revenue = sum(float(o.get('total_price', 0)) for o in orders if o.get('status') == 'Закрито')
    active_orders_count = sum(1 for o in orders if o.get('status') in ['pending', 'cooking', 'ready'])
    
    avg_rating = 5.0
    if reviews:
        avg_rating = round(sum(int(r.get('rating', 5)) for r in reviews) / len(reviews), 1)
        
    return {
        'total_revenue': total_revenue,
        'active_orders': active_orders_count,
        'avg_rating': avg_rating,
        'devices_online': len(active_devices)
    }

# Наповнення бази даних дефолтними стравами, якщо вона абсолютно порожня
if db.menu.count_documents({}) == 0:
    logger.info("База даних страв порожня. Проводиться первинне наповнення демо-меню...")
    demo_menu = [
        {"name": "Кібер Бургер з Яловичиною", "price": 245.0, "category": "Бургери", "description": "Соковита котлета, сир чеддер, секретний соус Nexus, листя салату та карамелізована цибуля.", "image": "https://images.unsplash.com/photo-1568901346375-23c9450c58cd?q=80&w=500&auto=format&fit=crop", "available": True},
        {"name": "Картопля Фрі Неон", "price": 85.0, "category": "Гарніри", "description": "Хрустка картопля зі спеціями та фірмовим сирним соусом на вибір.", "image": "https://images.unsplash.com/photo-1573080496219-bb080dd4f877?q=80&w=500&auto=format&fit=crop", "available": True},
        {"name": "Піца Маргарита Квант", "price": 195.0, "category": "Піца", "description": "Класичний соус з томатів, багато моцарели, свіжий базилік та оливкова олія.", "image": "https://images.unsplash.com/photo-1604382354936-07c5d9983bd3?q=80&w=500&auto=format&fit=crop", "available": True},
        {"name": "Лимонад Блу Кюрасао", "price": 70.0, "category": "Напої", "description": "Освіжаючий напій з сиропом блю кюрасао, лимоном, м'ятою та льодом.", "image": "https://images.unsplash.com/photo-1513558161293-cdaf765ed2fd?q=80&w=500&auto=format&fit=crop", "available": True}
    ]
    db.menu.insert_many(demo_menu)

# ==============================================================================
# 3. МАРШРУТИ HTTP (FLASK ROUTING LOGIC)
# ==============================================================================
@app.route('/')
@app.route('/<int:table_id>')
def index(table_id=None):
    """Головна сторінка меню для відвідувача закладу"""
    if table_id is not None:
        table = str(table_id)
    else:
        table = request.args.get('table', 'Самовивіз')
    return render_template_string(CUSTOMER_HTML, table_id=table)

@app.route('/admin')
def admin():
    """Екран адмін-панелі (вимагає авторизації в сесії)"""
    if not session.get('admin_logged'):
        return redirect(url_for('login'))
    return render_template_string(ADMIN_HTML)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Вікно входу для адміністратора закладу"""
    error = None
    if request.method == 'POST':
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            session['admin_logged'] = True
            logger.info("Адміністратор успішно авторизувався в системі.")
            return redirect(url_for('admin'))
        else:
            error = "Невірний пароль доступу! Спробуйте ще раз."
            logger.warning("Спроба несанкціонованого входу в адмін-панель з неправильним паролем.")
    return render_template_string(LOGIN_HTML, error=error)

@app.route('/logout')
def logout():
    """Вихід з облікового запису адміністратора"""
    session.pop('admin_logged', None)
    return redirect(url_for('login'))

@app.route('/export_db')
def export_db():
    """Експорт повної копії бази даних у форматі JSON структури"""
    if not session.get('admin_logged'):
        return jsonify({'error': 'Доступ заборонено (Unauthorized)'}), 401
    data = {
        'menu': get_all_menu(),
        'orders': get_all_orders(),
        'reviews': get_all_reviews()
    }
    return jsonify(data)

# ==============================================================================
# 4. ОБРОБНИКИ ПОДІЙ REAL-TIME SOCKET.IO СЕРВЕРА
# ==============================================================================
@socketio.on('connect')
def handle_connect():
    """Обробка підключення нового сокет-клієнта"""
    emit('menu_sync', get_all_menu())
    emit('reviews_sync', get_all_reviews())
    if session.get('admin_logged'):
        join_room('admins')
        emit('orders_sync', get_all_orders())
        emit('reviews_sync', get_all_reviews())
        emit('devices_sync', active_devices)
        emit('analytics_sync', calculate_dashboard_stats())

@socketio.on('join_admin_room')
def handle_join_admin_room():
    """Явне приєднання адмін-вкладки до кімнати розсилки сповіщень"""
    if session.get('admin_logged'):
        join_room('admins')
        emit('orders_sync', get_all_orders())
        emit('reviews_sync', get_all_reviews())
        emit('devices_sync', active_devices)
        emit('analytics_sync', calculate_dashboard_stats())

@socketio.on('toggle_monitoring_tab')
def handle_toggle_monitoring_tab(data):
    """
    Контролює стан перегляду вкладки моніторингу адміністратором.
    Управляє активацією та деактивацією важких функцій html2canvas на клієнтах.
    """
    global active_monitoring_admins
    is_active = data.get('active', False)
    
    if session.get('admin_logged'):
        if is_active:
            active_monitoring_admins += 1
        else:
            active_monitoring_admins = max(0, active_monitoring_admins - 1)
            
        # Якщо хоча б один адмін дивиться екран моніторингу — надсилаємо команду клієнтам увімкнути захоплення
        status_to_broadcast = (active_monitoring_admins > 0)
        socketio.emit('server_stream_control', {'allowed': status_to_broadcast})
        logger.info(f"Стан відеомоніторингу змінено. Активних адмінів: {active_monitoring_admins}. Стрімінг: {status_to_broadcast}")

@socketio.on('client_init')
def handle_client_init(data):
    """Ініціалізація клієнтського девайса з прив'язкою до унікального UUID"""
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
        # Відправляємо клієнту поточний глобальний стан стрімінгу, щоб він знав, чи запускати html2canvas loop
        emit('server_stream_control', {'allowed': (active_monitoring_admins > 0)})

@socketio.on('disconnect')
def handle_disconnect():
    """Видалення пристрою зі списку активного моніторингу при розриві з'єднання"""
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
    """Оновлення живих даних про дії користувача (кошик, скролл, відкриті вікна)"""
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
    """Ретрансляція графічного кадру від клієнта до кімнати адміністраторів"""
    if active_monitoring_admins > 0:
        socketio.emit('receive_frame', {
            'frame': data.get('frame'),
            'uuid': data.get('uuid'),
            'sid': request.sid
        }, room='admins')

@socketio.on('call_waiter_event')
def handle_call_waiter(data):
    """Обробка виклику офіціанта до певного столу"""
    table = data.get('table', 'Самовивіз')
    socketio.emit('waiter_alert', {'table': table, 'time': get_kyiv_time_short()}, room='admins')

@socketio.on('order_create')
def handle_order_create(data):
    """Створення замовлення з прив'язкою до унікального апаратного UUID клієнта"""
    last_order = db.orders.find_one(sort=[('order_number', -1)])
    order_num = 1
    if last_order and 'order_number' in last_order:
        order_num = last_order['order_number'] + 1

    order_data = {
        'order_number': order_num,
        'client_uuid': data.get('uuid', 'unknown_device'),
        'items': data.get('items', []),
        'total_price': float(data.get('total_price', 0)),
        'table': data.get('table', 'Самовивіз'),
        'comment': data.get('comment', ''),
        'status': 'pending',
        'timestamp': get_kyiv_time(),
        'time_str': get_kyiv_time_str()
    }
    
    db.orders.insert_one(order_data)
    
    # Сповіщення адмінів та клієнтів
    socketio.emit('orders_sync', get_all_orders(), room='admins')
    socketio.emit('analytics_sync', calculate_dashboard_stats(), room='admins')
    socketio.emit('new_order_alert', serialize_doc(order_data), room='admins')
    
    # Інформуємо конкретного користувача через глобальну шину або поверненням результату 콜백у
    socketio.emit('order_status_update_client', {
        'order_number': order_num,
        'client_uuid': order_data['client_uuid'],
        'table': order_data['table'],
        'status': 'pending',
        'message': 'Очікує підтвердження адміністратором ⏳'
    })
    
    return {'status': 'success', 'order_number': order_num}

@socketio.on('order_status_update')
def handle_order_status_update(data):
    """Зміна поточного статусу приготування страви (Підтримує Drag-and-Drop зміни)"""
    if session.get('admin_logged'):
        order_id = data.get('id')
        new_status = data.get('status')
        
        order = db.orders.find_one({"_id": ObjectId(order_id)})
        if order:
            db.orders.update_one({"_id": ObjectId(order_id)}, {"$set": {"status": new_status}})
            
            status_messages = {
                'pending': 'Очікує підтвердження ⏳',
                'cooking': 'Готується на кухні закладу 🍳',
                'ready': 'Вже прямує до вашого столу! Смачного! 🍽️',
                'Закрито': 'Оплачено, закрито. Дякуємо за візит!  ❤️'
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
            socketio.emit('analytics_sync', calculate_dashboard_stats(), room='admins')

@socketio.on('order_delete')
def handle_order_delete(data):
    """Видалення картки замовлення з бази даних"""
    if session.get('admin_logged'):
        db.orders.delete_one({"_id": ObjectId(data['id'])})
        socketio.emit('orders_sync', get_all_orders(), room='admins')
        socketio.emit('analytics_sync', calculate_dashboard_stats(), room='admins')

@socketio.on('get_my_orders_data')
def handle_get_my_orders_data(data):
    """Запит повної історії покупок. Шукає безпосередньо по UUID пристрою"""
    uuid = data.get('uuid', '')
    numbers = data.get('numbers', [])
    table = data.get('table', '')
    
    # Шукаємо замовлення, які або містять UUID клієнта, або збігаються з номерами з локальної пам'яті
    query = {
        "$or": [
            {"client_uuid": uuid},
            {"order_number": {"$in": numbers}},
            {"table": table, "status": {"$ne": "Закрито"}}
        ]
    }
    return [serialize_doc(o) for o in db.orders.find(query).sort("timestamp", -1)]

@socketio.on('menu_save')
def handle_menu_save(data):
    """Збереження нової або редагованої позиції в меню кафе"""
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
    """Вилучення страви з асортименту меню"""
    if session.get('admin_logged'):
        db.menu.delete_one({"_id": ObjectId(data['id'])})
        socketio.emit('menu_sync', get_all_menu())

@socketio.on('review_add')
def handle_review_add(data):
    """Додавання текстового відгуку від відвідувача"""
    review_data = {
        'name': data.get('name', 'Анонімний гість'),
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
    """Видалення відгуку модератором"""
    if session.get('admin_logged'):
        db.reviews.delete_one({"_id": ObjectId(data['id'])})
        socketio.emit('reviews_sync', get_all_reviews(), room='admins')
        socketio.emit('analytics_sync', calculate_dashboard_stats(), room='admins')

@socketio.on('admin_clear_db')
def handle_admin_clear_db():
    """Повне занулення колекцій бази даних закладу"""
    if session.get('admin_logged'):
        db.menu.delete_many({})
        db.orders.delete_many({})
        db.reviews.delete_many({})
        handle_admin_init_sync()

@socketio.on('admin_import_db')
def handle_admin_import_db(data):
    """Розгортання бекапу даних з файлу конфігурації JSON"""
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
            
        handle_admin_init_sync()

# ==============================================================================
# 5. ШАБЛОНИ ВЕБ-ІНТЕРФЕЙСІВ КЛІЄНТА ТА АДМІНІСТРАТОРА (HTML/CSS/JS)
# ==============================================================================

CUSTOMER_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Nexus Menu - Замовлення страв</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #09090b; color: #f4f4f5; font-family: system-ui, -apple-system, sans-serif; -webkit-tap-highlight-color: transparent; }
        .hide-scroll::-webkit-scrollbar { display: none; }
        .glass-card { background: linear-gradient(145deg, #18181b 0%, #0f0f11 100%); border: 1px solid #27272a; }
        .glass-card:hover { border-color: #4f46e5; box-shadow: 0 0 20px rgba(79, 70, 229, 0.12); }
        .cyber-glow { box-shadow: 0 0 15px rgba(79, 70, 229, 0.3); }
    </style>
</head>
<body class="pb-32 relative antialiased selection:bg-indigo-500 selection:text-white">

    <div id="toast-box" class="fixed top-6 left-4 right-4 z-[99999] hidden bg-zinc-900/95 border border-indigo-500/40 backdrop-blur-md p-4 rounded-2xl shadow-2xl items-center gap-3 transition-all duration-300">
        <div class="w-8 h-8 rounded-lg bg-indigo-600/20 flex items-center justify-center text-indigo-400">
            <i class="fas fa-bell text-sm"></i>
        </div>
        <p id="toast-text" class="text-xs font-bold text-zinc-200 flex-1"></p>
    </div>

    <header class="fixed top-0 left-0 right-0 bg-zinc-950/80 backdrop-blur-lg border-b border-zinc-800/80 mountaineer z-40 p-4 flex justify-between items-center">
        <div class="flex items-center gap-3">
            <div class="w-11 h-11 rounded-2xl bg-indigo-600 flex items-center justify-center font-black text-white text-sm tracking-wider cyber-glow">
                #{{ table_id }}
            </div>
            <div>
                <div class="text-[10px] text-zinc-500 uppercase tracking-widest font-black">Локація</div>
                <div class="text-xs font-bold text-zinc-200 flex items-center gap-1.5">
                    <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                    Стіл зарезервовано
                </div>
            </div>
        </div>
        <div class="flex gap-2">
            <button onclick="callWaiter()" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-500 border border-amber-500/20 px-3.5 py-2 rounded-xl font-bold text-xs transition-all flex items-center gap-2 active:scale-95 shadow-md shadow-amber-500/5">
                <i class="fas fa-concierge-bell"></i> Виклик офіціанта
            </button>
        </div>
    </header>

    <div id="status-widget" class="hidden mt-24 mx-4 p-4 rounded-2xl bg-indigo-950/30 border border-indigo-500/30 items-center gap-4 animate-pulse">
        <div class="w-10 h-10 rounded-xl bg-indigo-500/20 flex items-center justify-center text-indigo-400 text-lg">
            <i class="fas fa-spinner fa-spin"></i>
        </div>
        <div class="flex-1">
            <div class="text-[9px] uppercase font-black text-indigo-400 tracking-widest">Статус вашої страви</div>
            <div id="status-text" class="font-bold text-xs text-zinc-200 mt-0.5">Замовлення обробляється сервером...</div>
        </div>
    </div>

    <main class="pt-24 px-4">
        <div class="flex flex-col gap-1 mb-4">
            <div class="text-[10px] uppercase font-black tracking-widest text-indigo-400">Цифрове меню</div>
            <div class="flex justify-between items-center">
                <h1 class="text-2xl font-black tracking-tight text-zinc-100">NEXUS <span class="text-indigo-500">CAFE</span></h1>
                <button onclick="openModal('orders-modal')" class="text-xs font-bold text-indigo-400 bg-indigo-500/10 px-3.5 py-2 rounded-xl border border-indigo-500/20 flex items-center gap-2 active:scale-95 transition-all">
                    <i class="fas fa-receipt"></i> Історія чеків
                </button>
            </div>
        </div>

        <div class="mb-5 relative">
            <span class="absolute inset-y-0 left-0 pl-3.5 flex items-center text-zinc-500 text-xs">
                <i class="fas fa-search"></i>
            </span>
            <input type="text" id="search-input" oninput="filterMenu()" placeholder="Пошук улюбленої страви чи напою..." class="w-full bg-zinc-900 border border-zinc-800/80 rounded-xl py-3 pl-10 pr-4 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500 transition-all">
        </div>
        
        <div class="flex space-x-2 overflow-x-auto hide-scroll py-2 mb-4 sticky top-[73px] z-30 bg-[#09090b]/90 backdrop-blur-md -mx-4 px-4 border-b border-zinc-900" id="category-bar"></div>
        
        <div class="grid grid-cols-2 gap-3.5" id="menu-grid"></div>
    </main>

    <div id="float-cart-bar" class="fixed bottom-0 left-0 right-0 p-4 z-40 bg-gradient-to-t from-[#09090b] via-[#09090b]/95 to-transparent hidden">
        <button onclick="openModal('cart-modal')" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white p-4 rounded-2xl shadow-xl shadow-indigo-600/15 flex justify-between items-center border border-indigo-500/30 active:scale-95 transition-all">
            <div class="flex items-center gap-2.5">
                <span id="float-cart-count" class="bg-indigo-800 px-2.5 py-1 rounded-lg font-black text-xs min-w-[24px]">0</span>
                <span class="text-xs font-black uppercase tracking-wider flex items-center gap-2">Переглянути кошик</span>
            </div>
            <span class="text-sm font-black bg-indigo-700/60 px-3 py-1.5 rounded-xl"><span id="float-cart-total">0</span> ₴</span>
        </button>
    </div>

    <div id="detail-modal" class="fixed inset-0 z-[60] bg-black/80 backdrop-blur-sm hidden flex-col justify-end">
        <div class="bg-zinc-950 border-t border-zinc-800 rounded-t-[2.5rem] max-h-[90vh] flex flex-col p-6 overflow-y-auto hide-scroll">
            <div class="w-12 h-1.5 bg-zinc-800 rounded-full mx-auto mb-4" onclick="closeModal('detail-modal')"></div>
            <div id="detail-modal-content" class="space-y-4"></div>
        </div>
    </div>

    <div id="cart-modal" class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm hidden flex-col justify-end">
        <div class="bg-zinc-950 border-t border-zinc-800 rounded-t-[2.5rem] max-h-[85vh] flex flex-col p-6">
            <div class="flex justify-between items-center mb-4">
                <h2 class="text-lg font-black flex items-center gap-2"><i class="fas fa-shopping-basket text-indigo-500"></i> Ваше замовлення</h2>
                <button onclick="closeModal('cart-modal')" class="text-zinc-500 hover:text-zinc-300 font-bold p-2"><i class="fas fa-times"></i></button>
            </div>
            
            <div id="cart-items-list" class="flex-1 overflow-y-auto space-y-3 my-2 pr-1 hide-scroll"></div>
            
            <div class="space-y-3 mt-4 pt-4 border-t border-zinc-800/80">
                <input type="text" id="order-comment" placeholder="Особливі побажання (напр., без цибулі, соус окремо)..." class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-xs text-zinc-200 focus:outline-none focus:border-indigo-500">
                <label class="flex items-center gap-3 cursor-pointer bg-zinc-900 p-3 rounded-xl border border-zinc-800/80">
                    <input type="checkbox" id="order-takeaway" class="rounded bg-zinc-950 border-zinc-700 text-indigo-600 focus:ring-0 w-4 h-4">
                    <span class="text-xs text-zinc-300 font-bold">Замовлення з собою (на виніс)</span>
                </label>
                <div class="flex justify-between items-center py-2">
                    <span class="text-xs font-black text-zinc-400 uppercase tracking-widest">Загальна сума:</span>
                    <span class="text-xl font-black text-indigo-400"><span id="modal-cart-total">0</span> ₴</span>
                </div>
                <button onclick="submitOrder()" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white py-4 rounded-xl font-black uppercase tracking-wider text-xs shadow-lg transition-all flex items-center justify-center gap-2">
                    Надіслати замовлення на кухню
                </button>
            </div>
        </div>
    </div>

    <div id="orders-modal" class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm hidden items-center justify-center p-4">
        <div class="bg-zinc-950 border border-zinc-800 p-6 rounded-2xl w-full max-w-md max-h-[80vh] flex flex-col shadow-2xl">
            <div class="flex justify-between items-center mb-4 border-b border-zinc-800 pb-3">
                <h3 class="text-base font-black flex items-center gap-2"><i class="fas fa-history text-indigo-500"></i> Ваша історія страв</h3>
                <button onclick="closeModal('orders-modal')" class="text-zinc-500 hover:text-zinc-300 font-bold"><i class="fas fa-times"></i></button>
            </div>
            <div id="my-orders-list" class="flex-1 overflow-y-auto space-y-3 hide-scroll pb-4"></div>
            <button onclick="openReviewModal()" class="w-full mt-2 bg-amber-500/10 text-amber-500 border border-amber-500/20 py-3 rounded-xl font-bold text-xs transition-all flex items-center justify-center gap-2 active:scale-95">
                <i class="fas fa-star"></i> Залишити відгук про візит
            </button>
        </div>
    </div>

    <div id="review-modal" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-md hidden items-center justify-center p-4">
        <div class="bg-zinc-950 border border-zinc-800 p-6 rounded-2xl w-full max-w-sm shadow-2xl">
            <h3 class="text-base font-black text-center mb-1">Поділіться враженнями</h3>
            <p class="text-center text-[11px] text-zinc-500 mb-4">Ваша думка робить нас кращими</p>
            <div id="stars-container" class="flex justify-center gap-2.5 mb-4 text-3xl"></div>
            <textarea id="review-comment" placeholder="Напишіть що вам сподобалось або над чим нам варто попрацювати..." rows="3" class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-xs text-zinc-200 focus:outline-none focus:border-indigo-500 resize-none placeholder:text-zinc-600"></textarea>
            <div class="flex gap-3 mt-4">
                <button onclick="closeModal('review-modal')" class="flex-1 bg-zinc-900 border border-zinc-800 text-zinc-400 p-3 rounded-xl text-xs font-bold active:scale-95 transition-all">Скасувати</button>
                <button onclick="submitReview()" class="flex-1 bg-indigo-600 text-white p-3 rounded-xl text-xs font-bold shadow-lg active:scale-95 transition-all">Надіслати</button>
            </div>
        </div>
    </div>

    <div id="nexus-global-modal" class="fixed inset-0 z-[999999] bg-black/80 backdrop-blur-sm hidden items-center justify-center p-4">
        <div class="bg-zinc-950 border border-zinc-800 p-6 rounded-2xl w-full max-w-sm shadow-2xl space-y-4">
            <h3 id="nexus-modal-title" class="text-xs font-black uppercase tracking-widest text-indigo-400">Система</h3>
            <p id="nexus-modal-text" class="text-xs text-zinc-300 font-medium leading-relaxed"></p>
            <div class="flex gap-3 pt-2">
                <button id="nexus-btn-cancel" class="hidden flex-1 bg-zinc-900 border border-zinc-800 hover:bg-zinc-800 text-zinc-400 p-3 rounded-xl text-xs font-bold transition-all">Скасувати</button>
                <button id="nexus-btn-confirm" class="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white p-3 rounded-xl text-xs font-bold shadow-lg transition-all">Зрозуміло</button>
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
            modal.classList.remove('hidden');
            modal.classList.add('flex');
            modalCallback = function() { modal.classList.add('hidden'); };
        }

        const socket = io();
        const tableId = "{{ table_id }}";
        let menuItems = [], currentCategory = 'Всі', selectedRating = 5;
        let activeModal = 'none';
        let isStreamAllowedByAdmin = false;

        // Глибока інтеграція UUID пристрою: генерація та збереження у LocalStorage
        let clientUUID = localStorage.getItem('nexus_device_uuid');
        if (!clientUUID) {
            clientUUID = 'device_' + Math.random().toString(36).substr(2, 9) + '_' + Date.now().toString(36);
            localStorage.setItem('nexus_device_uuid', clientUUID);
        }

        // Завантаження збереженого кошика з прив'язкою до UUID пристрою та столу
        let cart = JSON.parse(localStorage.getItem(`nexus_cart_${tableId}_${clientUUID}`) || '{}');

        socket.on('connect', () => {
            socket.emit('client_init', { uuid: clientUUID, table: tableId, user_agent: navigator.userAgent });
            sendLiveTelemetry();
        });

        socket.on('menu_sync', (data) => {
            menuItems = data; 
            renderCategories(); 
            renderMenu(); 
            updateCartUI();
        });

        // Слухач сервера для управління трансляцією екрана (захист від навантаження)
        socket.on('server_stream_control', (data) => {
            isStreamAllowedByAdmin = !!data.allowed;
            console.log("Дозвіл на захоплення екрана від адміна:", isStreamAllowedByAdmin);
        });

        socket.on('order_status_update_client', (data) => {
            let myOrdersNums = JSON.parse(localStorage.getItem(`my_orders_${clientUUID}`) || '[]');
            if (data.client_uuid === clientUUID || myOrdersNums.includes(data.order_number) || data.table === tableId) {
                const widget = document.getElementById('status-widget');
                if(data.status === 'Закрито') {
                    widget.classList.add('hidden');
                    showToast("Ваш чек оплачено. Дякуємо за візит!");
                } else {
                    widget.classList.remove('hidden');
                    widget.classList.add('flex');
                    document.getElementById('status-text').innerText = data.message;
                    showToast(`Оновлено статус замовлення #${data.order_number}: ${data.message}`);
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

        // Оптимізована логіка стрімінгу: html2canvas виконується ТІЛЬКИ тоді, коли адмін дивиться вкладку
        setInterval(() => {
            if (!isStreamAllowedByAdmin) return; // Повністю блокуємо виконання, якщо вкладка закрита!
            
            html2canvas(document.body, {
                scale: 0.3,
                useCORS: true,
                logging: false
            }).then(canvas => {
                const frameData = canvas.toDataURL('image/jpeg', 0.35);
                socket.emit('stream_frame', { uuid: clientUUID, frame: frameData });
            }).catch(e => {});
        }, 3000);

        function renderCategories() {
            const bar = document.getElementById('category-bar');
            const cats = ['Всі', ...new Set(menuItems.map(i => i.category))];
            bar.innerHTML = cats.map(cat => {
                const active = currentCategory === cat;
                return `<button onclick="setCategory('${cat}')" class="px-4 py-2.5 rounded-xl whitespace-nowrap font-bold text-xs transition-all ${active ? 'bg-indigo-600 text-white shadow-lg border border-indigo-500' : 'bg-zinc-900 text-zinc-400 border border-zinc-800/60'}">${cat}</button>`;
            }).join('');
        }

        function setCategory(cat) { 
            currentCategory = cat; 
            renderCategories(); 
            renderMenu(); 
            sendLiveTelemetry(); 
        }

        function filterMenu() {
            renderMenu();
        }

        function renderMenu() {
            const grid = document.getElementById('menu-grid');
            const searchVal = document.getElementById('search-input').value.toLowerCase().trim();
            
            let filtered = currentCategory === 'Всі' ? menuItems : menuItems.filter(i => i.category === currentCategory);
            
            if (searchVal) {
                filtered = filtered.filter(i => i.name.toLowerCase().includes(searchVal) || (i.description && i.description.toLowerCase().includes(searchVal)));
            }
            
            if(filtered.length === 0) { 
                grid.innerHTML = `<div class="col-span-2 text-center text-zinc-500 py-12 text-xs font-bold">Нічого не знайдено за запитом</div>`; 
                return; 
            }

            grid.innerHTML = filtered.map(item => {
                const avail = item.available !== false;
                const img = item.image ? `<img src="${item.image}" class="w-full h-28 object-cover rounded-xl mb-2 border border-zinc-800/30" onclick="openDetailModal('${item._id}')" />` : `<div class="w-full h-28 bg-zinc-900 flex items-center justify-center text-2xl rounded-xl mb-2 border border-zinc-800/80" onclick="openDetailModal('${item._id}')">🍽️</div>`;
                return `
                    <div class="glass-card rounded-2xl p-2.5 flex flex-col justify-between ${!avail ? 'opacity-35 grayscale select-none' : ''}">
                        <div class="cursor-pointer" onclick="openDetailModal('${item._id}')">
                            ${img}
                            <h3 class="font-bold text-xs text-zinc-200 line-clamp-1">${item.name}</h3>
                            <p class="text-[10px] text-zinc-500 line-clamp-2 mt-0.5">${item.description || 'Класичний рецепт від шефа.'}</p>
                        </div>
                        <div class="mt-2.5 flex items-center justify-between border-t border-zinc-800/60 pt-2">
                            <span class="text-xs font-black text-indigo-400">${item.price} ₴</span>
                            ${avail ? `<button onclick="addToCart('${item._id}')" class="bg-indigo-600 hover:bg-indigo-500 w-7 h-7 rounded-lg font-black text-white flex items-center justify-center active:scale-90 shadow transition-all"><i class="fas fa-plus text-[10px]"></i></button>` : `<span class="text-[8px] bg-zinc-900 text-zinc-500 px-1.5 py-0.5 rounded font-bold uppercase">Вул.</span>`}
                        </div>
                    </div>`;
            }).join('');
        }

        function openDetailModal(id) {
            const item = menuItems.find(m => m._id === id);
            if (!item) return;
            const container = document.getElementById('detail-modal-content');
            const img = item.image ? `<img src="${item.image}" class="w-full h-56 object-cover rounded-2xl shadow-xl border border-zinc-800" />` : `<div class="w-full h-48 bg-zinc-900 flex items-center justify-center text-4xl rounded-2xl border border-zinc-800">🍽️</div>`;
            
            container.innerHTML = `
                ${img}
                <div>
                    <span class="text-[9px] font-black uppercase bg-indigo-600/20 text-indigo-400 px-2 py-1 rounded-md border border-indigo-500/20">${item.category}</span>
                    <h3 class="text-lg font-black text-zinc-100 mt-2">${item.name}</h3>
                    <p class="text-xs text-zinc-400 leading-relaxed mt-1.5">${item.description || 'Детальний опис страви відсутній.'}</p>
                </div>
                <div class="flex items-center justify-between pt-4 border-t border-zinc-900">
                    <div>
                        <div class="text-[9px] uppercase font-black text-zinc-500">Вартість</div>
                        <div class="text-xl font-black text-indigo-400">${item.price} ₴</div>
                    </div>
                    ${item.available !== false ? `<button onclick="addToCart('${item._id}'); closeModal('detail-modal'); showToast('Додано в кошик!')" class="bg-indigo-600 hover:bg-indigo-500 px-5 py-3 rounded-xl font-bold text-xs text-white shadow-lg active:scale-95 transition-all flex items-center gap-2"><i class="fas fa-shopping-bag"></i> Замовити страву</button>` : `<span class="text-xs font-bold text-zinc-500 bg-zinc-900 px-3 py-2 rounded-xl">Тимчасово відсутня</span>`}
                </div>
            `;
            openModal('detail-modal');
        }

        function addToCart(id) { 
            cart[id] = (cart[id] || 0) + 1; 
            updateCartUI(); 
            sendLiveTelemetry(); 
        }
        
        function changeQty(id, delta) { 
            if(!cart[id]) return; 
            cart[id] += delta; 
            if(cart[id] <= 0) delete cart[id]; 
            updateCartUI(); 
            sendLiveTelemetry(); 
        }

        function updateCartUI() {
            let totalCount = 0, totalPrice = 0;
            const list = document.getElementById('cart-items-list');
            let html = '';
            
            Object.keys(cart).forEach(id => {
                const item = menuItems.find(m => m._id === id);
                if(item) {
                    totalCount += cart[id]; 
                    totalPrice += item.price * cart[id];
                    html += `
                        <div class="flex items-center justify-between bg-zinc-900/50 p-3 rounded-xl border border-zinc-800/80 shadow-inner">
                            <div class="flex-1 min-w-0 pr-2">
                                <h4 class="font-bold text-xs text-zinc-200 truncate">${item.name}</h4>
                                <p class="text-[11px] text-indigo-400 font-bold mt-0.5">${item.price} ₴</p>
                            </div>
                            <div class="flex items-center gap-3 bg-zinc-950 px-2.5 py-1 rounded-xl border border-zinc-800">
                                <button onclick="changeQty('${id}', -1)" class="text-zinc-500 hover:text-white font-black px-1 active:scale-75 transition-all"><i class="fas fa-minus text-[10px]"></i></button>
                                <span class="text-xs font-bold text-zinc-200 min-w-[14px] text-center">${cart[id]}</span>
                                <button onclick="changeQty('${id}', 1)" class="text-zinc-500 hover:text-white font-black px-1 active:scale-75 transition-all"><i class="fas fa-plus text-[10px]"></i></button>
                            </div>
                        </div>`;
                }
            });
            
            // Запис стану кошика в локальне сховище для стійкості при перезавантаженні сторінки
            localStorage.setItem(`nexus_cart_${tableId}_${clientUUID}`, JSON.stringify(cart));
            
            list.innerHTML = html || `<div class="text-center text-zinc-600 py-10 text-xs font-medium">Кошик порожній. Оберіть щось смачненьке вище!</div>`;
            const floatBar = document.getElementById('float-cart-bar');
            if(totalCount > 0) {
                floatBar.classList.remove('hidden');
                document.getElementById('float-cart-count').innerText = totalCount;
                document.getElementById('float-cart-total').innerText = totalPrice;
                document.getElementById('modal-cart-total').innerText = totalPrice;
            } else { 
                floatBar.classList.add('hidden'); 
            }
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
            let total = 0; 
            itemsList.forEach(i => total += i.price * i.qty);

            socket.emit('order_create', {
                uuid: clientUUID, // Передаємо унікальний UUID клієнта
                items: itemsList, total_price: total,
                table: takeaway ? 'На виніс' : tableId, comment: comment
            }, (res) => {
                if(res && res.status === 'success') {
                    showAlert(`Замовлення #${res.order_number} успішно надіслано на кухню! Слідкуйте за його статусом.`, "Успіх");
                    cart = {}; 
                    document.getElementById('order-comment').value = '';
                    document.getElementById('order-takeaway').checked = false;
                    updateCartUI(); 
                    closeModal('cart-modal');
                    
                    let myOrdersNums = JSON.parse(localStorage.getItem(`my_orders_${clientUUID}`) || '[]');
                    myOrdersNums.push(res.order_number);
                    localStorage.setItem(`my_orders_${clientUUID}`, JSON.stringify(myOrdersNums));
                }
            });
        }

        function loadMyOrders() {
            const list = document.getElementById('my-orders-list');
            let myOrdersNums = JSON.parse(localStorage.getItem(`my_orders_${clientUUID}`) || '[]');
            
            socket.emit('get_my_orders_data', { uuid: clientUUID, numbers: myOrdersNums, table: tableId }, (orders) => {
                if(!orders || orders.length === 0) { 
                    list.innerHTML = `<div class="text-center text-zinc-600 py-8 text-xs font-bold">Історія замовлень порожня</div>`; 
                    return; 
                }
                list.innerHTML = orders.map(o => {
                    let statusColor = 'text-amber-500 bg-amber-500/5 border-amber-500/20'; 
                    let statusTxt = 'Нове замовлення';
                    if(o.status === 'cooking') { statusColor = 'text-indigo-400 bg-indigo-500/5 border-indigo-500/20'; statusTxt = 'Готується на кухні'; }
                    if(o.status === 'ready') { statusColor = 'text-emerald-400 bg-emerald-500/5 border-emerald-500/20'; statusTxt = 'Готово до видачі'; }
                    if(o.status === 'Закрито') { statusColor = 'text-zinc-500 bg-zinc-900 border-zinc-800'; statusTxt = 'Оплачено'; }
                    
                    const itemsStr = o.items.map(i => `<div class="flex justify-between text-[11px] text-zinc-400"><span>${i.name} x${i.qty}</span><span>${i.price * i.qty} ₴</span></div>`).join('');
                    return `
                        <div class="bg-zinc-900/60 border border-zinc-800 p-4 rounded-xl space-y-2">
                            <div class="flex justify-between items-center border-b border-zinc-800/60 pb-1.5">
                                <span class="font-black text-xs text-zinc-300">Чек #${o.order_number}</span>
                                <span class="text-[10px] font-bold px-2 py-0.5 rounded-md border ${statusColor}">${statusTxt}</span>
                            </div>
                            <div class="space-y-1">${itemsStr}</div>
                            <div class="flex justify-between items-center pt-2 border-t border-zinc-800 mt-2">
                                <span class="text-[9px] text-zinc-500 font-medium">${o.time_str}</span>
                                <span class="font-black text-xs text-indigo-400">${o.total_price} ₴</span>
                            </div>
                        </div>`;
                }).join('');
            });
        }

        function callWaiter() { 
            socket.emit('call_waiter_event', { table: tableId }); 
            showToast("🔔 Сигнал офіціанту надіслано. Зачекайте хвилинку."); 
        }
        
        function openReviewModal() { 
            closeModal('orders-modal'); 
            openModal('review-modal'); 
            renderStars(); 
        }
        
        function renderStars() {
            const container = document.getElementById('stars-container'); 
            let html = '';
            for(let i=1; i<=5; i++) {
                html += `<i onclick="setRating(${i})" class="${i <= selectedRating ? 'fas' : 'far'} fa-star text-amber-500 cursor-pointer transition-transform active:scale-125"></i>`;
            }
            container.innerHTML = html;
        }
        
        function setRating(r) { selectedRating = r; renderStars(); }
        
        function submitReview() {
            const comment = document.getElementById('review-comment').value;
            socket.emit('review_add', { name: `Гість (Стіл #${tableId})`, text: comment, rating: selectedRating });
            document.getElementById('review-comment').value = ''; 
            closeModal('review-modal'); 
            showToast("❤️ Дякуємо за ваш щирий відгук!");
        }

        function openModal(id) { 
            document.getElementById(id).classList.remove('hidden'); 
            if(id==='cart-modal' || id==='detail-modal') document.getElementById(id).classList.add('flex'); 
            if(id==='orders-modal') loadMyOrders(); 
            activeModal = id; 
            sendLiveTelemetry(); 
        }
        
        function closeModal(id) { 
            document.getElementById(id).classList.add('hidden'); 
            if(id==='cart-modal' || id==='detail-modal') document.getElementById(id).classList.remove('flex'); 
            activeModal = 'none'; 
            sendLiveTelemetry(); 
        }
        
        function showToast(msg) { 
            const box = document.getElementById('toast-box'); 
            document.getElementById('toast-text').innerText = msg; 
            box.classList.remove('hidden'); 
            box.classList.add('flex'); 
            setTimeout(() => { box.classList.add('hidden'); }, 3500); 
        }

        document.getElementById('nexus-btn-confirm').addEventListener('click', () => { if (modalCallback) modalCallback(); });
    </script>
</body>
</html>
"""

ADMIN_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <title>Панель Керування Nexus Cafe Pro</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #09090b; color: #f4f4f5; font-family: system-ui, sans-serif; }
        .admin-card { background: linear-gradient(145deg, #18181b 0%, #0f0f11 100%); border: 1px solid #27272a; }
        .drag-hover { border-color: #4f46e5 !important; background: rgba(79, 70, 229, 0.05) !important; }
        .mutual-dropzone { min-h: [450px]; transition: all 0.2s ease; }
    </style>
</head>
<body class="p-6 antialiased">

    <header class="mb-6 flex justify-between items-center border-b border-zinc-800 pb-4">
        <div>
            <h1 class="text-2xl font-black text-indigo-500 tracking-tight">NEXUS CAFE PRO <span class="text-zinc-500 text-sm font-bold">| Панель Автоматизації</span></h1>
            <p class="text-xs text-zinc-500 mt-0.5">Інтерактивна черга замовлень з Drag-and-Drop та живий телеметричний контроль</p>
        </div>
        <div class="flex gap-3 items-center">
            <button onclick="exportDatabase()" class="bg-zinc-900 border border-zinc-800 text-xs px-3.5 py-2 rounded-xl hover:bg-zinc-800 font-bold transition-all"><i class="fas fa-download mr-1.5 text-indigo-400"></i> Експорт</button>
            <label class="bg-zinc-900 border border-zinc-800 text-xs px-3.5 py-2 rounded-xl hover:bg-zinc-800 font-bold cursor-pointer transition-all"><i class="fas fa-upload mr-1.5 text-indigo-400"></i> Імпорт JSON <input type="file" id="import-file" onchange="importDatabase()" class="hidden"></label>
            <button onclick="clearDatabase()" class="bg-red-950/30 border border-red-800/40 text-red-400 text-xs px-3.5 py-2 rounded-xl hover:bg-red-900/30 font-bold transition-all">Скинути БД</button>
            <a href="/logout" class="bg-zinc-800 hover:bg-zinc-700 text-xs px-4 py-2 rounded-xl font-bold transition-all">Вихід</a>
        </div>
    </header>

    <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <div class="admin-card rounded-2xl p-4 flex justify-between items-center">
            <div>
                <div class="text-[10px] text-zinc-500 uppercase tracking-widest font-black">Каса (Виручка)</div>
                <div id="stat-revenue" class="text-xl font-black text-emerald-400 mt-1">0 ₴</div>
            </div>
            <div class="text-2xl text-emerald-500 bg-emerald-500/10 w-11 h-11 rounded-xl flex items-center justify-center"><i class="fas fa-wallet"></i></div>
        </div>
        <div class="admin-card rounded-2xl p-4 flex justify-between items-center">
            <div>
                <div class="text-[10px] text-zinc-500 uppercase tracking-widest font-black">Активні чеки</div>
                <div id="stat-active" class="text-xl font-black text-indigo-400 mt-1">0 шт</div>
            </div>
            <div class="text-2xl text-indigo-500 bg-indigo-500/10 w-11 h-11 rounded-xl flex items-center justify-center"><i class="fas fa-utensils"></i></div>
        </div>
        <div class="admin-card rounded-2xl p-4 flex justify-between items-center">
            <div>
                <div class="text-[10px] text-zinc-500 uppercase tracking-widest font-black">Рейтинг закладу</div>
                <div id="stat-rating" class="text-xl font-black text-amber-400 mt-1">5.0 / 5</div>
            </div>
            <div class="text-2xl text-amber-500 bg-amber-500/10 w-11 h-11 rounded-xl flex items-center justify-center"><i class="fas fa-star"></i></div>
        </div>
        <div class="admin-card rounded-2xl p-4 flex justify-between items-center">
            <div>
                <div class="text-[10px] text-zinc-500 uppercase tracking-widest font-black">Столи Online</div>
                <div id="stat-online" class="text-xl font-black text-zinc-200 mt-1">0 девайсів</div>
            </div>
            <div class="text-2xl text-zinc-400 bg-zinc-700/20 w-11 h-11 rounded-xl flex items-center justify-center"><i class="fas fa-wifi"></i></div>
        </div>
    </div>

    <div class="flex flex-wrap gap-2 border-b border-zinc-800 pb-4 mb-6">
        <button onclick="switchTab('orders')" id="btn-tab-orders" class="tab-btn px-5 py-3 rounded-xl text-xs font-bold transition-all bg-indigo-600 text-white shadow-lg border border-indigo-500">
            <i class="fas fa-folder-open mr-2"></i>Черга замовлень (Drag-and-Drop)
        </button>
        <button onclick="switchTab('monitoring')" id="btn-tab-monitoring" class="tab-btn px-5 py-3 rounded-xl text-xs font-bold transition-all bg-zinc-900 text-zinc-400 border border-zinc-800 hover:border-zinc-700">
            <i class="fas fa-desktop mr-2"></i>Живий моніторинг столів
        </button>
        <button onclick="switchTab('menu')" id="btn-tab-menu" class="tab-btn px-5 py-3 rounded-xl text-xs font-bold transition-all bg-zinc-900 text-zinc-400 border border-zinc-800 hover:border-zinc-700">
            <i class="fas fa-hamburger mr-2"></i>Конструктор страв
        </button>
        <button onclick="switchTab('reviews')" id="btn-tab-reviews" class="tab-btn px-5 py-3 rounded-xl text-xs font-bold transition-all bg-zinc-900 text-zinc-400 border border-zinc-800 hover:border-zinc-700">
            <i class="fas fa-comment-alt mr-2"></i>Відгуки відвідувачів
        </button>
    </div>

    <div id="tab-orders" class="tab-content space-y-4">
        <div class="grid grid-cols-1 md:grid-cols-3 gap-5" id="orders-kanban-board">
            
            <div class="admin-card rounded-2xl p-4 flex flex-col bg-zinc-950/30" ondragover="allowDrop(event)" ondragenter="highlightDropzone(this)" ondragleave="unhighlightDropzone(this)" ondrop="handleCardDrop(event, 'pending')">
                <div class="flex justify-between items-center mb-3 border-b border-zinc-800 pb-2">
                    <h4 class="text-xs font-black uppercase tracking-wider text-amber-500 flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-amber-500"></span> Нові замовлення
                    </h4>
                    <span id="counter-pending" class="text-[10px] bg-amber-500/10 text-amber-500 font-black px-2 py-0.5 rounded-md">0</span>
                </div>
                <div id="queue-pending" class="space-y-3 flex-1 mutual-dropzone min-h-[450px]"></div>
            </div>

            <div class="admin-card rounded-2xl p-4 flex flex-col bg-zinc-950/30" ondragover="allowDrop(event)" ondragenter="highlightDropzone(this)" ondragleave="unhighlightDropzone(this)" ondrop="handleCardDrop(event, 'cooking')">
                <div class="flex justify-between items-center mb-3 border-b border-zinc-800 pb-2">
                    <h4 class="text-xs font-black uppercase tracking-wider text-indigo-400 flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-indigo-500"></span> Готуються на кухні
                    </h4>
                    <span id="counter-cooking" class="text-[10px] bg-indigo-400/10 text-indigo-400 font-black px-2 py-0.5 rounded-md">0</span>
                </div>
                <div id="queue-cooking" class="space-y-3 flex-1 mutual-dropzone min-h-[450px]"></div>
            </div>

            <div class="admin-card rounded-2xl p-4 flex flex-col bg-zinc-950/30" ondragover="allowDrop(event)" ondragenter="highlightDropzone(this)" ondragleave="unhighlightDropzone(this)" ondrop="handleCardDrop(event, 'ready')">
                <div class="flex justify-between items-center mb-3 border-b border-zinc-800 pb-2">
                    <h4 class="text-xs font-black uppercase tracking-wider text-emerald-400 flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-emerald-500"></span> Готові до видачі
                    </h4>
                    <span id="counter-ready" class="text-[10px] bg-emerald-400/10 text-emerald-400 font-black px-2 py-0.5 rounded-md">0</span>
                </div>
                <div id="queue-ready" class="space-y-3 flex-1 mutual-dropzone min-h-[450px]"></div>
            </div>
            
        </div>
    </div>

    <div id="tab-monitoring" class="tab-content space-y-4 hidden">
        <div class="flex justify-between items-center border-b border-zinc-800 pb-2">
            <h2 class="text-xs uppercase tracking-widest font-black text-zinc-400"><i class="fas fa-desktop text-indigo-400 mr-2"></i> Активні екрани відвідувачів у реальному часі</h2>
            <span class="text-[10px] text-zinc-500 font-bold">Оновлення кожні 3 сек (Запит працює лише у цій вкладці)</span>
        </div>
        <div id="devices-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
            <p class="text-zinc-600 text-xs font-medium py-4">Немає активних підключень пристроїв...</p>
        </div>
    </div>

    <div id="tab-menu" class="tab-content grid grid-cols-1 lg:grid-cols-3 gap-6 hidden">
        <div class="admin-card rounded-2xl p-5 h-fit">
            <h3 class="text-xs font-black uppercase tracking-widest mb-4 text-zinc-400 border-b border-zinc-800 pb-2">Картка страви</h3>
            <form id="menu-form" onsubmit="saveMenuItem(event)" class="space-y-3.5 text-xs">
                <input type="hidden" id="menu-id">
                <div>
                    <label class="block text-zinc-500 font-bold mb-1">Назва страви</label>
                    <input type="text" id="menu-name" required class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-zinc-200 focus:outline-none focus:border-indigo-500">
                </div>
                <div>
                    <label class="block text-zinc-500 font-bold mb-1">Категорія (Група)</label>
                    <input type="text" id="menu-category" required placeholder="Напр: Бургери, Напої, Десерти" class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-zinc-200 focus:outline-none focus:border-indigo-500">
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-zinc-500 font-bold mb-1">Ціна (₴)</label>
                        <input type="number" step="0.01" id="menu-price" required class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-zinc-200 focus:outline-none focus:border-indigo-500">
                    </div>
                    <div class="flex items-end pb-3 pl-2">
                        <label class="flex items-center gap-2 cursor-pointer select-none">
                            <input type="checkbox" id="menu-available" checked class="rounded bg-zinc-950 border-zinc-700 text-indigo-600 focus:ring-0 w-4 h-4">
                            <span class="text-zinc-300 font-bold">Є в наявності</span>
                        </label>
                    </div>
                </div>
                <div>
                    <label class="block text-zinc-500 font-bold mb-1">Опис складу / інгредієнтів</label>
                    <textarea id="menu-description" rows="3" class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-zinc-200 focus:outline-none focus:border-indigo-500 resize-none"></textarea>
                </div>
                <div>
                    <label class="block text-zinc-500 font-bold mb-1">Посилання на зображення (URL фото)</label>
                    <input type="text" id="menu-image" placeholder="https://images.unsplash.com/..." class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-zinc-200 focus:outline-none focus:border-indigo-500">
                </div>
                <div class="flex gap-2 pt-3 border-t border-zinc-800/60">
                    <button type="button" onclick="resetMenuForm()" class="flex-1 bg-zinc-900 border border-zinc-800 py-3 rounded-xl text-zinc-400 font-bold hover:bg-zinc-800 transition-all">Очистити</button>
                    <button type="submit" class="flex-1 bg-indigo-600 hover:bg-indigo-500 py-3 rounded-xl text-white font-bold shadow-lg shadow-indigo-600/10 transition-all">Зберегти страву</button>
                </div>
            </form>
        </div>

        <div class="admin-card rounded-2xl p-5 lg:col-span-2 flex flex-col">
            <h3 class="text-xs font-black uppercase tracking-widest mb-4 text-zinc-400 border-b border-zinc-800 pb-2">Поточний асортимент закладу</h3>
            <div class="overflow-y-auto max-h-[600px] text-xs space-y-2.5 pr-1 hide-scroll" id="admin-menu-list"></div>
        </div>
    </div>

    <div id="tab-reviews" class="tab-content admin-card rounded-2xl p-5 hidden">
        <h3 class="text-xs font-black uppercase tracking-widest mb-4 text-zinc-400 border-b border-zinc-800 pb-2"><i class="fas fa-star text-amber-500 mr-1.5"></i> Відгуки та зірки гостей кафе</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4" id="admin-reviews-list"></div>
    </div>

    <div id="nexus-global-modal" class="fixed inset-0 z-[99999] bg-black/80 backdrop-blur-sm hidden items-center justify-center p-4">
        <div class="bg-zinc-950 border border-zinc-800 p-6 rounded-2xl w-full max-w-sm shadow-2xl space-y-4">
            <h3 id="nexus-modal-title" class="text-xs font-black uppercase tracking-widest text-indigo-400">Системне повідомлення</h3>
            <p id="nexus-modal-text" class="text-xs text-zinc-300 font-medium leading-relaxed"></p>
            <div class="flex gap-3 pt-2">
                <button id="nexus-btn-cancel" class="hidden flex-1 bg-zinc-900 border border-zinc-800 hover:bg-zinc-800 text-zinc-400 p-3 rounded-xl text-xs font-bold transition-all">Скасувати</button>
                <button id="nexus-btn-confirm" class="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white p-3 rounded-xl text-xs font-bold shadow-lg transition-all">Виконати</button>
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
            modal.classList.remove('hidden');
            modal.classList.add('flex');
            modalCallback = function() { modal.classList.add('hidden'); };
        }

        function showConfirm(message, onConfirm, title = "Підтвердження дії") {
            const modal = document.getElementById('nexus-global-modal');
            document.getElementById('nexus-modal-title').innerText = title;
            document.getElementById('nexus-modal-text').innerText = message;
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
        let currentDevices = {}, currentActiveTab = 'orders';

        socket.on('connect', () => { 
            socket.emit('join_admin_room'); 
        });

        // КЕРУВАННЯ ВКЛАДКАМИ ТА ОПТИМІЗАЦІЄЮ СТРІМІНГУ
        function switchTab(tabName) {
            currentActiveTab = tabName;
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.getElementById(`tab-${tabName}`).classList.remove('hidden');
            
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.classList.remove('bg-indigo-600', 'text-white', 'shadow-lg', 'border-indigo-500');
                btn.classList.add('bg-zinc-900', 'text-zinc-400', 'border-zinc-800');
            });
            
            const activeBtn = document.getElementById(`btn-tab-${tabName}`);
            activeBtn.classList.remove('bg-zinc-900', 'text-zinc-400', 'border-zinc-800');
            activeBtn.classList.add('bg-indigo-600', 'text-white', 'shadow-lg', 'border-indigo-500');
            
            // Якщо адмін перейшов у вкладку моніторингу — активуємо стрімінг, інакше вимикаємо для економії системних ресурсів
            if (tabName === 'monitoring') {
                socket.emit('toggle_monitoring_tab', { active: true });
            } else {
                socket.emit('toggle_monitoring_tab', { active: false });
            }
        }

        // Обробка синхронізації верхніх аналітичних метрик
        socket.on('analytics_sync', (stats) => {
            document.getElementById('stat-revenue').innerText = `${stats.total_revenue} ₴`;
            document.getElementById('stat-active').innerText = `${stats.active_orders} шт`;
            document.getElementById('stat-rating').innerText = `${stats.avg_rating} / 5`;
            document.getElementById('stat-online').innerText = `${stats.devices_online} девайсів`;
        });

        socket.on('devices_sync', (devices) => {
            currentDevices = devices;
            if(currentActiveTab === 'monitoring') renderDevices();
        });

        socket.on('receive_frame', (data) => {
            if (currentActiveTab !== 'monitoring') return;
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
            showAlert(`Надійшло нове замовлення #${order.order_number}! Стіл: ${order.table}. До сплати: ${order.total_price} ₴`, "Нове замовлення");
        });

        socket.on('waiter_alert', (data) => {
            playAlertSound();
            showAlert(`🔔 Офіціанта викликають на Стіл #${data.table} (Час відмітки: ${data.time})`, "Виклик офіціанта!");
        });

        // ==============================================================================
        // РЕАЛІЗАЦІЯ DRAG AND DROP АРХІТЕКТУРИ ДЛЯ КАНБАН-ДОШКИ ЗАМОВЛЕНЬ
        // ==============================================================================
        function allowDrop(ev) {
            ev.preventDefault();
        }

        function drag(ev, id) {
            ev.dataTransfer.setData("text/order-id", id);
        }

        function highlightDropzone(el) {
            el.classList.add('drag-hover');
        }

        function unhighlightDropzone(el) {
            el.classList.remove('drag-hover');
        }

        function handleCardDrop(ev, targetStatus) {
            ev.preventDefault();
            unhighlightDropzone(ev.currentTarget);
            const orderId = ev.dataTransfer.getData("text/order-id");
            if(orderId) {
                updateOrderStatus(orderId, targetStatus);
            }
        }

        socket.on('orders_sync', (orders) => {
            const pendingBox = document.getElementById('queue-pending');
            const cookingBox = document.getElementById('queue-cooking');
            const readyBox = document.getElementById('queue-ready');
            
            pendingBox.innerHTML = ''; 
            cookingBox.innerHTML = ''; 
            readyBox.innerHTML = '';

            let counts = { pending: 0, cooking: 0, ready: 0 };

            orders.forEach(o => {
                if (o.status === 'Закрито') return;
                counts[o.status]++;
                
                const itemsHtml = o.items.map(i => `<div class="font-medium text-zinc-300 text-[11px]">${i.name} <span class="text-indigo-400 font-black">x${i.qty}</span></div>`).join('');
                const commentHtml = o.comment ? `<div class="text-[10px] text-amber-500 bg-amber-500/10 p-2 rounded-lg mt-1 border border-amber-500/10">💡 ${o.comment}</div>` : '';
                
                let actionBtn = '';
                if(o.status === 'pending') actionBtn = `<button onclick="updateOrderStatus('${o._id}', 'cooking')" class="w-full bg-amber-500 hover:bg-amber-400 text-zinc-950 font-black p-2 rounded-lg mt-2 text-[10px] uppercase tracking-wider transition-all">Прийняти в роботу</button>`;
                if(o.status === 'cooking') actionBtn = `<button onclick="updateOrderStatus('${o._id}', 'ready')" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-black p-2 rounded-lg mt-2 text-[10px] uppercase tracking-wider transition-all">Позначити готовим</button>`;
                if(o.status === 'ready') actionBtn = `<button onclick="updateOrderStatus('${o._id}', 'Закрито')" class="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-black p-2 rounded-lg mt-2 text-[10px] uppercase tracking-wider transition-all">Закрити / Сплачено</button>`;

                const cardHtml = `
                    <div id="card-${o._id}" draggable="true" ondragstart="drag(event, '${o._id}')" class="bg-zinc-900 border border-zinc-800/80 p-3.5 rounded-xl text-xs space-y-1.5 cursor-grab active:cursor-grabbing hover:border-zinc-700/80 transition-all shadow-md active:scale-95 select-none">
                        <div class="flex justify-between items-center border-b border-zinc-800/60 pb-1.5">
                            <span class="font-black text-indigo-400 text-xs">Чек #${o.order_number}</span>
                            <span class="bg-zinc-950 text-zinc-400 px-2 py-0.5 rounded text-[9px] font-bold border border-zinc-800">Стіл ${o.table}</span>
                        </div>
                        <div class="space-y-1 max-h-24 overflow-y-auto pr-1 hide-scroll">${itemsHtml}</div>
                        ${commentHtml}
                        <div class="flex justify-between items-center pt-2 font-black border-t border-zinc-800/60 mt-2 text-zinc-300">
                            <span class="text-xs text-zinc-200">${o.total_price} ₴</span>
                            <button onclick="deleteOrder('${o._id}')" class="text-red-500 text-[10px] font-bold hover:underline">Видалити</button>
                        </div>
                        ${actionBtn}
                    </div>`;

                if(o.status === 'pending') pendingBox.innerHTML += cardHtml;
                if(o.status === 'cooking') cookingBox.innerHTML += cardHtml;
                if(o.status === 'ready') readyBox.innerHTML += cardHtml;
            });

            document.getElementById('counter-pending').innerText = counts.pending;
            document.getElementById('counter-cooking').innerText = counts.cooking;
            document.getElementById('counter-ready').innerText = counts.ready;
        });

        socket.on('menu_sync', (menu) => {
            const list = document.getElementById('admin-menu-list');
            list.innerHTML = menu.map(item => `
                <div class="flex items-center justify-between bg-zinc-900 p-3 rounded-xl border border-zinc-800/80">
                    <div class="flex items-center gap-3.5">
                        ${item.image ? `<img src="${item.image}" class="w-12 h-12 object-cover rounded-xl border border-zinc-800">` : `<div class="w-12 h-12 bg-zinc-950 flex items-center justify-center rounded-xl text-lg border border-zinc-800">🍽️</div>`}
                        <div>
                            <h4 class="font-black text-zinc-200 text-xs">${item.name} <span class="text-zinc-500 font-bold text-[10px]">(${item.category})</span></h4>
                            <p class="font-bold text-indigo-400 text-[11px] mt-0.5">${item.price} ₴ — ${item.available ? '<span class="text-emerald-400 text-[10px]">В наявності</span>' : '<span class="text-zinc-500 text-[10px]">Знято з продажу</span>'}</p>
                        </div>
                    </div>
                    <div class="flex gap-2.5 text-xs font-bold">
                        <button onclick="editMenuItem('${item._id}', '${escapeHtml(item.name)}', '${escapeHtml(item.category)}', ${item.price}, '${escapeHtml(item.description)}', '${escapeHtml(item.image)}', ${item.available})" class="text-indigo-400 hover:text-indigo-300">Редагувати</button>
                        <button onclick="deleteMenuItem('${item._id}')" class="text-red-500 hover:text-red-400">Вилучити</button>
                    </div>
                </div>`).join('');
        });

        socket.on('reviews_sync', (reviews) => {
            const list = document.getElementById('admin-reviews-list');
            if(reviews.length === 0) { 
                list.innerHTML = '<p class="text-zinc-600 text-xs font-bold p-2">Поки що немає відгуків від гостей.</p>'; 
                return; 
            }
            list.innerHTML = reviews.map(r => {
                let stars = ''; 
                for(let i=1; i<=5; i++) stars += `<i class="${i<=r.rating?'fas':'far'} fa-star text-amber-500 text-[10px]"></i>`;
                return `
                    <div class="bg-zinc-900 border border-zinc-800 p-4 rounded-xl text-xs flex flex-col justify-between shadow-md">
                        <div class="space-y-1.5">
                            <div class="flex justify-between items-center border-b border-zinc-800 pb-1.5 mb-1.5">
                                <span class="font-black text-zinc-300 text-xs">${r.name}</span>
                                <span class="flex gap-0.5">${stars}</span>
                            </div>
                            <p class="text-zinc-400 leading-relaxed font-medium">${r.text || '<span class="text-zinc-600 italic">Без текстового повідомлення</span>'}</p>
                        </div>
                        <div class="flex justify-between items-center border-t border-zinc-800 pt-2 mt-3 text-[10px] text-zinc-500">
                            <span>${r.time_str}</span>
                            <button onclick="deleteReview('${r._id}')" class="text-red-500 font-bold hover:underline">Видалити відгук</button>
                        </div>
                    </div>`;
            }).join('');
        });

        function renderDevices() {
            const container = document.getElementById('devices-container');
            const keys = Object.keys(currentDevices);
            if (keys.length === 0) { 
                container.innerHTML = '<p class="text-zinc-600 text-xs font-bold p-2">Немає підключених столів в реальному часі...</p>'; 
                return; 
            }
            
            container.innerHTML = keys.map(uuid => {
                const d = currentDevices[uuid];
                return `
                    <div class="admin-card rounded-2xl p-4 space-y-3">
                        <div class="flex justify-between items-center border-b border-zinc-800/60 pb-2">
                            <span class="bg-indigo-600 text-white font-black px-2.5 py-1 rounded-xl text-[11px] cyber-glow">Стіл #${d.table}</span>
                            <span class="text-[10px] text-zinc-500 font-bold">Активний: ${d.last_seen}</span>
                        </div>
                        <div class="text-[11px] space-y-1 text-zinc-400 font-medium bg-zinc-950/40 p-2.5 rounded-xl border border-zinc-800/40 shadow-inner">
                            <div class="flex justify-between"><span class="text-zinc-500">Категорія:</span> <span class="text-zinc-200 font-black">${d.category}</span></div>
                            <div class="flex justify-between"><span class="text-zinc-500">Поточний кошик:</span> <span class="text-indigo-400 font-black">${d.cart_total} ₴</span></div>
                            <div class="flex justify-between"><span class="text-zinc-500">Екран/Модалка:</span> <span class="text-zinc-300">${d.modal}</span></div>
                            <div class="flex justify-between"><span class="text-zinc-500">Скролл сторінки:</span> <span class="text-zinc-300">${d.scroll}%</span></div>
                        </div>
                        <div class="relative mt-2 border border-zinc-800/80 rounded-xl overflow-hidden bg-zinc-950 h-48 flex items-center justify-center shadow-md">
                            <div id="placeholder-${uuid}" class="absolute text-[10px] text-zinc-600 font-black flex flex-col items-center gap-2">
                                <i class="fas fa-circle-notch fa-spin text-indigo-500 text-sm"></i> Очікування кадру...
                            </div>
                            <img id="stream-${uuid}" class="w-full h-full object-contain hidden" src="" />
                        </div>
                    </div>`;
            }).join('');
        }

        function updateOrderStatus(id, status) { 
            socket.emit('order_status_update', { id, status }); 
        }
        
        function deleteOrder(id) { 
            showConfirm('Вилучити замовлення з черги журналу?', () => { socket.emit('order_delete', { id }); });
        }
        
        function deleteReview(id) { 
            showConfirm('Видалити цей відгук користувача безповоротно?', () => { socket.emit('reviews_delete', { id }); });
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
            switchTab('menu');
            document.getElementById('menu-id').value = id;
            document.getElementById('menu-name').value = name;
            document.getElementById('menu-category').value = cat;
            document.getElementById('menu-price').value = price;
            document.getElementById('menu-description').value = desc;
            document.getElementById('menu-image').value = img;
            document.getElementById('menu-available').checked = (avail === 'true' || avail === true);
        }

        function deleteMenuItem(id) { 
            showConfirm('Повністю видалити страву з асортименту закладу?', () => { socket.emit('menu_delete', { id }); });
        }
        
        function resetMenuForm() { 
            document.getElementById('menu-form').reset(); 
            document.getElementById('menu-id').value = ''; 
        }
        
        function clearDatabase() { 
            showConfirm('Увага! Ви впевнені, що бажаєте ПОВНІСТЮ занулити всю базу даних? Дані меню, чеків та відгуків буде стерто.', () => { socket.emit('admin_clear_db'); }); 
        }
        
        function exportDatabase() { 
            window.location.href = '/export_db'; 
        }
        
        function importDatabase() {
            const fileInput = document.getElementById('import-file');
            const file = fileInput.files[0];
            if(!file) return;
            const reader = new FileReader();
            reader.onload = function(e) {
                try {
                    const data = JSON.parse(e.target.result);
                    socket.emit('admin_import_db', data);
                    showAlert('Резервний бекап бази успішно інтегровано!');
                    fileInput.value = '';
                } catch(err) { 
                    showAlert('Помилка валідації файлу. Перевірте формат JSON структури.'); 
                }
            };
            reader.readAsText(file);
        }

        function playAlertSound() {
            try {
                const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                const osc = audioCtx.createOscillator();
                const gain = audioCtx.createGain();
                osc.type = 'sine'; 
                osc.frequency.setValueAtTime(620.00, audioCtx.currentTime); 
                gain.gain.setValueAtTime(0.12, audioCtx.currentTime);
                osc.connect(gain); 
                gain.connect(audioCtx.destination);
                osc.start(); 
                osc.stop(audioCtx.currentTime + 0.25);
            } catch(e) {}
        }

        function escapeHtml(str) { 
            if(!str) return ''; 
            return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;"); 
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
    <title>Авторизація адміністратора</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-zinc-950 flex items-center justify-center h-screen text-white antialiased">
    <div class="bg-zinc-900 p-8 rounded-2xl shadow-2xl w-full max-w-md border border-zinc-800/80">
        <h2 class="text-2xl font-black mb-1.5 text-center text-indigo-500 tracking-tight">Вхід до Nexus Cafe</h2>
        <p class="text-center text-xs text-zinc-500 mb-6">Введіть пін-код для верифікації профілю менеджера закладу</p>
        
        {% if error %}
            <div class="bg-red-500/10 border border-red-500/20 text-red-400 p-3.5 rounded-xl mb-4 text-xs text-center font-bold">{{ error }}</div>
        {% endif %}
        
        <form method="POST" class="space-y-4">
            <div>
                <label class="block text-[10px] font-black uppercase tracking-widest mb-2 text-zinc-500">Код доступу</label>
                <input type="password" name="password" required autofocus class="w-full p-4 rounded-xl bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-indigo-500 tracking-widest text-center text-xl font-black">
            </div>
            <button type="submit" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-black py-4 rounded-xl transition shadow-lg active:scale-[0.98]">Увійти до панелі</button>
        </form>
    </div>
</body>
</html>
"""

# ==============================================================================
# 6. ЗАПУСК ОПЕРАЦІЙНОГО СЕРВЕРА
# ==============================================================================
if __name__ == '__main__':
    logger.info("Запуск сервера автоматизації ресторанів Nexus Cafe Pro на порту 5000...")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
