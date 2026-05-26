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
# 1. ІНІЦІАЛІЗАЦІЯ
# ==============================================================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'nexus-pro-ultra-key-2026')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', max_http_buffer_size=5000000)

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/cafe_db')
ADMIN_PASSWORD = "1111"

client = MongoClient(MONGO_URI)
db = client.get_default_database(default='cafe_db')

# ==============================================================================
# 2. ДОПОМІЖНІ ФУНКЦІЇ (ЧАС ТА СЕРІАЛІЗАЦІЯ)
# ==============================================================================
def get_kyiv_time(): return datetime.now(timezone.utc) + timedelta(hours=3)
def get_kyiv_time_str(): return get_kyiv_time().strftime('%d.%m.%Y %H:%M:%S')
def get_kyiv_time_short(): return get_kyiv_time().strftime('%H:%M')

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
def get_all_closed_orders(): return [serialize_doc(o) for o in db.orders.find({"status": "Закрито"}).sort("timestamp", -1)]

# ==============================================================================
# 3. КЛІЄНТСЬКИЙ ІНТЕРФЕЙС
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
                <div class="text-[10px] text-zinc-500 uppercase tracking-wider font-bold">Номер столу</div>
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
                <input type="text" id="order-comment" placeholder="Коментар до замовлення (напр. без луку)..." class="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-3 text-sm text-zinc-200 focus:outline-none focus:border-indigo-500">
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

    <script>
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

        socket.on('order_status_update', (data) => {
            const widget = document.getElementById('status-widget');
            if(data.status === 'Закрито') {
                widget.classList.add('hidden'); showToast("Замовлення оплачено та закрито.");
            } else {
                widget.classList.remove('hidden'); widget.classList.add('flex');
                document.getElementById('status-text').innerText = data.message;
                showToast(`Статус оновлено: ${data.status}`);
            }
            if(activeModal === 'orders-modal') loadMyOrders();
        });

        // LIVE ТРАНСЛЯЦІЯ ДЛЯ АДМІНА
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
                if(item && item.available !== false) {
                    totalCount += cart[id]; totalPrice += item.price * cart[id];
                    html += `
                        <div class="flex items-center justify-between bg-zinc-900/80 p-3 rounded-xl border border-zinc-800">
                            <div class="flex-1 min-w-0 pr-2">
                                <h4 class="font-bold text-sm text-zinc-200 truncate">${item.name}</h4>
                                <p class="text-[11px] text-indigo-400 font-bold mt-0.5">${item.price} ₴</p>
                            </div>
                            <div class="flex items-center gap-3 bg-zinc-950 px-2 py-1.5 rounded-xl border border-zinc-800">
                                <button onclick="changeQty('${id}', -1)" class="text-zinc-400 hover:text-white font-black px-1.5"><i class="fas fa-minus text-[10px]"></i></button>
                                <span class="font-black text-sm text-zinc-100 min-w-[16px] text-center">${cart[id]}</span>
                                <button onclick="changeQty('${id}', 1)" class="text-zinc-400 hover:text-white font-black px-1.5"><i class="fas fa-plus text-[10px]"></i></button>
                            </div>
                        </div>`;
                } else { delete cart[id]; }
            });

            if(list) list.innerHTML = html || '<div class="text-center text-zinc-600 py-10 text-sm font-bold">Кошик порожній</div>';
            document.getElementById('float-cart-count').innerText = totalCount;
            document.getElementById('float-cart-total').innerText = totalPrice;
            document.getElementById('modal-cart-total').innerText = totalPrice;
            document.getElementById('float-cart-bar').classList.toggle('hidden', totalCount === 0);
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
                    cart = {}; updateCartUI(); sendLiveTelemetry();
                    closeModal('cart-modal'); document.getElementById('order-comment').value = '';
                    document.getElementById('order-takeaway').checked = false; showToast("Замовлення надіслано на кухню!");
                }
            });
        }

        function loadMyOrders() {
            socket.emit('get_client_orders', { uuid: clientUUID }, (orders) => {
                const container = document.getElementById('my-orders-list');
                if(!orders.length) { container.innerHTML = `<div class="text-center text-zinc-500 py-8 text-sm font-bold">Історія порожня</div>`; return; }
                container.innerHTML = orders.map(o => {
                    let badge = o.status === 'Нове' ? 'bg-blue-500/10 text-blue-400 border-blue-500/30' : (o.status === 'Готується' ? 'bg-amber-500/10 text-amber-400 border-amber-500/30' : (o.status === 'Закрито' ? 'bg-zinc-800 text-zinc-400 border-zinc-700' : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'));
                    return `
                    <div class="bg-zinc-900 border border-zinc-800 p-4 rounded-xl space-y-2">
                        <div class="flex justify-between items-center border-b border-zinc-800 pb-2">
                            <span class="text-xs text-zinc-400 font-bold"><i class="far fa-clock"></i> ${o.time} ${o.takeaway ? '| З собою' : ''}</span>
                            <span class="px-2 py-0.5 text-[10px] font-black border uppercase rounded ${badge}">${o.status}</span>
                        </div>
                        <div class="space-y-1">
                            ${o.items.map(i => `<div class="flex justify-between text-xs text-zinc-300"><span>${i.name}</span> <span class="font-bold">x${i.qty}</span></div>`).join('')}
                        </div>
                        <div class="flex justify-between items-end pt-2">
                            <span class="text-[10px] text-zinc-500 uppercase font-bold tracking-wider">Сума</span><span class="text-lg font-black text-white">${o.total} ₴</span>
                        </div>
                    </div>`;
                }).join('');
            });
        }

        function callWaiter() { socket.emit('call_waiter', { table: tableId, uuid: clientUUID }); showToast("Офіціант вже прямує!"); }

        function openReviewModal() {
            closeModal('orders-modal');
            selectedRating = 5; renderStars();
            document.getElementById('review-comment').value = ''; openModal('review-modal');
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
                if(res && res.success) { closeModal('review-modal'); showToast("Дякуємо за ваш відгук!"); }
            });
        }

        function openModal(id) { activeModal = id; document.getElementById(id).classList.remove('hidden'); document.getElementById(id).classList.add('flex'); if(id === 'orders-modal') loadMyOrders(); sendLiveTelemetry(); }
        function closeModal(id) { activeModal = 'none'; document.getElementById(id).classList.add('hidden'); document.getElementById(id).classList.remove('flex'); sendLiveTelemetry(); }
        
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
# 4. АДМІНІСТРАТИВНИЙ ІНТЕРФЕЙС (ПАНЕЛЬ КЕРУВАННЯ)
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
                <button onclick="switchTab('menu')" id="btn-menu" class="w-full text-left px-4 py-3 rounded-xl text-xs font-bold text-slate-400 hover:bg-slate-800 hover:text-white transition-all flex items-center gap-3"><i class="fas fa-box-open w-4"></i> База Меню</button>
                <button onclick="switchTab('devices')" id="btn-devices" class="w-full text-left px-4 py-3 rounded-xl text-xs font-bold text-slate-400 hover:bg-slate-800 hover:text-white transition-all flex items-center gap-3"><i class="fas fa-mobile-alt w-4"></i> Аналітика Пристроїв</button>
                <button onclick="switchTab('history')" id="btn-history" class="w-full text-left px-4 py-3 rounded-xl text-xs font-bold text-slate-400 hover:bg-slate-800 hover:text-white transition-all flex items-center gap-3"><i class="fas fa-history w-4"></i> Архів та Клієнти</button>
                <button onclick="switchTab('settings')" id="btn-settings" class="w-full text-left px-4 py-3 rounded-xl text-xs font-bold text-slate-400 hover:bg-slate-800 hover:text-white transition-all flex items-center gap-3"><i class="fas fa-cog w-4"></i> База Даних</button>
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
            <h1 class="text-2xl font-black tracking-tight border-b border-slate-800 pb-3 text-slate-100 flex items-center gap-2"><i class="fas fa-network-wired text-indigo-500"></i> Радар Пристроїв</h1>
            <div class="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden shadow-xl">
                <table class="w-full text-left text-xs">
                    <thead class="bg-slate-950 text-slate-400 border-b border-slate-800 uppercase tracking-wider font-bold">
                        <tr><th class="p-4">Клієнт (UUID)</th><th class="p-4">Стіл</th><th class="p-4">Статус</th><th class="p-4">Кошик</th><th class="p-4 text-right">Моніторинг</th></tr>
                    </thead>
                    <tbody id="devices-table" class="divide-y divide-slate-800"></tbody>
                </table>
            </div>
        </div>

        <div id="tab-history" class="tab-content hidden space-y-6">
            <h1 class="text-2xl font-black tracking-tight border-b border-slate-800 pb-3 text-slate-100 flex items-center gap-2"><i class="fas fa-users text-indigo-500"></i> Архів чеків та Клієнти</h1>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6" id="history-grid"></div>
        </div>

        <div id="tab-settings" class="tab-content hidden space-y-6 max-w-2xl">
            <h1 class="text-2xl font-black tracking-tight border-b border-slate-800 pb-3 text-slate-100 flex items-center gap-2"><i class="fas fa-database text-indigo-500"></i> Управління Даними</h1>
            <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-5">
                <div class="flex items-center justify-between border-b border-slate-800 pb-5">
                    <div><h3 class="font-bold text-sm text-slate-200 mb-1">Експорт Бази Даних</h3><p class="text-xs text-slate-500">Завантажити поточний стан бази у форматі JSON.</p></div>
                    <a href="/api/export" target="_blank" class="bg-indigo-600 hover:bg-indigo-500 text-white px-5 py-2.5 rounded-xl font-bold text-xs shadow-md flex items-center gap-2"><i class="fas fa-download"></i> Скачати</a>
                </div>
                <div class="flex items-center justify-between border-b border-slate-800 pb-5">
                    <div><h3 class="font-bold text-sm text-slate-200 mb-1">Імпорт Бази Даних</h3><p class="text-xs text-slate-500">Відновити дані з бекапу.</p></div>
                    <div>
                        <input type="file" id="import-file" accept=".json" class="hidden" onchange="importDB(event)">
                        <button onclick="document.getElementById('import-file').click()" class="bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 px-5 py-2.5 rounded-xl font-bold text-xs flex items-center gap-2"><i class="fas fa-upload"></i> Завантажити</button>
                    </div>
                </div>
                <div class="flex items-center justify-between pt-2">
                    <div><h3 class="font-bold text-sm text-rose-500 mb-1">Критична Зона</h3><p class="text-xs text-slate-500">Повне очищення всіх колекцій.</p></div>
                    <button onclick="clearDB()" class="bg-rose-600/10 hover:bg-rose-600 border border-rose-600 text-rose-500 hover:text-white px-5 py-2.5 rounded-xl font-black uppercase tracking-wider text-xs flex items-center gap-2"><i class="fas fa-trash-alt"></i> Знищити дані</button>
                </div>
            </div>
        </div>
    </main>

    <div id="menu-modal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 hidden items-center justify-center p-4">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-md p-6 shadow-2xl">
            <div class="flex justify-between items-center mb-4 border-b border-slate-800 pb-3">
                <h3 id="modal-title" class="text-lg font-black text-slate-100 flex items-center gap-2"></h3>
                <button onclick="closeModal('menu-modal')" class="text-slate-500 hover:text-white"><i class="fas fa-times"></i></button>
            </div>
            <input type="hidden" id="item-id">
            <div class="space-y-4">
                <div>
                    <label class="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1 block">Назва</label>
                    <input type="text" id="item-name" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-indigo-500">
                </div>
                <div class="grid grid-cols-2 gap-4">
                    <div><label class="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1 block">Категорія</label><input type="text" id="item-category" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-indigo-500"></div>
                    <div><label class="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1 block">Ціна (₴)</label><input type="number" id="item-price" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-indigo-500"></div>
                </div>
                <div><label class="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1 block">Опис</label><textarea id="item-description" rows="2" class="w-full bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm text-white focus:outline-none resize-none focus:border-indigo-500"></textarea></div>
                <div>
                    <label class="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1 block">Зображення (Авто-стиснення)</label>
                    <input type="file" accept="image/*" onchange="encodeImageFile(event)" class="w-full text-xs text-slate-400 bg-slate-950 p-2 border border-slate-800 rounded-xl cursor-pointer">
                    <img id="menu-image-preview" src="" class="hidden w-full h-32 object-cover rounded-xl mt-3 border border-slate-700">
                </div>
            </div>
            <button onclick="saveMenuItem()" class="w-full mt-6 bg-indigo-600 hover:bg-indigo-500 py-3.5 rounded-xl text-sm font-black text-white transition-all shadow-lg">Зберегти в базу</button>
        </div>
    </div>

    <div id="live-modal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 hidden items-center justify-center p-4">
        <div class="bg-slate-900 border border-slate-800 rounded-3xl w-full max-w-[320px] p-6 shadow-2xl relative overflow-hidden flex flex-col items-center">
            <button onclick="closeModal('live-modal')" class="absolute top-4 right-4 text-slate-500 hover:text-white z-10"><i class="fas fa-times text-xl"></i></button>
            <h3 class="text-sm font-black text-slate-300 mb-6 flex items-center gap-2"><i class="fas fa-satellite-dish text-rose-500 animate-pulse"></i> Live Дзеркало</h3>
            
            <div class="w-[260px] h-[520px] border-8 border-slate-950 rounded-[2.5rem] bg-zinc-950 relative shadow-inner overflow-hidden flex flex-col">
                <div class="absolute top-0 inset-x-0 h-6 bg-slate-950 rounded-b-2xl mx-16"></div> <div class="p-4 pt-8 bg-zinc-900/80 border-b border-zinc-800 flex justify-between items-center text-white">
                    <div class="font-bold text-[10px] uppercase">Стіл #<span id="live-phone-table" class="text-indigo-400"></span></div>
                    <div id="live-phone-modal" class="text-[10px] font-bold bg-rose-500/20 text-rose-400 px-2 rounded">Меню</div>
                </div>
                <div class="flex-1 p-4 relative">
                    <p class="text-[10px] text-zinc-500 font-bold uppercase mb-1">Дивиться категорію:</p>
                    <div id="live-phone-category" class="bg-indigo-600/20 text-indigo-400 font-bold text-sm px-3 py-1.5 rounded-lg border border-indigo-500/30 inline-block mb-4">Всі</div>
                    
                    <p class="text-[10px] text-zinc-500 font-bold uppercase mb-1 mt-4">Глибина скролу:</p>
                    <div class="w-full bg-zinc-800 rounded-full h-2.5 mb-1 overflow-hidden">
                        <div id="live-phone-scroll" class="bg-emerald-500 h-2.5 rounded-full" style="width: 0%"></div>
                    </div>
                    <div class="text-right text-[10px] text-zinc-400 font-bold"><span id="live-phone-scroll-val">0</span>%</div>
                </div>
                <div class="bg-zinc-900 border-t border-zinc-800 p-4">
                    <div class="flex justify-between items-center">
                        <span class="text-[10px] text-zinc-400 font-bold uppercase">В кошику на суму:</span>
                        <span id="live-phone-cart" class="text-lg font-black text-white">0 ₴</span>
                    </div>
                </div>
            </div>
            <p class="text-[9px] text-slate-500 mt-4 text-center">Транслюються тільки дії всередині цифрового меню. Без порушення приватності.</p>
        </div>
    </div>

    <script>
        const socket = io();
        let allMenu = [], allOrders = [], allDevices = [], allClosedOrders = [];
        let currentImageBase64 = '';
        let activeLiveUUID = null;

        socket.on('connect', () => { socket.emit('admin_init'); });
        socket.on('menu_sync', data => { allMenu = data; renderMenu(); });
        socket.on('orders_sync', data => { allOrders = data; renderOrders(); });
        socket.on('closed_orders_sync', data => { allClosedOrders = data; renderHistory(); });
        socket.on('devices_sync', data => { allDevices = data; renderDevices(); updateLiveMirror(); });
        
        socket.on('waiter_alert', data => {
            showToast(`<i class="fas fa-bell animate-bounce text-amber-400"></i> Стіл #${data.table} кличе офіціанта!`);
        });

        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.getElementById('tab-' + tabId).classList.remove('hidden');
            document.querySelectorAll('nav button').forEach(b => {
                b.classList.remove('bg-indigo-600', 'text-white', 'shadow-md');
                b.classList.add('text-slate-400');
            });
            document.getElementById('btn-' + tabId).classList.add('bg-indigo-600', 'text-white', 'shadow-md');
            document.getElementById('btn-' + tabId).classList.remove('text-slate-400');
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
                return `
                    <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 flex flex-col justify-between shadow-xl relative overflow-hidden group">
                        <div class="absolute top-0 left-0 w-1 h-full ${o.status === 'Нове' ? 'bg-blue-500' : (o.status === 'Готується' ? 'bg-amber-500' : 'bg-emerald-500')}"></div>
                        <div>
                            <div class="flex justify-between items-center mb-4 pl-3">
                                <div><span class="text-xl font-black text-slate-100">Стіл #${o.table}</span><p class="text-[10px] text-slate-500 font-bold mt-1">${o.time} ${o.takeaway ? '• З собою' : ''}</p></div>
                                <span class="text-xs font-bold ${badgeColor} px-3 py-1 rounded-lg border uppercase">${o.status}</span>
                            </div>
                            <div class="space-y-1 mb-4 bg-slate-950/50 p-3 rounded-xl border border-slate-800">
                                ${o.items.map(i => `<div class="flex justify-between text-sm py-1 border-b border-slate-800/40 text-slate-300"><span>${i.name}</span><span class="font-black text-indigo-400">x${i.qty}</span></div>`).join('')}
                            </div>
                            ${o.comment ? `<p class="mb-4 p-3 bg-amber-500/10 border border-amber-500/20 text-amber-400 rounded-xl text-xs font-medium">${o.comment}</p>` : ''}
                        </div>
                        <div class="pt-4 border-t border-slate-800 flex items-center justify-between pl-3">
                            <div><div class="text-[10px] text-slate-500 font-bold uppercase">Сума чеку</div><div class="text-xl font-black text-emerald-400">${o.total} ₴</div></div>
                            <button onclick="updateOrderStatus('${o._id}', '${nextStatus}')" class="${btnClass} font-bold text-xs px-4 py-2.5 rounded-xl shadow-lg">${btnText}</button>
                        </div>
                    </div>`;
            }).join('');
        }

        function renderHistory() {
            const grid = document.getElementById('history-grid');
            if(allClosedOrders.length === 0) { grid.innerHTML = `<p class="text-slate-500 text-sm font-bold">Архів порожній</p>`; return; }
            
            // Групуємо закриті чеки по UUID пристрою
            let grouped = {};
            allClosedOrders.forEach(o => {
                if(!grouped[o.uuid]) grouped[o.uuid] = { uuid: o.uuid, total_spent: 0, orders: [] };
                grouped[o.uuid].orders.push(o);
                grouped[o.uuid].total_spent += o.total;
            });

            grid.innerHTML = Object.values(grouped).map(g => `
                <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-lg">
                    <div class="flex justify-between items-center border-b border-slate-800 pb-3 mb-4">
                        <div>
                            <div class="text-xs text-indigo-400 font-bold uppercase tracking-wider mb-1">Клієнт (Пристрій)</div>
                            <div class="text-sm font-mono text-slate-300">${g.uuid.substring(0, 16)}...</div>
                        </div>
                        <div class="text-right">
                            <div class="text-xs text-slate-500 font-bold uppercase tracking-wider mb-1">LTV (Приніс грошей)</div>
                            <div class="text-xl font-black text-emerald-400">${g.total_spent} ₴</div>
                        </div>
                    </div>
                    <div class="space-y-3 max-h-48 overflow-y-auto hide-scroll pr-2">
                        ${g.orders.map(o => `
                            <div class="bg-slate-950 p-3 rounded-xl border border-slate-800 flex justify-between items-center">
                                <div>
                                    <span class="text-xs font-bold text-slate-200">Стіл #${o.table}</span>
                                    <p class="text-[10px] text-slate-500 mt-0.5">${o.timestamp}</p>
                                </div>
                                <div class="text-sm font-black text-slate-300">${o.total} ₴</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `).join('');
        }

        function renderDevices() {
            const tbody = document.getElementById('devices-table');
            if(allDevices.length === 0) { tbody.innerHTML = `<tr><td colspan="5" class="p-6 text-center text-slate-500 text-sm font-bold">Немає підключених пристроїв</td></tr>`; return; }
            tbody.innerHTML = allDevices.map(d => {
                const isOnline = (new Date() - new Date(d.last_seen.replace(/(\d+).(\d+).(\d+) (\d+):(\d+):(\d+)/, '$3-$2-$1T$4:$5:$6'))) < 300000; 
                return `
                    <tr class="hover:bg-slate-800/50 transition-colors">
                        <td class="p-4 border-b border-slate-800"><div class="text-xs font-bold text-slate-200">${d.uuid.substring(0,16)}...</div></td>
                        <td class="p-4 border-b border-slate-800"><span class="bg-slate-950 border border-slate-800 px-2 py-1 rounded text-xs font-black">#${d.table}</span></td>
                        <td class="p-4 border-b border-slate-800"><div class="text-xs font-bold text-slate-300 flex items-center gap-2"><span class="w-1.5 h-1.5 rounded-full ${isOnline ? 'bg-emerald-500 animate-pulse' : 'bg-slate-600'}"></span> ${d.last_seen}</div></td>
                        <td class="p-4 border-b border-slate-800"><div class="text-[10px] text-emerald-400 font-bold">${d.cart_total || 0} ₴</div></td>
                        <td class="p-4 border-b border-slate-800 text-right">
                            <button onclick="openLiveMirror('${d.uuid}')" class="bg-indigo-600/20 text-indigo-400 hover:bg-indigo-600 hover:text-white border border-indigo-500/30 px-3 py-1.5 rounded-lg text-xs font-bold transition-all"><i class="fas fa-play text-[10px]"></i> Live Mirror</button>
                        </td>
                    </tr>`;
            }).join('');
        }

        function updateLiveMirror() {
            if(!activeLiveUUID) return;
            const d = allDevices.find(x => x.uuid === activeLiveUUID);
            if(!d) return;
            document.getElementById('live-phone-table').innerText = d.table;
            document.getElementById('live-phone-category').innerText = d.last_category || 'Всі';
            document.getElementById('live-phone-cart').innerText = (d.cart_total || 0) + ' ₴';
            document.getElementById('live-phone-scroll').style.width = (d.scroll || 0) + '%';
            document.getElementById('live-phone-scroll-val').innerText = d.scroll || 0;
            
            const modalBadge = document.getElementById('live-phone-modal');
            if(d.modal === 'cart-modal') { modalBadge.innerText = 'Оформлення'; modalBadge.className = 'text-[10px] font-bold bg-indigo-500/20 text-indigo-400 px-2 py-0.5 rounded'; }
            else if(d.modal === 'orders-modal') { modalBadge.innerText = 'Історія'; modalBadge.className = 'text-[10px] font-bold bg-amber-500/20 text-amber-400 px-2 py-0.5 rounded'; }
            else if(d.modal === 'review-modal') { modalBadge.innerText = 'Відгук'; modalBadge.className = 'text-[10px] font-bold bg-emerald-500/20 text-emerald-400 px-2 py-0.5 rounded'; }
            else { modalBadge.innerText = 'Скролить меню'; modalBadge.className = 'text-[10px] font-bold bg-slate-700 text-slate-300 px-2 py-0.5 rounded'; }
        }

        function openLiveMirror(uuid) {
            activeLiveUUID = uuid;
            updateLiveMirror();
            document.getElementById('live-modal').classList.remove('hidden');
            document.getElementById('live-modal').classList.add('flex');
        }

        function renderMenu() {
            const grid = document.getElementById('menu-grid');
            grid.innerHTML = allMenu.map(item => {
                const avail = item.available !== false;
                const encoded = encodeURIComponent(JSON.stringify(item));
                const img = item.image ? `<img src="${item.image}" class="w-full h-32 object-cover rounded-xl mb-3 border border-slate-800" />` : `<div class="w-full h-32 bg-slate-950 flex items-center justify-center text-3xl rounded-xl mb-3 border border-slate-800"><i class="fas fa-image text-slate-800"></i></div>`;
                return `
                    <div class="bg-slate-900 border border-slate-800 p-4 rounded-2xl flex flex-col justify-between ${!avail ? 'opacity-60 grayscale' : ''}">
                        <div>${img}<div class="flex justify-between items-start mb-1"><h4 class="font-bold text-sm text-slate-200 line-clamp-1 pr-2">${item.name}</h4><span class="text-sm font-black text-indigo-400">${item.price}₴</span></div><p class="text-[10px] text-slate-500 font-bold uppercase bg-slate-950 inline-block px-2 py-0.5 rounded">${item.category}</p></div>
                        <div class="mt-4 pt-3 border-t border-slate-800 flex items-center justify-between">
                            <label class="flex items-center gap-2 cursor-pointer bg-slate-950 px-2 py-1 rounded-lg border border-slate-800"><input type="checkbox" ${avail ? 'checked' : ''} onchange="toggleStock('${item._id}')" class="rounded bg-slate-900 border-slate-700 text-indigo-600 focus:ring-0 w-3 h-3"><span class="text-[10px] font-bold ${avail ? 'text-emerald-400' : 'text-slate-500'} uppercase">На складі</span></label>
                            <div class="flex gap-2"><button onclick="openEditModal('${encoded}')" class="w-7 h-7 bg-indigo-500/10 text-indigo-400 rounded-lg flex items-center justify-center hover:bg-indigo-500/20"><i class="fas fa-pen text-[10px]"></i></button><button onclick="deleteMenuItem('${item._id}')" class="w-7 h-7 bg-rose-500/10 text-rose-500 rounded-lg flex items-center justify-center hover:bg-rose-500/20"><i class="fas fa-trash text-[10px]"></i></button></div>
                        </div>
                    </div>`;
            }).join('');
        }

        function updateOrderStatus(id, status) { socket.emit('order_update', { id: id, status: status }); }
        function toggleStock(id) { socket.emit('stock_toggle', { id: id }); }
        function deleteMenuItem(id) { if(confirm('Видалити страву з бази?')) socket.emit('menu_delete', { id: id }); }

        function openAddModal() {
            document.getElementById('modal-title').innerHTML = '<i class="fas fa-plus text-indigo-500"></i> Створити позицію';
            document.getElementById('item-id').value = ''; document.getElementById('item-name').value = ''; document.getElementById('item-category').value = ''; document.getElementById('item-price').value = ''; document.getElementById('item-description').value = '';
            currentImageBase64 = ''; document.getElementById('menu-image-preview').classList.add('hidden');
            document.getElementById('menu-modal').classList.remove('hidden'); document.getElementById('menu-modal').classList.add('flex');
        }

        function openEditModal(encodedData) {
            const item = JSON.parse(decodeURIComponent(encodedData));
            document.getElementById('modal-title').innerHTML = '<i class="fas fa-edit text-indigo-500"></i> Редагувати картку';
            document.getElementById('item-id').value = item._id; document.getElementById('item-name').value = item.name; document.getElementById('item-category').value = item.category; document.getElementById('item-price').value = item.price; document.getElementById('item-description').value = item.description || '';
            currentImageBase64 = item.image || '';
            const preview = document.getElementById('menu-image-preview');
            if(item.image) { preview.src = item.image; preview.classList.remove('hidden'); } else { preview.classList.add('hidden'); }
            document.getElementById('menu-modal').classList.remove('hidden'); document.getElementById('menu-modal').classList.add('flex');
        }

        function closeModal(id = 'menu-modal') { 
            document.getElementById(id).classList.add('hidden'); 
            document.getElementById(id).classList.remove('flex'); 
            if(id === 'live-modal') activeLiveUUID = null;
        }

        function encodeImageFile(e) {
            const file = e.target.files[0];
            if(!file) return;
            const reader = new FileReader();
            reader.onload = function(event) {
                const img = new Image();
                img.onload = function() {
                    const canvas = document.createElement('canvas');
                    const MAX_WIDTH = 600; let width = img.width, height = img.height;
                    if (width > MAX_WIDTH) { height *= MAX_WIDTH / width; width = MAX_WIDTH; }
                    canvas.width = width; canvas.height = height;
                    const ctx = canvas.getContext('2d'); ctx.drawImage(img, 0, 0, width, height);
                    currentImageBase64 = canvas.toDataURL('image/jpeg', 0.8);
                    const p = document.getElementById('menu-image-preview'); p.src = currentImageBase64; p.classList.remove('hidden');
                };
                img.src = event.target.result;
            };
            reader.readAsDataURL(file);
        }

        function saveMenuItem() {
            const name = document.getElementById('item-name').value.trim(), price = document.getElementById('item-price').value, category = document.getElementById('item-category').value.trim();
            if(!name || !price || !category) return alert('Заповніть назву, категорію та ціну!');
            socket.emit('menu_save', { id: document.getElementById('item-id').value || null, name: name, category: category, price: parseFloat(price), description: document.getElementById('item-description').value.trim(), image: currentImageBase64 || null });
            closeModal('menu-modal'); showToast("Збережено!");
        }

        function clearDB() { if(confirm('Видалити всі страви і чеки?')) { socket.emit('admin_clear_db'); showToast("Очищено."); } }

        function importDB(e) {
            const file = e.target.files[0]; if(!file) return;
            const reader = new FileReader();
            reader.onload = function(event) {
                try {
                    const data = JSON.parse(event.target.result);
                    if(confirm("Перезаписати базу?")) { socket.emit('admin_import_db', data); showToast("Імпортовано!"); }
                } catch(err) { alert("Помилка JSON!"); }
            };
            reader.readAsText(file); e.target.value = '';
        }

        function showToast(html) {
            const box = document.getElementById('toast-admin');
            box.innerHTML = html; box.classList.remove('hidden');
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
</head>
<body class="bg-slate-950 flex items-center justify-center h-screen text-slate-100">
    <div class="bg-slate-900 border border-slate-800 p-10 rounded-3xl w-full max-w-sm shadow-[0_0_50px_rgba(79,70,229,0.1)]">
        <h2 class="text-2xl font-black text-center mb-1 tracking-tight">NEXUS SECURE</h2>
        <p class="text-xs text-slate-500 text-center mb-8 uppercase tracking-wider font-bold">Система Управління</p>
        <form method="POST" action="/login" class="space-y-4">
            <input type="password" name="password" placeholder="Секретний PIN" required class="w-full p-4 bg-slate-950 border border-slate-800 rounded-2xl text-center text-lg font-black tracking-[0.5em] focus:outline-none focus:border-indigo-500 text-white transition">
            <button type="submit" class="w-full bg-indigo-600 hover:bg-indigo-500 py-4 rounded-2xl text-sm font-black uppercase tracking-wider transition-all shadow-lg">Увійти</button>
        </form>
    </div>
</body>
</html>
"""

# ==============================================================================
# 5. СТАНДАРТНІ HTTP МАРШРУТИ
# ==============================================================================
@app.route('/')
def index_redirect(): return redirect('/1')

@app.route('/<int:table_id>')
def customer_interface(table_id): return render_template_string(CUSTOMER_HTML, table_id=table_id)

@app.route('/admin')
def admin_panel():
    if session.get('admin_logged'): return render_template_string(ADMIN_HTML)
    return render_template_string(LOGIN_HTML)

@app.route('/login', methods=['POST'])
def handle_login():
    if request.form.get('password') == ADMIN_PASSWORD: session['admin_logged'] = True
    return redirect('/admin')

@app.route('/logout')
def handle_logout():
    session.pop('admin_logged', None)
    return redirect('/admin')

@app.route('/api/export')
def api_export():
    if not session.get('admin_logged'): return jsonify({"error": "Unauthorized"}), 401
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
    if uuid:
        join_room(uuid)
        db.devices.update_one(
            {"uuid": uuid},
            {"$set": {
                "user_agent": data.get('user_agent', 'Unknown'),
                "table": data.get('table'),
                "last_seen": get_kyiv_time_str()
            }},
            upsert=True
        )
        socketio.emit('devices_sync', get_all_devices(), room='admins')
    emit('menu_sync', get_all_menu())

@socketio.on('client_telemetry')
def handle_client_telemetry(data):
    uuid = data.get('uuid')
    if uuid:
        db.devices.update_one(
            {"uuid": uuid},
            {"$set": {
                "last_category": data.get('category'),
                "cart_total": data.get('cart_total', 0),
                "modal": data.get('modal', 'none'),
                "scroll": data.get('scroll', 0),
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
        emit('closed_orders_sync', get_all_closed_orders())
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
            detailed_items.append({"name": menu_item['name'], "qty": i['qty']})
            
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
        if new_status == 'Закрито':
            socketio.emit('closed_orders_sync', get_all_closed_orders(), room='admins')

@socketio.on('call_waiter')
def handle_call_waiter(data):
    socketio.emit('waiter_alert', {"table": data.get('table')}, room='admins')

@socketio.on('get_client_orders')
def handle_get_client_orders(data):
    uuid = data.get('uuid')
    return [serialize_doc(o) for o in db.orders.find({"uuid": uuid}).sort("timestamp", -1)]

@socketio.on('stock_toggle')
def handle_stock_toggle(data):
    if session.get('admin_logged'):
        item = db.menu.find_one({"_id": ObjectId(data['id'])})
        if item:
            new_state = False if item.get('available') != False else True
            db.menu.update_one({"_id": ObjectId(data['id'])}, {"$set": {"available": new_state}})
            socketio.emit('menu_sync', get_all_menu())

@socketio.on('menu_save')
def handle_menu_save(data):
    if session.get('admin_logged'):
        item_data = {
            "name": data.get('name'), "category": data.get('category'),
            "price": float(data.get('price', 0)), "description": data.get('description', ''),
            "available": True
        }
        if data.get('image'): item_data['image'] = data.get('image')
            
        item_id = data.get('id')
        if item_id: db.menu.update_one({"_id": ObjectId(item_id)}, {"$set": item_data})
        else: db.menu.insert_one(item_data)
            
        socketio.emit('menu_sync', get_all_menu())

@socketio.on('menu_delete')
def handle_menu_delete(data):
    if session.get('admin_logged'):
        db.menu.delete_one({"_id": ObjectId(data['id'])})
        socketio.emit('menu_sync', get_all_menu())

@socketio.on('admin_clear_db')
def handle_admin_clear_db():
    if session.get('admin_logged'):
        db.menu.delete_many({}); db.orders.delete_many({}); db.reviews.delete_many({})
        handle_admin_init()

@socketio.on('admin_import_db')
def handle_admin_import_db(data):
    if session.get('admin_logged'):
        db.menu.delete_many({}); db.orders.delete_many({}); db.reviews.delete_many({})
        if data.get('menu'): 
            for i in data['menu']: i.pop('_id', None)
            db.menu.insert_many(data['menu'])
        if data.get('orders'):
            for i in data['orders']: i.pop('_id', None); i['timestamp'] = get_kyiv_time()
            db.orders.insert_many(data['orders'])
        if data.get('reviews'):
            for i in data['reviews']: i.pop('_id', None)
            db.reviews.insert_many(data['reviews'])
        handle_admin_init()

# ==============================================================================
# 7. СТАРТ СЕРВЕРА
# ==============================================================================
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=10000)
