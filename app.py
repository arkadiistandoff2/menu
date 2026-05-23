import gevent.monkey
gevent.monkey.patch_all()
import os
import uuid
from datetime import datetime
from flask import Flask, render_template_string, request, session, redirect, url_for
from flask_socketio import SocketIO, emit
from pymongo import MongoClient
from bson.objectid import ObjectId

# ==========================================
# КОНФІГУРАЦІЯ ТА НАЛАШТУВАННЯ
# ==========================================

app = Flask(__name__)
app.config['SECRET_KEY'] = 'cafe_secret_key_2024'
socketio = SocketIO(app, cors_allowed_origins="*")

# ПІДКЛЮЧЕННЯ ДО БАЗИ ДАНИХ (Встав свій URI)
MONGO_URI = "mongodb://SofterX:Zlata@ac-jstiscf-shard-00-00.lmu80a8.mongodb.net:27017,ac-jstiscf-shard-00-01.lmu80a8.mongodb.net:27017,ac-jstiscf-shard-00-02.lmu80a8.mongodb.net:27017/?ssl=true&replicaSet=atlas-xocnt5-shard-0&authSource=admin&appName=Cluster0" 
ADMIN_PASSWORD = "admin123"

try:
    client = MongoClient(MONGO_URI)
    db = client['cafe_automation']
    menu_col = db['menu']
    orders_col = db['orders']
    sessions_col = db['active_sessions']
    print("✅ База даних підключена!")
except Exception as e:
    print(f"❌ Помилка підключення до БД: {e}")

# ==========================================
# ГОЛОВНИЙ ШАБЛОН (HTML/CSS/JS в одному блоці)
# ==========================================

