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
# 1. CORE SYSTEM & SECURITY INITIALIZATION
# ==============================================================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'nexus-ultra-secure-key-2026')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/cafe_db')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

client = MongoClient(MONGO_URI)
db = client.get_default_database(default='cafe_db')

# In-memory storage for real-time monitoring
active_sessions = {}

def serialize_doc(doc):
    if not doc: return None
    doc['_id'] = str(doc['_id'])
    return doc

# ==============================================================================
# 2. FRONTEND TEMPLATES (TAILWIND + GLASSMORPHISM + NEON)
# ==============================================================================

CUSTOMER_HTML = """
<!DOCTYPE html>
<html lang="uk" class="dark">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Nexus Premium Cafe</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socketio/4.7.2/socketio.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800;900&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Outfit', sans-serif; background-color: #050505; color: #f3f4f6; -webkit-tap-highlight-color: transparent; }
        .glass-panel { background: rgba(20, 20, 25, 0.75); backdrop-filter: blur(16px); border: 1px solid rgba(255,255,255,0.05); }
        .neon-accent { text-shadow: 0 0 20px rgba(139, 92, 246, 0.5); }
        .gradient-text { background: linear-gradient(135deg, #a78bfa 0%, #3b82f6 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .hide-scroll::-webkit-scrollbar { display: none; }
        .btn-press:active { transform: scale(0.96); }
        .processing-bg { background: linear-gradient(270deg, #4f46e5, #7c3aed, #2563eb); background-size: 600% 600%; animation: processing 2s ease infinite; }
        @keyframes processing { 0%{background-position:0% 50%} 50%{background-position:100% 50%} 100%{background-position:0% 50%} }
    </style>
</head>
<body class="pb-36 relative overflow-x-hidden">

    <div class="fixed top-[-10%] left-[-10%] w-96 h-96 bg-indigo-600/20 rounded-full blur-[100px] pointer-events-none z-0"></div>
    <div class="fixed bottom-[-10%] right-[-10%] w-96 h-96 bg-purple-600/20 rounded-full blur-[100px] pointer-events-none z-0"></div>

    <header class="glass-panel sticky top-0 z-40 px-5 py-4 border-b border-white/5">
        <div class="max-w-7xl mx-auto flex justify-between items-center relative z-10">
            <div class="flex items-center gap-3">
                <div class="w-12 h-12 rounded-2xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center shadow-[0_0_20px_rgba(99,102,241,0.4)]">
                    <i class="fas fa-hexagon-nodes text-2xl text-white"></i>
                </div>
                <div>
                    <h1 class="text-2xl font-black tracking-tight gradient-text uppercase">Nexus</h1>
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 bg-emerald-500 rounded-full animate-pulse"></span>
                        <p class="text-xs text-gray-400 font-semibold tracking-widest uppercase">System Online</p>
                    </div>
                </div>
            </div>
            
            <div class="hidden sm:flex items-center gap-2 bg-white/5 border border-white/10 px-4 py-2 rounded-xl">
                <i class="fas fa-star text-amber-400"></i>
                <div class="text-xs font-bold text-gray-300">
                    <span class="block text-gray-500 uppercase text-[9px] tracking-wider">Level 1</span>
                    <span id="loyalty-points">0</span> XP
                </div>
            </div>
        </div>
    </header>

    <main class="max-w-7xl mx-auto px-5 pt-8 relative z-10">
        
        <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center mb-8 gap-4">
            <h2 class="text-3xl font-black flex items-center gap-3">
                Меню <span class="text-indigo-500">.</span>
            </h2>
            <div class="flex gap-2 w-full sm:w-auto">
                <button onclick="openMyOrders()" class="flex-1 sm:flex-none btn-press px-5 py-3 bg-white/5 hover:bg-white/10 border border-white/10 rounded-xl font-bold text-sm transition flex items-center justify-center gap-2">
                    <i class="fas fa-history text-indigo-400"></i> Замовлення
                </button>
                <button onclick="callWaiter()" class="flex-1 sm:flex-none btn-press px-5 py-3 bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/20 rounded-xl font-bold text-sm text-amber-400 transition flex items-center justify-center gap-2">
                    <i class="fas fa-bell"></i> Офіціант
                </button>
            </div>
        </div>

        <div id="category-bar" class="flex gap-3 overflow-x-auto hide-scroll pb-4 mb-6 sticky top-24 z-30 bg-[#050505]/90 backdrop-blur-md -mx-5 px-5"></div>

        <div id="menu-grid" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5"></div>
    </main>

    <div id="smart-cart" class="fixed bottom-0 left-0 right-0 p-5 z-40 transform translate-y-full transition-transform duration-500 ease-out pointer-events-none">
        <div class="max-w-3xl mx-auto glass-panel rounded-3xl p-2 pl-6 flex items-center justify-between border border-indigo-500/30 shadow-[0_10px_40px_rgba(0,0,0,0.8)] pointer-events-auto">
            <div class="flex items-center gap-4">
                <div class="relative">
                    <i class="fas fa-bag-shopping text-2xl text-indigo-400"></i>
                    <span id="cart-badge" class="absolute -top-2 -right-2 bg-rose-500 text-white text-xs font-black w-5 h-5 flex items-center justify-center rounded-full shadow-lg">0</span>
                </div>
                <div>
                    <p class="text-xs text-gray-400 font-bold uppercase tracking-wider">Сума до сплати</p>
                    <p class="text-2xl font-black text-white"><span id="cart-total-amount">0</span> ₴</p>
                </div>
            </div>
            <button onclick="startCheckout()" class="btn-press bg-indigo-600 text-white px-8 py-4 rounded-2xl font-black text-sm uppercase tracking-wider shadow-[0_0_20px_rgba(79,70,229,0.4)] flex items-center gap-2">
                Оформити <i class="fas fa-arrow-right"></i>
            </button>
        </div>
    </div>

    <div id="checkout-modal" class="fixed inset-0 bg-black/90 backdrop-blur-xl z-50 hidden flex-col items-center justify-end sm:justify-center p-0 sm:p-4 opacity-0 transition-opacity duration-300">
        <div class="w-full max-w-lg glass-panel rounded-t-[2rem] sm:rounded-3xl p-6 relative flex flex-col max-h-[90vh] transform translate-y-full sm:translate-y-0 transition-transform duration-300" id="checkout-content">
            <button onclick="closeCheckout()" class="absolute top-6 right-6 w-10 h-10 bg-white/5 rounded-full flex items-center justify-center text-gray-400 hover:text-white hover:bg-white/10 transition"><i class="fas fa-times text-xl"></i></button>
            
            <h3 class="text-2xl font-black mb-6">Оформлення</h3>
            
            <div id="checkout-items" class="flex-1 overflow-y-auto hide-scroll space-y-3 mb-6"></div>
            
            <div class="space-y-4 mb-6">
                <div class="bg-white/5 border border-white/10 p-4 rounded-2xl flex items-center gap-4">
                    <div class="w-12 h-12 bg-indigo-500/20 rounded-xl flex items-center justify-center text-indigo-400"><i class="fas fa-location-dot text-xl"></i></div>
                    <div class="flex-1">
                        <label class="text-xs text-gray-400 font-bold uppercase block mb-1">Номер вашого столу</label>
                        <select id="table-number" class="w-full bg-transparent text-white font-black text-lg focus:outline-none appearance-none">
                            <option value="1" class="bg-gray-900">Стіл #01</option>
                            <option value="2" class="bg-gray-900">Стіл #02 (VIP)</option>
                            <option value="3" class="bg-gray-900">Стіл #03</option>
                            <option value="4" class="bg-gray-900">Стіл #04</option>
                        </select>
                    </div>
                    <i class="fas fa-chevron-down text-gray-600"></i>
                </div>
            </div>

            <div class="border-t border-white/10 pt-6 flex justify-between items-end mb-6">
                <div>
                    <p class="text-xs text-gray-400 font-bold uppercase mb-1">Отримуєте XP балів</p>
                    <p class="text-amber-400 font-black flex items-center gap-1"><i class="fas fa-bolt"></i> +<span id="checkout-xp">0</span></p>
                </div>
                <div class="text-right">
                    <p class="text-xs text-gray-400 font-bold uppercase mb-1">До сплати</p>
                    <p class="text-3xl font-black text-white"><span id="checkout-final-total">0</span> ₴</p>
                </div>
            </div>

            <button id="pay-btn" onclick="processOrder()" class="w-full processing-bg text-white font-black text-lg py-5 rounded-2xl shadow-[0_0_30px_rgba(79,70,229,0.4)] transition-all flex items-center justify-center gap-3">
                <i class="fab fa-apple text-2xl"></i> Pay
            </button>
        </div>
    </div>

    <div id="orders-modal" class="fixed inset-0 bg-black/90 backdrop-blur-xl z-50 hidden flex-col items-center justify-center p-4 opacity-0 transition-opacity">
        <div class="w-full max-w-2xl glass-panel rounded-3xl p-6 sm:p-8 relative max-h-[85vh] flex flex-col">
            <button onclick="closeMyOrders()" class="absolute top-6 right-6 text-gray-400 hover:text-white"><i class="fas fa-times text-2xl"></i></button>
            <h3 class="text-2xl font-black mb-6 flex items-center gap-3"><i class="fas fa-radar text-indigo-500"></i> Ваші радари</h3>
            
            <div id="orders-list" class="flex-1 overflow-y-auto hide-scroll space-y-4"></div>
        </div>
    </div>

    <div id="toast-root" class="fixed top-6 left-1/2 transform -translate-x-1/2 z-[100] flex flex-col gap-2 w-full max-w-sm px-4 pointer-events-none"></div>

    <script>
        // 1. Управління UUID клієнта
        let clientUUID = localStorage.getItem('nexus_uuid');
        if (!clientUUID) {
            clientUUID = crypto.randomUUID ? crypto.randomUUID() : 'user-' + Date.now();
            localStorage.setItem('nexus_uuid', clientUUID);
        }

        const socket = io({ auth: { uuid: clientUUID } });
        let menuData = [];
        let cart = JSON.parse(localStorage.getItem('nexus_cart_' + clientUUID)) || {};
        let activeCategory = 'all';

        // 2. Ініціалізація Socket
        socket.on('connect', () => {
            socket.emit('client_init', { uuid: clientUUID, table: localStorage.getItem('nexus_table') || '1' });
            updateCartUI();
        });

        socket.on('menu_sync', (data) => {
            menuData = data;
            renderCategories();
            renderMenu();
            updateCartUI();
        });

        socket.on('order_status_update', (data) => {
            showToast(`Ваше замовлення тепер: ${data.status}`, 'success');
            if(!document.getElementById('orders-modal').classList.contains('hidden')) {
                loadMyOrders();
            }
        });

        // 3. UI Рендерінг
        function renderCategories() {
            const bar = document.getElementById('category-bar');
            const cats = ['all', ...new Set(menuData.map(i => i.category))];
            
            bar.innerHTML = cats.map(cat => {
                const label = cat === 'all' ? 'Всі позиції' : cat;
                const isActive = cat === activeCategory;
                const baseStyle = "px-6 py-3 rounded-2xl font-bold text-sm whitespace-nowrap transition-all border";
                const activeStyle = isActive 
                    ? "bg-indigo-600 border-indigo-500 text-white shadow-[0_0_15px_rgba(79,70,229,0.4)]" 
                    : "bg-white/5 border-white/10 text-gray-400 hover:bg-white/10 hover:text-white";
                return `<button onclick="setCategory('${cat}')" class="btn-press ${baseStyle} ${activeStyle}">${label}</button>`;
            }).join('');
        }

        function setCategory(cat) {
            activeCategory = cat;
            renderCategories();
            renderMenu();
        }

        function renderMenu() {
            const grid = document.getElementById('menu-grid');
            const items = activeCategory === 'all' ? menuData : menuData.filter(i => i.category === activeCategory);
            
            grid.innerHTML = items.map(item => {
                const qty = cart[item._id] || 0;
                return `
                <div class="glass-panel rounded-3xl p-2 flex flex-col relative group overflow-hidden border border-white/5 hover:border-indigo-500/30 transition-all">
                    <div class="p-5 flex-1">
                        <div class="flex justify-between items-start mb-3">
                            <div class="w-10 h-10 bg-gray-900 rounded-xl flex items-center justify-center text-indigo-400 border border-gray-800"><i class="fas fa-utensils text-sm"></i></div>
                            <span class="bg-white/5 text-gray-400 text-[10px] font-bold uppercase px-3 py-1 rounded-full border border-white/10">${item.category}</span>
                        </div>
                        <h3 class="text-lg font-bold text-white mb-1 group-hover:text-indigo-400 transition">${item.name}</h3>
                        <p class="text-xs text-gray-500 line-clamp-2">${item.description}</p>
                    </div>
                    
                    <div class="p-4 bg-black/40 rounded-2xl flex items-center justify-between border border-white/5 mt-auto">
                        <div class="font-black text-xl text-white">${item.price} <span class="text-sm text-indigo-500">₴</span></div>
                        
                        <div class="flex items-center gap-3">
                            ${qty > 0 ? `
                                <button onclick="modCart('${item._id}', -1)" class="w-8 h-8 rounded-xl bg-gray-800 text-white font-bold flex items-center justify-center hover:bg-gray-700 transition">-</button>
                                <span class="font-black w-4 text-center">${qty}</span>
                            ` : ''}
                            <button onclick="modCart('${item._id}', 1)" class="h-10 px-4 rounded-xl bg-indigo-600 text-white font-bold text-sm flex items-center gap-2 hover:bg-indigo-500 transition shadow-lg btn-press">
                                <i class="fas fa-plus"></i> ${qty > 0 ? '' : 'Додати'}
                            </button>
                        </div>
                    </div>
                </div>`;
            }).join('');
        }

        // 4. Логіка кошика
        function modCart(id, delta) {
            cart[id] = (cart[id] || 0) + delta;
            if (cart[id] <= 0) delete cart[id];
            localStorage.setItem('nexus_cart_' + clientUUID, JSON.stringify(cart));
            updateCartUI();
            renderMenu();
        }

        function updateCartUI() {
            const bar = document.getElementById('smart-cart');
            let total = 0, count = 0;
            
            Object.keys(cart).forEach(id => {
                const item = menuData.find(i => i._id === id);
                if (item) { total += item.price * cart[id]; count += cart[id]; }
            });

            document.getElementById('cart-badge').innerText = count;
            document.getElementById('cart-total-amount').innerText = total;

            if (count > 0) {
                bar.classList.remove('translate-y-full');
            } else {
                bar.classList.add('translate-y-full');
                closeCheckout();
            }
            
            // Оновлення XP
            const xp = parseInt(localStorage.getItem('nexus_xp_' + clientUUID) || '0');
            document.getElementById('loyalty-points').innerText = xp;
        }

        // 5. Оформлення та процесинг
        function startCheckout() {
            const m = document.getElementById('checkout-modal');
            const c = document.getElementById('checkout-content');
            const list = document.getElementById('checkout-items');
            
            let total = 0;
            list.innerHTML = Object.keys(cart).map(id => {
                const item = menuData.find(i => i._id === id);
                if(!item) return '';
                total += item.price * cart[id];
                return `
                <div class="flex justify-between items-center bg-white/5 p-4 rounded-2xl border border-white/10">
                    <div><p class="font-bold text-sm">${item.name}</p><p class="text-xs text-indigo-400 font-bold">${item.price} ₴ <span class="text-gray-500 font-normal">x ${cart[id]}</span></p></div>
                    <div class="font-black text-white">${item.price * cart[id]} ₴</div>
                </div>`;
            }).join('');
            
            document.getElementById('checkout-final-total').innerText = total;
            document.getElementById('checkout-xp').innerText = Math.floor(total * 0.1); // 10% кэшбек XP
            
            m.classList.remove('hidden');
            setTimeout(() => { m.classList.remove('opacity-0'); c.classList.remove('translate-y-full'); }, 10);
        }

        function closeCheckout() {
            const m = document.getElementById('checkout-modal');
            const c = document.getElementById('checkout-content');
            m.classList.add('opacity-0'); c.classList.add('translate-y-full');
            setTimeout(() => m.classList.add('hidden'), 300);
        }

        function processOrder() {
            const btn = document.getElementById('pay-btn');
            btn.innerHTML = '<i class="fas fa-spinner fa-spin text-2xl"></i> Обробка банку...';
            btn.classList.add('opacity-80', 'pointer-events-none');

            setTimeout(() => {
                const items = Object.keys(cart).map(id => {
                    const item = menuData.find(i => i._id === id);
                    return { id: id, name: item.name, price: item.price, qty: cart[id] };
                });
                
                const table = document.getElementById('table-number').value;
                localStorage.setItem('nexus_table', table);

                socket.emit('create_order', { uuid: clientUUID, table: table, items: items }, (res) => {
                    if (res.success) {
                        // Додаємо XP
                        let total = items.reduce((sum, i) => sum + (i.price * i.qty), 0);
                        let currentXP = parseInt(localStorage.getItem('nexus_xp_' + clientUUID) || '0');
                        localStorage.setItem('nexus_xp_' + clientUUID, currentXP + Math.floor(total * 0.1));
                        
                        cart = {};
                        localStorage.removeItem('nexus_cart_' + clientUUID);
                        updateCartUI();
                        renderMenu();
                        closeCheckout();
                        showToast('Оплачено! Замовлення на кухні.', 'success');
                        
                        btn.innerHTML = '<i class="fab fa-apple text-2xl"></i> Pay';
                        btn.classList.remove('opacity-80', 'pointer-events-none');
                    }
                });
            }, 1500); // Імітація затримки процесингу
        }

        // 6. Історія замовлень
        function openMyOrders() {
            document.getElementById('orders-modal').classList.remove('hidden');
            setTimeout(() => document.getElementById('orders-modal').classList.remove('opacity-0'), 10);
            loadMyOrders();
        }

        function closeMyOrders() {
            document.getElementById('orders-modal').classList.add('opacity-0');
            setTimeout(() => document.getElementById('orders-modal').classList.add('hidden'), 300);
        }

        function loadMyOrders() {
            const container = document.getElementById('orders-list');
            container.innerHTML = '<div class="text-center py-10"><i class="fas fa-circle-notch fa-spin text-3xl text-indigo-500"></i></div>';
            
            socket.emit('get_client_orders', { uuid: clientUUID }, (orders) => {
                if (orders.length === 0) {
                    container.innerHTML = '<div class="text-center py-10 text-gray-500"><i class="fas fa-ghost text-4xl mb-4 block opacity-50"></i>Історія чиста</div>';
                    return;
                }

                container.innerHTML = orders.map(o => {
                    let stClass = o.status === 'Нове' ? 'text-blue-400 bg-blue-500/10 border-blue-500/30' : 
                                 (o.status === 'Готується' ? 'text-amber-400 bg-amber-500/10 border-amber-500/30' : 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30');
                    return `
                    <div class="bg-white/5 border border-white/10 rounded-2xl p-5 flex flex-col gap-3">
                        <div class="flex justify-between items-center">
                            <div class="text-xs font-bold text-gray-500">${o.time} • Стіл ${o.table}</div>
                            <div class="px-3 py-1 rounded-lg border text-xs font-black uppercase tracking-wider ${stClass}">${o.status}</div>
                        </div>
                        <div class="text-sm text-gray-300">
                            ${o.items.map(i => `${i.name} <span class="text-gray-500">x${i.qty}</span>`).join(', ')}
                        </div>
                        <div class="font-black text-xl text-white mt-2">${o.total} ₴</div>
                    </div>`;
                }).join('');
            });
        }

        function callWaiter() {
            const table = localStorage.getItem('nexus_table') || '1';
            socket.emit('call_waiter', { uuid: clientUUID, table: table });
            showToast('Офіціант вже прямує до столу #' + table, 'info');
        }

        function showToast(msg, type) {
            const root = document.getElementById('toast-root');
            const el = document.createElement('div');
            const color = type === 'success' ? 'border-emerald-500/30 text-emerald-400 bg-emerald-500/10' : 'border-indigo-500/30 text-indigo-400 bg-indigo-500/10';
            const icon = type === 'success' ? 'fa-check' : 'fa-info-circle';
            
            el.className = `glass-panel ${color} px-5 py-3 rounded-xl flex items-center gap-3 shadow-2xl transform -translate-y-10 opacity-0 transition-all duration-300 border backdrop-blur-xl pointer-events-auto`;
            el.innerHTML = `<i class="fas ${icon}"></i> <span class="text-sm font-bold">${msg}</span>`;
            
            root.appendChild(el);
            requestAnimationFrame(() => { el.classList.remove('-translate-y-10', 'opacity-0'); });
            
            setTimeout(() => {
                el.classList.add('-translate-y-10', 'opacity-0');
                setTimeout(() => el.remove(), 300);
            }, 3000);
        }
    </script>
</body>
</html>
"""

