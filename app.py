import os
import time
from datetime import datetime
from bson.objectid import ObjectId
from flask import Flask, request, render_template_string, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room

# === КОНФІГУРАЦІЯ ТА БАЗА ДАНИХ ===
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'super-secret-cafe-key')
socketio = SocketIO(app, cors_allowed_origins="*")

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/cafe_db')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

from pymongo import MongoClient
client = MongoClient(MONGO_URI)
db = client.cafe_db

# Словник для зберігання живих сесій користувачів
live_users = {}

# === HTML ШАБЛОНИ (ВБУДОВАНІ) ===

# 1. Шаблон логіну адміна
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Вхід | Адмін</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 flex items-center justify-center h-screen">
    <div class="bg-white p-8 rounded-lg shadow-md w-96">
        <h2 class="text-2xl font-bold mb-6 text-center text-gray-800">Вхід в Панель</h2>
        <form method="POST" action="/login">
            <input type="password" name="password" placeholder="Пароль адміністратора" required class="w-full p-3 border rounded mb-4 focus:outline-none focus:ring-2 focus:ring-blue-500">
            <button type="submit" class="w-full bg-blue-600 text-white p-3 rounded font-bold hover:bg-blue-700">Увійти</button>
        </form>
    </div>
</body>
</html>
"""

# 2. Шаблон Клієнта (Мобільний Dark Mode)
CLIENT_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Меню Закладу</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <style>
        body { background-color: #121212; color: #ffffff; -webkit-tap-highlight-color: transparent; }
        .hide-scroll::-webkit-scrollbar { display: none; }
        .modal-overlay { background: rgba(0,0,0,0.8); backdrop-filter: blur(4px); }
        .smooth-transition { transition: all 0.3s ease; }
    </style>
</head>
<body class="font-sans pb-24">
    <!-- Шапка -->
    <header class="fixed top-0 left-0 right-0 bg-gray-900 shadow-md z-40 p-4 flex justify-between items-center">
        <div class="text-xl font-bold">Стіл #<span id="table-number">{{ table }}</span></div>
        <button onclick="callWaiter()" class="bg-red-600 text-white px-4 py-2 rounded-full font-bold text-sm shadow-lg active:scale-95 smooth-transition">Виклик офіціанта</button>
    </header>

    <!-- Віджет статусу замовлення -->
    <div id="status-widget" class="hidden fixed top-16 left-4 right-4 z-30 p-3 rounded-lg shadow-lg font-bold text-center text-white smooth-transition"></div>

    <main class="pt-20 px-4">
        <!-- Фільтр категорій -->
        <div class="flex space-x-3 overflow-x-auto hide-scroll py-2 mb-4" id="category-filter">
            <!-- Генерується через JS -->
        </div>

        <!-- Вітрина -->
        <div class="grid grid-cols-2 gap-4" id="menu-grid">
            <!-- Генерується через JS -->
        </div>
    </main>

    <!-- Плаваючий кошик -->
    <div id="cart-float" class="hidden fixed bottom-4 left-4 right-4 z-40 bg-white text-black p-4 rounded-2xl shadow-2xl flex justify-between items-center active:scale-95 smooth-transition" onclick="openCart()">
        <div class="font-bold">Переглянути кошик</div>
        <div class="font-bold text-xl"><span id="cart-float-total">0</span> ₴</div>
    </div>

    <!-- Модалка кошика -->
    <div id="cart-modal" class="fixed inset-0 z-50 hidden flex-col justify-end modal-overlay">
        <div class="bg-gray-900 rounded-t-3xl p-6 h-3/4 flex flex-col relative transform translate-y-full smooth-transition" id="cart-modal-content">
            <button onclick="closeCart()" class="absolute top-4 right-4 bg-gray-800 p-2 rounded-full w-10 h-10 flex items-center justify-center font-bold text-xl">&times;</button>
            <h2 class="text-2xl font-bold mb-4">Ваше замовлення</h2>
            <div id="cart-items" class="flex-1 overflow-y-auto hide-scroll space-y-4"></div>
            <div class="mt-4 pt-4 border-t border-gray-700">
                <div class="flex justify-between text-xl font-bold mb-4">
                    <span>Разом:</span>
                    <span><span id="cart-modal-total">0</span> ₴</span>
                </div>
                <button onclick="placeOrder()" id="confirm-btn" class="w-full bg-blue-600 text-white p-4 rounded-xl font-bold text-lg shadow-lg active:scale-95 smooth-transition">Підтвердити замовлення</button>
            </div>
        </div>
    </div>

    <!-- Універсальна модалка для повідомлень -->
    <div id="msg-modal" class="fixed inset-0 z-[60] hidden items-center justify-center modal-overlay opacity-0 smooth-transition">
        <div class="bg-gray-800 p-6 rounded-2xl w-5/6 max-w-sm text-center shadow-2xl">
            <h3 id="msg-title" class="text-xl font-bold mb-2"></h3>
            <p id="msg-text" class="text-gray-300 mb-6"></p>
            <button onclick="closeMsg()" class="w-full bg-blue-600 p-3 rounded-xl font-bold">Зрозуміло</button>
        </div>
    </div>

    <script>
        const socket = io();
        const tableId = "{{ table }}";
        let menuData = [];
        let cart = JSON.parse(localStorage.getItem('cafe_cart_' + tableId)) || {};
        let activeCategory = "Всі";
        let currentOrderId = localStorage.getItem('cafe_order_' + tableId) || null;

        // Ініціалізація
        socket.on('connect', () => {
            socket.emit('client_join', { table: tableId, user_agent: navigator.userAgent });
            socket.emit('get_menu');
            if (currentOrderId) socket.emit('check_order_status', { order_id: currentOrderId });
            sendLiveCart();
        });

        socket.on('menu_data', (data) => {
            menuData = data;
            renderCategories();
            renderMenu();
            updateCartUI();
        });

        socket.on('order_status_update', (data) => {
            if (data.order_id === currentOrderId) {
                const widget = document.getElementById('status-widget');
                widget.classList.remove('hidden', 'bg-blue-500', 'bg-yellow-500', 'bg-green-500');
                if (data.status === 'Нове') {
                    widget.classList.add('bg-blue-500');
                    widget.innerText = 'Замовлення відправлено на кухню';
                } else if (data.status === 'Готується') {
                    widget.classList.add('bg-yellow-500');
                    widget.innerText = 'Ваші страви вже готуються!';
                } else if (data.status === 'Готово') {
                    widget.classList.add('bg-green-500');
                    widget.innerText = 'Готово! Офіціант несе замовлення.';
                } else if (data.status === 'Закрито') {
                    widget.classList.add('hidden');
                    currentOrderId = null;
                    localStorage.removeItem('cafe_order_' + tableId);
                    showMsg("Дякуємо!", "Ваше замовлення закрито. Чекаємо вас знову!");
                }
            }
        });

        // UI Функції
        function renderCategories() {
            const container = document.getElementById('category-filter');
            const categories = ["Всі", ...new Set(menuData.map(i => i.category))];
            container.innerHTML = categories.map(cat => `
                <button onclick="setCategory('${cat}')" class="px-5 py-2 rounded-full whitespace-nowrap font-bold text-sm smooth-transition ${activeCategory === cat ? 'bg-white text-black' : 'bg-gray-800 text-gray-300'}">${cat}</button>
            `).join('');
        }

        function setCategory(cat) {
            activeCategory = cat;
            renderCategories();
            renderMenu();
            socket.emit('live_update', { table: tableId, action: 'view_category', category: cat });
        }

        function renderMenu() {
            const container = document.getElementById('menu-grid');
            const items = activeCategory === "Всі" ? menuData : menuData.filter(i => i.category === activeCategory);
            container.innerHTML = items.map(item => `
                <div class="bg-gray-800 rounded-2xl p-4 flex flex-col justify-between">
                    <div>
                        <h3 class="font-bold text-lg mb-1">${item.name}</h3>
                        <p class="text-xs text-gray-400 mb-2">${item.description}</p>
                    </div>
                    <div>
                        <div class="text-xl font-bold mb-3">${item.price} ₴</div>
                        <button onclick="addToCart('${item._id}')" class="w-full bg-blue-600 hover:bg-blue-700 py-2 rounded-xl font-bold active:scale-95 smooth-transition">Додати</button>
                    </div>
                </div>
            `).join('');
        }

        // Кошик
        function addToCart(id) {
            cart[id] = (cart[id] || 0) + 1;
            saveCart();
            updateCartUI();
        }

        function changeQty(id, delta) {
            cart[id] += delta;
            if (cart[id] <= 0) delete cart[id];
            saveCart();
            updateCartUI();
            if (Object.keys(cart).length === 0) closeCart();
        }

        function saveCart() {
            localStorage.setItem('cafe_cart_' + tableId, JSON.stringify(cart));
            sendLiveCart();
        }

        function sendLiveCart() {
            const cartDetails = Object.keys(cart).map(id => {
                const item = menuData.find(i => i._id === id);
                return item ? { name: item.name, qty: cart[id], price: item.price } : null;
            }).filter(i => i);
            socket.emit('live_update', { table: tableId, action: 'cart', cart: cartDetails });
        }

        function updateCartUI() {
            const floatBtn = document.getElementById('cart-float');
            if (Object.keys(cart).length === 0) {
                floatBtn.classList.add('hidden');
                return;
            }
            floatBtn.classList.remove('hidden');
            let total = 0;
            const itemsHtml = Object.keys(cart).map(id => {
                const item = menuData.find(i => i._id === id);
                if (!item) return '';
                total += item.price * cart[id];
                return `
                    <div class="flex justify-between items-center bg-gray-800 p-3 rounded-xl">
                        <div class="flex-1">
                            <div class="font-bold">${item.name}</div>
                            <div class="text-sm text-gray-400">${item.price} ₴ х ${cart[id]}</div>
                        </div>
                        <div class="flex items-center space-x-3">
                            <button onclick="changeQty('${id}', -1)" class="w-8 h-8 bg-gray-700 rounded-full font-bold">-</button>
                            <span class="font-bold w-4 text-center">${cart[id]}</span>
                            <button onclick="changeQty('${id}', 1)" class="w-8 h-8 bg-blue-600 rounded-full font-bold">+</button>
                        </div>
                    </div>
                `;
            }).join('');
            
            document.getElementById('cart-float-total').innerText = total;
            document.getElementById('cart-modal-total').innerText = total;
            document.getElementById('cart-items').innerHTML = itemsHtml;
        }

        function openCart() {
            document.getElementById('cart-modal').classList.remove('hidden');
            document.getElementById('cart-modal').classList.add('flex');
            setTimeout(() => { document.getElementById('cart-modal-content').classList.remove('translate-y-full'); }, 10);
        }

        function closeCart() {
            document.getElementById('cart-modal-content').classList.add('translate-y-full');
            setTimeout(() => { 
                document.getElementById('cart-modal').classList.add('hidden'); 
                document.getElementById('cart-modal').classList.remove('flex');
            }, 300);
        }

        // Дії
        function placeOrder() {
            if (Object.keys(cart).length === 0) return;
            if (currentOrderId) {
                showMsg("Увага", "Ви вже маєте активне замовлення. Дочекайтесь його завершення.");
                return;
            }
            
            const btn = document.getElementById('confirm-btn');
            btn.innerText = "Обробка...";
            btn.disabled = true;

            const orderItems = Object.keys(cart).map(id => ({ id: id, qty: cart[id] }));
            
            socket.emit('place_order', { table: tableId, items: orderItems }, (response) => {
                btn.innerText = "Підтвердити замовлення";
                btn.disabled = false;
                if (response.success) {
                    currentOrderId = response.order_id;
                    localStorage.setItem('cafe_order_' + tableId, currentOrderId);
                    cart = {};
                    saveCart();
                    closeCart();
                    updateCartUI();
                    showMsg("Успішно!", "Замовлення передано на кухню.");
                } else {
                    showMsg("Помилка", response.error);
                }
            });
        }

        function callWaiter() {
            socket.emit('call_waiter', { table: tableId });
            showMsg("Офіціанта викликано", "Він підійде до вас найближчим часом.");
        }

        // Кастомна модалка
        function showMsg(title, text) {
            document.getElementById('msg-title').innerText = title;
            document.getElementById('msg-text').innerText = text;
            const m = document.getElementById('msg-modal');
            m.classList.remove('hidden');
            m.classList.add('flex');
            setTimeout(() => m.classList.remove('opacity-0'), 10);
        }
        function closeMsg() {
            const m = document.getElementById('msg-modal');
            m.classList.add('opacity-0');
            setTimeout(() => { m.classList.add('hidden'); m.classList.remove('flex'); }, 300);
        }
    </script>
</body>
</html>
"""