BASE_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Cafe System</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;800&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', sans-serif; background-color: #0f0f0f; color: #e0e0e0; }
        .dark-card { background: #1a1a1a; border: 1px solid #333; border-radius: 16px; }
        .accent-gradient { background: linear-gradient(135deg, #6366f1 0%, #a855f7 100%); }
        .btn-active { transform: scale(0.95); transition: 0.1s; }
        .scroll-hide::-webkit-scrollbar { display: none; }
        .modal-bottom { transform: translateY(100%); transition: transform 0.3s ease-out; }
        .modal-bottom.active { transform: translateY(0); }
        .status-badge { transition: all 0.5s ease; }
        .blink { animation: blinker 1.5s linear infinite; }
        @keyframes blinker { 50% { opacity: 0.3; } }
    </style>
</head>
<body class="scroll-hide">

    {% if mode == 'client' %}
    <!-- ==========================================
         ІНТЕРФЕЙС КЛІЄНТА
    =========================================== -->
    <div id="app" class="max-w-md mx-auto min-h-screen pb-32">
        <!-- Шапка -->
        <header class="sticky top-0 z-50 bg-[#0f0f0f]/80 backdrop-blur-md p-4 flex justify-between items-center border-b border-white/10">
            <h1 class="text-2xl font-extrabold tracking-tighter text-white">Стіл #{{ table_id }}</h1>
            <button onclick="callWaiter()" class="bg-white text-black px-4 py-2 rounded-full text-xs font-bold uppercase tracking-widest active:scale-95 transition">
                Виклик офіціанта
            </button>
        </header>

        <!-- Статус замовлення -->
        <div id="status-container" class="px-4 mt-4 hidden">
            <div id="status-widget" class="p-4 rounded-2xl text-center font-bold text-sm uppercase tracking-wider shadow-lg shadow-indigo-500/20">
                Завантаження статусу...
            </div>
        </div>

        <!-- Категорії -->
        <div class="flex overflow-x-auto p-4 gap-2 scroll-hide">
            <button onclick="filterMenu('all')" class="cat-btn px-6 py-2 rounded-full bg-white text-black font-semibold text-sm whitespace-nowrap">Все</button>
            {% for cat in categories %}
            <button onclick="filterMenu('{{ cat }}')" class="cat-btn px-6 py-2 rounded-full bg-[#1a1a1a] text-gray-400 font-semibold text-sm border border-white/10 whitespace-nowrap">{{ cat }}</button>
            {% endfor %}
        </div>

        <!-- Вітрина меню -->
        <div id="menu-grid" class="px-4 grid grid-cols-1 gap-4">
            {% for item in menu %}
            <div class="menu-item dark-card p-4 flex flex-col gap-3" data-category="{{ item.category }}">
                <div class="flex justify-between items-start">
                    <h3 class="text-lg font-bold text-white">{{ item.name }}</h3>
                    <span class="text-indigo-400 font-extrabold">{{ item.price }} ₴</span>
                </div>
                <p class="text-gray-500 text-xs leading-relaxed">{{ item.description }}</p>
                <button onclick="addToCart('{{ item._id }}', '{{ item.name }}', {{ item.price }})" 
                        class="w-full py-3 mt-2 rounded-xl bg-white/5 border border-white/10 text-white font-bold hover:bg-white/10 active:bg-white active:text-black transition uppercase text-[10px] tracking-widest">
                    Додати в чек
                </button>
            </div>
            {% endfor %}
        </div>

        <!-- Плаваючий кошик -->
        <div id="cart-bar" class="fixed bottom-6 left-4 right-4 z-[60] hidden">
            <button onclick="toggleCart(true)" class="w-full accent-gradient p-4 rounded-2xl flex justify-between items-center shadow-2xl shadow-indigo-500/40">
                <span class="font-extrabold uppercase text-xs tracking-widest">Переглянути замовлення</span>
                <span id="cart-total-bar" class="bg-black/20 px-3 py-1 rounded-lg font-bold">0 ₴</span>
            </button>
        </div>

        <!-- Модалка кошика -->
        <div id="cart-modal-overlay" onclick="toggleCart(false)" class="fixed inset-0 bg-black/80 z-[70] hidden backdrop-blur-sm"></div>
        <div id="cart-modal" class="fixed bottom-0 left-0 right-0 z-[80] bg-[#1a1a1a] rounded-t-[32px] p-6 modal-bottom border-t border-white/10">
            <div class="w-12 h-1.5 bg-white/20 mx-auto rounded-full mb-6"></div>
            <h2 class="text-2xl font-bold mb-6">Ваш вибір</h2>
            <div id="cart-items" class="space-y-4 max-h-[50vh] overflow-y-auto pr-2">
                <!-- Товари кошика -->
            </div>
            <div class="mt-8 pt-6 border-t border-white/10">
                <div class="flex justify-between items-center mb-6">
                    <span class="text-gray-400 font-semibold">Разом до сплати:</span>
                    <span id="cart-total-modal" class="text-3xl font-black text-white">0 ₴</span>
                </div>
                <button onclick="confirmOrder()" id="confirm-btn" class="w-full py-5 rounded-2xl accent-gradient text-white font-black uppercase tracking-widest text-sm shadow-xl shadow-indigo-500/30">
                    Надіслати замовлення
                </button>
            </div>
        </div>
    </div>

    <script>
        const socket = io();
        const tableId = "{{ table_id }}";
        let cart = JSON.parse(localStorage.getItem('cafe_cart_' + tableId)) || {};
        let activeOrderId = localStorage.getItem('active_order_' + tableId);

        // Живий моніторинг кошика для адміна
        function syncCartWithAdmin() {
            socket.emit('update_live_cart', {
                table_id: tableId,
                cart: cart,
                device: navigator.userAgent
            });
        }

        function addToCart(id, name, price) {
            if (cart[id]) cart[id].qty++;
            else cart[id] = { name, price, qty: 1 };
            renderCart();
            syncCartWithAdmin();
        }

        function updateQty(id, delta) {
            cart[id].qty += delta;
            if (cart[id].qty <= 0) delete cart[id];
            renderCart();
            syncCartWithAdmin();
        }

        function renderCart() {
            const container = document.getElementById('cart-items');
            const cartBar = document.getElementById('cart-bar');
            let total = 0;
            let html = '';

            Object.keys(cart).forEach(id => {
                const item = cart[id];
                total += item.price * item.qty;
                html += `
                    <div class="flex justify-between items-center bg-white/5 p-4 rounded-2xl border border-white/5">
                        <div>
                            <h4 class="font-bold text-white">${item.name}</h4>
                            <p class="text-xs text-gray-500">${item.price} ₴</p>
                        </div>
                        <div class="flex items-center gap-4">
                            <button onclick="updateQty('${id}', -1)" class="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center font-bold">-</button>
                            <span class="font-bold w-4 text-center">${item.qty}</span>
                            <button onclick="updateQty('${id}', 1)" class="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center font-bold">+</button>
                        </div>
                    </div>
                `;
            });

            container.innerHTML = html || '<p class="text-center text-gray-500 py-10">Кошик порожній...</p>';
            document.getElementById('cart-total-bar').innerText = total + ' ₴';
            document.getElementById('cart-total-modal').innerText = total + ' ₴';
            cartBar.style.display = total > 0 ? 'block' : 'none';
            localStorage.setItem('cafe_cart_' + tableId, JSON.stringify(cart));
            if (total === 0) toggleCart(false);
        }

        function toggleCart(show) {
            const modal = document.getElementById('cart-modal');
            const overlay = document.getElementById('cart-modal-overlay');
            if (show) {
                overlay.classList.remove('hidden');
                setTimeout(() => modal.classList.add('active'), 10);
            } else {
                modal.classList.remove('active');
                setTimeout(() => overlay.classList.add('hidden'), 300);
            }
        }

        function filterMenu(cat) {
            document.querySelectorAll('.menu-item').forEach(item => {
                item.style.display = (cat === 'all' || item.dataset.category === cat) ? 'flex' : 'none';
            });
            document.querySelectorAll('.cat-btn').forEach(btn => {
                btn.className = 'cat-btn px-6 py-2 rounded-full font-semibold text-sm border border-white/10 whitespace-nowrap ' + 
                                (btn.innerText.toLowerCase() === cat.toLowerCase() ? 'bg-white text-black' : 'bg-[#1a1a1a] text-gray-400');
            });
        }

        function confirmOrder() {
            if (Object.keys(cart).length === 0) return;
            const btn = document.getElementById('confirm-btn');
            btn.disabled = true;
            btn.innerText = "Відправка...";

            socket.emit('place_order', {
                table_id: tableId,
                items: cart
            });
        }

        function callWaiter() {
            socket.emit('waiter_call', { table_id: tableId });
            alert("Офіціант уже поспішає до вас!");
        }

        socket.on('order_confirmed', (data) => {
            if (data.table_id === tableId) {
                localStorage.setItem('active_order_' + tableId, data.order_id);
                activeOrderId = data.order_id;
                cart = {};
                localStorage.removeItem('cafe_cart_' + tableId);
                renderCart();
                toggleCart(false);
                updateStatusWidget(data.status);
            }
        });

        socket.on('status_update', (data) => {
            if (data.order_id === activeOrderId) {
                updateStatusWidget(data.status);
            }
        });

        function updateStatusWidget(status) {
            const container = document.getElementById('status-container');
            const widget = document.getElementById('status-widget');
            container.classList.remove('hidden');

            if (status === 'new') {
                widget.className = "p-4 rounded-2xl text-center font-bold text-sm uppercase tracking-wider bg-indigo-600 text-white shadow-lg shadow-indigo-500/20";
                widget.innerText = "✉️ Замовлення надіслано";
            } else if (status === 'cooking') {
                widget.className = "p-4 rounded-2xl text-center font-bold text-sm uppercase tracking-wider bg-amber-500 text-black shadow-lg shadow-amber-500/20 blink";
                widget.innerText = "🔥 Готується на кухні";
            } else if (status === 'ready') {
                widget.className = "p-4 rounded-2xl text-center font-bold text-sm uppercase tracking-wider bg-green-500 text-black shadow-lg shadow-green-500/20";
                widget.innerText = "✅ Вже несемо до вас!";
            } else {
                container.classList.add('hidden');
                localStorage.removeItem('active_order_' + tableId);
            }
        }

        // Ініціалізація
        renderCart();
        syncCartWithAdmin();
        if (activeOrderId) socket.emit('get_order_status', { order_id: activeOrderId });
    </script>

    {% elif mode == 'admin' %}
    <!-- ==========================================
         ІНТЕРФЕЙС АДМІНІСТРАТОРА
    =========================================== -->
    <div class="flex h-screen overflow-hidden">
        <!-- Бокова панель -->
        <aside class="w-72 bg-[#1a1a1a] border-r border-white/10 p-6 flex flex-col gap-8">
            <div class="text-2xl font-black italic text-indigo-500 tracking-tighter">ADMIN PANEL</div>
            
            <nav class="flex flex-col gap-2">
                <button onclick="switchTab('orders')" class="nav-btn w-full text-left p-4 rounded-xl bg-indigo-600 text-white font-bold flex items-center gap-3 transition">
                    📋 Поточні замовлення
                </button>
                <button onclick="switchTab('menu')" class="nav-btn w-full text-left p-4 rounded-xl text-gray-400 hover:bg-white/5 font-bold flex items-center gap-3 transition">
                    🍔 Конструктор меню
                </button>
                <button onclick="switchTab('devices')" class="nav-btn w-full text-left p-4 rounded-xl text-gray-400 hover:bg-white/5 font-bold flex items-center gap-3 transition">
                    📱 Пристрої (Live)
                </button>
            </nav>

            <div class="mt-auto p-4 bg-black/30 rounded-2xl border border-white/5">
                <p class="text-[10px] uppercase text-gray-500 tracking-widest mb-1">Статус сервера</p>
                <div class="flex items-center gap-2">
                    <div class="w-2 h-2 rounded-full bg-green-500"></div>
                    <span class="text-xs font-bold">ONLINE</span>
                </div>
            </div>
        </aside>

        <!-- Основна зона -->
        <main class="flex-1 overflow-y-auto p-8 scroll-hide">
            
            <!-- Статистика -->
            <div id="stats-bar" class="grid grid-cols-4 gap-6 mb-10">
                <div class="dark-card p-6 border-l-4 border-green-500">
                    <p class="text-xs text-gray-500 uppercase font-bold tracking-widest mb-2">Каса</p>
                    <h2 id="stat-revenue" class="text-3xl font-black">0 ₴</h2>
                </div>
                <div class="dark-card p-6 border-l-4 border-amber-500">
                    <p class="text-xs text-gray-500 uppercase font-bold tracking-widest mb-2">В черзі</p>
                    <h2 id="stat-queue" class="text-3xl font-black">0</h2>
                </div>
                <div class="dark-card p-6 border-l-4 border-indigo-500">
                    <p class="text-xs text-gray-500 uppercase font-bold tracking-widest mb-2">Всього чеків</p>
                    <h2 id="stat-total" class="text-3xl font-black">0</h2>
                </div>
                <div class="dark-card p-6 border-l-4 border-purple-500">
                    <p class="text-xs text-gray-500 uppercase font-bold tracking-widest mb-2">Топ продажів</p>
                    <h2 id="stat-top" class="text-sm font-black text-gray-300">---</h2>
                </div>
            </div>

            <!-- Вкладка: Замовлення -->
            <div id="tab-orders" class="tab-content">
                <h2 class="text-2xl font-black mb-6">Живий потік замовлень</h2>
                <div id="admin-orders-list" class="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6">
                    <!-- Картки замовлень -->
                </div>
            </div>

            <!-- Вкладка: Меню -->
            <div id="tab-menu" class="tab-content hidden">
                <div class="flex justify-between items-center mb-8">
                    <h2 class="text-2xl font-black">Керування стравами</h2>
                    <button onclick="openMenuModal()" class="bg-indigo-600 px-6 py-3 rounded-xl font-bold text-sm">Додати страву</button>
                </div>
                <div id="admin-menu-list" class="grid grid-cols-1 gap-4">
                    <!-- Елементи меню -->
                </div>
            </div>

            <!-- Вкладка: Пристрої -->
            <div id="tab-devices" class="tab-content hidden">
                <h2 class="text-2xl font-black mb-6">Активні сесії (Живий Ефір)</h2>
                <div id="admin-devices-list" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                    <!-- Картки девайсів -->
                </div>
            </div>
        </main>
    </div>

    <!-- Модалка меню -->
    <div id="menu-modal" class="fixed inset-0 bg-black/90 z-[100] hidden items-center justify-center p-6 backdrop-blur-md">
        <div class="dark-card w-full max-w-lg p-8">
            <h3 class="text-2xl font-bold mb-6">Параметри страви</h3>
            <div class="space-y-4">
                <input type="text" id="m-name" placeholder="Назва страви" class="w-full bg-black/40 border border-white/10 p-4 rounded-xl outline-none focus:border-indigo-500">
                <input type="number" id="m-price" placeholder="Ціна (₴)" class="w-full bg-black/40 border border-white/10 p-4 rounded-xl outline-none focus:border-indigo-500">
                <input type="text" id="m-cat" placeholder="Категорія (Кава, Десерти...)" class="w-full bg-black/40 border border-white/10 p-4 rounded-xl outline-none focus:border-indigo-500">
                <textarea id="m-desc" placeholder="Опис інгредієнтів" class="w-full bg-black/40 border border-white/10 p-4 rounded-xl outline-none focus:border-indigo-500 h-32"></textarea>
                <div class="flex gap-4 pt-4">
                    <button onclick="closeMenuModal()" class="flex-1 bg-white/5 py-4 rounded-xl font-bold">Скасувати</button>
                    <button onclick="saveMenuItem()" class="flex-1 accent-gradient py-4 rounded-xl font-bold shadow-lg shadow-indigo-500/20">Зберегти</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        const socket = io();
        let currentTab = 'orders';

        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
            document.getElementById('tab-' + tabId).classList.remove('hidden');
            document.querySelectorAll('.nav-btn').forEach(btn => {
                btn.classList.remove('bg-indigo-600', 'text-white');
                btn.classList.add('text-gray-400');
            });
            event.currentTarget.classList.add('bg-indigo-600', 'text-white');
            currentTab = tabId;
            if (tabId === 'menu') loadMenu();
        }

        // Завантаження замовлень
        socket.on('update_admin_orders', (data) => {
            const list = document.getElementById('admin-orders-list');
            let html = '';
            let revenue = 0;
            let queue = 0;

            data.orders.forEach(order => {
                if (order.status === 'completed') revenue += order.total;
                else queue++;

                if (order.status !== 'completed') {
                    html += `
                        <div class="dark-card p-6 flex flex-col gap-4 border-t-4 ${order.status === 'new' ? 'border-indigo-500 blink' : 'border-amber-500'}">
                            <div class="flex justify-between items-center">
                                <span class="text-2xl font-black italic">Стіл #${order.table_id}</span>
                                <span class="text-[10px] text-gray-500">${order.time}</span>
                            </div>
                            <div class="space-y-2 py-4 border-y border-white/5">
                                ${Object.values(order.items).map(item => `
                                    <div class="flex justify-between text-sm">
                                        <span class="text-gray-300 font-semibold">${item.name}</span>
                                        <span class="font-black">x${item.qty}</span>
                                    </div>
                                `).join('')}
                            </div>
                            <div class="flex justify-between items-center mb-2">
                                <span class="text-xs uppercase font-bold text-gray-500 tracking-widest">Разом:</span>
                                <span class="text-xl font-black">${order.total} ₴</span>
                            </div>
                            <div class="grid grid-cols-3 gap-2">
                                <button onclick="changeStatus('${order._id}', 'cooking')" class="bg-amber-500/10 text-amber-500 py-2 rounded-lg text-[10px] font-black uppercase hover:bg-amber-500 hover:text-black transition">Кухня</button>
                                <button onclick="changeStatus('${order._id}', 'ready')" class="bg-green-500/10 text-green-500 py-2 rounded-lg text-[10px] font-black uppercase hover:bg-green-500 hover:text-black transition">Готово</button>
                                <button onclick="changeStatus('${order._id}', 'completed')" class="bg-indigo-500/10 text-indigo-500 py-2 rounded-lg text-[10px] font-black uppercase hover:bg-indigo-500 hover:text-white transition">Закрити</button>
                            </div>
                        </div>
                    `;
                }
            });

            list.innerHTML = html || '<div class="col-span-full py-20 text-center text-gray-600 font-bold uppercase tracking-widest">Замовлень немає</div>';
            document.getElementById('stat-revenue').innerText = revenue + ' ₴';
            document.getElementById('stat-queue').innerText = queue;
            document.getElementById('stat-total').innerText = data.orders.length;
        });

        // Живі пристрої
        socket.on('update_devices', (sessions) => {
            const list = document.getElementById('admin-devices-list');
            list.innerHTML = Object.entries(sessions).map(([sid, data]) => `
                <div class="dark-card p-4 relative overflow-hidden">
                    <div class="absolute top-2 right-2 w-2 h-2 rounded-full bg-green-500"></div>
                    <p class="text-[10px] font-black text-indigo-400 mb-1">Стіл #${data.table_id}</p>
                    <h4 class="text-xs font-bold text-gray-300 mb-3 truncate">${data.device.split(' ')[0]}</h4>
                    <div class="space-y-1">
                        ${Object.values(data.cart).map(i => `
                            <div class="flex justify-between text-[10px] opacity-60">
                                <span>${i.name}</span>
                                <span>x${i.qty}</span>
                            </div>
                        `).join('') || '<p class="text-[10px] text-gray-600 italic">Кошик порожній</p>'}
                    </div>
                </div>
            `).join('');
        });

        function changeStatus(id, status) {
            socket.emit('admin_change_status', { order_id: id, status: status });
        }

        // Керування меню через базу
        function loadMenu() {
            fetch('/api/menu').then(r => r.json()).then(data => {
                const list = document.getElementById('admin-menu-list');
                list.innerHTML = data.map(item => `
                    <div class="dark-card p-4 flex justify-between items-center">
                        <div>
                            <span class="text-[10px] uppercase font-bold text-gray-600 tracking-widest">${item.category}</span>
                            <h4 class="font-bold text-white text-lg">${item.name}</h4>
                            <p class="text-xs text-indigo-400 font-black">${item.price} ₴</p>
                        </div>
                        <div class="flex gap-2">
                            <button onclick="deleteMenuItem('${item._id}')" class="bg-red-500/10 text-red-500 px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-wider hover:bg-red-500 hover:text-white transition">Видалити</button>
                        </div>
                    </div>
                `).join('');
            });
        }

        function openMenuModal() { document.getElementById('menu-modal').classList.replace('hidden', 'flex'); }
        function closeMenuModal() { document.getElementById('menu-modal').classList.replace('flex', 'hidden'); }

        function saveMenuItem() {
            const data = {
                name: document.getElementById('m-name').value,
                price: parseFloat(document.getElementById('m-price').value),
                category: document.getElementById('m-cat').value,
                description: document.getElementById('m-desc').value
            };
            fetch('/api/menu', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            }).then(() => {
                closeMenuModal();
                loadMenu();
            });
        }

        function deleteMenuItem(id) {
            fetch('/api/menu/' + id, {method: 'DELETE'}).then(() => loadMenu());
        }

        socket.on('alert_waiter', (data) => {
            const audio = new Audio('https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3');
            audio.play();
            alert("⚠️ ВИКЛИК ОФІЦІАНТА: СТІЛ #" + data.table_id);
        });
    </script>
    {% endif %}

</body>
</html>
"""

# ==========================================
# BACKEND ЛОГІКА (Маршрути та Сокети)
# ==========================================

@app.route('/')
@app.route('/table/<table_id>')
def client_index(table_id="1"):
    menu = list(menu_col.find())
    for item in menu: item['_id'] = str(item['_id'])
    categories = sorted(list(set(item['category'] for item in menu)))
    return render_template_string(BASE_HTML, mode='client', table_id=table_id, menu=menu, categories=categories)

@app.route('/admin')
def admin_panel():
    if not session.get('is_admin'):
        return f"""
        <body style="background:#000;color:#fff;display:flex;align-items:center;justify-center;height:100vh;font-family:sans-serif;">
            <form action="/admin/login" method="POST" style="background:#111;padding:40px;border-radius:20px;border:1px solid #333;">
                <h2 style="margin-bottom:20px;">Вхід в систему</h2>
                <input type="password" name="password" placeholder="Пароль" style="background:#222;border:1px solid #444;color:#fff;padding:12px;width:250px;border-radius:10px;margin-bottom:15px;display:block;">
                <button type="submit" style="background:#6366f1;color:#fff;border:none;padding:12px;width:100%;border-radius:10px;font-weight:bold;cursor:pointer;">Увійти</button>
            </form>
        </body>
        """
    return render_template_string(BASE_HTML, mode='admin')

@app.route('/admin/login', methods=['POST'])
def admin_login():
    if request.form.get('password') == ADMIN_PASSWORD:
        session['is_admin'] = True
        return redirect(url_for('admin_panel'))
    return "Пароль невірний. <a href='/admin'>Назад</a>"

# API для керування меню
@app.route('/api/menu', methods=['GET', 'POST'])
def api_menu():
    if request.method == 'GET':
        items = list(menu_col.find())
        for i in items: i['_id'] = str(i['_id'])
        return items
    if session.get('is_admin'):
        data = request.json
        menu_col.insert_one(data)
        return {"status": "ok"}

@app.route('/api/menu/<id>', methods=['DELETE'])
def api_delete_menu(id):
    if session.get('is_admin'):
        menu_col.delete_one({'_id': ObjectId(id)})
        return {"status": "ok"}

# СОКЕТИ ДЛЯ РЕАЛЬНОГО ЧАСУ
active_sessions = {}

@socketio.on('connect')
def handle_connect():
    # Надсилаємо актуальні замовлення адміну при підключенні
    orders = list(orders_col.find().sort('time', -1))
    for o in orders: o['_id'] = str(o['_id'])
    emit('update_admin_orders', {'orders': orders}, broadcast=True)

@socketio.on('update_live_cart')
def handle_cart_sync(data):
    sid = request.sid
    active_sessions[sid] = {
        'table_id': data['table_id'],
        'cart': data['cart'],
        'device': data['device']
    }
    emit('update_devices', active_sessions, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in active_sessions:
        del active_sessions[request.sid]
        emit('update_devices', active_sessions, broadcast=True)

@socketio.on('place_order')
def handle_order(data):
    # Розрахунок ціни на бекенді (БЕЗПЕКА)
    total = 0
    items_verified = {}
    for item_id, cart_data in data['items'].items():
        db_item = menu_col.find_one({'_id': ObjectId(item_id)})
        if db_item:
            price = db_item['price']
            total += price * cart_data['qty']
            items_verified[item_id] = {
                'name': db_item['name'],
                'price': price,
                'qty': cart_data['qty']
            }
    
    order = {
        'table_id': data['table_id'],
        'items': items_verified,
        'total': total,
        'status': 'new',
        'time': datetime.now().strftime("%H:%M:%S")
    }
    res = orders_col.insert_one(order)
    order_id = str(res.inserted_id)
    
    emit('order_confirmed', {'table_id': data['table_id'], 'order_id': order_id, 'status': 'new'}, broadcast=True)
    
    # Оновлення списку в адміна
    orders = list(orders_col.find().sort('time', -1))
    for o in orders: o['_id'] = str(o['_id'])
    emit('update_admin_orders', {'orders': orders}, broadcast=True)

@socketio.on('admin_change_status')
def change_status(data):
    orders_col.update_one({'_id': ObjectId(data['order_id'])}, {'$set': {'status': data['status']}})
    emit('status_update', {'order_id': data['order_id'], 'status': data['status']}, broadcast=True)
    
    orders = list(orders_col.find().sort('time', -1))
    for o in orders: o['_id'] = str(o['_id'])
    emit('update_admin_orders', {'orders': orders}, broadcast=True)

@socketio.on('get_order_status')
def get_status(data):
    order = orders_col.find_one({'_id': ObjectId(data['order_id'])})
    if order:
        emit('status_update', {'order_id': data['order_id'], 'status': order['status']})

@socketio.on('waiter_call')
def waiter_call(data):
    emit('alert_waiter', {'table_id': data['table_id']}, broadcast=True)

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)
