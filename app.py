import eventlet
eventlet.monkey_patch()

import os
import time
import uuid
from datetime import datetime
from bson.objectid import ObjectId
from flask import Flask, request, render_template_string, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room
from pymongo import MongoClient

# ==============================================================================
# 1. CORE SYSTEM INITIALIZATION
# ==============================================================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'nexus-omega-ultra-key-2026')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/cafe_db')
ADMIN_PASSWORD = "1111"  # Встановлено твій пароль

client = MongoClient(MONGO_URI)
db = client.get_default_database(default='cafe_db')

def serialize_doc(doc):
    if not doc: return None
    doc['_id'] = str(doc['_id'])
    return doc

# ==============================================================================
# 2. CLIENT FRONTEND TEMPLATE (DYNAMIC TABLES, CODES, COMMENTS, REVIEWS)
# ==============================================================================
CUSTOMER_HTML = """
<!DOCTYPE html>
<html lang="uk" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Premium Cafe System</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socketio/4.7.2/socketio.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700;900&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Outfit', sans-serif; background-color: #06060c; color: #f3f4f6; }
        .glass { background: rgba(18, 18, 29, 0.75); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.06); }
        .gradient-text { background: linear-gradient(135deg, #8b5cf6 0%, #3b82f6 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .hide-scroll::-webkit-scrollbar { display: none; }
        .btn-active:active { transform: scale(0.95); }
    </style>
</head>
<body class="pb-32 min-h-screen relative overflow-x-hidden">

    <div class="fixed top-[-20%] left-[-10%] w-[500px] h-[500px] bg-purple-600/10 rounded-full blur-[120px] pointer-events-none"></div>
    <div class="fixed bottom-[-20%] right-[-10%] w-[500px] h-[500px] bg-blue-600/10 rounded-full blur-[120px] pointer-events-none"></div>

    <header class="glass sticky top-0 z-40 px-6 py-4 border-b border-white/5">
        <div class="max-w-7xl mx-auto flex justify-between items-center">
            <div class="flex items-center gap-3">
                <div class="w-11 h-11 bg-gradient-to-tr from-purple-600 to-blue-500 rounded-xl flex items-center justify-center shadow-lg shadow-purple-500/20">
                    <i class="fas fa-layer-group text-white text-xl"></i>
                </div>
                <div>
                    <h1 class="text-xl font-black tracking-wider uppercase gradient-text">NEXUS CAFE</h1>
                    <p class="text-[10px] text-emerald-400 font-bold uppercase tracking-widest flex items-center gap-1">
                        <span class="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-ping"></span> Стіл #{{ table_id }}
                    </p>
                </div>
            </div>
            <div class="flex gap-2">
                <button onclick="openModal('reviews-modal')" class="btn-active px-4 py-2 bg-white/5 hover:bg-white/10 rounded-xl text-xs font-bold transition flex items-center gap-2 border border-white/5">
                    <i class="fas fa-star text-amber-400"></i> Відгук
                </button>
                <button onclick="openModal('orders-modal')" class="btn-active px-4 py-2 bg-white/5 hover:bg-white/10 rounded-xl text-xs font-bold transition flex items-center gap-2 border border-white/5">
                    <i class="fas fa-receipt text-blue-400"></i> Мої чеки
                </button>
            </div>
        </div>
    </header>

    <main class="max-w-7xl mx-auto px-6 pt-8">
        <div id="categories" class="flex gap-3 overflow-x-auto hide-scroll pb-4 mb-6"></div>
        <div id="menu-grid" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6"></div>
    </main>

    <div id="cart-bar" class="fixed bottom-0 left-0 right-0 p-5 z-40 transform translate-y-full transition-transform duration-500 pointer-events-none">
        <div class="max-w-3xl mx-auto glass rounded-2xl p-3 pl-6 flex items-center justify-between border border-purple-500/30 shadow-2xl pointer-events-auto">
            <div>
                <p class="text-xs text-gray-400 font-bold uppercase tracking-wider">Ваше замовлення</p>
                <p class="text-2xl font-black text-white"><span id="cart-total">0</span> ₴</p>
            </div>
            <button onclick="openCheckout()" class="btn-active bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-500 hover:to-blue-500 text-white px-8 py-4 rounded-xl font-black text-sm uppercase tracking-wider shadow-lg shadow-purple-500/20 flex items-center gap-2 transition">
                Перейти до кошика <i class="fas fa-shopping-basket"></i>
            </button>
        </div>
    </div>

    <div id="checkout-modal" class="fixed inset-0 bg-black/80 backdrop-blur-md z-50 hidden items-center justify-center p-4">
        <div class="w-full max-w-xl glass rounded-2xl p-6 relative flex flex-col max-h-[90vh]">
            <button onclick="closeModal('checkout-modal')" class="absolute top-4 right-4 text-gray-400 hover:text-white"><i class="fas fa-times text-xl"></i></button>
            <h3 class="text-2xl font-black mb-4 flex items-center gap-2"><i class="fas fa-shopping-cart text-purple-500"></i> Ваш кошик</h3>
            
            <div id="checkout-list" class="flex-1 overflow-y-auto hide-scroll space-y-3 pr-1 mb-4"></div>
            
            <div class="space-y-4 border-t border-white/5 pt-4">
                <label class="flex items-center gap-3 bg-white/5 p-4 rounded-xl border border-white/5 cursor-pointer">
                    <input type="checkbox" id="order-takeaway" class="w-5 h-5 rounded accent-purple-600">
                    <div>
                        <p class="font-bold text-sm text-white">Замовлення з собою</p>
                        <p class="text-xs text-gray-400">Упакувати в крафтовий пакет</p>
                    </div>
                </label>
                
                <div>
                    <label class="text-xs text-gray-400 font-bold uppercase tracking-wider block mb-1">Коментар до кухні</label>
                    <textarea id="order-comment" rows="2" placeholder="Наприклад: без цибулі, зробити гарячішим..." class="w-full bg-white/5 border border-white/10 rounded-xl p-3 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-purple-500 transition"></textarea>
                </div>
            </div>

            <div class="flex justify-between items-center mt-6 pt-4 border-t border-white/5">
                <div>
                    <p class="text-xs text-gray-400 font-bold uppercase">Разом до сплати</p>
                    <p class="text-3xl font-black text-white"><span id="checkout-total">0</span> ₴</p>
                </div>
                <button onclick="submitOrder()" class="btn-active bg-emerald-600 hover:bg-emerald-500 text-white font-black px-8 py-4 rounded-xl uppercase text-sm tracking-wider shadow-lg shadow-emerald-500/20 transition">
                    Підтвердити замовлення
                </button>
            </div>
        </div>
    </div>

    <div id="orders-modal" class="fixed inset-0 bg-black/80 backdrop-blur-md z-50 hidden items-center justify-center p-4">
        <div class="w-full max-w-xl glass rounded-2xl p-6 relative flex flex-col max-h-[85vh]">
            <button onclick="closeModal('orders-modal')" class="absolute top-4 right-4 text-gray-400 hover:text-white"><i class="fas fa-times text-xl"></i></button>
            <h3 class="text-xl font-black mb-4 flex items-center gap-2"><i class="fas fa-clock text-blue-500"></i> Статус ваших замовлень</h3>
            <div id="my-orders-list" class="flex-1 overflow-y-auto hide-scroll space-y-4"></div>
        </div>
    </div>

    <div id="reviews-modal" class="fixed inset-0 bg-black/80 backdrop-blur-md z-50 hidden items-center justify-center p-4">
        <div class="w-full max-w-md glass rounded-2xl p-6 relative">
            <button onclick="closeModal('reviews-modal')" class="absolute top-4 right-4 text-gray-400 hover:text-white"><i class="fas fa-times text-xl"></i></button>
            <h3 class="text-xl font-black mb-2 flex items-center gap-2"><i class="fas fa-comment-heart text-amber-500"></i> Залишити відгук</h3>
            <p class="text-xs text-gray-400 mb-4">Ваша думка допомагає нам ставати кращими!</p>
            
            <div class="flex justify-center gap-3 mb-4 text-2xl" id="stars-container">
                <i class="far fa-star cursor-pointer text-amber-400 transition" onclick="setRating(1)"></i>
                <i class="far fa-star cursor-pointer text-amber-400 transition" onclick="setRating(2)"></i>
                <i class="far fa-star cursor-pointer text-amber-400 transition" onclick="setRating(3)"></i>
                <i class="far fa-star cursor-pointer text-amber-400 transition" onclick="setRating(4)"></i>
                <i class="far fa-star cursor-pointer text-amber-400 transition" onclick="setRating(5)"></i>
            </div>
            
            <textarea id="review-comment" rows="3" placeholder="Напишіть ваші враження..." class="w-full bg-white/5 border border-white/10 rounded-xl p-3 text-sm text-white mb-4 focus:outline-none focus:border-amber-500 transition"></textarea>
            <button onclick="submitReview()" class="w-full py-3 bg-amber-500 hover:bg-amber-400 text-gray-900 font-black rounded-xl text-sm uppercase tracking-wider transition">Надіслати відгук</button>
        </div>
    </div>

    <div id="toast-container" class="fixed top-4 left-1/2 transform -translate-x-1/2 z-[100] flex flex-col gap-2 w-full max-w-sm px-4 pointer-events-none"></div>

    <script>
        const tableId = "{{ table_id }}";
        let clientUUID = localStorage.getItem('nexus_client_uuid');
        if (!clientUUID) {
            clientUUID = 'u-' + Math.random().toString(36).substr(2, 9) + '-' + Date.now();
            localStorage.setItem('nexus_client_uuid', clientUUID);
        }

        const socket = io({ auth: { uuid: clientUUID } });
        let menuItems = [];
        let cart = {};
        let activeCategory = 'all';
        let currentRating = 0;

        socket.on('connect', () => {
            socket.emit('client_init', { uuid: clientUUID, table: tableId });
        });

        socket.on('menu_sync', (data) => {
            menuItems = data;
            renderCategories();
            renderMenu();
            updateCartState();
        });

        socket.on('order_status_update', (data) => {
            showToast(`Статус вашого замовлення змінено на: ${data.status}`, 'info');
            if (!document.getElementById('orders-modal').classList.contains('hidden')) {
                loadMyOrders();
            }
        });

        socket.on('notification', (data) => {
            showToast(data.message, data.type || 'info');
        });

        function renderCategories() {
            const container = document.getElementById('categories');
            const cats = ['all', ...new Set(menuItems.map(i => i.category))];
            container.innerHTML = cats.map(cat => {
                const label = cat === 'all' ? 'Все меню' : cat;
                const active = cat === activeCategory;
                return `<button onclick="setCategory('${cat}')" class="btn-active px-5 py-2.5 rounded-xl text-xs font-bold border transition whitespace-nowrap ${active ? 'bg-purple-600 border-purple-500 text-white shadow-lg shadow-purple-500/20' : 'bg-white/5 border-white/5 text-gray-400 hover:text-white' }">${label}</button>`;
            }).join('');
        }

        function setCategory(cat) {
            activeCategory = cat;
            renderCategories();
            renderMenu();
        }

        function renderMenu() {
            const grid = document.getElementById('menu-grid');
            const filtered = activeCategory === 'all' ? menuItems : menuItems.filter(i => i.category === activeCategory);
            
            grid.innerHTML = filtered.map(item => {
                const count = cart[item._id] || 0;
                const outOfStock = item.available === false;
                
                return `
                <div class="glass rounded-2xl overflow-hidden flex flex-col border relative ${outOfStock ? 'opacity-40 border-white/5' : 'border-white/5 hover:border-purple-500/20 transition-all'}">
                    <div class="h-44 bg-gray-900 flex items-center justify-center relative overflow-hidden">
                        ${item.image ? `<img src="${item.image}" class="w-full h-full object-cover">` : `<i class="fas fa-utensils text-4xl text-white/10"></i>`}
                        ${outOfStock ? `<div class="absolute inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center"><span class="bg-rose-600 text-white font-black text-xs px-3 py-1.5 rounded-lg uppercase tracking-wider">Немає в наявності</span></div>` : ''}
                    </div>
                    <div class="p-5 flex-1 flex flex-col justify-between">
                        <div>
                            <h3 class="font-bold text-lg text-white mb-1">${item.name}</h3>
                            <p class="text-xs text-gray-400 line-clamp-2 mb-4">${item.description || 'Немає опису.'}</p>
                        </div>
                        <div class="flex items-center justify-between pt-2 border-t border-white/5">
                            <span class="text-xl font-black text-white">${item.price} ₴</span>
                            ${outOfStock ? '' : `
                                <div class="flex items-center gap-2">
                                    ${count > 0 ? `
                                        <button onclick="changeQty('${item._id}', -1)" class="w-8 h-8 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center hover:bg-white/10 text-white font-bold transition">-</button>
                                        <span class="font-black text-sm w-4 text-center text-white">${count}</span>
                                    ` : ''}
                                    <button onclick="changeQty('${item._id}', 1)" class="btn-active h-8 px-4 rounded-lg bg-purple-600 hover:bg-purple-500 text-white font-bold text-xs flex items-center gap-1.5 shadow-md shadow-purple-500/10 transition">
                                        <i class="fas fa-plus"></i> ${count > 0 ? '' : 'Додати'}
                                    </button>
                                </div>
                            `}
                        </div>
                    </div>
                </div>`;
            }).join('');
        }

        function changeQty(id, delta) {
            const item = menuItems.find(i => i._id === id);
            if (item && item.available === false && delta > 0) return;
            
            cart[id] = (cart[id] || 0) + delta;
            if (cart[id] <= 0) delete cart[id];
            updateCartState();
            renderMenu();
            if(!document.getElementById('checkout-modal').classList.contains('hidden')) {
                renderCheckoutList();
            }
        }

        function updateCartState() {
            let total = 0, count = 0;
            Object.keys(cart).forEach(id => {
                const item = menuItems.find(i => i._id === id);
                if (item) {
                    if (item.available === false) {
                        delete cart[id];
                    } else {
                        total += item.price * cart[id];
                        count += cart[id];
                    }
                }
            });

            document.getElementById('cart-total').innerText = total;
            document.getElementById('checkout-total').innerText = total;
            
            const bar = document.getElementById('cart-bar');
            if (count > 0) bar.classList.remove('translate-y-full');
            else { bar.classList.add('translate-y-full'); closeModal('checkout-modal'); }
        }

        function openCheckout() {
            openModal('checkout-modal');
            renderCheckoutList();
        }

        function renderCheckoutList() {
            const list = document.getElementById('checkout-list');
            let html = '';
            Object.keys(cart).forEach(id => {
                const item = menuItems.find(i => i._id === id);
                if (!item) return;
                html += `
                <div class="flex items-center justify-between bg-white/5 p-4 rounded-xl border border-white/5">
                    <div class="flex-1 pr-2">
                        <p class="font-bold text-sm text-white">${item.name}</p>
                        <p class="text-xs text-purple-400 font-bold">${item.price} ₴</p>
                    </div>
                    <div class="flex items-center gap-3">
                        <button onclick="changeQty('${item._id}', -1)" class="w-7 h-7 bg-white/5 hover:bg-white/10 rounded-md flex items-center justify-center font-bold border border-white/10">-</button>
                        <span class="font-black text-sm text-white w-4 text-center">${cart[id]}</span>
                        <button onclick="changeQty('${item._id}', 1)" class="w-7 h-7 bg-white/5 hover:bg-white/10 rounded-md flex items-center justify-center font-bold border border-white/10">+</button>
                        <span class="font-black text-sm text-white pl-2 min-w-[50px] text-right">${item.price * cart[id]} ₴</span>
                    </div>
                </div>`;
            });
            list.innerHTML = html;
        }

        function submitOrder() {
            const items = Object.keys(cart).map(id => {
                const item = menuItems.find(i => i._id === id);
                return { id, name: item.name, price: item.price, qty: cart[id] };
            });

            const payload = {
                uuid: clientUUID,
                table: tableId,
                items: items,
                takeaway: document.getElementById('order-takeaway').checked,
                comment: document.getElementById('order-comment').value
            };

            socket.emit('create_order', payload, (res) => {
                if (res.success) {
                    showToast('Замовлення надіслано на кухню!', 'success');
                    cart = {};
                    document.getElementById('order-comment').value = '';
                    document.getElementById('order-takeaway').checked = false;
                    updateCartState();
                    renderMenu();
                    closeModal('checkout-modal');
                }
            });
        }

        function loadMyOrders() {
            socket.emit('get_client_orders', { uuid: clientUUID }, (orders) => {
                const container = document.getElementById('my-orders-list');
                if(!orders.length) {
                    container.innerHTML = `<p class="text-center text-sm text-gray-500 py-6">У вас немає активних замовлень</p>`;
                    return;
                }
                container.innerHTML = orders.map(o => {
                    let badgeColor = o.status === 'Нове' ? 'bg-blue-500/10 text-blue-400 border-blue-500/20' : (o.status === 'Готується' ? 'bg-amber-500/10 text-amber-400 border-amber-500/20' : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20');
                    return `
                    <div class="bg-white/5 border border-white/5 p-4 rounded-xl space-y-2">
                        <div class="flex justify-between items-center">
                            <span class="text-xs text-gray-400 font-bold">${o.time} ${o.takeaway ? '• З собою' : ''}</span>
                            <span class="px-2.5 py-1 text-[10px] font-black border uppercase tracking-wider rounded-md ${badgeColor}">${o.status}</span>
                        </div>
                        <p class="text-sm text-gray-200">${o.items.map(i => `${i.name} (x${i.qty})`).join(', ')}</p>
                        ${o.comment ? `<p class="text-xs text-purple-400/80 bg-purple-500/5 p-2 rounded-lg border border-purple-500/10">Коментар: ${o.comment}</p>` : ''}
                        <p class="text-lg font-black text-white pt-1">Всього: ${o.total} ₴</p>
                    </div>`;
                }).join('');
            });
        }

        function setRating(val) {
            currentRating = val;
            const stars = document.getElementById('stars-container').children;
            for(let i=0; i<5; i++) {
                if(i < val) { stars[i].className = "fas fa-star cursor-pointer text-amber-400 transition"; }
                else { stars[i].className = "far fa-star cursor-pointer text-amber-400 transition"; }
            }
        }

        function submitReview() {
            if(currentRating === 0) { showToast('Будь ласка, оберіть кількість зірок!', 'error'); return; }
            const comment = document.getElementById('review-comment').value;
            
            socket.emit('submit_review', { table: tableId, rating: currentRating, comment }, (res) => {
                if(res.success) {
                    showToast('Дякуємо за ваш відгук!', 'success');
                    setRating(0);
                    document.getElementById('review-comment').value = '';
                    closeModal('reviews-modal');
                }
            });
        }

        function openModal(id) {
            document.getElementById(id).classList.remove('hidden');
            document.getElementById(id).classList.add('flex');
            if(id === 'orders-modal') loadMyOrders();
        }
        function closeModal(id) {
            document.getElementById(id).classList.remove('flex');
            document.getElementById(id).classList.add('hidden');
        }

        function showToast(msg, type) {
            const root = document.getElementById('toast-container');
            const el = document.createElement('div');
            let colors = type === 'success' ? 'border-emerald-500/30 text-emerald-400 bg-emerald-500/10' : (type === 'error' ? 'border-rose-500/30 text-rose-400 bg-rose-500/10' : 'border-blue-500/30 text-blue-400 bg-blue-500/10');
            el.className = `glass ${colors} border px-4 py-3 rounded-xl shadow-2xl flex items-center gap-2 text-sm font-bold pointer-events-auto transition duration-300`;
            el.innerHTML = `<i class="fas ${type === 'success' ? 'fa-check-circle': 'fa-info-circle'}"></i> <span>${msg}</span>`;
            root.appendChild(el);
            setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 3500);
        }
    </script>
</body>
</html>
"""