ADMIN_HTML = """
<!DOCTYPE html>
<html lang="uk" class="dark">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nexus KDS & Admin</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socketio/4.7.2/socketio.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #030305; color: #fff; font-family: system-ui, -apple-system, sans-serif; overflow: hidden; }
        .kanban-col { background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05); border-radius: 1.5rem; display: flex; flex-direction: column; }
        .glass-card { background: rgba(30, 30, 35, 0.8); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.1); border-radius: 1rem; }
        .hide-scroll::-webkit-scrollbar { display: none; }
    </style>
</head>
<body class="h-screen flex flex-col">

    <header class="bg-[#0a0a0f] border-b border-white/10 p-4 flex justify-between items-center z-10 shadow-xl">
        <div class="flex items-center gap-4">
            <div class="w-10 h-10 bg-indigo-600 rounded-lg flex items-center justify-center text-xl shadow-[0_0_15px_rgba(79,70,229,0.5)]"><i class="fas fa-server"></i></div>
            <div>
                <h1 class="font-black text-lg uppercase tracking-widest text-indigo-400">Kitchen Display</h1>
                <p class="text-[10px] text-gray-500 uppercase font-bold tracking-widest" id="conn-status">Connected</p>
            </div>
        </div>
        
        <div class="flex items-center gap-4">
            <div id="waiter-alerts" class="flex gap-2"></div>
            <a href="/logout" class="px-4 py-2 bg-rose-500/10 text-rose-500 border border-rose-500/20 rounded-lg text-sm font-bold hover:bg-rose-500/20 transition"><i class="fas fa-power-off"></i></a>
        </div>
    </header>

    <main class="flex-1 overflow-hidden p-6 grid grid-cols-3 gap-6">
        
        <div class="kanban-col h-full overflow-hidden">
            <div class="p-4 border-b border-white/10 bg-blue-500/5 rounded-t-3xl">
                <h2 class="font-black text-blue-400 flex items-center gap-2 uppercase tracking-wider text-sm"><i class="fas fa-inbox"></i> Вхідні (Нові) <span class="bg-blue-500 text-white text-xs px-2 py-0.5 rounded-full ml-auto" id="count-new">0</span></h2>
            </div>
            <div class="flex-1 overflow-y-auto hide-scroll p-4 space-y-4" id="col-new"></div>
        </div>

        <div class="kanban-col h-full overflow-hidden shadow-[0_0_30px_rgba(245,158,11,0.05)] border-amber-500/20">
            <div class="p-4 border-b border-amber-500/20 bg-amber-500/10 rounded-t-3xl">
                <h2 class="font-black text-amber-400 flex items-center gap-2 uppercase tracking-wider text-sm"><i class="fas fa-fire animate-pulse"></i> В роботі <span class="bg-amber-500 text-gray-900 text-xs px-2 py-0.5 rounded-full ml-auto font-bold" id="count-cook">0</span></h2>
            </div>
            <div class="flex-1 overflow-y-auto hide-scroll p-4 space-y-4" id="col-cook"></div>
        </div>

        <div class="kanban-col h-full overflow-hidden">
            <div class="p-4 border-b border-white/10 bg-emerald-500/5 rounded-t-3xl">
                <h2 class="font-black text-emerald-400 flex items-center gap-2 uppercase tracking-wider text-sm"><i class="fas fa-check-double"></i> Готово (Видача) <span class="bg-emerald-500 text-white text-xs px-2 py-0.5 rounded-full ml-auto" id="count-done">0</span></h2>
            </div>
            <div class="flex-1 overflow-y-auto hide-scroll p-4 space-y-4" id="col-done"></div>
        </div>

    </main>

    <audio id="sound-new" src="https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3" preload="auto"></audio>
    <audio id="sound-bell" src="https://assets.mixkit.co/active_storage/sfx/995/995-preview.mp3" preload="auto"></audio>

    <script>
        const socket = io();
        let currentOrders = [];

        socket.on('connect', () => { socket.emit('admin_init'); document.getElementById('conn-status').innerText = 'Online'; document.getElementById('conn-status').className = "text-[10px] text-emerald-500 uppercase font-bold tracking-widest"; });
        socket.on('disconnect', () => { document.getElementById('conn-status').innerText = 'Offline'; document.getElementById('conn-status').className = "text-[10px] text-rose-500 uppercase font-bold tracking-widest"; });

        socket.on('kds_sync', (orders) => {
            currentOrders = orders;
            renderKanban();
        });

        socket.on('new_order_alert', (order) => {
            try { document.getElementById('sound-new').play(); } catch(e){}
            currentOrders.unshift(order);
            renderKanban();
        });

        socket.on('waiter_alert', (data) => {
            try { document.getElementById('sound-bell').play(); } catch(e){}
            const root = document.getElementById('waiter-alerts');
            const el = document.createElement('div');
            el.className = "bg-amber-500 text-gray-900 font-black text-xs px-4 py-2 rounded-lg flex items-center gap-2 cursor-pointer hover:bg-amber-400 animate-pulse";
            el.innerHTML = `<i class="fas fa-bell"></i> Стіл ${data.table}`;
            el.onclick = () => el.remove();
            root.appendChild(el);
        });

        function renderKanban() {
            const cols = { 'Нове': document.getElementById('col-new'), 'Готується': document.getElementById('col-cook'), 'Готово': document.getElementById('col-done') };
            Object.values(cols).forEach(c => c.innerHTML = '');
            
            let counts = { 'Нове': 0, 'Готується': 0, 'Готово': 0 };

            currentOrders.forEach(o => {
                if(o.status === 'Закрито') return;
                counts[o.status]++;
                
                const itemsHtml = o.items.map(i => `
                    <div class="flex justify-between text-sm py-1.5 border-b border-white/5 last:border-0 text-gray-300">
                        <span>${i.name}</span> <span class="font-black text-white bg-white/10 px-2 rounded">x${i.qty}</span>
                    </div>`).join('');
                
                let btnHtml = '';
                if(o.status === 'Нове') btnHtml = `<button onclick="updateStatus('${o._id}', 'Готується')" class="w-full mt-3 py-2 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-xl text-sm transition shadow-lg">Готувати <i class="fas fa-arrow-right"></i></button>`;
                if(o.status === 'Готується') btnHtml = `<button onclick="updateStatus('${o._id}', 'Готово')" class="w-full mt-3 py-2 bg-amber-500 hover:bg-amber-400 text-gray-900 font-black rounded-xl text-sm transition shadow-[0_0_15px_rgba(245,158,11,0.4)]">На видачу <i class="fas fa-check"></i></button>`;
                if(o.status === 'Готово') btnHtml = `<button onclick="updateStatus('${o._id}', 'Закрито')" class="w-full mt-3 py-2 bg-emerald-600 hover:bg-emerald-500 text-white font-bold rounded-xl text-sm transition shadow-lg">Закрити чек <i class="fas fa-times"></i></button>`;

                const card = `
                <div class="glass-card p-4">
                    <div class="flex justify-between items-center mb-3">
                        <div class="text-2xl font-black text-white">#${o.table}</div>
                        <div class="text-xs text-gray-400 font-bold bg-black/40 px-2 py-1 rounded-md">${o.time}</div>
                    </div>
                    <div class="bg-black/20 rounded-xl p-3 mb-2 border border-white/5">${itemsHtml}</div>
                    <div class="flex justify-between items-center mt-3 pt-3 border-t border-white/10">
                        <span class="text-xs text-gray-500 font-bold uppercase tracking-wider">Сума</span>
                        <span class="font-black text-lg text-indigo-400">${o.total} ₴</span>
                    </div>
                    ${btnHtml}
                </div>`;
                
                cols[o.status].innerHTML += card;
            });

            document.getElementById('count-new').innerText = counts['Нове'];
            document.getElementById('count-cook').innerText = counts['Готується'];
            document.getElementById('count-done').innerText = counts['Готово'];
        }

        function updateStatus(id, newStatus) {
            socket.emit('admin_update_order', { id, status: newStatus });
        }
    </script>
</body>
</html>
"""

