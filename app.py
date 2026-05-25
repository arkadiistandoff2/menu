import eventlet
eventlet.monkey_patch()

import os
from datetime import datetime
from bson.objectid import ObjectId
from flask import Flask, request, render_template_string, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room
from pymongo import MongoClient

# ==============================================================================
# 1. ІНІЦІАЛІЗАЦІЯ ТА НАЛАШТУВАННЯ БАЗИ ДАНИХ
# ==============================================================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'nexus-pos-premium-key-2026')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/cafe_db')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

client = MongoClient(MONGO_URI)
db = client.get_default_database(default='cafe_db')

# Словник для моніторингу активних гостей в реальному часі
live_sessions = {}

def serialize_doc(doc):
    if not doc: return None
    d = dict(doc)
    d['_id'] = str(d['_id'])
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.strftime('%H:%M')
    return d

def get_all_menu():
    return [serialize_doc(i) for i in db.menu.find()]

def get_active_orders():
    return [serialize_doc(o) for o in db.orders.find({"status": {"$ne": "Закрито"}}).sort("timestamp", -1)]

def get_all_reviews():
    return [serialize_doc(r) for r in db.reviews.find().sort("timestamp", -1)]

# ==============================================================================
# 2. КЛІЄНТСЬКИЙ ІНТЕРФЕЙС (MOBILE-FIRST DIGITAL MENU)
# ==============================================================================
CUSTOMER_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Digital Menu - Стіл #{{ table_id }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
    <style>
        body { background-color: #09090b; color: #f4f4f5; font-family: system-ui, sans-serif; -webkit-tap-highlight-color: transparent; }
        .hide-scroll::-webkit-scrollbar { display: none; }
        .glass-card { background: linear-gradient(145deg, #18181b 0%, #0f0f11 100%); border: 1px solid #27272a; }
        .glass-card:hover { border-color: #4f46e5; box-shadow: 0 0 15px rgba(79, 70, 229, 0.15); }
    </style>
</head>
<body class="pb-28">

    <div id="toast-box" class="fixed top-4 left-4 right-4 z-50 hidden bg-zinc-900 border border-zinc-800 p-4 rounded-xl shadow-2xl flex items-center gap-3 transition-all duration-300">
        <div class="w-2 h-2 rounded-full bg-indigo-500 animate-ping"></div>
        <p id="toast-text" class="text-sm font-bold text-zinc-200"></p>
    </div>

    <header class="fixed top-0 left-0 right-0 bg-zinc-950/80 backdrop-blur-md border-b border-zinc-800 z-40 p-4 flex justify-between items-center">
        <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl bg-indigo-600 flex items-center justify-center font-black text-white shadow-lg">
                #{{ table_id }}
            </div>
            <div>
                <div class="text-[10px] text-zinc-500 uppercase tracking-wider font-bold">Ваш Стіл</div>
                <div class="text-xs font-bold text-emerald-400 flex items-center gap-1">
                    <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span> Станція онлайн
                </div>
            </div>
        </div>
        <button onclick="openReviewModal()" class="bg-zinc-900 hover:bg-zinc-800 text-zinc-300 border border-zinc-800 px-3.5 py-1.5 rounded-xl font-bold text-xs transition-all">
            ⭐ Залишити відгук
        </button>
    </header>

    <div id="status-widget" class="hidden mt-24 mx-4 p-4 rounded-2xl bg-indigo-950/40 border border-indigo-800/60 items-center gap-4">
        <div class="text-2xl">🍳</div>
        <div>
            <div class="text-[10px] uppercase font-bold text-indigo-400 tracking-wider">Статус поточного замовлення</div>
            <div id="status-text" class="font-bold text-sm text-zinc-200">Ваше замовлення готується...</div>
        </div>
    </div>

    <main class="pt-24 px-4">
        <h1 class="text-2xl font-black tracking-tight mb-4">Преміум <span class="text-indigo-500">Меню</span></h1>
        
        <div class="flex space-x-2 overflow-x-auto hide-scroll py-2 mb-6 sticky top-16 z-30 bg-[#09090b]/90 backdrop-blur-sm -mx-4 px-4" id="category-bar"></div>

        <div class="grid grid-cols-2 gap-4" id="menu-grid"></div>
    </main>

    <div id="float-cart-bar" class="fixed bottom-0 left-0 right-0 p-4 z-40 bg-gradient-to-t from-[#09090b] via-[#09090b] to-transparent hidden">
        <button onclick="openModal('cart-modal')" class="w-full bg-indigo-600 text-white p-4 rounded-2xl shadow-xl flex justify-between items-center border border-indigo-500/30 active:scale-95 transition-all">
            <div class="flex items-center gap-2">
                <span id="float-cart-count" class="bg-indigo-800 px-2 py-0.5 rounded-md font-bold text-xs">0</span>
                <span class="text-xs font-bold uppercase tracking-wider">Переглянути кошик</span>
            </div>
            <span class="text-base font-black bg-indigo-700/50 px-3 py-1 rounded-xl"><span id="float-cart-total">0</span> ₴</span>
        </button>
    </div>

    <div id="cart-modal" class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm hidden flex-col justify-end">
        <div class="bg-zinc-950 border-t border-zinc-800 rounded-t-[2rem] max-h-[85vh] flex flex-col p-6">
            <div class="flex justify-between items-center mb-4">
                <h2 class="text-xl font-black">Ваше замовлення</h2>
                <button onclick="closeModal('cart-modal')" class="text-zinc-500 font-bold text-sm">Закрити</button>
            </div>
            <div id="cart-items-list" class="flex-1 overflow-y-auto space-y-3 my-2 pr-1 hide-scroll"></div>
            
            <div class="space-y-3 mt-4 pt-4 border-t border-zinc-800">
                <input type="text" id="order-comment" placeholder="Коментар до замовлення (напр. без луку)..." class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-xs text-zinc-200 focus:outline-none focus:border-indigo-500">
                <label class="flex items-center gap-2 cursor-pointer p-1">
                    <input type="checkbox" id="order-takeaway" class="rounded bg-zinc-900 border-zinc-800 text-indigo-600 focus:ring-0">
                    <span class="text-xs text-zinc-400 font-medium">Замовлення з собою (на виніс)</span>
                </label>
                <div class="flex justify-between items-center py-2">
                    <span class="text-xs font-bold text-zinc-400">Разом до сплати:</span>
                    <span class="text-2xl font-black text-indigo-400"><span id="modal-cart-total">0</span> ₴</span>
                </div>
                <button onclick="submitOrder()" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white p-4 rounded-xl font-bold text-sm shadow-lg transition-all">
                    🚀 Надіслати замовлення в КДС
                </button>
            </div>
        </div>
    </div>

    <div id="review-modal" class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm hidden items-center justify-center p-4">
        <div class="bg-zinc-950 border border-zinc-800 p-6 rounded-2xl w-full max-w-sm">
            <h3 class="text-lg font-black text-center mb-4">Оцініть наш сервіс</h3>
            <div id="stars-container" class="flex justify-center gap-2 mb-4"></div>
            <textarea id="review-comment" placeholder="Напишіть ваші враження..." rows="3" class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-xs text-zinc-200 focus:outline-none focus:border-indigo-500 resize-none"></textarea>
            <div class="flex gap-3 mt-4">
                <button onclick="closeModal('review-modal')" class="flex-1 bg-zinc-900 border border-zinc-800 text-zinc-400 p-2.5 rounded-xl text-xs font-bold">Скасувати</button>
                <button onclick="submitReview()" class="flex-1 bg-indigo-600 text-white p-2.5 rounded-xl text-xs font-bold">Надіслати</button>
            </div>
        </div>
    </div>

    <script>
        const socket = io();
        const tableId = "{{ table_id }}";
        let menuItems = [];
        let cart = {};
        let currentCategory = 'Всі';
        let selectedRating = 5;
        const uuid = 'user_' + Math.random().toString(36).substr(2, 9) + '_' + Date.now();

        socket.on('connect', () => {
            socket.emit('client_init', { uuid: uuid, table: tableId });
            sendLiveActivity();
        });

        socket.on('menu_sync', (data) => {
            menuItems = data;
            renderCategories();
            renderMenu();
            updateCartUI();
        });

        socket.on('order_status_update', (data) => {
            const widget = document.getElementById('status-widget');
            const txt = document.getElementById('status-text');
            if(data.status === 'Закрито') {
                widget.classList.add('hidden');
                showToast("Дякуємо! Замовлення завершено.");
            } else {
                widget.classList.remove('hidden');
                widget.classList.add('flex');
                txt.innerText = data.message;
            }
        });

        function renderCategories() {
            const bar = document.getElementById('category-bar');
            const cats = ['Всі', ...new Set(menuItems.map(i => i.category))];
            bar.innerHTML = cats.map(cat => {
                const active = currentCategory === cat;
                return `<button onclick="setCategory('${cat}')" class="px-4 py-1.5 rounded-xl whitespace-nowrap font-bold text-xs transition-all ${active ? 'bg-indigo-600 text-white shadow-md' : 'bg-zinc-900 text-zinc-400'}" >${cat}</button>`;
            }).join('');
        }

        function setCategory(cat) {
            currentCategory = cat;
            renderCategories();
            renderMenu();
            sendLiveActivity();
        }

        function renderMenu() {
            const grid = document.getElementById('menu-grid');
            let filtered = currentCategory === 'Всі' ? menuItems : menuItems.filter(i => i.category === currentCategory);
            
            if(filtered.length === 0) {
                grid.innerHTML = `<div class="col-span-2 text-center text-zinc-500 py-8 text-xs">Тут поки нічого немає</div>`;
                return;
            }

            grid.innerHTML = filtered.map(item => {
                const avail = item.available !== false;
                const img = item.image ? `<img src="${item.image}" class="w-full h-28 object-cover rounded-xl mb-2" />` : `<div class="w-full h-28 bg-zinc-900 flex items-center justify-center text-2xl rounded-xl mb-2">🍽️</div>`;
                return `
                    <div class="glass-card rounded-2xl p-2.5 flex flex-col justify-between ${!avail ? 'opacity-50' : ''}">
                        <div>
                            ${img}
                            <h3 class="font-bold text-xs text-zinc-100 truncate">${item.name}</h3>
                            <p class="text-[10px] text-zinc-400 line-clamp-2 mt-0.5 leading-tight">${item.description || ''}</p>
                        </div>
                        <div class="mt-3 flex items-center justify-between">
                            <span class="text-sm font-black text-indigo-400">${item.price} ₴</span>
                            ${avail ? `<button onclick="addToCart('${item._id}')" class="bg-indigo-600 w-7 h-7 rounded-lg font-bold text-white flex items-center justify-center active:scale-95 transition-all">+</button>` : `<span class="text-[9px] bg-zinc-800 text-zinc-500 px-1.5 py-0.5 rounded">Немає</span>`}
                        </div>
                    </div>
                `;
            }).join('');
        }

        function addToCart(id) {
            cart[id] = (cart[id] || 0) + 1;
            updateCartUI();
            sendLiveActivity();
        }

        function changeQty(id, delta) {
            if(!cart[id]) return;
            cart[id] += delta;
            if(cart[id] <= 0) delete cart[id];
            updateCartUI();
            sendLiveActivity();
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
                        <div class="flex items-center justify-between bg-zinc-900/60 p-3 rounded-xl border border-zinc-800">
                            <div class="flex-1 min-w-0 pr-2">
                                <h4 class="font-bold text-xs text-zinc-200 truncate">${item.name}</h4>
                                <p class="text-[11px] text-indigo-400 font-bold mt-0.5">${item.price} ₴</p>
                            </div>
                            <div class="flex items-center gap-2 bg-zinc-950 px-2 py-1 rounded-xl border border-zinc-800">
                                <button onclick="changeQty('${id}', -1)" class="text-zinc-400 font-bold px-1 text-xs">-</button>
                                <span class="font-bold text-xs text-zinc-200 min-w-[12px] text-center">${cart[id]}</span>
                                <button onclick="changeQty('${id}', 1)" class="text-zinc-400 font-bold px-1 text-xs">+</button>
                            </div>
                        </div>
                    `;
                }
            });

            if(list) list.innerHTML = html || '<div class="text-center text-zinc-600 py-6 text-xs">Кошик порожній</div>';
            document.getElementById('float-cart-count').innerText = totalCount;
            document.getElementById('float-cart-total').innerText = totalPrice;
            document.getElementById('modal-cart-total').innerText = totalPrice;
            
            document.getElementById('float-cart-bar').classList.toggle('hidden', totalCount === 0);
        }

        function sendLiveActivity() {
            let count = 0, total = 0;
            Object.keys(cart).forEach(id => {
                const item = menuItems.find(m => m._id === id);
                if(item) { count += cart[id]; total += item.price * cart[id]; }
            });
            socket.emit('client_activity', { uuid: uuid, table: tableId, category: currentCategory, cart_count: count, cart_total: total });
        }

        function submitOrder() {
            const items = Object.keys(cart).map(id => ({ id: id, qty: cart[id] }));
            if(items.length === 0) return;
            
            socket.emit('order_submit', {
                uuid: uuid, table: tableId, items: items,
                comment: document.getElementById('order-comment').value,
                takeaway: document.getElementById('order-takeaway').checked
            }, (res) => {
                if(res && res.success) {
                    cart = {};
                    updateCartUI();
                    sendLiveActivity();
                    closeModal('cart-modal');
                    document.getElementById('order-comment').value = '';
                    showToast("Замовлення успішно надіслано на кухню!");
                }
            });
        }

        function openReviewModal() {
            selectedRating = 5;
            renderStars();
            document.getElementById('review-comment').value = '';
            openModal('review-modal');
        }

        function renderStars() {
            const container = document.getElementById('stars-container');
            container.innerHTML = '';
            for(let i=1; i<=5; i++) {
                const star = document.createElement('span');
                star.className = `cursor-pointer text-3xl transition-colors ${i <= selectedRating ? 'text-amber-400' : 'text-zinc-700'}`;
                star.innerHTML = '★';
                star.onclick = () => { selectedRating = i; renderStars(); };
                container.appendChild(star);
            }
        }

        function submitReview() {
            socket.emit('review_submit', {
                table: tableId, rating: selectedRating,
                comment: document.getElementById('review-comment').value.trim()
            }, (res) => {
                if(res && res.success) {
                    closeModal('review-modal');
                    showToast("Дякуємо за ваш відгук!");
                }
            });
        }

        function openModal(id) { document.getElementById(id).classList.remove('hidden'); document.getElementById(id).classList.add('flex'); }
        function closeModal(id) { document.getElementById(id).classList.add('hidden'); document.getElementById(id).classList.remove('flex'); }
        
        function showToast(text) {
            const box = document.getElementById('toast-box');
            document.getElementById('toast-text').innerText = text;
            box.classList.remove('hidden');
            setTimeout(() => box.classList.add('hidden'), 3500);
        }
    </script>
</body>
</html>
"""

# ==============================================================================
# 3. АДМІНІСТРАТИВНИЙ ІНТЕРФЕЙС (REAL-TIME POS & KDS CONTROL PANEL)
# ==============================================================================
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NEXUS CONTROL CENTER</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
    <style>
        body { background-color: #020617; color: #f8fafc; font-family: system-ui, sans-serif; }
        .hide-scroll::-webkit-scrollbar { width: 4px; }
        .hide-scroll::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 4px; }
    </style>
</head>
<body class="flex h-screen overflow-hidden">

    <aside class="w-64 bg-slate-900 border-r border-slate-800 p-5 flex flex-col justify-between">
        <div>
            <div class="mb-8">
                <h2 class="text-xl font-black text-indigo-500 tracking-wider">NEXUS POS</h2>
                <p class="text-[10px] text-slate-500 uppercase font-bold tracking-widest mt-0.5">Control Station v2.6</p>
            </div>
            <nav class="space-y-2">
                <button onclick="switchTab('orders')" id="btn-orders" class="w-full text-left px-4 py-2.5 rounded-xl text-xs font-bold bg-indigo-600 text-white transition-all">📋 Монітор КДС (Замовлення)</button>
                <button onclick="switchTab('menu')" id="btn-menu" class="w-full text-left px-4 py-2.5 rounded-xl text-xs font-bold text-slate-400 hover:bg-slate-800 transition-all">🍔 Управління меню</button>
                <button onclick="switchTab('reviews')" id="btn-reviews" class="w-full text-left px-4 py-2.5 rounded-xl text-xs font-bold text-slate-400 hover:bg-slate-800 transition-all">⭐ Сервіс і Відгуки</button>
                <button onclick="switchTab('live')" id="btn-live" class="w-full text-left px-4 py-2.5 rounded-xl text-xs font-bold text-slate-400 hover:bg-slate-800 transition-all">👥 Живий моніторинг залів</button>
            </nav>
        </div>
        <a href="/logout" class="text-xs font-bold text-rose-500 hover:underline p-2">🔒 Вийти з системи</a>
    </aside>

    <main class="flex-1 overflow-y-auto p-8 hide-scroll">
        
        <div id="tab-orders" class="tab-content space-y-6">
            <h1 class="text-2xl font-black tracking-tight border-b border-slate-800 pb-3">Активна черга КДС</h1>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6" id="kds-grid"></div>
        </div>

        <div id="tab-menu" class="tab-content hidden space-y-6">
            <div class="flex justify-between items-center border-b border-slate-800 pb-3">
                <h1 class="text-2xl font-black tracking-tight">Номенклатура & Склад</h1>
                <button onclick="openAddModal()" class="bg-indigo-600 hover:bg-indigo-500 px-4 py-2 rounded-xl text-xs font-bold shadow-md transition-all">+ Створити позицію</button>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4" id="menu-grid"></div>
        </div>

        <div id="tab-reviews" class="tab-content hidden space-y-6">
            <h1 class="text-2xl font-black tracking-tight border-b border-slate-800 pb-3">Фідбек та оцінки клієнтів</h1>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4" id="reviews-grid"></div>
        </div>

        <div id="tab-live" class="tab-content hidden space-y-6">
            <h1 class="text-2xl font-black tracking-tight border-b border-slate-800 pb-3">Активні сесії за столами</h1>
            <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4" id="live-grid"></div>
        </div>
    </main>

    <div id="menu-modal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 hidden items-center justify-center p-4">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-md p-6 space-y-4">
            <h3 id="modal-title" class="text-base font-black border-b border-slate-800 pb-2">Редагувати страву</h3>
            <input type="hidden" id="item-id">
            
            <div class="space-y-1">
                <label class="text-[10px] text-slate-400 font-bold uppercase">Назва страви</label>
                <input type="text" id="item-name" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs focus:outline-none focus:border-indigo-500">
            </div>
            <div class="grid grid-cols-2 gap-4">
                <div class="space-y-1">
                    <label class="text-[10px] text-slate-400 font-bold uppercase">Категорія</label>
                    <select id="item-category" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs focus:outline-none text-slate-200">
                        <option value="Кава">Кава</option>
                        <option value="Бургери">Бургери</option>
                        <option value="Снеки">Снеки</option>
                        <option value="Десерти">Десерти</option>
                    </select>
                </div>
                <div class="space-y-1">
                    <label class="text-[10px] text-slate-400 font-bold uppercase">Ціна (₴)</label>
                    <input type="number" id="item-price" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs focus:outline-none focus:border-indigo-500">
                </div>
            </div>
            <div class="space-y-1">
                <label class="text-[10px] text-slate-400 font-bold uppercase">Опис компонентів</label>
                <textarea id="item-description" rows="2" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs focus:outline-none resize-none"></textarea>
            </div>
            <div class="space-y-1">
                <label class="text-[10px] text-slate-400 font-bold uppercase">Фотографія (Менеджер зображень)</label>
                <input type="file" accept="image/*" onchange="encodeImageFile(event)" class="w-full text-xs text-slate-400 bg-slate-950 p-2 border border-slate-800 rounded-xl">
                <img id="menu-image-preview" src="" class="hidden w-full h-24 object-cover rounded-xl mt-2 border border-slate-700">
            </div>
            <div class="flex gap-3 pt-2">
                <button onclick="closeModal()" class="flex-1 bg-slate-800 hover:bg-slate-700 p-3 rounded-xl text-xs font-bold">Скасувати</button>
                <button onclick="saveMenuItem()" class="flex-1 bg-indigo-600 hover:bg-indigo-500 p-3 rounded-xl text-xs font-bold">Зберегти карту</button>
            </div>
        </div>
    </div>

    <script>
        const socket = io();
        let allMenu = [], allOrders = [], allReviews = [], allLive = [];
        let currentImageBase64 = '';

        socket.on('connect', () => { socket.emit('admin_init'); });
        socket.on('menu_sync', data => { allMenu = data; renderMenu(); });
        socket.on('orders_sync', data => { allOrders = data; renderOrders(); });
        socket.on('reviews_sync', data => { allReviews = data; renderReviews(); });
        socket.on('live_sync', data => { allLive = data; renderLive(); });

        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.getElementById('tab-' + tabId).classList.remove('hidden');
            document.querySelectorAll('nav button').forEach(b => b.classList.remove('bg-indigo-600', 'text-white'));
            document.querySelectorAll('nav button').forEach(b => b.classList.add('text-slate-400'));
            document.getElementById('btn-' + tabId).classList.add('bg-indigo-600', 'text-white');
            document.getElementById('btn-' + tabId).classList.remove('text-slate-400');
        }

        function renderOrders() {
            const grid = document.getElementById('kds-grid');
            if(allOrders.length === 0) { grid.innerHTML = `<p class="text-slate-500 text-xs">Активних замовлень в черзі немає</p>`; return; }
            grid.innerHTML = allOrders.map(o => {
                let badgeColor = o.status === 'Нове' ? 'bg-blue-950 text-blue-400 border-blue-800' : 'bg-amber-950 text-amber-400 border-amber-800';
                let btnText = o.status === 'Нове' ? '🍳 Почати готувати' : '✨ Готово до видачі';
                if (o.status === 'Готово') { badgeColor = 'bg-emerald-950 text-emerald-400 border-emerald-800'; btnText = '💵 Розрахувати & Закрити'; }
                
                const nextStatus = o.status === 'Нове' ? 'Готується' : (o.status === 'Готується' ? 'Готово' : 'Закрито');
                const itemsHtml = o.items.map(i => `<div class="flex justify-between text-xs font-medium py-0.5 border-b border-slate-800/40 text-slate-300"><span>• ${i.name}</span><span class="font-black text-indigo-400">x${i.qty}</span></div>`).join('');
                
                return `
                    <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 flex flex-col justify-between shadow-lg">
                        <div>
                            <div class="flex justify-between items-center mb-3">
                                <span class="bg-slate-950 border border-slate-700 px-3 py-1 rounded-xl text-xs font-black">Стіл #${o.table}</span>
                                <span class="text-[11px] font-semibold ${badgeColor} px-2 py-0.5 rounded-lg border">${o.status}</span>
                            </div>
                            <div class="text-[10px] text-slate-500 font-bold mb-2">Час створення: ${o.time} | ${o.takeaway ? '🥡 З собою' : '🍽️ В залі'}</div>
                            <div class="space-y-1 max-h-36 overflow-y-auto hide-scroll">${itemsHtml}</div>
                            ${o.comment ? `<p class="mt-3 p-2 bg-slate-950 text-amber-400 border border-amber-950 rounded-xl text-[11px] leading-tight font-medium">💬 ${o.comment}</p>` : ''}
                        </div>
                        <div class="mt-5 pt-3 border-t border-slate-800/60 flex items-center justify-between">
                            <div><div class="text-[10px] text-slate-500 font-bold">Сума чеку</div><div class="text-base font-black text-emerald-400">${o.total} ₴</div></div>
                            <button onclick="updateOrderStatus('${o._id}', '${nextStatus}')" class="bg-slate-950 hover:bg-slate-800 text-slate-200 border border-slate-700 font-bold text-xs px-3 py-2 rounded-xl transition-all">${btnText}</button>
                        </div>
                    </div>
                `;
            }).join('');
        }

        function renderMenu() {
            const grid = document.getElementById('menu-grid');
            grid.innerHTML = allMenu.map(item => {
                const avail = item.available !== false;
                const encoded = encodeURIComponent(JSON.stringify(item));
                const img = item.image ? `<img src="${item.image}" class="w-full h-24 object-cover rounded-xl mb-2" />` : `<div class="w-full h-24 bg-slate-950 flex items-center justify-center text-xl rounded-xl mb-2">🍽️</div>`;
                return `
                    <div class="bg-slate-900 border border-slate-800 p-3 rounded-xl flex flex-col justify-between">
                        <div>
                            ${img}
                            <h4 class="font-bold text-xs text-slate-200 truncate">${item.name}</h4>
                            <div class="text-[10px] text-slate-400 font-medium mt-0.5">${item.category} | <span class="text-indigo-400 font-bold">${item.price} ₴</span></div>
                        </div>
                        <div class="mt-3 pt-2 border-t border-slate-800/60 flex items-center justify-between">
                            <label class="flex items-center gap-1.5 cursor-pointer">
                                <input type="checkbox" ${avail ? 'checked' : ''} onchange="toggleStock('${item._id}')" class="rounded bg-slate-950 border-slate-800 text-indigo-600 focus:ring-0 w-3.5 h-3.5">
                                <span class="text-[10px] font-bold ${avail ? 'text-emerald-400' : 'text-slate-500'}">Доступний</span>
                            </label>
                            <div class="flex gap-2">
                                <button onclick="openEditModal('${encoded}')" class="text-[10px] font-bold text-indigo-400 hover:underline">Ред.</button>
                                <button onclick="deleteMenuItem('${item._id}')" class="text-[10px] font-bold text-rose-500 hover:underline">Вид.</button>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
        }

        function renderReviews() {
            const grid = document.getElementById('reviews-grid');
            if(allReviews.length === 0) { grid.innerHTML = `<p class="text-slate-500 text-xs">Відгуків поки немає</p>`; return; }
            grid.innerHTML = allReviews.map(r => {
                let stars = '';
                for(let i=1; i<=5; i++) stars += i <= r.rating ? '★' : '☆';
                return `
                    <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl space-y-1">
                        <div class="flex justify-between items-center">
                            <span class="text-xs font-black bg-slate-950 border border-slate-800 px-2.5 py-1 rounded-lg">Стіл #${r.table}</span>
                            <span class="text-sm font-black text-amber-400 tracking-wider">${stars}</span>
                        </div>
                        <p class="text-xs text-slate-300 font-medium pt-1 leading-relaxed">${r.comment || '<span class="text-slate-600 italic">Без текстового коментаря</span>'}</p>
                        <div class="text-right pt-2"><button onclick="deleteReview('${r._id}')" class="text-[10px] font-bold text-rose-500 hover:underline">Видалити</button></div>
                    </div>
                `;
            }).join('');
        }

        function renderLive() {
            const grid = document.getElementById('live-grid');
            if(allLive.length === 0) { grid.innerHTML = `<p class="text-slate-500 text-xs col-span-4">У залах немає активних сесій</p>`; return; }
            grid.innerHTML = allLive.map(s => `
                <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl flex flex-col justify-between">
                    <div>
                        <div class="flex justify-between items-center mb-2">
                            <span class="bg-indigo-950 text-indigo-400 border border-indigo-900 text-[11px] font-black px-2 py-0.5 rounded-lg">Стіл #${s.table}</span>
                            <span class="text-[9px] text-slate-500">Live</span>
                        </div>
                        <p class="text-[11px] text-slate-400">Дивиться: <span class="text-slate-200 font-bold">${s.category || 'Всі'}</span></p>
                    </div>
                    <div class="mt-4 pt-2 border-t border-slate-800 flex justify-between items-center">
                        <span class="text-[10px] text-slate-500 font-bold">Кошик:</span>
                        <span class="text-xs font-black text-emerald-400">${s.cart_total || 0} ₴</span>
                    </div>
                </div>
            `).join('');
        }

        function updateOrderStatus(id, status) { socket.emit('order_update', { id: id, status: status }); }
        function toggleStock(id) { socket.emit('stock_toggle', { id: id }); }
        function deleteMenuItem(id) { if(confirm('Видалити страву з меню?')) socket.emit('menu_delete', { id: id }); }
        function deleteReview(id) { if(confirm('Видалити цей відгук?')) socket.emit('review_delete', { id: id }); }

        function openAddModal() {
            document.getElementById('modal-title').innerText = 'Створити картку страви';
            document.getElementById('item-id').value = '';
            document.getElementById('item-name').value = '';
            document.getElementById('item-category').value = 'Кава';
            document.getElementById('item-price').value = '';
            document.getElementById('item-description').value = '';
            currentImageBase64 = '';
            document.getElementById('menu-image-preview').classList.add('hidden');
            document.getElementById('menu-modal').classList.remove('hidden');
        }

        function openEditModal(encodedData) {
            const item = JSON.parse(decodeURIComponent(encodedData));
            document.getElementById('modal-title').innerText = 'Редагувати картку страви';
            document.getElementById('item-id').value = item._id;
            document.getElementById('item-name').value = item.name;
            document.getElementById('item-category').value = item.category;
            document.getElementById('item-price').value = item.price;
            document.getElementById('item-description').value = item.description || '';
            currentImageBase64 = item.image || '';
            
            const preview = document.getElementById('menu-image-preview');
            if(item.image) { preview.src = item.image; preview.classList.remove('hidden'); }
            else { preview.classList.add('hidden'); }
            
            document.getElementById('menu-modal').classList.remove('hidden');
        }

        function closeModal() { document.getElementById('menu-modal').classList.add('hidden'); }

        function encodeImageFile(e) {
            const file = e.target.files[0];
            if(!file) return;
            const r = new FileReader();
            r.onloadend = function() {
                currentImageBase64 = r.result;
                const p = document.getElementById('menu-image-preview');
                p.src = r.result; p.classList.remove('hidden');
            }
            r.readAsDataURL(file);
        }

        function saveMenuItem() {
            const name = document.getElementById('item-name').value.trim();
            const price = document.getElementById('item-price').value;
            if(!name || !price) return alert('Вкажіть назву та роздрібну вартість!');
            
            socket.emit('menu_save', {
                id: document.getElementById('item-id').value || null,
                name: name,
                category: document.getElementById('item-category').value,
                price: parseFloat(price),
                description: document.getElementById('item-description').value.trim(),
                image: currentImageBase64 || null
            });
            closeModal();
        }
    </script>
</body>
</html>
"""

# --- ШАБЛОН АВТОРИЗАЦІЇ АДМІНА ---
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Вхід в систему POS</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 flex items-center justify-center h-screen text-slate-100">
    <div class="bg-slate-900 border border-slate-800 p-8 rounded-2xl w-full max-w-sm shadow-2xl">
        <h2 class="text-xl font-black text-center mb-2 tracking-tight">Авторизація Dashboard</h2>
        <p class="text-xs text-slate-500 text-center mb-6 uppercase tracking-wider font-semibold">Адміністративна панель</p>
        <form method="POST" action="/login" class="space-y-4">
            <input type="password" name="password" placeholder="Введіть секретний токен" required class="w-full p-3.5 bg-slate-950 border border-slate-800 rounded-xl text-xs text-center font-bold tracking-widest focus:outline-none focus:border-indigo-500 text-white">
            <button type="submit" class="w-full bg-indigo-600 hover:bg-indigo-500 py-3 rounded-xl text-xs font-bold transition-all shadow-lg">Увійти в систему</button>
        </form>
    </div>
</body>
</html>
"""

# ==============================================================================
# 4. СТАНДАРТНІ HTTP МАРШРУТИ (FLASK WEB ROUTING)
# ==============================================================================
@app.route('/')
def index_redirect():
    return redirect('/1')

@app.route('/<int:table_id>')
def customer_interface(table_id):
    return render_template_string(CUSTOMER_HTML, table_id=table_id)

@app.route('/admin')
def admin_panel():
    if session.get('admin_logged'):
        return render_template_string(ADMIN_HTML)
    return render_template_string(LOGIN_HTML)

@app.route('/login', methods=['POST'])
def handle_login():
    if request.form.get('password') == ADMIN_PASSWORD:
        session['admin_logged'] = True
        return redirect('/admin')
    return redirect('/admin')

@app.route('/logout')
def handle_logout():
    session.pop('admin_logged', None)
    return redirect('/admin')

# ==============================================================================
# 5. КУР'ЄРСЬКІ КОНТРОЛЕРИ В РЕАЛЬНОМУ ЧАСІ (SOCKET.IO PIPELINE)
# ==============================================================================
@app.route('/api/health')
def health_check():
    return {"status": "healthy"}, 200

@socketio.on('client_init')
def handle_client_init(data):
    uuid = data.get('uuid')
    table = data.get('table')
    if uuid:
        join_room(uuid)
    if table:
        join_room(f"table_{table}")
    emit('menu_sync', get_all_menu())

@socketio.on('client_activity')
def handle_client_activity(data):
    sid = request.sid
    data['sid'] = sid
    data['last_seen'] = datetime.now().strftime('%H:%M:%S')
    live_sessions[sid] = data
    socketio.emit('live_sync', list(live_sessions.values()), room='admins')

@socketio.on('admin_init')
def handle_admin_init():
    join_room('admins')
    emit('menu_sync', get_all_menu())
    emit('orders_sync', get_active_orders())
    emit('reviews_sync', get_all_reviews())
    emit('live_sync', list(live_sessions.values()))

@socketio.on('order_submit')
def handle_order_submit(data):
    uuid = data.get('uuid')
    table = data.get('table')
    items = data.get('items', [])
    
    total = 0
    detailed_items = []
    for i in items:
        menu_item = db.menu.find_one({"_id": ObjectId(i['id'])})
        if menu_item:
            total += menu_item['price'] * i['qty']
            detailed_items.append({
                "id": i['id'],
                "name": menu_item['name'],
                "qty": i['qty'],
                "price": menu_item['price']
            })
            
    order_doc = {
        "uuid": uuid,
        "table": table,
        "items": detailed_items,
        "total": total,
        "status": "Нове",
        "comment": data.get('comment', '').strip(),
        "takeaway": data.get('takeaway', False),
        "timestamp": datetime.now()
    }
    
    res = db.orders.insert_one(order_doc)
    order_doc['_id'] = str(res.inserted_id)
    order_doc['timestamp'] = order_doc['timestamp'].strftime('%H:%M')
    
    socketio.emit('orders_sync', get_active_orders(), room='admins')
    return {"success": True, "order": order_doc}

@socketio.on('order_update')
def handle_order_update(data):
    order_id = data.get('id')
    new_status = data.get('status')
    
    db.orders.update_one({"_id": ObjectId(order_id)}, {"$set": {"status": new_status}})
    
    order = db.orders.find_one({"_id": ObjectId(order_id)})
    if order:
        user_uuid = order.get('uuid')
        status_messages = {
            "Готується": "Ваше замовлення вже готується шеф-кухарем! 🍳",
            "Готово": "Ваше замовлення готове й прямує до столу! ✨",
            "Закрито": "Дякуємо за візит! Замовлення успішно розраховано. 👋"
        }
        msg = status_messages.get(new_status, f"Статус замовлення оновлено на: {new_status}")
        socketio.emit('order_status_update', {"order_id": order_id, "status": new_status, "message": msg}, room=user_uuid)
        
    socketio.emit('orders_sync', get_active_orders(), room='admins')

@socketio.on('stock_toggle')
def handle_stock_toggle(data):
    item = db.menu.find_one({"_id": ObjectId(data['id'])})
    if item:
        new_state = False if item.get('available') != False else True
        db.menu.update_one({"_id": ObjectId(data['id'])}, {"$set": {"available": new_state}})
        socketio.emit('menu_sync', get_all_menu())

@socketio.on('menu_save')
def handle_menu_save(data):
    item_id = data.get('id')
    menu_data = {
        "name": data.get('name'),
        "category": data.get('category'),
        "price": float(data.get('price', 0)),
        "description": data.get('description', ''),
        "available": True
    }
    if data.get('image'):
        menu_data['image'] = data.get('image')
        
    if item_id:
        db.menu.update_one({"_id": ObjectId(item_id)}, {"$set": menu_data})
    else:
        db.menu.insert_one(menu_data)
        
    socketio.emit('menu_sync', get_all_menu())

@socketio.on('menu_delete')
def handle_menu_delete(data):
    db.menu.delete_one({"_id": ObjectId(data['id'])})
    socketio.emit('menu_sync', get_all_menu())

@socketio.on('review_submit')
def handle_review_submit(data):
    review_doc = {
        "table": data.get('table'),
        "rating": int(data.get('rating', 5)),
        "comment": data.get('comment', '').strip(),
        "timestamp": datetime.now()
    }
    db.reviews.insert_one(review_doc)
    socketio.emit('reviews_sync', get_all_reviews(), room='admins')
    return {"success": True}

@socketio.on('review_delete')
def handle_review_delete(data):
    db.reviews.delete_one({"_id": ObjectId(data['id'])})
    socketio.emit('reviews_sync', get_all_reviews(), room='admins')

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in live_sessions:
        del live_sessions[sid]
        socketio.emit('live_sync', list(live_sessions.values()), room='admins')

# ==============================================================================
# 6. СТАРТ СЕРВЕРА ТА СЕЕДИНГ (FALLBACK INJECTOR)
# ==============================================================================
if __name__ == '__main__':
    if db.menu.count_documents({}) == 0:
        db.menu.insert_many([
            {"name": "Cyber Cappuccino", "category": "Кава", "price": 65.0, "description": "Подвійний еспресо, свіже ультрапастеризоване молоко, 250мл", "available": True},
            {"name": "Neon Burger", "category": "Бургери", "price": 240.0, "description": "Крафтова булка, яловичина сухого визрівання, секретний сирний соус", "available": True},
            {"name": "Glitch Fries", "category": "Снеки", "price": 85.0, "description": "Хрустка картопля фрі у спеціях фрі-стайл з пармезановим снігом", "available": True}
        ])
    socketio.run(app, host='0.0.0.0', port=10000)