# 3. Шаблон Адміна (Десктоп Dashboard)
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Панель Моніторингу</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <style>
        .blink { animation: blinker 1.5s linear infinite; }
        @keyframes blinker { 50% { border-color: transparent; } }
        .modal-overlay { background: rgba(0,0,0,0.6); }
    </style>
</head>
<body class="bg-gray-100 text-gray-800 font-sans">
    
    <div class="flex h-screen overflow-hidden">
        <!-- Навігація -->
        <aside class="w-64 bg-gray-900 text-white flex flex-col">
            <div class="p-6 text-2xl font-bold border-b border-gray-800">Cafe System</div>
            <nav class="flex-1 p-4 space-y-2">
                <button onclick="switchTab('orders')" id="tab-orders" class="w-full text-left p-3 rounded bg-blue-600 font-bold">Поточні замовлення</button>
                <button onclick="switchTab('menu')" id="tab-menu" class="w-full text-left p-3 rounded hover:bg-gray-800 transition">Конструктор меню</button>
                <button onclick="switchTab('devices')" id="tab-devices" class="w-full text-left p-3 rounded hover:bg-gray-800 transition">Пристрої (Живий Ефір)</button>
            </nav>
            <div class="p-4 border-t border-gray-800"><a href="/logout" class="text-red-400 hover:text-red-300">Вийти</a></div>
        </aside>

        <!-- Головна зона -->
        <main class="flex-1 flex flex-col h-full overflow-hidden">
            <!-- Статистика -->
            <header class="bg-white shadow p-6 flex justify-between gap-4 z-10">
                <div class="bg-green-100 p-4 rounded-xl flex-1 border border-green-200">
                    <div class="text-sm text-green-700 font-bold">Каса (Закриті)</div>
                    <div class="text-3xl font-bold text-green-900"><span id="stat-revenue">0</span> ₴</div>
                </div>
                <div class="bg-yellow-100 p-4 rounded-xl flex-1 border border-yellow-200">
                    <div class="text-sm text-yellow-700 font-bold">В черзі кухарів</div>
                    <div class="text-3xl font-bold text-yellow-900" id="stat-cooking">0</div>
                </div>
                <div class="bg-blue-100 p-4 rounded-xl flex-1 border border-blue-200">
                    <div class="text-sm text-blue-700 font-bold">Всього чеків</div>
                    <div class="text-3xl font-bold text-blue-900" id="stat-total-orders">0</div>
                </div>
                <div class="bg-purple-100 p-4 rounded-xl flex-1 border border-purple-200">
                    <div class="text-sm text-purple-700 font-bold">Топ продажів</div>
                    <div class="text-xl font-bold text-purple-900 mt-2" id="stat-top-item">-</div>
                </div>
            </header>

            <!-- Вкладки контенту -->
            <div class="flex-1 p-6 overflow-y-auto">
                <!-- Вкладка 1: Замовлення -->
                <div id="view-orders" class="h-full">
                    <h2 class="text-2xl font-bold mb-4">Активні замовлення</h2>
                    <div id="orders-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"></div>
                </div>

                <!-- Вкладка 2: Меню -->
                <div id="view-menu" class="hidden h-full flex gap-6">
                    <div class="flex-1 bg-white p-6 rounded shadow overflow-y-auto">
                        <h2 class="text-xl font-bold mb-4">Список страв</h2>
                        <div id="admin-menu-list" class="space-y-3"></div>
                    </div>
                    <div class="w-1/3 bg-white p-6 rounded shadow">
                        <h2 class="text-xl font-bold mb-4" id="menu-form-title">Додати страву</h2>
                        <form id="menu-form" onsubmit="saveMenuItem(event)">
                            <input type="hidden" id="menu-id">
                            <div class="mb-3"><label class="block text-sm font-bold mb-1">Назва</label><input type="text" id="menu-name" required class="w-full border p-2 rounded"></div>
                            <div class="mb-3"><label class="block text-sm font-bold mb-1">Ціна (₴)</label><input type="number" id="menu-price" required class="w-full border p-2 rounded"></div>
                            <div class="mb-3"><label class="block text-sm font-bold mb-1">Категорія</label><input type="text" id="menu-category" required class="w-full border p-2 rounded"></div>
                            <div class="mb-4"><label class="block text-sm font-bold mb-1">Опис (інгредієнти)</label><textarea id="menu-desc" class="w-full border p-2 rounded"></textarea></div>
                            <button type="submit" class="w-full bg-blue-600 text-white p-3 rounded font-bold hover:bg-blue-700">Зберегти у хмару</button>
                            <button type="button" onclick="resetMenuForm()" class="w-full mt-2 bg-gray-200 text-gray-800 p-2 rounded hover:bg-gray-300">Очистити форму</button>
                        </form>
                    </div>
                </div>

                <!-- Вкладка 3: Живий ефір -->
                <div id="view-devices" class="hidden h-full">
                    <h2 class="text-2xl font-bold mb-4 flex items-center">
                        <span class="w-3 h-3 bg-red-500 rounded-full mr-2 animate-pulse"></span> Підключені гості
                    </h2>
                    <div id="devices-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"></div>
                </div>
            </div>
        </main>
    </div>

    <!-- Модалка виклику офіціанта -->
    <div id="waiter-modal" class="fixed inset-0 z-50 hidden items-center justify-center modal-overlay">
        <div class="bg-red-600 text-white p-8 rounded-xl shadow-2xl text-center w-96 transform scale-100">
            <h2 class="text-4xl font-bold mb-4">ВИКЛИК!</h2>
            <p class="text-2xl mb-6">Стіл <span id="waiter-table" class="font-black text-4xl"></span></p>
            <button onclick="document.getElementById('waiter-modal').classList.add('hidden')" class="bg-white text-red-600 font-bold px-8 py-3 rounded-full text-xl hover:bg-gray-100">Прийнято</button>
        </div>
    </div>
    
    <!-- Аудіо для сповіщень -->
    <audio id="audio-alert" src="https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3" preload="auto"></audio>
    <audio id="audio-waiter" src="https://assets.mixkit.co/active_storage/sfx/995/995-preview.mp3" preload="auto"></audio>

    <script>
        const socket = io();
        let orders = [];
        let menuItems = [];
        let liveDevices = {};

        socket.on('connect', () => { socket.emit('admin_join'); socket.emit('get_admin_data'); });

        socket.on('admin_init_data', (data) => {
            orders = data.orders;
            menuItems = data.menu;
            liveDevices = data.live_users;
            updateDashboard();
            renderOrders();
            renderMenuAdmin();
            renderDevices();
        });

        socket.on('new_order_alert', (order) => {
            orders.unshift(order);
            playSound('audio-alert');
            updateDashboard();
            renderOrders();
        });

        socket.on('waiter_called', (data) => {
            playSound('audio-waiter');
            document.getElementById('waiter-table').innerText = '#' + data.table;
            document.getElementById('waiter-modal').classList.remove('hidden');
            document.getElementById('waiter-modal').classList.add('flex');
        });

        socket.on('live_users_update', (users) => {
            liveDevices = users;
            renderDevices();
        });

        socket.on('menu_data', (data) => {
            menuItems = data;
            renderMenuAdmin();
        });

        // Навігація
        function switchTab(tab) {
            ['orders', 'menu', 'devices'].forEach(t => {
                document.getElementById('view-' + t).classList.add('hidden');
                document.getElementById('tab-' + t).classList.replace('bg-blue-600', 'hover:bg-gray-800');
            });
            document.getElementById('view-' + tab).classList.remove('hidden');
            document.getElementById('tab-' + tab).classList.replace('hover:bg-gray-800', 'bg-blue-600');
        }

        // Рендер Замовлень
        function renderOrders() {
            const container = document.getElementById('orders-container');
            const active = orders.filter(o => o.status !== 'Закрито');
            container.innerHTML = active.map(o => {
                const isNew = o.status === 'Нове';
                const cardBorder = isNew ? 'border-4 border-blue-500 blink' : 'border border-gray-200';
                const itemsHtml = o.items.map(i => `<div class="flex justify-between text-sm py-1 border-b"><span class="font-bold">${i.name}</span><span>${i.qty} шт</span></div>`).join('');
                
                return `
                <div class="bg-white rounded-xl shadow p-5 ${cardBorder}">
                    <div class="flex justify-between items-center mb-4 pb-2 border-b-2">
                        <div class="text-2xl font-black">Стіл #${o.table}</div>
                        <div class="text-gray-500 text-sm">${new Date(o.timestamp.$date || o.timestamp).toLocaleTimeString()}</div>
                    </div>
                    <div class="mb-4 h-32 overflow-y-auto">${itemsHtml}</div>
                    <div class="text-xl font-bold mb-4 text-right">Сума: ${o.total} ₴</div>
                    <div class="flex gap-2">
                        ${isNew ? `<button onclick="changeStatus('${o._id}', 'Готується')" class="flex-1 bg-yellow-500 text-white font-bold py-2 rounded hover:bg-yellow-600">На Кухню</button>` : ''}
                        ${o.status === 'Готується' ? `<button onclick="changeStatus('${o._id}', 'Готово')" class="flex-1 bg-green-500 text-white font-bold py-2 rounded hover:bg-green-600">Готово (Видача)</button>` : ''}
                        ${o.status === 'Готово' ? `<button onclick="changeStatus('${o._id}', 'Закрито')" class="flex-1 bg-gray-800 text-white font-bold py-2 rounded hover:bg-gray-900">Сплачено (Закрити)</button>` : ''}
                    </div>
                </div>`;
            }).join('');
        }

        function changeStatus(orderId, newStatus) {
            socket.emit('admin_change_status', { order_id: orderId, status: newStatus });
            const order = orders.find(o => o._id === orderId);
            if (order) order.status = newStatus;
            updateDashboard();
            renderOrders();
        }

        // Рендер Меню
        function renderMenuAdmin() {
            const container = document.getElementById('admin-menu-list');
            container.innerHTML = menuItems.map(m => `
                <div class="border rounded p-4 flex justify-between items-center hover:bg-gray-50">
                    <div>
                        <div class="font-bold text-lg">${m.name} <span class="text-sm font-normal text-gray-500 ml-2">(${m.category})</span></div>
                        <div class="text-gray-600 text-sm">${m.price} ₴</div>
                    </div>
                    <div class="space-x-2">
                        <button onclick="editMenu('${m._id}')" class="bg-yellow-100 text-yellow-700 px-3 py-1 rounded font-bold hover:bg-yellow-200">Правка</button>
                        <button onclick="deleteMenu('${m._id}')" class="bg-red-100 text-red-700 px-3 py-1 rounded font-bold hover:bg-red-200">Видалити</button>
                    </div>
                </div>
            `).join('');
        }

        function editMenu(id) {
            const item = menuItems.find(m => m._id === id);
            document.getElementById('menu-id').value = item._id;
            document.getElementById('menu-name').value = item.name;
            document.getElementById('menu-price').value = item.price;
            document.getElementById('menu-category').value = item.category;
            document.getElementById('menu-desc').value = item.description;
            document.getElementById('menu-form-title').innerText = "Редагувати страву";
        }

        function deleteMenu(id) {
            socket.emit('admin_delete_menu', { id: id });
        }

        function resetMenuForm() {
            document.getElementById('menu-id').value = '';
            document.getElementById('menu-form').reset();
            document.getElementById('menu-form-title').innerText = "Додати страву";
        }

        function saveMenuItem(e) {
            e.preventDefault();
            const data = {
                id: document.getElementById('menu-id').value,
                name: document.getElementById('menu-name').value,
                price: parseFloat(document.getElementById('menu-price').value),
                category: document.getElementById('menu-category').value,
                description: document.getElementById('menu-desc').value
            };
            socket.emit('admin_save_menu', data);
            resetMenuForm();
        }

        // Рендер Живих Пристроїв
        function renderDevices() {
            const container = document.getElementById('devices-container');
            const devices = Object.values(liveDevices);
            if (devices.length === 0) {
                container.innerHTML = '<div class="col-span-3 text-center text-gray-400 mt-10">Немає активних клієнтів</div>';
                return;
            }
            container.innerHTML = devices.map(d => {
                let cartHtml = '<div class="text-gray-400 text-sm italic">Кошик порожній</div>';
                if (d.cart && d.cart.length > 0) {
                    const total = d.cart.reduce((sum, item) => sum + (item.price * item.qty), 0);
                    cartHtml = `
                        <div class="bg-gray-100 p-2 rounded mt-2 text-sm">
                            <div class="font-bold text-gray-700 border-b pb-1 mb-1">Збирає кошик:</div>
                            ${d.cart.map(i => `<div class="flex justify-between"><span>${i.name} x${i.qty}</span><span>${i.price * i.qty} ₴</span></div>`).join('')}
                            <div class="text-right font-bold mt-1 pt-1 border-t">Проміжно: ${total} ₴</div>
                        </div>`;
                }
                
                const os = d.ua.toLowerCase().includes('iphone') ? 'iPhone Apple' : (d.ua.toLowerCase().includes('android') ? 'Android Phone' : 'Windows/Mac PC');
                
                return `
                <div class="bg-white rounded shadow border-l-4 border-green-500 p-4 relative">
                    <div class="absolute top-2 right-2 w-3 h-3 bg-green-500 rounded-full animate-pulse"></div>
                    <div class="font-black text-xl mb-1">Стіл #${d.table}</div>
                    <div class="text-xs text-gray-500 mb-3">${os} | Останній клік: ${d.last_seen}</div>
                    <div class="text-sm bg-blue-50 text-blue-800 p-2 rounded mb-2 border border-blue-100">Переглядає: <b>${d.category || 'Всі'}</b></div>
                    ${cartHtml}
                </div>`;
            }).join('');
        }

        function updateDashboard() {
            const closed = orders.filter(o => o.status === 'Закрито');
            const cooking = orders.filter(o => o.status === 'Готується').length;
            const revenue = closed.reduce((sum, o) => sum + o.total, 0);
            
            document.getElementById('stat-revenue').innerText = revenue;
            document.getElementById('stat-cooking').innerText = cooking;
            document.getElementById('stat-total-orders').innerText = orders.length;

            // Топ продажів (проста логіка)
            const itemCounts = {};
            orders.forEach(o => o.items.forEach(i => { itemCounts[i.name] = (itemCounts[i.name] || 0) + i.qty; }));
            let topItem = "-", maxCount = 0;
            for (const [name, count] of Object.entries(itemCounts)) {
                if (count > maxCount) { maxCount = count; topItem = name; }
            }
            document.getElementById('stat-top-item').innerText = topItem;
        }

        function playSound(id) {
            const el = document.getElementById(id);
            if (el) { el.currentTime = 0; el.play().catch(e => console.log('Аудіо заблоковано браузером до першого кліку')); }
        }
    </script>