# ==============================================================================
# 3. ROUTES & CORE LOGIC
# ==============================================================================
@app.route('/')
def index():
    return render_template_string(CUSTOMER_HTML)

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin'))
    if session.get('admin_logged_in'):
        return render_template_string(ADMIN_HTML)
    return """
    <form method="POST" style="background:#000; height:100vh; display:flex; align-items:center; justify-content:center;">
        <input type="password" name="password" placeholder="Admin Key" style="padding:15px; font-size:20px; border-radius:10px; border:1px solid #333; background:#111; color:#fff; text-align:center;">
    </form>
    """

@app.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('index'))

# ==============================================================================
# 4. SOCKET.IO - REALTIME UUID ARCHITECTURE
# ==============================================================================
@socketio.on('client_init')
def handle_client_init(data):
    # UUID authorization approach for robust connection handling
    client_id = data.get('uuid')
    if client_id:
        join_room(client_id) # Private room for specific client UUID
        
    menu = [serialize_doc(i) for i in db.menu.find()]
    emit('menu_sync', menu)

@socketio.on('create_order')
def create_order(data):
    client_id = data.get('uuid')
    items = data.get('items', [])
    total = sum(i['price'] * i['qty'] for i in items)
    
    order = {
        'uuid': client_id,
        'table': data.get('table', '1'),
        'items': items,
        'total': total,
        'status': 'Нове',
        'time': datetime.now().strftime('%H:%M'),
        'timestamp': datetime.now()
    }
    
    res = db.orders.insert_one(order)
    order['_id'] = str(res.inserted_id)
    order.pop('timestamp', None)
    
    # Broadcast to Kitchen Display System (Admins)
    socketio.emit('new_order_alert', order, room='admins')
    return {'success': True}

