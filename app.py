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
# Збільшуємо ліміт пакетів для передачі великих фотографій
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', max_http_buffer_size=5000000)

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/cafe_db')
ADMIN_PASSWORD = "1111"

client = MongoClient(MONGO_URI)
db = client.get_default_database(default='cafe_db')

# ==============================================================================
# 2. ДОПОМІЖНІ ФУНКЦІЇ (ЧАС ТА СЕРІАЛІЗАЦІЯ)
# ==============================================================================
def get_kyiv_time():
    # Київський час (UTC+3)
    return datetime.now(timezone.utc) + timedelta(hours=3)

def get_kyiv_time_str():
    return get_kyiv_time().strftime('%d.%m.%Y %H:%M:%S')

def get_kyiv_time_short():
    return get_kyiv_time().strftime('%H:%M')

def serialize_doc(doc):
    if not doc: return None
    d = dict(doc)
    d['_id'] = str(d['_id'])
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.strftime('%d.%m.%Y %H:%M')
    return d

def get_all_menu(): return [serialize_doc(i) for i in db.menu.find()]
def get_active_orders(): return [serialize_doc(o) for o in db.orders.find({"status": {"$ne": "Закрито"}}).sort("timestamp", -1)]
def get_all_reviews(): return [serialize_doc(r) for r in db.reviews.find().sort("timestamp", -1)]
def get_all_devices(): return [serialize_doc(d) for d in db.devices.find().sort("last_seen", -1)]