</body>
</html>
"""

# === ДОПОМІЖНІ ФУНКЦІЇ ===
def serialize_doc(doc):
    if '_id' in doc: doc['_id'] = str(doc['_id'])
    if 'timestamp' in doc: doc['timestamp'] = doc['timestamp'].isoformat()
    return doc

def get_current_time_str():
    return datetime.now().strftime("%H:%M:%S")

# === FLASK РОУТИ ===
@app.route('/')
def index():
    table = request.args.get('table', '1') # По дефолту стіл 1, якщо не вказано ?table=...
    return render_template_string(CLIENT_HTML, table=table)

@app.route('/admin')
def admin():
    if not session.get('admin_logged_in'):
        return redirect(url_for('login'))
    return render_template_string(ADMIN_HTML)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin'))
        return "Невірний пароль. <a href='/login'>Назад</a>"
    return render_template_string(LOGIN_HTML)

@app.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('login'))


# === SOCKET.IO ПОДІЇ (РЕАЛЬНИЙ ЧАС) ===

@socketio.on('client_join')
def handle_client_join(data):
    sid = request.sid
    live_users[sid] = {
        'table': data.get('table'),
        'ua': data.get('user_agent', 'Unknown'),
        'last_seen': get_current_time_str(),
        'category': 'Всі',
        'cart': []
    }
    join_room(f"table_{data.get('table')}")
    socketio.emit('live_users_update', live_users, room='admin')

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in live_users:
        del live_users[sid]
        socketio.emit('live_users_update', live_users, room='admin')

@socketio.on('get_menu')
def send_menu():
    items = [serialize_doc(i) for i in db.menu.find()]
    emit('menu_data', items)

@socketio.on('live_update')
def handle_live_update(data):
    sid = request.sid
    if sid in live_users:
        if data.get('action') == 'view_category':
            live_users[sid]['category'] = data.get('category')
        elif data.get('action') == 'cart':
            live_users[sid]['cart'] = data.get('cart')
        live_users[sid]['last_seen'] = get_current_time_str()
        socketio.emit('live_users_update', live_users, room='admin')

@socketio.on('call_waiter')
def handle_call_waiter(data):
    socketio.emit('waiter_called', {'table': data.get('table')}, room='admin')

@socketio.on('place_order')
def handle_place_order(data):
    table = data.get('table')
    client_items = data.get('items', [])
    
    if not client_items:
        return {'success': False, 'error': 'Кошик порожній'}

    # БЕЗПЕКА: Прорахунок суми виключно на сервері через БД
    total = 0
    order_items_db = []
    
    for c_item in client_items:
        db_item = db.menu.find_one({"_id": ObjectId(c_item['id'])})
        if db_item:
            qty = int(c_item['qty'])
            total += db_item['price'] * qty
            order_items_db.append({
                'id': str(db_item['_id']),
                'name': db_item['name'],
                'price': db_item['price'],
                'qty': qty
            })
            
    if total == 0:
        return {'success': False, 'error': 'Помилка товарів'}

    new_order = {
        'table': table,
        'items': order_items_db,
        'total': total,
        'status': 'Нове',
        'timestamp': datetime.now()
    }
    
    res = db.orders.insert_one(new_order)
    order_id_str = str(res.inserted_id)
    new_order['_id'] = order_id_str
    new_order = serialize_doc(new_order)

    # Сповіщаємо адміна
    socketio.emit('new_order_alert', new_order, room='admin')
    
    # Очищаємо живий кошик для цього користувача
    sid = request.sid
    if sid in live_users:
        live_users[sid]['cart'] = []
        socketio.emit('live_users_update', live_users, room='admin')

    return {'success': True, 'order_id': order_id_str}

@socketio.on('check_order_status')
def check_order_status(data):
    order = db.orders.find_one({"_id": ObjectId(data['order_id'])})
    if order:
        emit('order_status_update', {'order_id': str(order['_id']), 'status': order['status']})

# --- АДМІНСЬКІ СОКЕТИ ---

@socketio.on('admin_join')
def admin_join():
    join_room('admin')

@socketio.on('get_admin_data')
def send_admin_data():
    orders = [serialize_doc(o) for o in list(db.orders.find().sort("timestamp", -1))]
    menu = [serialize_doc(i) for i in db.menu.find()]
    emit('admin_init_data', {'orders': orders, 'menu': menu, 'live_users': live_users})

@socketio.on('admin_change_status')
def admin_change_status(data):
    order_id = data.get('order_id')
    new_status = data.get('status')
    
    db.orders.update_one({"_id": ObjectId(order_id)}, {"$set": {"status": new_status}})
    order = db.orders.find_one({"_id": ObjectId(order_id)})
    
    if order:
        # Відправляємо оновлення конкретному столу
        socketio.emit('order_status_update', {'order_id': order_id, 'status': new_status}, room=f"table_{order['table']}")

@socketio.on('admin_save_menu')
def admin_save_menu(data):
    menu_data = {
        'name': data['name'],
        'price': float(data['price']),
        'category': data['category'],
        'description': data['description']
    }
    
    if data.get('id'):
        db.menu.update_one({"_id": ObjectId(data['id'])}, {"$set": menu_data})
    else:
        db.menu.insert_one(menu_data)
        
    updated_menu = [serialize_doc(i) for i in db.menu.find()]
    socketio.emit('menu_data', updated_menu) # Оновлюємо у всіх клієнтів миттєво

@socketio.on('admin_delete_menu')
def admin_delete_menu(data):
    db.menu.delete_one({"_id": ObjectId(data['id'])})
    updated_menu = [serialize_doc(i) for i in db.menu.find()]
    socketio.emit('menu_data', updated_menu)


# === ЗАПУСК ===
if __name__ == '__main__':
    # Створюємо тестове меню, якщо база порожня
    if db.menu.count_documents({}) == 0:
        db.menu.insert_many([
            {"name": "Капучино", "price": 65, "category": "Кава", "description": "Класичний з молоком (250мл)"},
            {"name": "Еспресо", "price": 40, "category": "Кава", "description": "Міцна арабіка (30мл)"},
            {"name": "Круасан", "price": 75, "category": "Випічка", "description": "З шоколадом (120г)"},
            {"name": "Чизкейк", "price": 95, "category": "Десерти", "description": "Нью-Йорк (150г)"}
        ])
    
    print("Сервер запущено! Адмінка: http://127.0.0.1:5000/admin (Пароль: admin123)")
    print("Меню клієнта (Стіл 3): http://127.0.0.1:5000/?table=3")
    
    socketio.run(app, debug=True, host='0.0.0.1', port=5000, allow_unsafe_werkzeug=True)