@socketio.on('get_client_orders')
def get_client_orders(data):
    client_id = data.get('uuid')
    orders = [serialize_doc(o) for o in db.orders.find({"uuid": client_id}).sort("timestamp", -1)]
    return orders

@socketio.on('call_waiter')
def call_waiter(data):
    socketio.emit('waiter_alert', {'table': data.get('table')}, room='admins')

# --- ADMIN SOCKETS ---
@socketio.on('admin_init')
def admin_init():
    if session.get('admin_logged_in'):
        join_room('admins')
        orders = [serialize_doc(o) for o in db.orders.find({"status": {"$ne": "Закрито"}}).sort("timestamp", -1)]
        emit('kds_sync', orders)

@socketio.on('admin_update_order')
def admin_update_order(data):
    if session.get('admin_logged_in'):
        db.orders.update_one({"_id": ObjectId(data['id'])}, {"$set": {"status": data['status']}})
        
        # Notify specific UUID client that their order changed
        updated = db.orders.find_one({"_id": ObjectId(data['id'])})
        if updated and updated.get('uuid'):
            socketio.emit('order_status_update', {'status': data['status']}, room=updated['uuid'])
            
        # Refresh KDS for all admins
        orders = [serialize_doc(o) for o in db.orders.find({"status": {"$ne": "Закрито"}}).sort("timestamp", -1)]
        socketio.emit('kds_sync', orders, room='admins')

if __name__ == '__main__':
    if db.menu.count_documents({}) == 0:
        db.menu.insert_many([
            {"name": "Cyber Cappuccino", "category": "Кава", "price": 85, "description": "Подвійний еспресо з хмарною пінкою"},
            {"name": "Neon Burger", "category": "Бургери", "price": 250, "description": "Чорна булочка, подвійна яловича котлета, чеддер"},
            {"name": "Glitch Fries", "category": "Снеки", "price": 95, "description": "Картопля фрі з трюфельним соусом та пармезаном"}
        ])
    socketio.run(app, host='0.0.0.0', port=10000)