# ==============================================================================
# 3. KDS & ADMINISTRATION FRONTEND TEMPLATE (STOCK CONTROL, ACTIONS, REVIEWS PURGE)
# ==============================================================================
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="uk" class="dark">
<head>
    <meta charset="UTF-8">
    <title>Nexus Panel | KDS, Склад & Відгуки</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socketio/4.7.2/socketio.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #040408; color: #fff; font-family: system-ui, sans-serif; }
        .glass-card { background: rgba(22, 22, 33, 0.8); backdrop-filter: blur(12px); border: 1px solid rgba(255,255,255,0.05); }
        .hide-scroll::-webkit-scrollbar { display: none; }
    </style>
</head>
<body class="h-screen flex flex-col overflow-hidden">

    <header class="bg-[#0b0b12] border-b border-white/10 p-4 flex justify-between items-center shadow-2xl">
        <div class="flex items-center gap-3">
            <div class="w-10 h-10 bg-purple-600 rounded-xl flex items-center justify-center text-white text-lg shadow-lg shadow-purple-500/30"><i class="fas fa-tools"></i></div>
            <div>
                <h1 class="font-black tracking-wider text-sm text-purple-400 uppercase">Nexus Premium Admin Console</h1>
                <p class="text-[10px] text-emerald-400 font-bold uppercase tracking-wider" id="admin-status">Синхронізація активна</p>
            </div>
        </div>
        <div class="flex items-center gap-3">
            <button onclick="switchTab('kds')" class="px-4 py-2 rounded-lg font-bold text-xs bg-purple-600 text-white shadow" id="tab-btn-kds">KDS Кухня</button>
            <button onclick="switchTab('menu')" class="px-4 py-2 rounded-lg font-bold text-xs bg-white/5 hover:bg-white/10 text-gray-300 transition" id="tab-btn-menu">Керування Меню & Склад</button>
            <button onclick="switchTab('reviews')" class="px-4 py-2 rounded-lg font-bold text-xs bg-white/5 hover:bg-white/10 text-gray-300 transition" id="tab-btn-reviews">Книга відгуків</button>
            <a href="/logout" class="px-3 py-2 bg-rose-500/10 hover:bg-rose-500/20 text-rose-500 rounded-lg text-xs transition border border-rose-500/20"><i class="fas fa-sign-out-alt"></i></a>
        </div>
    </header>

    <div id="section-kds" class="flex-1 grid grid-cols-3 gap-6 p-6 overflow-hidden">
        <div class="flex flex-col h-full bg-white/[0.01] border border-white/5 rounded-2xl overflow-hidden">
            <div class="p-4 bg-blue-500/10 border-b border-blue-500/20 text-sm font-black text-blue-400 flex justify-between items-center uppercase tracking-wider"><span>Нові замовлення</span><span id="count-new" class="bg-blue-500 text-white text-xs px-2 py-0.5 rounded-full">0</span></div>
            <div id="col-new" class="flex-1 overflow-y-auto p-4 space-y-4 hide-scroll"></div>
        </div>
        <div class="flex flex-col h-full bg-white/[0.01] border border-white/5 rounded-2xl overflow-hidden">
            <div class="p-4 bg-amber-500/10 border-b border-amber-500/20 text-sm font-black text-amber-400 flex justify-between items-center uppercase tracking-wider"><span>Готуються</span><span id="count-cook" class="bg-amber-500 text-gray-900 text-xs px-2 py-0.5 rounded-full font-bold">0</span></div>
            <div id="col-cook" class="flex-1 overflow-y-auto p-4 space-y-4 hide-scroll"></div>
        </div>
        <div class="flex flex-col h-full bg-white/[0.01] border border-white/5 rounded-2xl overflow-hidden">
            <div class="p-4 bg-emerald-500/10 border-b border-emerald-500/20 text-sm font-black text-emerald-400 flex justify-between items-center uppercase tracking-wider"><span>Готово до видачі</span><span id="count-done" class="bg-emerald-500 text-white text-xs px-2 py-0.5 rounded-full">0</span></div>
            <div id="col-done" class="flex-1 overflow-y-auto p-4 space-y-4 hide-scroll"></div>
        </div>
    </div>

    <div id="section-menu" class="flex-1 p-6 overflow-hidden hidden grid grid-cols-3 gap-6">
        <div class="glass-card p-5 rounded-2xl flex flex-col space-y-4 overflow-y-auto hide-scroll">
            <h3 class="font-black text-md text-purple-400 border-b border-white/10 pb-2 uppercase tracking-wider">Додати / Редагувати позицію</h3>
            <input type="hidden" id="item-id">
            <div>
                <label class="text-[10px] uppercase font-bold text-gray-400 block mb-1">Назва страви</label>
                <input type="text" id="item-name" class="w-full bg-white/5 border border-white/10 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-purple-500">
            </div>
            <div class="grid grid-cols-2 gap-3">
                <div>
                    <label class="text-[10px] uppercase font-bold text-gray-400 block mb-1">Ціна (₴)</label>
                    <input type="number" id="item-price" class="w-full bg-white/5 border border-white/10 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-purple-500">
                </div>
                <div>
                    <label class="text-[10px] uppercase font-bold text-gray-400 block mb-1">Категорія</label>
                    <input type="text" id="item-category" placeholder="Кава, Бургери..." class="w-full bg-white/5 border border-white/10 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-purple-500">
                </div>
            </div>
            <div>
                <label class="text-[10px] uppercase font-bold text-gray-400 block mb-1">Опис позиції</label>
                <textarea id="item-description" rows="2" class="w-full bg-white/5 border border-white/10 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-purple-500"></textarea>
            </div>
            <div>
                <label class="text-[10px] uppercase font-bold text-gray-400 block mb-1">Фото страви (Прямо в Базу)</label>
                <input type="file" id="item-file" accept="image/*" class="w-full text-xs text-gray-400 file:mr-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-xs file:font-bold file:bg-purple-600 file:text-white hover:file:bg-purple-500 cursor-pointer">
                <div id="image-preview-container" class="mt-2 hidden"><img id="image-preview" class="h-20 w-full object-cover rounded-xl border border-white/10"></div>
            </div>
            <div class="flex gap-2 pt-2">
                <button onclick="clearItemForm()" class="flex-1 py-3 bg-white/5 hover:bg-white/10 rounded-xl font-bold text-xs border border-white/5 transition">Очистити</button>
                <button onclick="saveMenuItem()" class="flex-1 py-3 bg-purple-600 hover:bg-purple-500 text-white font-bold rounded-xl text-xs transition shadow-md shadow-purple-500/20">Зберегти в базу</button>
            </div>
        </div>
        
        <div class="col-span-2 bg-white/[0.01] border border-white/5 rounded-2xl flex flex-col overflow-hidden">
            <div class="p-4 bg-white/5 border-b border-white/10 font-bold text-sm tracking-wider uppercase">Усі позиції в базі даних ресторану</div>
            <div id="warehouse-list" class="flex-1 overflow-y-auto p-4 space-y-3 hide-scroll"></div>
        </div>
    </div>

    <div id="section-reviews" class="flex-1 p-6 overflow-hidden hidden flex-col">
        <div class="bg-white/[0.01] border border-white/5 rounded-2xl flex flex-col h-full overflow-hidden">
            <div class="p-4 bg-amber-500/5 border-b border-white/10 font-black text-sm text-amber-400 uppercase tracking-wider">Відгуки клієнтів в реальному часі</div>
            <div id="admin-reviews-list" class="flex-1 overflow-y-auto p-6 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 hide-scroll"></div>
        </div>
    </div>

    <script>
        const socket = io();
        let activeOrders = [];
        let base64ImageStr = "";

        socket.on('connect', () => {
            socket.emit('admin_init');
        });

        socket.on('kds_sync', (orders) => {
            activeOrders = orders;
            renderKDS();
        });

        socket.on('warehouse_sync', (menu) => {
            renderWarehouse(menu);
        });

        socket.on('reviews_sync', (reviews) => {
            renderReviews(reviews);
        });

        // 1. KDS Engine
        function renderKDS() {
            const cols = { 'Нове': document.getElementById('col-new'), 'Готується': document.getElementById('col-cook'), 'Готово': document.getElementById('col-done') };
            Object.values(cols).forEach(c => c.innerHTML = '');
            let counts = { 'Нове': 0, 'Готується': 0, 'Готово': 0 };

            activeOrders.forEach(o => {
                if (o.status === 'Закрито') return;
                counts[o.status]++;
                
                let actionBtn = '';
                if(o.status === 'Нове') actionBtn = `<button onclick="changeStatus('${o._id}', 'Готується')" class="w-full mt-4 py-2.5 bg-blue-600 hover:bg-blue-500 rounded-xl font-bold text-xs text-white transition">Почати готувати <i class="fas fa-fire ml-1"></i></button>`;
                if(o.status === 'Готується') actionBtn = `<button onclick="changeStatus('${o._id}', 'Готово')" class="w-full mt-4 py-2.5 bg-amber-500 hover:bg-amber-400 rounded-xl font-black text-xs text-gray-900 transition shadow-lg shadow-amber-500/20">Готово на видачу <i class="fas fa-check ml-1"></i></button>`;
                if(o.status === 'Готово') actionBtn = `<button onclick="changeStatus('${o._id}', 'Закрито')" class="w-full mt-4 py-2.5 bg-emerald-600 hover:bg-emerald-500 rounded-xl font-bold text-xs text-white transition">Закрити чек <i class="fas fa-times ml-1"></i></button>`;

                const card = `
                <div class="glass-card p-4 rounded-xl relative border border-white/5">
                    <div class="flex justify-between items-center mb-3">
                        <span class="text-2xl font-black text-white">Стіл #${o.table}</span>
                        <span class="text-xs text-gray-400 font-bold bg-black/40 px-2 py-1 rounded">${o.time}</span>
                    </div>
                    ${o.takeaway ? `<div class="mb-2"><span class="bg-rose-500/20 text-rose-400 font-black text-[9px] uppercase tracking-widest px-2 py-0.5 rounded border border-rose-500/30">З собою</span></div>` : ''}
                    <div class="bg-black/30 rounded-xl p-3 border border-white/5 space-y-1">
                        ${o.items.map(i => `<div class="flex justify-between text-xs text-gray-300"><span>${i.name}</span><span class="font-bold text-white bg-white/10 px-1.5 py-0.2 rounded">x${i.qty}</span></div>`).join('')}
                    </div>
                    ${o.comment ? `<div class="mt-2 text-xs text-purple-400 bg-purple-500/5 p-2 rounded border border-purple-500/10">Коментар: ${o.comment}</div>` : ''}
                    <div class="flex justify-between items-center mt-3 pt-3 border-t border-white/5">
                        <span class="text-xs text-gray-500 uppercase font-bold">Всього</span>
                        <span class="font-black text-md text-purple-400">${o.total} ₴</span>
                    </div>
                    ${actionBtn}
                </div>`;
                cols[o.status].innerHTML += card;
            });

            document.getElementById('count-new').innerText = counts['Нове'];
            document.getElementById('count-cook').innerText = counts['Готується'];
            document.getElementById('count-done').innerText = counts['Готово'];
        }

        function changeStatus(id, newStatus) {
            socket.emit('admin_update_order', { id, status: newStatus });
        }

        // 2. Warehouse Engine & Base64 Converter
        document.getElementById('item-file').addEventListener('change', function(e) {
            const file = e.target.files[0];
            if(file) {
                const reader = new FileReader();
                reader.onload = function(evt) {
                    base64ImageStr = evt.target.result;
                    document.getElementById('image-preview').src = base64ImageStr;
                    document.getElementById('image-preview-container').classList.remove('hidden');
                };
                reader.readAsDataURL(file);
            }
        });

        function saveMenuItem() {
            const payload = {
                id: document.getElementById('item-id').value,
                name: document.getElementById('item-name').value,
                price: parseFloat(document.getElementById('item-price').value),
                category: document.getElementById('item-category').value,
                description: document.getElementById('item-description').value,
                image: base64ImageStr
            };
            if(!payload.name || !payload.price || !payload.category) { alert('Заповніть обовʼязкові поля!'); return; }
            socket.emit('admin_save_menu_item', payload);
            clearItemForm();
        }

        function renderWarehouse(menu) {
            const container = document.getElementById('warehouse-list');
            container.innerHTML = menu.map(i => `
            <div class="glass-card p-4 rounded-xl flex items-center justify-between border border-white/5">
                <div class="flex items-center gap-4">
                    <div class="w-14 h-14 bg-gray-900 rounded-lg overflow-hidden flex items-center justify-center border border-white/10 flex-shrink-0">
                        ${i.image ? `<img src="${i.image}" class="w-full h-full object-cover">` : `<i class="fas fa-utensils text-gray-600"></i>`}
                    </div>
                    <div>
                        <h4 class="font-bold text-sm text-white">${i.name} <span class="text-xs font-normal text-gray-500">(${i.category})</span></h4>
                        <p class="text-xs text-purple-400 font-bold">${i.price} ₴</p>
                    </div>
                </div>
                <div class="flex items-center gap-2">
                    <button onclick="toggleStock('${i._id}')" class="px-3 py-2 rounded-lg font-bold text-xs border transition ${i.available !== false ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20 hover:bg-emerald-500/20' : 'bg-rose-500/10 text-rose-400 border-rose-500/20 hover:bg-rose-500/20'}">
                        ${i.available !== false ? '<i class="fas fa-check-circle mr-1"></i> На складі' : '<i class="fas fa-times-circle mr-1"></i> Немає'}
                    </button>
                    <button onclick="editItem(${JSON.stringify(i).replace(/"/g, '&quot;')})" class="p-2 bg-white/5 hover:bg-white/10 rounded-lg text-gray-300 border border-white/5"><i class="fas fa-edit"></i></button>
                    <button onclick="deleteItem('${i._id}')" class="p-2 bg-rose-500/10 hover:bg-rose-500/20 rounded-lg text-rose-500 border border-rose-500/20"><i class="fas fa-trash-alt"></i></button>
                </div>
            </div>`).join('');
        }

        function toggleStock(id) { socket.emit('admin_toggle_stock', { id }); }
        function deleteItem(id) { if(confirm('Видалити страву з бази назавжди?')) socket.emit('admin_delete_item', { id }); }

        function editItem(i) {
            document.getElementById('item-id').value = i._id;
            document.getElementById('item-name').value = i.name;
            document.getElementById('item-price').value = i.price;
            document.getElementById('item-category').value = i.category;
            document.getElementById('item-description').value = i.description || '';
            base64ImageStr = i.image || '';
            if(i.image) {
                document.getElementById('image-preview').src = i.image;
                document.getElementById('image-preview-container').classList.remove('hidden');
            } else {
                document.getElementById('image-preview-container').class-list.add('hidden');
            }
        }

        function clearItemForm() {
            document.getElementById('item-id').value = '';
            document.getElementById('item-name').value = '';
            document.getElementById('item-price').value = '';
            document.getElementById('item-category').value = '';
            document.getElementById('item-description').value = '';
            document.getElementById('item-file').value = '';
            base64ImageStr = "";
            document.getElementById('image-preview-container').classList.add('hidden');
        }

        // 3. Reviews Engine
        function renderReviews(reviews) {
            const container = document.getElementById('admin-reviews-list');
            if(!reviews.length) { container.innerHTML = `<p class="col-span-full text-center text-sm text-gray-500">Книга відгуків порожня</p>`; return; }
            
            container.innerHTML = reviews.map(r => {
                let stars = '';
                for(let i=1; i<=5; i++) stars += `<i class="${i <= r.rating ? 'fas' : 'far'} fa-star text-amber-400 text-xs"></i>`;
                return `
                <div class="glass-card p-4 rounded-xl relative border border-white/5 flex flex-col justify-between">
                    <div>
                        <div class="flex justify-between items-center mb-2">
                            <span class="font-black text-sm text-purple-400">Стіл #${r.table}</span>
                            <span class="text-[10px] text-gray-500">${r.time}</span>
                        </div>
                        <div class="mb-2 flex gap-0.5">${stars}</div>
                        <p class="text-xs text-gray-200 italic">"${r.comment || 'Без текстового коментаря'}"</p>
                    </div>
                    <div class="mt-4 pt-3 border-t border-white/5 flex justify-end">
                        <button onclick="purgeReview('${r._id}')" class="px-2.5 py-1.5 bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/20 text-rose-500 text-[10px] font-bold rounded-lg transition uppercase tracking-wider">Видалити</button>
                    </div>
                </div>`;
            }).join('');
        }

        function purgeReview(id) { if(confirm('Видалити цей відгук із книги?')) socket.emit('admin_delete_review', { id }); }

        // 4. Tab System Switching
        function switchTab(tab) {
            document.getElementById('section-kds').classList.add('hidden');
            document.getElementById('section-menu').classList.add('hidden');
            document.getElementById('section-reviews').classList.add('hidden');
            document.getElementById('section-' + tab).classList.remove('hidden');
            if(tab === 'reviews') document.getElementById('section-reviews').classList.add('flex');

            ['kds', 'menu', 'reviews'].forEach(t => {
                const btn = document.getElementById('tab-btn-' + t);
                if(t === tab) { btn.className = "px-4 py-2 rounded-lg font-bold text-xs bg-purple-600 text-white shadow"; }
                else { btn.className = "px-4 py-2 rounded-lg font-bold text-xs bg-white/5 hover:bg-white/10 text-gray-300 transition"; }
            });
        }
    </script>