# ==============================================================================
# 3. КЛІЄНТСЬКИЙ ІНТЕРФЕЙС (MOBILE-FIRST)
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
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #09090b; color: #f4f4f5; font-family: system-ui, -apple-system, sans-serif; -webkit-tap-highlight-color: transparent; }
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
            <div class="w-10 h-10 rounded-xl bg-indigo-600 flex items-center justify-center font-black text-white shadow-lg">
                #{{ table_id }}
            </div>
            <div>
                <div class="text-[10px] text-zinc-500 uppercase tracking-wider font-bold">Номер столу</div>
                <div class="text-xs font-bold text-emerald-400 flex items-center gap-1">
                    <i class="fas fa-wifi text-[10px]"></i> Система активна
                </div>
            </div>
        </div>
        <div class="flex gap-2">
            <button onclick="callWaiter()" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-500 border border-amber-500/20 px-3 py-1.5 rounded-xl font-bold text-xs transition-all flex items-center gap-2">
                <i class="fas fa-concierge-bell"></i> Офіціант
            </button>
            <button onclick="openModal('review-modal')" class="bg-zinc-900 hover:bg-zinc-800 text-zinc-300 border border-zinc-800 px-3 py-1.5 rounded-xl font-bold text-xs transition-all flex items-center gap-2">
                <i class="fas fa-star"></i> Відгук
            </button>
        </div>
    </header>

    <div id="status-widget" class="hidden mt-24 mx-4 p-4 rounded-2xl bg-indigo-950/40 border border-indigo-800/60 items-center gap-4">
        <div class="text-2xl text-indigo-400"><i class="fas fa-fire"></i></div>
        <div>
            <div class="text-[10px] uppercase font-bold text-indigo-400 tracking-wider">Статус поточного замовлення</div>
            <div id="status-text" class="font-bold text-sm text-zinc-200">Замовлення готується...</div>
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
                <div>
                    <label class="text-[10px] text-zinc-500 font-bold uppercase tracking-wider mb-1 block">Побажання для кухні</label>
                    <input type="text" id="order-comment" placeholder="Наприклад: без цибулі, подати гарячим..." class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-sm text-zinc-200 focus:outline-none focus:border-indigo-500">
                </div>
                <label class="flex items-center gap-3 cursor-pointer bg-zinc-900 p-3 rounded-xl border border-zinc-800">
                    <input type="checkbox" id="order-takeaway" class="rounded bg-zinc-950 border-zinc-700 text-indigo-600 focus:ring-0 w-5 h-5">
                    <span class="text-sm text-zinc-300 font-bold">Замовлення з собою (на виніс)</span>
                </label>
                <div class="flex justify-between items-center py-2">
                    <span class="text-xs font-bold text-zinc-400 uppercase tracking-wider">До сплати:</span>
                    <span class="text-2xl font-black text-indigo-400"><span id="modal-cart-total">0</span> ₴</span>
                </div>
                <button onclick="submitOrder()" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white py-4 rounded-xl font-black uppercase tracking-wider text-sm shadow-lg transition-all flex items-center justify-center gap-2">
                    <i class="fas fa-paper-plane"></i> Відправити на кухню
                </button>
            </div>
        </div>
    </div>

    <div id="review-modal" class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm hidden items-center justify-center p-4">
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

    <div id="orders-modal" class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm hidden items-center justify-center p-4">
        <div class="bg-zinc-950 border border-zinc-800 p-6 rounded-2xl w-full max-w-md max-h-[80vh] flex flex-col">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-lg font-black flex items-center gap-2"><i class="fas fa-history text-indigo-500"></i> Історія замовлень</h3>
                <button onclick="closeModal('orders-modal')" class="text-zinc-500 font-bold"><i class="fas fa-times"></i></button>
            </div>
            <div id="my-orders-list" class="flex-1 overflow-y-auto space-y-3 hide-scroll"></div>
        </div>
    </div>

    <script>
        const socket = io();
        const tableId = "{{ table_id }}";
        let menuItems = [];
        let cart = {};
        let currentCategory = 'Всі';
        let selectedRating = 5;
        
        let clientUUID = localStorage.getItem('nexus_device_uuid');
        if (!clientUUID) {
            clientUUID = 'device_' + Math.random().toString(36).substr(2, 9) + '_' + Date.now();
            localStorage.setItem('nexus_device_uuid', clientUUID);
        }

        socket.on('connect', () => {
            socket.emit('client_init', { uuid: clientUUID, table: tableId, user_agent: navigator.userAgent });
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
                showToast("Дякуємо! Ваше замовлення оплачено та закрито.");
            } else {
                widget.classList.remove('hidden');
                widget.classList.add('flex');
                txt.innerText = data.message;
                showToast(`Статус оновлено: ${data.status}`);
            }
            if(!document.getElementById('orders-modal').classList.contains('hidden')) loadMyOrders();
        });

        function renderCategories() {
            const bar = document.getElementById('category-bar');
            const cats = ['Всі', ...new Set(menuItems.map(i => i.category))];
            bar.innerHTML = cats.map(cat => {
                const active = currentCategory === cat;
                return `<button onclick="setCategory('${cat}')" class="px-4 py-2 rounded-xl whitespace-nowrap font-bold text-xs transition-all ${active ? 'bg-indigo-600 text-white shadow-lg border border-indigo-500' : 'bg-zinc-900 text-zinc-400 border border-zinc-800'}" >${cat}</button>`;
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
                grid.innerHTML = `<div class="col-span-2 text-center text-zinc-500 py-10 text-sm font-bold"><i class="fas fa-box-open text-3xl mb-2 block"></i>Позицій не знайдено</div>`;
                return;
            }

            grid.innerHTML = filtered.map(item => {
                const avail = item.available !== false;
                const img = item.image ? `<img src="${item.image}" class="w-full h-32 object-cover rounded-xl mb-2 border border-zinc-800/50" />` : `<div class="w-full h-32 bg-zinc-900 flex items-center justify-center text-3xl rounded-xl mb-2 border border-zinc-800"><i class="fas fa-image text-zinc-700"></i></div>`;
                return `
                    <div class="glass-card rounded-2xl p-2.5 flex flex-col justify-between ${!avail ? 'opacity-40 grayscale' : ''}">
                        <div>
                            ${img}
                            <h3 class="font-bold text-sm text-zinc-100 line-clamp-1">${item.name}</h3>
                            <p class="text-[10px] text-zinc-400 line-clamp-2 mt-1 leading-relaxed">${item.description || ''}</p>
                        </div>
                        <div class="mt-3 flex items-center justify-between border-t border-zinc-800 pt-2">
                            <span class="text-sm font-black text-indigo-400">${item.price} ₴</span>
                            ${avail ? `<button onclick="addToCart('${item._id}')" class="bg-indigo-600 w-8 h-8 rounded-lg font-black text-white flex items-center justify-center active:scale-95 transition-all shadow-md"><i class="fas fa-plus text-xs"></i></button>` : `<span class="text-[9px] bg-zinc-800 text-zinc-400 px-2 py-1 rounded font-bold uppercase tracking-wider">Немає</span>`}
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
                if(item && item.available !== false) {
                    totalCount += cart[id];
                    totalPrice += item.price * cart[id];
                    html += `
                        <div class="flex items-center justify-between bg-zinc-900/80 p-3 rounded-xl border border-zinc-800">
                            <div class="flex-1 min-w-0 pr-2">
                                <h4 class="font-bold text-sm text-zinc-200 truncate">${item.name}</h4>
                                <p class="text-[11px] text-indigo-400 font-bold mt-0.5">${item.price} ₴</p>
                            </div>
                            <div class="flex items-center gap-3 bg-zinc-950 px-2 py-1.5 rounded-xl border border-zinc-800">
                                <button onclick="changeQty('${id}', -1)" class="text-zinc-400 hover:text-white font-black px-1.5 transition"><i class="fas fa-minus text-[10px]"></i></button>
                                <span class="font-black text-sm text-zinc-100 min-w-[16px] text-center">${cart[id]}</span>
                                <button onclick="changeQty('${id}', 1)" class="text-zinc-400 hover:text-white font-black px-1.5 transition"><i class="fas fa-plus text-[10px]"></i></button>
                            </div>
                        </div>
                    `;
                } else {
                    delete cart[id]; // Видаляємо, якщо зникло зі складу
                }
            });

            if(list) list.innerHTML = html || '<div class="text-center text-zinc-600 py-10 text-sm font-bold"><i class="fas fa-shopping-cart text-3xl mb-3 block"></i>Кошик порожній</div>';
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
            socket.emit('client_activity', { uuid: clientUUID, table: tableId, category: currentCategory, cart_count: count, cart_total: total, ua: navigator.userAgent });
        }

        function submitOrder() {
            const items = Object.keys(cart).map(id => ({ id: id, qty: cart[id] }));
            if(items.length === 0) return;
            
            socket.emit('order_submit', {
                uuid: clientUUID, table: tableId, items: items,
                comment: document.getElementById('order-comment').value,
                takeaway: document.getElementById('order-takeaway').checked
            }, (res) => {
                if(res && res.success) {
                    cart = {};
                    updateCartUI();
                    sendLiveActivity();
                    closeModal('cart-modal');
                    document.getElementById('order-comment').value = '';
                    document.getElementById('order-takeaway').checked = false;
                    showToast("Замовлення успішно відправлено на кухню!");
                }
            });
        }

        function loadMyOrders() {
            socket.emit('get_client_orders', { uuid: clientUUID }, (orders) => {
                const container = document.getElementById('my-orders-list');
                if(!orders.length) {
                    container.innerHTML = `<div class="text-center text-zinc-500 py-8 text-sm font-bold"><i class="fas fa-folder-open text-3xl mb-2 block"></i>Історія порожня</div>`;
                    return;
                }
                container.innerHTML = orders.map(o => {
                    let badge = o.status === 'Нове' ? 'bg-blue-500/10 text-blue-400 border-blue-500/30' : (o.status === 'Готується' ? 'bg-amber-500/10 text-amber-400 border-amber-500/30' : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30');
                    return `
                    <div class="bg-zinc-900 border border-zinc-800 p-4 rounded-xl space-y-2">
                        <div class="flex justify-between items-center border-b border-zinc-800 pb-2">
                            <span class="text-xs text-zinc-400 font-bold"><i class="far fa-clock"></i> ${o.time} ${o.takeaway ? '| <i class="fas fa-shopping-bag"></i> З собою' : ''}</span>
                            <span class="px-2 py-0.5 text-[10px] font-black border uppercase rounded ${badge}">${o.status}</span>
                        </div>
                        <div class="space-y-1">
                            ${o.items.map(i => `<div class="flex justify-between text-xs text-zinc-300"><span>${i.name}</span> <span class="font-bold">x${i.qty}</span></div>`).join('')}
                        </div>
                        <div class="flex justify-between items-end pt-2">
                            <span class="text-[10px] text-zinc-500 uppercase font-bold tracking-wider">Сума</span>
                            <span class="text-lg font-black text-white">${o.total} ₴</span>
                        </div>
                    </div>`;
                }).join('');
            });
        }

        function callWaiter() {
            socket.emit('call_waiter', { table: tableId, uuid: clientUUID });
            showToast("Офіціант вже прямує до вашого столу!");
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
                const star = document.createElement('i');
                star.className = `fas fa-star cursor-pointer transition-colors ${i <= selectedRating ? 'text-amber-400' : 'text-zinc-700'}`;
                star.onclick = () => { selectedRating = i; renderStars(); };
                container.appendChild(star);
            }
        }

        function submitReview() {
            socket.emit('review_submit', {
                table: tableId, rating: selectedRating,
                comment: document.getElementById('review-comment').value.trim(), uuid: clientUUID
            }, (res) => {
                if(res && res.success) {
                    closeModal('review-modal');
                    showToast("Дякуємо за ваш відгук! Це робить нас кращими.");
                }
            });
        }

        function openModal(id) { document.getElementById(id).classList.remove('hidden'); document.getElementById(id).classList.add('flex'); if(id === 'orders-modal') loadMyOrders(); }
        function closeModal(id) { document.getElementById(id).classList.add('hidden'); document.getElementById(id).classList.remove('flex'); }
        
        function showToast(text) {
            const box = document.getElementById('toast-box');
            document.getElementById('toast-text').innerText = text;
            box.classList.remove('hidden');
            setTimeout(() => box.classList.add('hidden'), 4000);
        }
    </script>
</body>
</html>
"""

# ==============================================================================
# 4. АДМІНІСТРАТИВНИЙ ІНТЕРФЕЙС (REAL-TIME POS & CONTROL PANEL)
# ==============================================================================
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NEXUS CONTROL STATION</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #020617; color: #f8fafc; font-family: system-ui, sans-serif; }
        .hide-scroll::-webkit-scrollbar { width: 6px; }
        .hide-scroll::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 4px; }
        .hide-scroll::-webkit-scrollbar-track { background: transparent; }
    </style>
</head>
<body class="flex h-screen overflow-hidden">

    <div id="toast-admin" class="fixed bottom-6 right-6 z-[100] hidden bg-indigo-600 text-white px-5 py-3 rounded-xl shadow-2xl flex items-center gap-3 border border-indigo-500 font-bold text-sm"></div>

    <aside class="w-64 bg-slate-900 border-r border-slate-800 p-5 flex flex-col justify-between z-20 shadow-2xl relative">
        <div>
            <div class="mb-8 flex items-center gap-3 border-b border-slate-800 pb-4">
                <div class="w-10 h-10 bg-indigo-600 rounded-lg flex items-center justify-center text-white text-xl shadow-lg shadow-indigo-500/20"><i class="fas fa-server"></i></div>
                <div>
                    <h2 class="text-base font-black text-indigo-400 tracking-wider">NEXUS POS</h2>
                    <p class="text-[9px] text-emerald-400 uppercase font-black tracking-widest flex items-center gap-1"><span class="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse"></span> Online</p>
                </div>
            </div>
            <nav class="space-y-1.5">
                <button onclick="switchTab('orders')" id="btn-orders" class="w-full text-left px-4 py-3 rounded-xl text-xs font-bold bg-indigo-600 text-white transition-all shadow-md flex items-center gap-3"><i class="fas fa-utensils w-4"></i> Панель Кухні</button>
                <button onclick="switchTab('menu')" id="btn-menu" class="w-full text-left px-4 py-3 rounded-xl text-xs font-bold text-slate-400 hover:bg-slate-800 hover:text-white transition-all flex items-center gap-3"><i class="fas fa-box-open w-4"></i> База та Склад</button>
                <button onclick="switchTab('devices')" id="btn-devices" class="w-full text-left px-4 py-3 rounded-xl text-xs font-bold text-slate-400 hover:bg-slate-800 hover:text-white transition-all flex items-center gap-3"><i class="fas fa-mobile-alt w-4"></i> Пристрої</button>
                <button onclick="switchTab('reviews')" id="btn-reviews" class="w-full text-left px-4 py-3 rounded-xl text-xs font-bold text-slate-400 hover:bg-slate-800 hover:text-white transition-all flex items-center gap-3"><i class="fas fa-star w-4"></i> Відгуки</button>
                <button onclick="switchTab('settings')" id="btn-settings" class="w-full text-left px-4 py-3 rounded-xl text-xs font-bold text-slate-400 hover:bg-slate-800 hover:text-white transition-all flex items-center gap-3"><i class="fas fa-cog w-4"></i> Налаштування БД</button>
            </nav>
        </div>
        <a href="/logout" class="text-xs font-bold text-rose-500 hover:bg-rose-500/10 p-3 rounded-xl transition-all border border-transparent hover:border-rose-500/20 flex items-center gap-2"><i class="fas fa-sign-out-alt"></i> Вийти з системи</a>
    </aside>

    <main class="flex-1 overflow-y-auto bg-[#020617] p-8 hide-scroll relative">
        
        <div id="tab-orders" class="tab-content space-y-6">
            <h1 class="text-2xl font-black tracking-tight border-b border-slate-800 pb-3 text-slate-100 flex items-center gap-2"><i class="fas fa-fire text-indigo-500"></i> Активні Замовлення</h1>
            <div class="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6" id="orders-grid"></div>
        </div>

        <div id="tab-menu" class="tab-content hidden space-y-6">
            <div class="flex justify-between items-center border-b border-slate-800 pb-3">
                <h1 class="text-2xl font-black tracking-tight text-slate-100 flex items-center gap-2"><i class="fas fa-box text-indigo-500"></i> Номенклатура</h1>
                <button onclick="openAddModal()" class="bg-indigo-600 hover:bg-indigo-500 px-5 py-2.5 rounded-xl text-xs font-bold shadow-lg transition-all flex items-center gap-2"><i class="fas fa-plus"></i> Створити картку</button>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-5" id="menu-grid"></div>
        </div>

        <div id="tab-devices" class="tab-content hidden space-y-6">
            <h1 class="text-2xl font-black tracking-tight border-b border-slate-800 pb-3 text-slate-100 flex items-center gap-2"><i class="fas fa-network-wired text-indigo-500"></i> Аналітика Пристроїв</h1>
            <div class="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden">
                <table class="w-full text-left text-xs">
                    <thead class="bg-slate-950 text-slate-400 border-b border-slate-800 uppercase tracking-wider font-bold">
                        <tr><th class="p-4">Пристрій / UUID</th><th class="p-4">IP Адреса</th><th class="p-4">Стіл</th><th class="p-4">Остання активність</th><th class="p-4">Кошик</th></tr>
                    </thead>
                    <tbody id="devices-table" class="divide-y divide-slate-800"></tbody>
                </table>
            </div>
        </div>

        <div id="tab-reviews" class="tab-content hidden space-y-6">
            <h1 class="text-2xl font-black tracking-tight border-b border-slate-800 pb-3 text-slate-100 flex items-center gap-2"><i class="fas fa-comments text-indigo-500"></i> Відгуки клієнтів</h1>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5" id="reviews-grid"></div>
        </div>

        <div id="tab-settings" class="tab-content hidden space-y-6 max-w-2xl">
            <h1 class="text-2xl font-black tracking-tight border-b border-slate-800 pb-3 text-slate-100 flex items-center gap-2"><i class="fas fa-database text-indigo-500"></i> Управління Даними</h1>
            
            <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-5">
                <div class="flex items-center justify-between border-b border-slate-800 pb-5">
                    <div>
                        <h3 class="font-bold text-sm text-slate-200 mb-1">Експорт Бази Даних</h3>
                        <p class="text-xs text-slate-500">Завантажити поточний стан бази у форматі JSON (бекап).</p>
                    </div>
                    <a href="/api/export" target="_blank" class="bg-indigo-600 hover:bg-indigo-500 text-white px-5 py-2.5 rounded-xl font-bold text-xs shadow-md transition-all flex items-center gap-2"><i class="fas fa-download"></i> Скачати</a>
                </div>
                
                <div class="flex items-center justify-between border-b border-slate-800 pb-5">
                    <div>
                        <h3 class="font-bold text-sm text-slate-200 mb-1">Імпорт Бази Даних</h3>
                        <p class="text-xs text-slate-500">Завантажити бекап (JSON). <span class="text-rose-400 font-bold">Обережно, старі дані будуть перезаписані!</span></p>
                    </div>
                    <div>
                        <input type="file" id="import-file" accept=".json" class="hidden" onchange="importDB(event)">
                        <button onclick="document.getElementById('import-file').click()" class="bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 px-5 py-2.5 rounded-xl font-bold text-xs transition-all flex items-center gap-2"><i class="fas fa-upload"></i> Завантажити</button>
                    </div>
                </div>

                <div class="flex items-center justify-between pt-2">
                    <div>
                        <h3 class="font-bold text-sm text-rose-500 mb-1">Критична Зона</h3>
                        <p class="text-xs text-slate-500">Повне очищення всіх колекцій бази даних (крім пристроїв).</p>
                    </div>
                    <button onclick="clearDB()" class="bg-rose-600/10 hover:bg-rose-600 border border-rose-600 text-rose-500 hover:text-white px-5 py-2.5 rounded-xl font-black uppercase tracking-wider text-xs transition-all flex items-center gap-2"><i class="fas fa-trash-alt"></i> Знищити дані</button>
                </div>
            </div>
        </div>
    </main>

    <div id="menu-modal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 hidden items-center justify-center p-4">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-md p-6 shadow-2xl">
            <div class="flex justify-between items-center mb-4 border-b border-slate-800 pb-3">
                <h3 id="modal-title" class="text-lg font-black text-slate-100 flex items-center gap-2"><i class="fas fa-edit text-indigo-500"></i> Редагувати</h3>
                <button onclick="closeModal()" class="text-slate-500 hover:text-white"><i class="fas fa-times"></i></button>
            </div>
            <input type="hidden" id="item-id">
            
            <div class="space-y-4">
                <div>
                    <label class="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1 block">Назва</label>
                    <input type="text" id="item-name" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-indigo-500">
                </div>
                <div class="grid grid-cols-2 gap-4">
                    <div>
                        <label class="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1 block">Категорія</label>
                        <input type="text" id="item-category" placeholder="Кава, Бургери..." class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-indigo-500">
                    </div>
                    <div>
                        <label class="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1 block">Ціна (₴)</label>
                        <input type="number" id="item-price" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-indigo-500">
                    </div>
                </div>
                <div>
                    <label class="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1 block">Опис</label>
                    <textarea id="item-description" rows="2" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm text-white focus:outline-none resize-none focus:border-indigo-500"></textarea>
                </div>
                <div>
                    <label class="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1 block">Зображення (Авто-стиснення)</label>
                    <input type="file" accept="image/*" onchange="encodeImageFile(event)" class="w-full text-xs text-slate-400 bg-slate-950 p-2 border border-slate-800 rounded-xl cursor-pointer file:mr-4 file:py-1.5 file:px-4 file:rounded-lg file:border-0 file:text-xs file:font-bold file:bg-indigo-600 file:text-white hover:file:bg-indigo-500">
                    <img id="menu-image-preview" src="" class="hidden w-full h-32 object-cover rounded-xl mt-3 border border-slate-700">
                </div>
            </div>
            <button onclick="saveMenuItem()" class="w-full mt-6 bg-indigo-600 hover:bg-indigo-500 py-3.5 rounded-xl text-sm font-black text-white transition-all shadow-lg flex items-center justify-center gap-2"><i class="fas fa-save"></i> Зберегти в базу</button>
        </div>
    </div>

    <script>
        const socket = io();
        let allMenu = [], allOrders = [], allReviews = [], allDevices = [];
        let currentImageBase64 = '';

        socket.on('connect', () => { socket.emit('admin_init'); });
        socket.on('menu_sync', data => { allMenu = data; renderMenu(); });
        socket.on('orders_sync', data => { allOrders = data; renderOrders(); });
        socket.on('reviews_sync', data => { allReviews = data; renderReviews(); });
        socket.on('devices_sync', data => { allDevices = data; renderDevices(); });
        
        socket.on('waiter_alert', data => {
            showToast(`<i class="fas fa-bell animate-bounce text-amber-400"></i> Столик #${data.table} викликає офіціанта!`);
        });

        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.getElementById('tab-' + tabId).classList.remove('hidden');
            document.querySelectorAll('nav button').forEach(b => {
                b.classList.remove('bg-indigo-600', 'text-white', 'shadow-md');
                b.classList.add('text-slate-400');
            });
            const btn = document.getElementById('btn-' + tabId);
            btn.classList.add('bg-indigo-600', 'text-white', 'shadow-md');
            btn.classList.remove('text-slate-400');
        }

        function renderOrders() {
            const grid = document.getElementById('orders-grid');
            if(allOrders.length === 0) { grid.innerHTML = `<p class="text-slate-500 text-sm font-bold"><i class="fas fa-inbox text-2xl block mb-2 opacity-50"></i>Немає активних замовлень</p>`; return; }
            grid.innerHTML = allOrders.map(o => {
                let badgeColor = o.status === 'Нове' ? 'bg-blue-900/50 text-blue-400 border-blue-500/30' : 'bg-amber-900/50 text-amber-400 border-amber-500/30';
                let btnText = o.status === 'Нове' ? '<i class="fas fa-fire mr-1"></i> Почати готувати' : '<i class="fas fa-check-double mr-1"></i> На видачу';
                let btnClass = o.status === 'Нове' ? 'bg-blue-600 hover:bg-blue-500' : 'bg-amber-500 hover:bg-amber-400 text-gray-900';
                if (o.status === 'Готово') { 
                    badgeColor = 'bg-emerald-900/50 text-emerald-400 border-emerald-500/30'; 
                    btnText = '<i class="fas fa-check-circle mr-1"></i> Закрити чек'; 
                    btnClass = 'bg-emerald-600 hover:bg-emerald-500 text-white';
                }
                
                const nextStatus = o.status === 'Нове' ? 'Готується' : (o.status === 'Готується' ? 'Готово' : 'Закрито');
                const itemsHtml = o.items.map(i => `<div class="flex justify-between text-sm py-1 border-b border-slate-800/40 text-slate-300"><span>${i.name}</span><span class="font-black text-indigo-400">x${i.qty}</span></div>`).join('');
                
                return `
                    <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 flex flex-col justify-between shadow-xl relative overflow-hidden group">
                        <div class="absolute top-0 left-0 w-1 h-full ${o.status === 'Нове' ? 'bg-blue-500' : (o.status === 'Готується' ? 'bg-amber-500' : 'bg-emerald-500')}"></div>
                        <div>
                            <div class="flex justify-between items-center mb-4 pl-3">
                                <div>
                                    <span class="text-xl font-black text-slate-100">Стіл #${o.table}</span>
                                    <p class="text-[10px] text-slate-500 font-bold uppercase tracking-wider mt-1"><i class="far fa-clock"></i> ${o.time} ${o.takeaway ? '• <span class="text-rose-400"><i class="fas fa-shopping-bag"></i> З собою</span>' : ''}</p>
                                </div>
                                <span class="text-xs font-bold ${badgeColor} px-3 py-1 rounded-lg border uppercase tracking-wider">${o.status}</span>
                            </div>
                            <div class="space-y-1 mb-4 bg-slate-950/50 p-3 rounded-xl border border-slate-800">${itemsHtml}</div>
                            ${o.comment ? `<p class="mb-4 p-3 bg-amber-500/10 border border-amber-500/20 text-amber-400 rounded-xl text-xs font-medium"><i class="fas fa-comment-alt mr-1"></i> ${o.comment}</p>` : ''}
                        </div>
                        <div class="pt-4 border-t border-slate-800 flex items-center justify-between pl-3">
                            <div><div class="text-[10px] text-slate-500 font-bold uppercase">Сума чеку</div><div class="text-xl font-black text-emerald-400">${o.total} ₴</div></div>
                            <button onclick="updateOrderStatus('${o._id}', '${nextStatus}')" class="${btnClass} font-bold text-xs px-4 py-2.5 rounded-xl transition-all shadow-lg">${btnText}</button>
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
                const img = item.image ? `<img src="${item.image}" class="w-full h-32 object-cover rounded-xl mb-3 border border-slate-800" />` : `<div class="w-full h-32 bg-slate-950 flex items-center justify-center text-3xl rounded-xl mb-3 border border-slate-800"><i class="fas fa-image text-slate-800"></i></div>`;
                return `
                    <div class="bg-slate-900 border border-slate-800 p-4 rounded-2xl flex flex-col justify-between ${!avail ? 'opacity-60 grayscale' : ''}">
                        <div>
                            ${img}
                            <div class="flex justify-between items-start mb-1">
                                <h4 class="font-bold text-sm text-slate-200 line-clamp-1 pr-2">${item.name}</h4>
                                <span class="text-sm font-black text-indigo-400">${item.price}₴</span>
                            </div>
                            <p class="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-2 bg-slate-950 inline-block px-2 py-0.5 rounded border border-slate-800">${item.category}</p>
                        </div>
                        <div class="mt-4 pt-3 border-t border-slate-800 flex items-center justify-between">
                            <label class="flex items-center gap-2 cursor-pointer bg-slate-950 px-2 py-1 rounded-lg border border-slate-800">
                                <input type="checkbox" ${avail ? 'checked' : ''} onchange="toggleStock('${item._id}')" class="rounded bg-slate-900 border-slate-700 text-indigo-600 focus:ring-0 w-3 h-3">
                                <span class="text-[10px] font-bold ${avail ? 'text-emerald-400' : 'text-slate-500'} uppercase">На складі</span>
                            </label>
                            <div class="flex gap-2">
                                <button onclick="openEditModal('${encoded}')" class="w-7 h-7 bg-indigo-500/10 text-indigo-400 rounded-lg flex items-center justify-center hover:bg-indigo-500/20 transition"><i class="fas fa-pen text-[10px]"></i></button>
                                <button onclick="deleteMenuItem('${item._id}')" class="w-7 h-7 bg-rose-500/10 text-rose-500 rounded-lg flex items-center justify-center hover:bg-rose-500/20 transition"><i class="fas fa-trash text-[10px]"></i></button>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
        }

        function renderDevices() {
            const tbody = document.getElementById('devices-table');
            if(allDevices.length === 0) { tbody.innerHTML = `<tr><td colspan="5" class="p-6 text-center text-slate-500 text-sm font-bold">Немає підключених пристроїв</td></tr>`; return; }
            tbody.innerHTML = allDevices.map(d => {
                const osIcon = d.user_agent.toLowerCase().includes('iphone') || d.user_agent.toLowerCase().includes('mac') ? '<i class="fab fa-apple text-slate-300"></i>' : (d.user_agent.toLowerCase().includes('android') ? '<i class="fab fa-android text-emerald-400"></i>' : '<i class="fas fa-desktop text-blue-400"></i>');
                const isOnline = (new Date() - new Date(d.last_seen.replace(/(\d+).(\d+).(\d+) (\d+):(\d+):(\d+)/, '$3-$2-$1T$4:$5:$6'))) < 300000; // 5 min
                return `
                    <tr class="hover:bg-slate-800/50 transition-colors">
                        <td class="p-4 border-b border-slate-800">
                            <div class="flex items-center gap-3">
                                <div class="w-8 h-8 rounded-full bg-slate-950 border border-slate-800 flex items-center justify-center text-lg">${osIcon}</div>
                                <div>
                                    <div class="text-xs font-bold text-slate-200">${d.uuid.substring(0,12)}...</div>
                                    <div class="text-[9px] text-slate-500 max-w-[150px] truncate" title="${d.user_agent}">${d.user_agent}</div>
                                </div>
                            </div>
                        </td>
                        <td class="p-4 border-b border-slate-800 text-xs font-mono text-indigo-400">${d.ip || 'Local'}</td>
                        <td class="p-4 border-b border-slate-800"><span class="bg-slate-950 border border-slate-800 px-2 py-1 rounded text-xs font-black">#${d.table}</span></td>
                        <td class="p-4 border-b border-slate-800">
                            <div class="text-xs font-bold text-slate-300 flex items-center gap-2">
                                <span class="w-1.5 h-1.5 rounded-full ${isOnline ? 'bg-emerald-500 animate-pulse' : 'bg-slate-600'}"></span> ${d.last_seen}
                            </div>
                        </td>
                        <td class="p-4 border-b border-slate-800">
                            <div class="text-xs text-slate-400">Розділ: <span class="text-white font-bold">${d.last_category || '-'}</span></div>
                            <div class="text-[10px] text-emerald-400 font-bold mt-0.5">В кошику: ${d.cart_total || 0} ₴</div>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        function renderReviews() {
            const grid = document.getElementById('reviews-grid');
            if(allReviews.length === 0) { grid.innerHTML = `<p class="text-slate-500 text-sm font-bold"><i class="fas fa-comment-slash text-2xl block mb-2 opacity-50"></i>Відгуків поки немає</p>`; return; }
            grid.innerHTML = allReviews.map(r => {
                let stars = '';
                for(let i=1; i<=5; i++) stars += `<i class="${i <= r.rating ? 'fas text-amber-400' : 'far text-slate-700'} fa-star"></i> `;
                return `
                    <div class="bg-slate-900 border border-slate-800 p-5 rounded-2xl flex flex-col justify-between">
                        <div>
                            <div class="flex justify-between items-center mb-3">
                                <span class="text-xs font-black bg-indigo-950 text-indigo-400 border border-indigo-900 px-3 py-1 rounded-lg">Стіл #${r.table}</span>
                                <span class="text-[10px] text-slate-500 font-bold"><i class="far fa-clock"></i> ${r.time}</span>
                            </div>
                            <div class="mb-3 text-sm">${stars}</div>
                            <p class="text-sm text-slate-300 font-medium leading-relaxed bg-slate-950/50 p-3 rounded-xl border border-slate-800">${r.comment || '<span class="text-slate-600 italic">Відвідувач не залишив коментаря</span>'}</p>
                        </div>
                        <div class="mt-4 pt-3 border-t border-slate-800 flex justify-end">
                            <button onclick="deleteReview('${r._id}')" class="text-xs font-bold text-rose-500 bg-rose-500/10 px-3 py-1.5 rounded-lg border border-rose-500/20 hover:bg-rose-500 hover:text-white transition-all"><i class="fas fa-trash-alt mr-1"></i> Видалити</button>
                        </div>
                    </div>
                `;
            }).join('');
        }

        function updateOrderStatus(id, status) { socket.emit('order_update', { id: id, status: status }); }
        function toggleStock(id) { socket.emit('stock_toggle', { id: id }); }
        function deleteMenuItem(id) { if(confirm('Видалити страву з бази назавжди?')) socket.emit('menu_delete', { id: id }); }
        function deleteReview(id) { if(confirm('Видалити відгук?')) socket.emit('review_delete', { id: id }); }

        function openAddModal() {
            document.getElementById('modal-title').innerHTML = '<i class="fas fa-plus text-indigo-500"></i> Створити позицію';
            document.getElementById('item-id').value = '';
            document.getElementById('item-name').value = '';
            document.getElementById('item-category').value = '';
            document.getElementById('item-price').value = '';
            document.getElementById('item-description').value = '';
            currentImageBase64 = '';
            document.getElementById('menu-image-preview').classList.add('hidden');
            document.getElementById('menu-modal').classList.remove('hidden');
            document.getElementById('menu-modal').classList.add('flex');
        }

        function openEditModal(encodedData) {
            const item = JSON.parse(decodeURIComponent(encodedData));
            document.getElementById('modal-title').innerHTML = '<i class="fas fa-edit text-indigo-500"></i> Редагувати картку';
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
            document.getElementById('menu-modal').classList.add('flex');
        }

        function closeModal() { 
            document.getElementById('menu-modal').classList.add('hidden'); 
            document.getElementById('menu-modal').classList.remove('flex'); 
        }

        // КЛІЄНТСЬКЕ СТИСНЕННЯ ЗОБРАЖЕНЬ (Щоб не впали WebSockets)
        function encodeImageFile(e) {
            const file = e.target.files[0];
            if(!file) return;
            const reader = new FileReader();
            reader.onload = function(event) {
                const img = new Image();
                img.onload = function() {
                    const canvas = document.createElement('canvas');
                    const MAX_WIDTH = 600; // Стискаємо до 600px по ширині
                    let width = img.width;
                    let height = img.height;
                    
                    if (width > MAX_WIDTH) {
                        height *= MAX_WIDTH / width;
                        width = MAX_WIDTH;
                    }
                    
                    canvas.width = width;
                    canvas.height = height;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0, width, height);
                    
                    // Конвертуємо в легкий JPEG
                    currentImageBase64 = canvas.toDataURL('image/jpeg', 0.8);
                    const p = document.getElementById('menu-image-preview');
                    p.src = currentImageBase64; 
                    p.classList.remove('hidden');
                };
                img.src = event.target.result;
            };
            reader.readAsDataURL(file);
        }

        function saveMenuItem() {
            const name = document.getElementById('item-name').value.trim();
            const price = document.getElementById('item-price').value;
            const category = document.getElementById('item-category').value.trim();
            if(!name || !price || !category) return alert('Заповніть назву, категорію та ціну!');
            
            socket.emit('menu_save', {
                id: document.getElementById('item-id').value || null,
                name: name,
                category: category,
                price: parseFloat(price),
                description: document.getElementById('item-description').value.trim(),
                image: currentImageBase64 || null
            });
            closeModal();
            showToast("Зміни успішно збережено в Базу Даних!");
        }

        // РОБОТА З БАЗОЮ (Import/Export/Clear)
        function clearDB() {
            if(confirm('УВАГА! Ви дійсно хочете видалити всі страви, чеки та відгуки? Цю дію неможливо скасувати!')) {
                socket.emit('admin_clear_db');
                showToast("Базу даних повністю очищено.");
            }
        }

        function importDB(e) {
            const file = e.target.files[0];
            if(!file) return;
            const reader = new FileReader();
            reader.onload = function(event) {
                try {
                    const data = JSON.parse(event.target.result);
                    if(confirm("Перезаписати поточну базу даних даними з файлу?")) {
                        socket.emit('admin_import_db', data);
                        showToast("Дані успішно імпортовано!");
                    }
                } catch(err) {
                    alert("Помилка читання JSON файлу!");
                }
            };
            reader.readAsText(file);
            e.target.value = ''; // Скидаємо input
        }

        function showToast(html) {
            const box = document.getElementById('toast-admin');
            box.innerHTML = html;
            box.classList.remove('hidden');
            setTimeout(() => box.classList.add('hidden'), 4000);
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
    <title>Вхід в NEXUS POS</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
</head>
<body class="bg-slate-950 flex items-center justify-center h-screen text-slate-100">
    <div class="bg-slate-900 border border-slate-800 p-10 rounded-3xl w-full max-w-sm shadow-[0_0_50px_rgba(79,70,229,0.1)]">
        <div class="flex justify-center mb-4"><div class="w-16 h-16 bg-indigo-600 rounded-2xl flex items-center justify-center text-3xl shadow-lg shadow-indigo-500/30"><i class="fas fa-lock"></i></div></div>
        <h2 class="text-2xl font-black text-center mb-1 tracking-tight">NEXUS SECURE</h2>
        <p class="text-xs text-slate-500 text-center mb-8 uppercase tracking-wider font-bold">Система Управління</p>
        <form method="POST" action="/login" class="space-y-4">
            <input type="password" name="password" placeholder="Секретний PIN" required class="w-full p-4 bg-slate-950 border border-slate-800 rounded-2xl text-center text-lg font-black tracking-[0.5em] focus:outline-none focus:border-indigo-500 text-white placeholder-slate-700 transition">
            <button type="submit" class="w-full bg-indigo-600 hover:bg-indigo-500 py-4 rounded-2xl text-sm font-black uppercase tracking-wider transition-all shadow-lg flex justify-center items-center gap-2"><i class="fas fa-sign-in-alt"></i> Авторизація</button>
        </form>
    </div>
</body>
</html>
"""

# ==============================================================================
# 5. СТАНДАРТНІ HTTP МАРШРУТИ (FLASK WEB ROUTING)
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

@app.route('/logout')
def handle_logout():
    session.pop('admin_logged', None)
    return redirect('/admin')

@app.route('/api/export')
def api_export():
    if not session.get('admin_logged'):
        return jsonify({"error": "Unauthorized"}), 401
    
    data = {
        "menu": get_all_menu(),
        "orders": [serialize_doc(o) for o in db.orders.find()],
        "reviews": get_all_reviews(),
        "devices": get_all_devices()
    }
    return jsonify(data)

# ==============================================================================
# 6. КОНТРОЛЕРИ В РЕАЛЬНОМУ ЧАСІ (SOCKET.IO)
# ==============================================================================
@socketio.on('client_init')
def handle_client_init(data):
    uuid = data.get('uuid')
    table = data.get('table')
    user_agent = data.get('user_agent', 'Unknown')
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip: ip = ip.split(',')[0]
    
    if uuid:
        join_room(uuid)
        # Реєструємо пристрій в базі
        db.devices.update_one(
            {"uuid": uuid},
            {"$set": {
                "ip": ip,
                "user_agent": user_agent,
                "table": table,
                "last_seen": get_kyiv_time_str()
            }},
            upsert=True
        )
        socketio.emit('devices_sync', get_all_devices(), room='admins')

    emit('menu_sync', get_all_menu())

@socketio.on('client_activity')
def handle_client_activity(data):
    uuid = data.get('uuid')
    if uuid:
        db.devices.update_one(
            {"uuid": uuid},
            {"$set": {
                "last_category": data.get('category'),
                "cart_total": data.get('cart_total', 0),
                "last_seen": get_kyiv_time_str()
            }}
        )
        socketio.emit('devices_sync', get_all_devices(), room='admins')

@socketio.on('admin_init')
def handle_admin_init():
    if session.get('admin_logged'):
        join_room('admins')
        emit('menu_sync', get_all_menu())
        emit('orders_sync', get_active_orders())
        emit('reviews_sync', get_all_reviews())
        emit('devices_sync', get_all_devices())

@socketio.on('order_submit')
def handle_order_submit(data):
    uuid = data.get('uuid')
    items = data.get('items', [])
    
    total = 0
    detailed_items = []
    for i in items:
        menu_item = db.menu.find_one({"_id": ObjectId(i['id'])})
        if menu_item:
            total += menu_item['price'] * i['qty']
            detailed_items.append({
                "name": menu_item['name'],
                "qty": i['qty']
            })
            
    order_doc = {
        "uuid": uuid,
        "table": data.get('table', '1'),
        "items": detailed_items,
        "total": total,
        "status": "Нове",
        "comment": data.get('comment', '').strip(),
        "takeaway": data.get('takeaway', False),
        "timestamp": get_kyiv_time(),
        "time": get_kyiv_time_short()
    }
    
    db.orders.insert_one(order_doc)
    socketio.emit('orders_sync', get_active_orders(), room='admins')
    return {"success": True}

@socketio.on('order_update')
def handle_order_update(data):
    if session.get('admin_logged'):
        order_id = data.get('id')
        new_status = data.get('status')
        db.orders.update_one({"_id": ObjectId(order_id)}, {"$set": {"status": new_status}})
        
        order = db.orders.find_one({"_id": ObjectId(order_id)})
        if order and order.get('uuid'):
            socketio.emit('order_status_update', {"status": new_status, "message": f"Статус замовлення: {new_status}"}, room=order['uuid'])
            
        socketio.emit('orders_sync', get_active_orders(), room='admins')

@socketio.on('call_waiter')
def handle_call_waiter(data):
    socketio.emit('waiter_alert', {"table": data.get('table')}, room='admins')

@socketio.on('stock_toggle')
def handle_stock_toggle(data):
    if session.get('admin_logged'):
        item = db.menu.find_one({"_id": ObjectId(data['id'])})
        if item:
            new_state = False if item.get('available') != False else True
            db.menu.update_one({"_id": ObjectId(data['id'])}, {"$set": {"available": new_state}})
            menu_data = get_all_menu()
            socketio.emit('menu_sync', menu_data)

@socketio.on('menu_save')
def handle_menu_save(data):
    if session.get('admin_logged'):
        item_data = {
            "name": data.get('name'),
            "category": data.get('category'),
            "price": float(data.get('price', 0)),
            "description": data.get('description', ''),
            "available": True
        }
        if data.get('image'):
            item_data['image'] = data.get('image')
            
        item_id = data.get('id')
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

@socketio.on('review_submit')
def handle_review_submit(data):
    db.reviews.insert_one({
        "table": data.get('table'),
        "rating": int(data.get('rating', 5)),
        "comment": data.get('comment', '').strip(),
        "timestamp": get_kyiv_time(),
        "time": get_kyiv_time_short()
    })
    socketio.emit('reviews_sync', get_all_reviews(), room='admins')
    return {"success": True}

@socketio.on('review_delete')
def handle_review_delete(data):
    if session.get('admin_logged'):
        db.reviews.delete_one({"_id": ObjectId(data['id'])})
        socketio.emit('reviews_sync', get_all_reviews(), room='admins')

@socketio.on('admin_clear_db')
def handle_admin_clear_db():
    if session.get('admin_logged'):
        db.menu.delete_many({})
        db.orders.delete_many({})
        db.reviews.delete_many({})
        handle_admin_init() # Оновити екрани всім

@socketio.on('admin_import_db')
def handle_admin_import_db(data):
    if session.get('admin_logged'):
        # Очищуємо старі
        db.menu.delete_many({})
        db.orders.delete_many({})
        db.reviews.delete_many({})
        
        # Вставляємо нові, видаляючи старі _id щоб Mongo згенерував нові або прийняв поточні
        if data.get('menu'): 
            for i in data['menu']: i.pop('_id', None)
            db.menu.insert_many(data['menu'])
        if data.get('orders'):
            for i in data['orders']: i.pop('_id', None)
            db.orders.insert_many(data['orders'])
        if data.get('reviews'):
            for i in data['reviews']: i.pop('_id', None)
            db.reviews.insert_many(data['reviews'])
            
        handle_admin_init()
        socketio.emit('menu_sync', get_all_menu())

# ==============================================================================
# 7. СТАРТ СЕРВЕРА
# ==============================================================================
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=10000)