</body>
</html>
"""

# ==============================================================================
# 4. FLASK ROUTING ARCHITECTURE (TABLE ROUTING, ADMIN AUTH)
# ==============================================================================
@app.route('/')
def global_index():
    # Fallback на перший столик, якщо зайшли без роуту стола
    return redirect('/1')

@app.route('/<table_id>')
def customer_index(table_id):
    # Динамічний роут під будь-який столик (/1, /2, /vip)
    return render_template_string(CUSTOMER_HTML, table_id=table_id)

@app.route('/admin', methods=['GET', 'POST'])
def admin_portal():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_logged'] = True
            return redirect(url_for('admin_portal'))
    if session.get('admin_logged'):
        return render_template_string(ADMIN_HTML)
    return """
    <form method="POST" style="background:#040408; height:100vh; display:flex; align-items:center; justify-content:center; margin:0; font-family:sans-serif;">
        <div style="background:#11111e; padding:30px; border-radius:20px; border:1px solid #222; text-align:center; box-shadow:0 10px 30px rgba(0,0,0,0.5);">
            <h2 style="color:#8b5cf6; margin-bottom:20px; text-transform:uppercase; letter-spacing:2px; font-size:18px;">Nexus Authentication</h2>
            <input type="password" name="password" placeholder="Введіть код доступу" style="padding:12px 20px; font-size:16px; border-radius:10px; border:1px solid #333; background:#07070c; color:#fff; text-align:center; outline:none; focus:border-purple-500; width:200px;">
        </div>
    </form>
    """

@app.route('/logout')
def admin_logout():
    session.pop('admin_logged', None)
    return redirect(url_for('global_index'))

# ==============================================================================
# 5. SOCKET.IO CONTROLLERS (PIPELINE PROCESSORS)
# ==============================================================================
@socketio.on('client_init')
def handle_client_init(data):
    client_id = data.get('uuid')
    if client_id:
        join_room(client_id)
    # Віддаємо тільки страви актуального меню
    menu = [serialize_doc(i) for i in db.menu.find()]
    emit('menu_sync', menu)

@socketio.on('create_order')
def handle_create_order(data):
    client_id = data.get('uuid')
    items = data.get('items', [])
    
    # Перераховуємо суму на бекенді для безпеки
    total = 0
    for i in items:
        db_item = db.menu.find_one({"_id": ObjectId(i['id'])})
        if db_item:
            total += db_item['price'] * i['qty']

    order = {
        'uuid': client_id,
        'table': data.get('table', '1'),
        'items': items,
        'total': total,
        'status': 'Нове',
        'takeaway': data.get('takeaway', False),
        'comment': data.get('comment', '').strip(),
        'time': datetime.now().strftime('%H:%M'),
        'timestamp': datetime.now()
    }
    
    res = db.orders.insert_one(order)
    order['_id'] = str(res.inserted_id)
    order.pop('timestamp', None)
    
    # Миттєво оновлюємо дошку KDS у адмінів
    socketio.emit('kds_sync', [serialize_doc(o) for o in db.orders.find({"status": {"$ne": "Закрито"}}).sort("timestamp", -1)], room='admins')
    return {'success': True}

@socketio.on('get_client_orders')
def handle_get_client_orders(data):
    client_id = data.get('uuid')
    return [serialize_doc(o) for o in db.orders.find({"uuid": client_id}).sort("timestamp", -1)]

@socketio.on('submit_review')
def handle_submit_review(data):
    review = {
        'table': data.get('table', '1'),
        'rating': int(data.get('rating', 5)),
        'comment': data.get('comment', '').strip(),
        'time': datetime.now().strftime('%d.%m %H:%M'),
        'timestamp': datetime.now()
    }
    db.reviews.insert_one(review)
    # Синхронізуємо відгуки у адміна
    socketio.emit('reviews_sync', [serialize_doc(r) for r in db.reviews.find().sort("timestamp", -1)], room='admins')
    return {'success': True}

# --- BACKEND ADMIN EMITTERS ---
@socketio.on('admin_init')
def handle_admin_init():
    if session.get('admin_logged'):
        join_room('admins')
        emit('kds_sync', [serialize_doc(o) for o in db.orders.find({"status": {"$ne": "Закрито"}}).sort("timestamp", -1)])
        emit('warehouse_sync', [serialize_doc(i) for i in db.menu.find()])
        emit('reviews_sync', [serialize_doc(r) for r in db.reviews.find().sort("timestamp", -1)])

@socketio.on('admin_update_order')
def handle_admin_update_order(data):
    if session.get('admin_logged'):
        o_id = data.get('id')
        new_status = data.get('status')
        
        db.orders.update_one({"_id": ObjectId(o_id)}, {"$set": {"status": new_status}})
        
        # Надсилаємо таргетоване сповіщення клієнту про готовність/зміну
        order = db.orders.find_one({"_id": ObjectId(o_id)})
        if order and order.get('uuid'):
            socketio.emit('order_status_update', {'status': new_status}, room=order['uuid'])
            
        # Оновлюємо KDS
        socketio.emit('kds_sync', [serialize_doc(o) for o in db.orders.find({"status": {"$ne": "Закрито"}}).sort("timestamp", -1)], room='admins')

@socketio.on('admin_toggle_stock')
def handle_admin_toggle_stock(data):
    if session.get('admin_logged'):
        item = db.menu.find_one({"_id": ObjectId(data['id'])})
        if item:
            # Виправлено !== на пітонівський !=
            new_state = False if item.get('available') != False else True
            db.menu.update_one({"_id": ObjectId(data['id'])}, {"$set": {"available": new_state}})
            
            # Сповіщаємо КЛІЄНТІВ, щоб у них миттєво зникла/з'явилася кнопка "Додати"
            updated_menu = [serialize_doc(i) for i in db.menu.find()]
            socketio.emit('menu_sync', updated_menu)
            socketio.emit('warehouse_sync', updated_menu, room='admins')
@socketio.on('admin_save_menu_item')
def handle_admin_save_menu_item(data):
    if session.get('admin_logged'):
        item_id = data.get('id')
        item_data = {
            'name': data['name'],
            'price': data['price'],
            'category': data['category'],
            'description': data['description']
        }
        # Оновлюємо картинку тільки якщо була завантажена нова
        if data.get('image'):
            item_data['image'] = data['image']

        if item_id:
            db.menu.update_one({"_id": ObjectId(item_id)}, {"$set": item_data})
        else:
            item_data['available'] = True
            db.menu.insert_one(item_data)
            
        updated_menu = [serialize_doc(i) for i in db.menu.find()]
        socketio.emit('menu_sync', updated_menu)
        socketio.emit('warehouse_sync', updated_menu, room='admins')

@socketio.on('admin_delete_item')
def handle_admin_delete_item(data):
    if session.get('admin_logged'):
        db.menu.delete_one({"_id": ObjectId(data['id'])})
        updated_menu = [serialize_doc(i) for i in db.menu.find()]
        socketio.emit('menu_sync', updated_menu)
        socketio.emit('warehouse_sync', updated_menu, room='admins')

@socketio.on('admin_delete_review')
def handle_admin_delete_review(data):
    if session.get('admin_logged'):
        db.reviews.delete_one({"_id": ObjectId(data['id'])})
        socketio.emit('reviews_sync', [serialize_doc(r) for r in db.reviews.find().sort("timestamp", -1)], room='admins')

if __name__ == '__main__':
    # Генерація демо-страд, якщо база чиста
    if db.menu.count_documents({}) == 0:
        db.menu.insert_many([
            {"name": "Cyber Cappuccino", "category": "Кава", "price": 85, "description": "Подвійний еспресо, збите ультрапастеризоване молоко", "available": true},
            {"name": "Neon Burger", "category": "Бургери", "price": 260, "description": "Мраморна яловичина, соус дорблю, карамелізована цибуля", "available": true},
            {"name": "Glitch Fries", "category": "Снеки", "price": 95, "description": "Хрустка картопля фрі з пармезаном та трюфельним маслом", "available": true}
        ])
    socketio.run(app, host='0.0.0.0', port=10000)
