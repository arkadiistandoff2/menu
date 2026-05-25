import eventlet
eventlet.monkey_patch()

import os
import time
from datetime import datetime
from bson.objectid import ObjectId
from flask import Flask, request, render_template_string, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room
from pymongo import MongoClient

# ==========================================
# 1. ІНІЦІАЛІЗАЦІЯ ТА НАЛАШТУВАННЯ
# ==========================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'super-secret-premium-key-2026')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/cafe_db')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

client = MongoClient(MONGO_URI)
db = client.get_default_database()

live_users = {}

# ==========================================
# 2. ПРЕМІУМ ШАБЛОНИ (DARK THEME)
# ==========================================

# --- ЛОГІН АДМІНА ---
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Вхід | System Admin</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;800&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', sans-serif; background-color: #0f172a; color: #f8fafc; }
        .glass { background: rgba(30, 41, 59, 0.7); backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, 0.1); }
    </style>
</head>
<body class="flex items-center justify-center h-screen bg-[url('https://images.unsplash.com/photo-1554118811-1e0d58224f24?q=80&w=2000&auto=format&fit=crop')] bg-cover bg-center">
    <div class="absolute inset-0 bg-slate-900/80 backdrop-blur-sm"></div>
    <div class="glass p-10 rounded-2xl shadow-2xl w-full max-w-md relative z-10">
        <div class="text-center mb-8">
            <div class="inline-flex items-center justify-center w-16 h-16 rounded-full bg-indigo-500/20 text-indigo-400 mb-4 shadow-[0_0_15px_rgba(99,102,241,0.5)]">
                <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"></path></svg>
            </div>
            <h2 class="text-3xl font-extrabold tracking-tight">Вхід в систему</h2>
            <p class="text-slate-400 mt-2 text-sm">Авторизуйтесь для доступу до панелі</p>
        </div>
        <form method="POST" action="/login">
            <div class="mb-6">
                <input type="password" name="password" placeholder="Пароль адміністратора" required 
                       class="w-full p-4 bg-slate-800/50 border border-slate-600 rounded-xl focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-all text-white placeholder-slate-500">
            </div>
            <button type="submit" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white p-4 rounded-xl font-bold transition-all shadow-[0_0_20px_rgba(99,102,241,0.4)] hover:shadow-[0_0_30px_rgba(99,102,241,0.6)]">
                Увійти до Dashboard
            </button>
        </form>
    </div>
</body>
</html>
"""

# --- МЕНЮ КЛІЄНТА (PREMIUM MOBILE DARK MODE) ---
CLIENT_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Digital Menu</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', sans-serif; background-color: #09090b; color: #fafafa; -webkit-tap-highlight-color: transparent; }
        .hide-scroll::-webkit-scrollbar { display: none; }
        .glass-nav { background: rgba(9, 9, 11, 0.85); backdrop-filter: blur(12px); border-bottom: 1px solid rgba(255,255,255,0.05); }
        .glass-card { background: linear-gradient(145deg, #18181b 0%, #0f0f11 100%); border: 1px solid rgba(255,255,255,0.05); }
        .glass-modal { background: rgba(24, 24, 27, 0.95); backdrop-filter: blur(20px); border-top: 1px solid rgba(255,255,255,0.1); }
        .smooth { transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); }
        .toast-enter { transform: translateY(-100%); opacity: 0; }
        .toast-active { transform: translateY(0); opacity: 1; }
        .btn-press:active { transform: scale(0.95); }
    </style>
</head>
<body class="pb-32">

    <!-- Toast Notification -->
    <div id="toast" class="fixed top-4 left-4 right-4 z-[100] glass-nav border border-slate-700 p-4 rounded-2xl shadow-2xl flex items-center gap-3 toast-enter smooth pointer-events-none">
        <div id="toast-icon" class="w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center text-blue-400"></div>
        <div>
            <h4 id="toast-title" class="font-bold text-sm text-white"></h4>
            <p id="toast-msg" class="text-xs text-slate-400"></p>
        </div>
    </div>

    <!-- Header -->
    <header class="fixed top-0 left-0 right-0 glass-nav z-40 p-4 flex justify-between items-center">
        <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-full bg-indigo-600 flex items-center justify-center font-black shadow-[0_0_15px_rgba(79,70,229,0.5)]">
                #{{ table }}
            </div>
            <div>
                <div class="text-xs text-slate-400 uppercase tracking-wider font-semibold">Ваш стіл</div>
                <div class="text-sm font-bold text-indigo-400 flex items-center gap-1">
                    <span class="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span> Онлайн
                </div>
            </div>
        </div>
        <button onclick="callWaiter()" class="btn-press bg-rose-500/10 text-rose-500 border border-rose-500/20 px-4 py-2 rounded-xl font-bold text-sm flex items-center gap-2 smooth hover:bg-rose-500/20">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"></path></svg>
            Офіціант
        </button>
    </header>

    <!-- Order Status Widget -->
    <div id="status-widget" class="hidden mt-24 mx-4 p-4 rounded-2xl shadow-lg border flex items-center gap-4 smooth">
        <div id="status-icon" class="w-12 h-12 rounded-full flex items-center justify-center text-2xl"></div>
        <div>
            <div class="text-xs uppercase tracking-wider font-bold mb-1 opacity-80" id="status-label">Статус</div>
            <div class="font-bold text-lg" id="status-text"></div>
        </div>
    </div>

    <!-- Main Content -->
    <main class="pt-24 px-4" id="main-content">
        <h1 class="text-3xl font-black mb-6 tracking-tight">Наше меню <span class="text-indigo-500">.</span></h1>
        
        <!-- Categories -->
        <div class="flex space-x-3 overflow-x-auto hide-scroll py-2 mb-6 sticky top-20 z-30 bg-[#09090b]/90 backdrop-blur-sm -mx-4 px-4" id="category-filter"></div>

        <!-- Menu Grid -->
        <div class="grid grid-cols-2 gap-4" id="menu-grid">
            <!-- Skeletons -->
            <div class="glass-card rounded-3xl p-4 animate-pulse"><div class="w-full h-24 bg-slate-800 rounded-2xl mb-4"></div><div class="h-4 bg-slate-800 rounded w-3/4 mb-2"></div><div class="h-4 bg-slate-800 rounded w-1/2"></div></div>
            <div class="glass-card rounded-3xl p-4 animate-pulse"><div class="w-full h-24 bg-slate-800 rounded-2xl mb-4"></div><div class="h-4 bg-slate-800 rounded w-3/4 mb-2"></div><div class="h-4 bg-slate-800 rounded w-1/2"></div></div>
        </div>
    </main>

    <!-- Floating Cart Area -->
    <div class="fixed bottom-0 left-0 right-0 p-4 z-40 bg-gradient-to-t from-[#09090b] via-[#09090b] to-transparent pointer-events-none">
        <button id="cart-float" onclick="openCart()" class="hidden btn-press pointer-events-auto w-full bg-indigo-600 text-white p-4 rounded-2xl shadow-[0_10px_30px_rgba(99,102,241,0.4)] flex justify-between items-center smooth border border-indigo-400/30">
            <div class="flex items-center gap-3">
                <div class="bg-indigo-800/50 p-2 rounded-xl">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z"></path></svg>
                </div>
                <div class="text-left">
                    <div class="text-xs text-indigo-200 font-semibold uppercase tracking-wider">Ваше замовлення</div>
                    <div class="font-bold text-sm"><span id="cart-float-count">0</span> позицій</div>
                </div>
            </div>
            <div class="text-xl font-black bg-indigo-900/50 px-4 py-2 rounded-xl"><span id="cart-float-total">0</span> ₴</div>
        </button>
    </div>

    <!-- Cart Modal -->
    <div id="cart-modal" class="fixed inset-0 z-50 hidden flex-col justify-end bg-black/60 backdrop-blur-sm smooth opacity-0">
        <div class="glass-modal rounded-t-[2rem] h-[85vh] flex flex-col relative transform translate-y-full smooth shadow-[0_-10px_40px_rgba(0,0,0,0.5)]" id="cart-modal-content">
            <div class="flex justify-center pt-3 pb-1"><div class="w-12 h-1.5 bg-slate-700 rounded-full"></div></div>
            
            <div class="p-6 flex-1 flex flex-col overflow-hidden">
                <div class="flex justify-between items-center mb-6">
                    <h2 class="text-2xl font-black">Кошик</h2>
                    <button onclick="closeCart()" class="btn-press bg-slate-800 text-slate-300 p-2 rounded-full hover:bg-slate-700 smooth">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                    </button>
                </div>
                
                <div id="cart-items" class="flex-1 overflow-y-auto hide-scroll space-y-3 pb-4"></div>
                
                <div class="mt-auto pt-6 border-t border-slate-800">
                    <div class="flex justify-between items-end mb-6">
                        <div class="text-slate-400 font-medium">До сплати:</div>
                        <div class="text-3xl font-black text-indigo-400"><span id="cart-modal-total">0</span> ₴</div>
                    </div>
                    <button onclick="placeOrder()" id="confirm-btn" class="btn-press w-full bg-indigo-600 text-white p-4 rounded-2xl font-bold text-lg flex justify-center items-center gap-2 shadow-[0_0_20px_rgba(99,102,241,0.4)] smooth">
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
                        Відправити на кухню
                    </button>
                </div>
            </div>
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
                const icon = document.getElementById('status-icon');
                const text = document.getElementById('status-text');
                const label = document.getElementById('status-label');
                
                widget.classList.remove('hidden', 'bg-blue-900/40', 'border-blue-500/30', 'bg-amber-900/40', 'border-amber-500/30', 'bg-emerald-900/40', 'border-emerald-500/30');
                icon.className = 'w-12 h-12 rounded-full flex items-center justify-center text-2xl shadow-lg';
                
                if (data.status === 'Нове') {
                    widget.classList.add('bg-blue-900/40', 'border-blue-500/30');
                    icon.classList.add('bg-blue-500/20', 'text-blue-400');
                    icon.innerHTML = '📨'; label.innerText = 'Прийнято'; label.className = 'text-xs uppercase font-bold mb-1 text-blue-400'; text.innerText = 'Відправлено на кухню';
                } else if (data.status === 'Готується') {
                    widget.classList.add('bg-amber-900/40', 'border-amber-500/30');
                    icon.classList.add('bg-amber-500/20', 'text-amber-400');
                    icon.innerHTML = '🍳'; label.innerText = 'В процесі'; label.className = 'text-xs uppercase font-bold mb-1 text-amber-400'; text.innerText = 'Кухарі вже готують!';
                } else if (data.status === 'Готово') {
                    widget.classList.add('bg-emerald-900/40', 'border-emerald-500/30');
                    icon.classList.add('bg-emerald-500/20', 'text-emerald-400');
                    icon.innerHTML = '✨'; label.innerText = 'Видача'; label.className = 'text-xs uppercase font-bold mb-1 text-emerald-400'; text.innerText = 'Офіціант вже несе!';
                } else if (data.status === 'Закрито') {
                    widget.classList.add('hidden');
                    currentOrderId = null;
                    localStorage.removeItem('cafe_order_' + tableId);
                    showToast("Дякуємо!", "Замовлення оплачено. Чекаємо вас знову!", "success");
                }
            }
        });

        // UI Функції
        function renderCategories() {
            const container = document.getElementById('category-filter');
            const categories = ["Всі", ...new Set(menuData.map(i => i.category))];
            container.innerHTML = categories.map(cat => {
                const isActive = activeCategory === cat;
                const baseClass = "px-6 py-2.5 rounded-2xl whitespace-nowrap font-bold text-sm smooth border border-transparent btn-press";
                const activeClass = isActive ? "bg-indigo-600 text-white shadow-[0_0_15px_rgba(99,102,241,0.5)] border-indigo-400/30" : "bg-slate-800/50 text-slate-400 hover:bg-slate-800 border-slate-700/50";
                return `<button onclick="setCategory('${cat}')" class="${baseClass} ${activeClass}">${cat}</button>`;
            }).join('');
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
            
            if(items.length === 0) {
                container.innerHTML = `<div class="col-span-2 text-center py-10 text-slate-500">Тут поки нічого немає</div>`;
                return;
            }

            container.innerHTML = items.map(item => `
                <div class="glass-card rounded-3xl p-1 flex flex-col justify-between relative overflow-hidden group">
                    <div class="p-4 pb-0 flex-1">
                        <div class="w-10 h-10 rounded-full bg-slate-800 mb-3 flex items-center justify-center text-slate-400 border border-slate-700">🍽️</div>
                        <h3 class="font-bold text-base leading-tight mb-1 text-white">${item.name}</h3>
                        <p class="text-[11px] text-slate-400 line-clamp-2 leading-relaxed">${item.description}</p>
                    </div>
                    <div class="p-4 pt-4 mt-auto flex items-center justify-between">
                        <div class="text-lg font-black text-indigo-300">${item.price} <span class="text-xs text-slate-500">₴</span></div>
                        <button onclick="addToCart('${item._id}', '${item.name}')" class="btn-press w-10 h-10 bg-indigo-600 hover:bg-indigo-500 rounded-xl flex items-center justify-center text-white shadow-lg smooth">
                            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 4v16m8-8H4"></path></svg>
                        </button>
                    </div>
                </div>
            `).join('');
        }

        // Кошик
        function addToCart(id, name) {
            cart[id] = (cart[id] || 0) + 1;
            saveCart();
            updateCartUI();
            showToast("Додано", `${name} у кошику`, "success");
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
            const itemIds = Object.keys(cart);
            
            if (itemIds.length === 0) {
                floatBtn.classList.add('hidden');
                return;
            }
            floatBtn.classList.remove('hidden');
            
            let total = 0;
            let count = 0;
            const itemsHtml = itemIds.map(id => {
                const item = menuData.find(i => i._id === id);
                if (!item) return '';
                total += item.price * cart[id];
                count += cart[id];
                return `
                    <div class="flex justify-between items-center bg-slate-800/50 p-4 rounded-2xl border border-slate-700/50">
                        <div class="flex-1 pr-4">
                            <div class="font-bold text-white mb-1">${item.name}</div>
                            <div class="text-sm font-semibold text-indigo-400">${item.price} ₴</div>
                        </div>
                        <div class="flex items-center space-x-4 bg-slate-900 rounded-xl p-1 border border-slate-700">
                            <button onclick="changeQty('${id}', -1)" class="btn-press w-8 h-8 flex items-center justify-center text-slate-400 hover:text-white smooth"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M20 12H4"></path></svg></button>
                            <span class="font-black w-4 text-center text-white">${cart[id]}</span>
                            <button onclick="changeQty('${id}', 1)" class="btn-press w-8 h-8 bg-indigo-600 rounded-lg flex items-center justify-center text-white smooth"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M12 4v16m8-8H4"></path></svg></button>
                        </div>
                    </div>
                `;
            }).join('');
            
            document.getElementById('cart-float-total').innerText = total;
            document.getElementById('cart-float-count').innerText = count;
            document.getElementById('cart-modal-total').innerText = total;
            document.getElementById('cart-items').innerHTML = itemsHtml;
        }

        function openCart() {
            const m = document.getElementById('cart-modal');
            const c = document.getElementById('cart-modal-content');
            m.classList.remove('hidden');
            m.classList.add('flex');
            setTimeout(() => { m.classList.remove('opacity-0'); c.classList.remove('translate-y-full'); }, 10);
        }

        function closeCart() {
            const m = document.getElementById('cart-modal');
            const c = document.getElementById('cart-modal-content');
            c.classList.add('translate-y-full');
            m.classList.add('opacity-0');
            setTimeout(() => { m.classList.add('hidden'); m.classList.remove('flex'); }, 300);
        }

        function placeOrder() {
            if (Object.keys(cart).length === 0) return;
            if (currentOrderId) {
                showToast("Увага", "Спочатку дочекайтесь попереднього замовлення.", "error");
                return;
            }
            
            const btn = document.getElementById('confirm-btn');
            btn.innerHTML = `<svg class="animate-spin w-6 h-6" viewBox="0 0 24 24" fill="none"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> Обробка...`;
            btn.disabled = true;

            const orderItems = Object.keys(cart).map(id => ({ id: id, qty: cart[id] }));
            
            socket.emit('place_order', { table: tableId, items: orderItems }, (response) => {
                btn.innerHTML = `<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg> Відправити на кухню`;
                btn.disabled = false;
                if (response.success) {
                    currentOrderId = response.order_id;
                    localStorage.setItem('cafe_order_' + tableId, currentOrderId);
                    cart = {};
                    saveCart();
                    closeCart();
                    updateCartUI();
                    showToast("Успішно!", "Замовлення прийнято кухнею", "success");
                    document.getElementById('main-content').scrollIntoView({behavior: 'smooth'});
                } else {
                    showToast("Помилка", response.error, "error");
                }
            });
        }

        function callWaiter() {
            socket.emit('call_waiter', { table: tableId });
            showToast("Виклик відправлено", "Офіціант підійде за мить", "info");
        }

        // Кастомні Toasts
        function showToast(title, msg, type) {
            const toast = document.getElementById('toast');
            document.getElementById('toast-title').innerText = title;
            document.getElementById('toast-msg').innerText = msg;
            
            const icon = document.getElementById('toast-icon');
            if(type === 'success') {
                icon.className = 'w-10 h-10 rounded-full bg-emerald-500/20 flex items-center justify-center text-emerald-400';
                icon.innerHTML = '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>';
            } else if(type === 'error') {
                icon.className = 'w-10 h-10 rounded-full bg-rose-500/20 flex items-center justify-center text-rose-400';
                icon.innerHTML = '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>';
            } else {
                icon.className = 'w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center text-blue-400';
                icon.innerHTML = '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';
            }

            toast.classList.remove('toast-enter');
            toast.classList.add('toast-active');
            setTimeout(() => {
                toast.classList.remove('toast-active');
                toast.classList.add('toast-enter');
            }, 3000);
        }
    </script>
</body>
</html>
"""

# --- ПАНЕЛЬ АДМІНА (PREMIUM DESKTOP DARK MODE) ---
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Control Center | Admin</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700;900&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', sans-serif; background-color: #020617; color: #f8fafc; }
        .hide-scroll::-webkit-scrollbar { width: 6px; }
        .hide-scroll::-webkit-scrollbar-track { background: #0f172a; }
        .hide-scroll::-webkit-scrollbar-thumb { background: #334155; border-radius: 10px; }
        .glass-panel { background: #0f172a; border: 1px solid #1e293b; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.5); }
        .smooth { transition: all 0.2s ease; }
        .pulse-border { animation: pulseBorder 2s infinite; }
        @keyframes pulseBorder { 0% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.7); } 70% { box-shadow: 0 0 0 10px rgba(59, 130, 246, 0); } 100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0); } }
    </style>
</head>
<body class="flex h-screen overflow-hidden">
    
    <!-- Sidebar -->
    <aside class="w-72 glass-panel border-r border-slate-800 flex flex-col z-20">
        <div class="p-6 border-b border-slate-800 flex items-center gap-3">
            <div class="w-10 h-10 bg-indigo-600 rounded-lg flex items-center justify-center text-white font-black shadow-[0_0_15px_rgba(99,102,241,0.5)]">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
            </div>
            <div>
                <div class="text-lg font-black tracking-wider">NEXUS POS</div>
                <div class="text-xs text-slate-400 font-medium">Control Center</div>
            </div>
        </div>
        <nav class="flex-1 p-4 space-y-2 overflow-y-auto hide-scroll">
            <div class="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2 mt-4 px-3">Управління</div>
            <button onclick="switchTab('orders')" id="tab-orders" class="w-full flex items-center gap-3 text-left px-4 py-3 rounded-xl bg-indigo-600 text-white font-semibold shadow-lg smooth">
                <svg class="w-5 h-5 opacity-80" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"></path></svg>
                Замовлення <span id="badge-orders" class="ml-auto bg-indigo-800 text-xs py-0.5 px-2 rounded-full hidden">0</span>
            </button>
            <button onclick="switchTab('menu')" id="tab-menu" class="w-full flex items-center gap-3 text-left px-4 py-3 rounded-xl text-slate-400 hover:bg-slate-800 hover:text-white font-semibold smooth">
                <svg class="w-5 h-5 opacity-80" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4"></path></svg>
                База страв
            </button>
            
            <div class="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2 mt-8 px-3">Моніторинг</div>
            <button onclick="switchTab('devices')" id="tab-devices" class="w-full flex items-center gap-3 text-left px-4 py-3 rounded-xl text-slate-400 hover:bg-slate-800 hover:text-white font-semibold smooth">
                <svg class="w-5 h-5 opacity-80" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"></path></svg>
                Радар Залу <span class="w-2 h-2 rounded-full bg-emerald-500 ml-auto animate-pulse"></span>
            </button>
        </nav>
        <div class="p-4 border-t border-slate-800">
            <a href="/logout" class="flex items-center gap-3 px-4 py-3 rounded-xl text-rose-400 hover:bg-rose-500/10 hover:text-rose-300 font-semibold smooth">
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"></path></svg>
                Вийти
            </a>
        </div>
    </aside>

    <!-- Main Content -->
    <main class="flex-1 flex flex-col h-full bg-[#020617] relative">
        
        <!-- Header Stats -->
        <header class="p-6 z-10">
            <div class="grid grid-cols-4 gap-4">
                <div class="glass-panel p-5 rounded-2xl border-emerald-500/20 relative overflow-hidden">
                    <div class="absolute -right-4 -top-4 w-16 h-16 bg-emerald-500/10 rounded-full blur-xl"></div>
                    <div class="text-sm text-emerald-400 font-bold mb-1 flex items-center gap-2"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg> Каса (Закриті)</div>
                    <div class="text-3xl font-black text-white"><span id="stat-revenue">0</span> <span class="text-lg text-emerald-500 font-bold">₴</span></div>
                </div>
                <div class="glass-panel p-5 rounded-2xl border-amber-500/20 relative overflow-hidden">
                    <div class="absolute -right-4 -top-4 w-16 h-16 bg-amber-500/10 rounded-full blur-xl"></div>
                    <div class="text-sm text-amber-400 font-bold mb-1 flex items-center gap-2"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 14v6m-3-3h6M6 10h2a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v2a2 2 0 002 2zm10 0h2a2 2 0 002-2V6a2 2 0 00-2-2h-2a2 2 0 00-2 2v2a2 2 0 002 2zM6 20h2a2 2 0 002-2v-2a2 2 0 00-2-2H6a2 2 0 00-2 2v2a2 2 0 002 2z"></path></svg> В черзі кухарів</div>
                    <div class="text-3xl font-black text-white" id="stat-cooking">0</div>
                </div>
                <div class="glass-panel p-5 rounded-2xl border-indigo-500/20 relative overflow-hidden">
                    <div class="absolute -right-4 -top-4 w-16 h-16 bg-indigo-500/10 rounded-full blur-xl"></div>
                    <div class="text-sm text-indigo-400 font-bold mb-1 flex items-center gap-2"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"></path></svg> Всього чеків</div>
                    <div class="text-3xl font-black text-white" id="stat-total-orders">0</div>
                </div>
                <div class="glass-panel p-5 rounded-2xl border-rose-500/20 relative overflow-hidden">
                    <div class="absolute -right-4 -top-4 w-16 h-16 bg-rose-500/10 rounded-full blur-xl"></div>
                    <div class="text-sm text-rose-400 font-bold mb-1 flex items-center gap-2"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"></path></svg> Топ продажів</div>
                    <div class="text-xl font-bold text-white mt-1 truncate" id="stat-top-item">-</div>
                </div>
            </div>
        </header>

        <!-- View: Orders -->
        <div id="view-orders" class="flex-1 overflow-y-auto hide-scroll px-6 pb-6">
            <div class="flex justify-between items-center mb-6">
                <h2 class="text-2xl font-black text-white flex items-center gap-3"><span class="w-2 h-8 bg-indigo-500 rounded-full block"></span> Операційний зал</h2>
            </div>
            <div id="orders-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6"></div>
        </div>

        <!-- View: Menu -->
        <div id="view-menu" class="hidden flex-1 overflow-hidden px-6 pb-6 flex gap-6">
            <div class="flex-1 glass-panel rounded-2xl flex flex-col overflow-hidden">
                <div class="p-5 border-b border-slate-800 bg-slate-900/50 flex justify-between items-center">
                    <h2 class="text-xl font-bold text-white">База страв</h2>
                    <div class="text-xs text-slate-400 bg-slate-800 px-3 py-1 rounded-full"><span id="menu-count">0</span> позицій</div>
                </div>
                <div class="flex-1 overflow-y-auto hide-scroll p-5 space-y-3" id="admin-menu-list"></div>
            </div>
            
            <div class="w-96 glass-panel rounded-2xl flex flex-col h-fit">
                <div class="p-5 border-b border-slate-800 bg-slate-900/50">
                    <h2 class="text-xl font-bold text-white flex items-center gap-2" id="menu-form-title">
                        <svg class="w-5 h-5 text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"></path></svg>
                        Додати позицію
                    </h2>
                </div>
                <form id="menu-form" onsubmit="saveMenuItem(event)" class="p-5">
                    <input type="hidden" id="menu-id">
                    <div class="mb-4">
                        <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Назва страви</label>
                        <input type="text" id="menu-name" required class="w-full bg-slate-800 border border-slate-700 text-white p-3 rounded-xl focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 smooth placeholder-slate-600" placeholder="Наприклад: Капучино">
                    </div>
                    <div class="mb-4">
                        <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Ціна (ГРН)</label>
                        <input type="number" id="menu-price" required class="w-full bg-slate-800 border border-slate-700 text-white p-3 rounded-xl focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 smooth placeholder-slate-600" placeholder="0.00">
                    </div>
                    <div class="mb-4">
                        <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Категорія</label>
                        <input type="text" id="menu-category" required class="w-full bg-slate-800 border border-slate-700 text-white p-3 rounded-xl focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 smooth placeholder-slate-600" placeholder="Наприклад: Кава">
                    </div>
                    <div class="mb-6">
                        <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Склад / Опис</label>
                        <textarea id="menu-desc" rows="3" class="w-full bg-slate-800 border border-slate-700 text-white p-3 rounded-xl focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 smooth placeholder-slate-600 hide-scroll" placeholder="Опис страви для клієнта..."></textarea>
                    </div>
                    <button type="submit" class="w-full bg-indigo-600 text-white p-3.5 rounded-xl font-bold hover:bg-indigo-500 shadow-[0_0_15px_rgba(99,102,241,0.3)] smooth mb-3">Зберегти у Cloud DB</button>
                    <button type="button" onclick="resetMenuForm()" class="w-full bg-slate-800 text-slate-300 p-3.5 rounded-xl font-bold hover:bg-slate-700 smooth border border-slate-700">Очистити форму</button>
                </form>
            </div>
        </div>

        <!-- View: Devices -->
        <div id="view-devices" class="hidden flex-1 overflow-y-auto hide-scroll px-6 pb-6">
            <div class="flex justify-between items-center mb-6">
                <h2 class="text-2xl font-black text-white flex items-center gap-3"><span class="w-2 h-8 bg-emerald-500 rounded-full block"></span> Радар Клієнтів</h2>
                <div class="text-sm font-semibold text-emerald-400 bg-emerald-500/10 px-4 py-2 rounded-full border border-emerald-500/20 flex items-center gap-2">
                    <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span> Моніторинг активний
                </div>
            </div>
            <div id="devices-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6"></div>
        </div>
    </main>

    <!-- Waiter Alert Modal -->
    <div id="waiter-modal" class="fixed inset-0 z-[100] hidden items-center justify-center bg-black/80 backdrop-blur-md smooth opacity-0">
        <div class="bg-rose-600 text-white p-10 rounded-[2rem] shadow-[0_0_100px_rgba(225,29,72,0.5)] text-center w-full max-w-md transform scale-95 smooth border border-rose-400/50" id="waiter-content">
            <div class="w-24 h-24 bg-white/20 rounded-full flex items-center justify-center mx-auto mb-6">
                <svg class="w-12 h-12 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"></path></svg>
            </div>
            <h2 class="text-3xl font-black mb-2 tracking-tight uppercase">Виклик Офіціанта</h2>
            <p class="text-xl text-rose-200 mb-8 font-medium">Стіл <span id="waiter-table" class="font-black text-5xl block mt-2 text-white"></span></p>
            <button onclick="closeWaiter()" class="w-full bg-white text-rose-600 font-black px-8 py-4 rounded-xl text-xl hover:bg-rose-50 smooth shadow-xl hover:scale-105 active:scale-95">Прийнято в роботу</button>
        </div>
    </div>
    
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
            const m = document.getElementById('waiter-modal');
            const c = document.getElementById('waiter-content');
            m.classList.remove('hidden');
            m.classList.add('flex');
            setTimeout(() => { m.classList.remove('opacity-0'); c.classList.remove('scale-95'); }, 10);
        });

        function closeWaiter() {
            const m = document.getElementById('waiter-modal');
            const c = document.getElementById('waiter-content');
            c.classList.add('scale-95');
            m.classList.add('opacity-0');
            setTimeout(() => { m.classList.add('hidden'); m.classList.remove('flex'); }, 300);
        }

        socket.on('live_users_update', (users) => {
            liveDevices = users;
            renderDevices();
        });

        socket.on('menu_data', (data) => {
            menuItems = data;
            renderMenuAdmin();
        });

        function switchTab(tab) {
            ['orders', 'menu', 'devices'].forEach(t => {
                document.getElementById('view-' + t).classList.add('hidden');
                document.getElementById('tab-' + t).className = "w-full flex items-center gap-3 text-left px-4 py-3 rounded-xl text-slate-400 hover:bg-slate-800 hover:text-white font-semibold smooth";
            });
            document.getElementById('view-' + tab).classList.remove('hidden');
            document.getElementById('tab-' + tab).className = "w-full flex items-center gap-3 text-left px-4 py-3 rounded-xl bg-indigo-600 text-white font-semibold shadow-[0_0_15px_rgba(99,102,241,0.4)] smooth";
        }

        function renderOrders() {
            const container = document.getElementById('orders-container');
            const active = orders.filter(o => o.status !== 'Закрито');
            
            if(active.length === 0) {
                container.innerHTML = `<div class="col-span-full flex flex-col items-center justify-center py-20 text-slate-600"><svg class="w-16 h-16 mb-4 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"></path></svg><div class="text-xl font-bold">Немає активних замовлень</div></div>`;
                return;
            }

            container.innerHTML = active.map(o => {
                const isNew = o.status === 'Нове';
                const isCooking = o.status === 'Готується';
                
                let statusBadge = '';
                let cardStyle = 'glass-panel border-slate-800';
                
                if(isNew) {
                    statusBadge = '<span class="bg-blue-500/20 text-blue-400 border border-blue-500/30 px-3 py-1 rounded-full text-xs font-bold flex items-center gap-1.5"><span class="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse"></span> НОВЕ</span>';
                    cardStyle = 'bg-slate-900 border border-blue-500/50 shadow-[0_0_20px_rgba(59,130,246,0.15)]';
                } else if(isCooking) {
                    statusBadge = '<span class="bg-amber-500/20 text-amber-400 border border-amber-500/30 px-3 py-1 rounded-full text-xs font-bold">ГОТУЄТЬСЯ</span>';
                } else {
                    statusBadge = '<span class="bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 px-3 py-1 rounded-full text-xs font-bold">ГОТОВО</span>';
                }

                const itemsHtml = o.items.map(i => `
                    <div class="flex justify-between items-start py-2 border-b border-slate-800/50 last:border-0">
                        <div class="text-sm font-semibold text-slate-300 pr-2">${i.name}</div>
                        <div class="text-sm font-black bg-slate-800 text-white px-2 py-0.5 rounded">${i.qty}x</div>
                    </div>
                `).join('');
                
                const timeStr = new Date(o.timestamp.$date || o.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
                
                return `
                <div class="rounded-2xl p-5 flex flex-col h-full relative overflow-hidden ${cardStyle}">
                    ${isNew ? '<div class="absolute top-0 left-0 w-1 h-full bg-blue-500"></div>' : ''}
                    <div class="flex justify-between items-start mb-4">
                        <div>
                            <div class="text-xs text-slate-500 font-bold mb-1 uppercase tracking-wider">${timeStr}</div>
                            <div class="text-2xl font-black text-white">Стіл #${o.table}</div>
                        </div>
                        ${statusBadge}
                    </div>
                    
                    <div class="flex-1 bg-slate-900/50 rounded-xl p-3 mb-4 border border-slate-800/50 overflow-y-auto hide-scroll max-h-48">
                        ${itemsHtml}
                    </div>
                    
                    <div class="flex justify-between items-center mb-4">
                        <div class="text-xs font-bold text-slate-500 uppercase">Сума чеку</div>
                        <div class="text-xl font-black text-white">${o.total} ₴</div>
                    </div>
                    
                    <div class="grid grid-cols-1 gap-2 mt-auto">
                        ${isNew ? `<button onclick="changeStatus('${o._id}', 'Готується')" class="w-full bg-blue-600 hover:bg-blue-500 text-white font-bold py-3 rounded-xl flex justify-center items-center gap-2 smooth shadow-lg"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 002-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path></svg> На Кухню</button>` : ''}
                        ${o.status === 'Готується' ? `<button onclick="changeStatus('${o._id}', 'Готово')" class="w-full bg-amber-600 hover:bg-amber-500 text-white font-bold py-3 rounded-xl flex justify-center items-center gap-2 smooth shadow-lg"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg> Видача (Готово)</button>` : ''}
                        ${o.status === 'Готово' ? `<button onclick="changeStatus('${o._id}', 'Закрито')" class="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-3 rounded-xl flex justify-center items-center gap-2 smooth shadow-lg"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z"></path></svg> Сплачено (Закрити)</button>` : ''}
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

        function renderMenuAdmin() {
            document.getElementById('menu-count').innerText = menuItems.length;
            const container = document.getElementById('admin-menu-list');
            container.innerHTML = menuItems.map(m => `
                <div class="bg-slate-800/50 border border-slate-700/50 rounded-xl p-4 flex justify-between items-center hover:bg-slate-800 smooth group">
                    <div class="flex gap-4 items-center">
                        <div class="w-12 h-12 rounded-lg bg-slate-900 flex items-center justify-center text-xl border border-slate-700">🍔</div>
                        <div>
                            <div class="font-bold text-lg text-white flex items-center gap-2">${m.name} <span class="text-[10px] uppercase font-bold bg-slate-700 text-slate-300 px-2 py-0.5 rounded">${m.category}</span></div>
                            <div class="text-indigo-400 font-black text-sm mt-0.5">${m.price} ₴</div>
                        </div>
                    </div>
                    <div class="flex gap-2 opacity-0 group-hover:opacity-100 smooth">
                        <button onclick="editMenu('${m._id}')" class="w-10 h-10 bg-amber-500/10 text-amber-500 rounded-lg flex items-center justify-center hover:bg-amber-500/20 smooth"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"></path></svg></button>
                        <button onclick="deleteMenu('${m._id}')" class="w-10 h-10 bg-rose-500/10 text-rose-500 rounded-lg flex items-center justify-center hover:bg-rose-500/20 smooth"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg></button>
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
            document.getElementById('menu-form-title').innerHTML = `<svg class="w-5 h-5 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path></svg> Редагування`;
        }

        function deleteMenu(id) {
            if(confirm("Точно видалити?")) socket.emit('admin_delete_menu', { id: id });
        }

        function resetMenuForm() {
            document.getElementById('menu-id').value = '';
            document.getElementById('menu-form').reset();
            document.getElementById('menu-form-title').innerHTML = `<svg class="w-5 h-5 text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"></path></svg> Додати позицію`;
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

        function renderDevices() {
            const container = document.getElementById('devices-container');
            const devices = Object.values(liveDevices);
            if (devices.length === 0) {
                container.innerHTML = `<div class="col-span-full flex flex-col items-center justify-center py-20 text-slate-600"><svg class="w-16 h-16 mb-4 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"></path></svg><div class="text-xl font-bold">У залі порожньо</div></div>`;
                return;
            }
            container.innerHTML = devices.map(d => {
                let cartHtml = '<div class="text-slate-500 text-xs font-medium text-center py-2 bg-slate-900 rounded-lg border border-slate-800">Меню відкрито, кошик порожній</div>';
                if (d.cart && d.cart.length > 0) {
                    const total = d.cart.reduce((sum, item) => sum + (item.price * item.qty), 0);
                    cartHtml = `
                        <div class="bg-slate-900 p-3 rounded-xl border border-slate-800">
                            <div class="text-xs font-bold text-indigo-400 uppercase tracking-wider mb-2 flex items-center gap-2"><svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 11-4 0 2 2 0 014 0z"></path></svg> Живий кошик:</div>
                            <div class="space-y-1 mb-2">
                                ${d.cart.map(i => `<div class="flex justify-between text-sm text-slate-300"><span class="truncate pr-2">${i.name} <span class="text-slate-500 text-xs">x${i.qty}</span></span><span class="font-bold">${i.price * i.qty} ₴</span></div>`).join('')}
                            </div>
                            <div class="flex justify-between items-center pt-2 border-t border-slate-700">
                                <span class="text-xs font-bold text-slate-500 uppercase">Проміжно</span>
                                <span class="font-black text-white">${total} ₴</span>
                            </div>
                        </div>`;
                }
                
                const os = d.ua.toLowerCase().includes('iphone') ? 'iOS Device' : (d.ua.toLowerCase().includes('android') ? 'Android' : 'Desktop');
                const osIcon = d.ua.toLowerCase().includes('iphone') ? '<svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor"><path d="M16.365 1.488a4.938 4.938 0 01-1.185 3.526 4.673 4.673 0 01-3.393 1.696c-.053-1.423.59-2.822 1.62-3.805A4.685 4.685 0 0116.365 1.488zM17.155 19.86c-1.332 1.956-2.73 3.93-4.908 3.95-2.124.02-2.827-1.282-5.267-1.282-2.46 0-3.232 1.262-5.247 1.302-2.226.04-3.86-2.196-5.213-4.152-2.75-3.99-4.856-11.28-2.046-16.162A5.968 5.968 0 014.28 1.114c2.062-.04 3.97 1.383 5.04 1.383 1.05 0 3.393-1.695 5.76-1.455 1.01.04 3.86.402 5.69 3.093-5.385 3.232-4.496 11.084 1.082 13.34-1.393 1.36-2.023 1.835-2.7 2.82z"/></svg>' : '<svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor"><path d="M12 21.312l-6.844-3.89V6.578L12 2.688l6.844 3.89v10.844z"/></svg>';
                
                return `
                <div class="glass-panel rounded-2xl p-5 border-t-4 border-t-emerald-500 relative">
                    <div class="absolute top-4 right-4 flex items-center gap-1.5 bg-emerald-500/10 text-emerald-400 px-2 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider border border-emerald-500/20">
                        <span class="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse"></span> ${d.last_seen}
                    </div>
                    <div class="flex items-center gap-3 mb-4">
                        <div class="w-10 h-10 bg-slate-800 rounded-xl flex items-center justify-center text-white font-black border border-slate-700 shadow-inner">
                            #${d.table}
                        </div>
                        <div>
                            <div class="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-0.5">Клієнт</div>
                            <div class="text-sm font-semibold text-slate-300 flex items-center gap-1">${osIcon} ${os}</div>
                        </div>
                    </div>
                    <div class="bg-indigo-500/10 text-indigo-400 text-xs font-semibold p-2.5 rounded-xl border border-indigo-500/20 mb-3 flex items-center gap-2">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"></path></svg>
                        Переглядає: ${d.category || 'Всі'}
                    </div>
                    ${cartHtml}
                </div>`;
            }).join('');
        }

        function updateDashboard() {
            const closed = orders.filter(o => o.status === 'Закрито');
            const active = orders.filter(o => o.status !== 'Закрито').length;
            const cooking = orders.filter(o => o.status === 'Готується').length;
            const revenue = closed.reduce((sum, o) => sum + o.total, 0);
            
            document.getElementById('stat-revenue').innerText = revenue;
            document.getElementById('stat-cooking').innerText = cooking;
            document.getElementById('stat-total-orders').innerText = orders.length;
            
            const badge = document.getElementById('badge-orders');
            if(active > 0) {
                badge.innerText = active;
                badge.classList.remove('hidden');
            } else {
                badge.classList.add('hidden');
            }

            const itemCounts = {};
            orders.forEach(o => o.items.forEach(i => { itemCounts[i.name] = (itemCounts[i.name] || 0) + i.qty; }));
            let topItem = "Поки немає", maxCount = 0;
            for (const [name, count] of Object.entries(itemCounts)) {
                if (count > maxCount) { maxCount = count; topItem = name; }
            }
            document.getElementById('stat-top-item').innerText = topItem;
        }

        function playSound(id) {
            const el = document.getElementById(id);
            if (el) { el.currentTime = 0; el.play().catch(e => console.log('Audio blocked')); }
        }
    </script>
</body>
</html>
"""

# ==========================================
# 3. ДОПОМІЖНІ ФУНКЦІЇ ТА ROUTES
# ==========================================

def serialize_doc(doc):
    if '_id' in doc: doc['_id'] = str(doc['_id'])
    if 'timestamp' in doc: doc['timestamp'] = doc['timestamp'].isoformat()
    return doc

def get_current_time_str():
    return datetime.now().strftime("%H:%M:%S")

@app.route('/')
def index():
    table = request.args.get('table', '1')
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
        return "<h2 style='color:red; text-align:center; font-family:sans-serif; margin-top:50px;'>Невірний пароль. <a href='/login'>Назад</a></h2>"
    return render_template_string(LOGIN_HTML)

@app.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('login'))

# ==========================================
# 4. WEBSOCKET СИСТЕМА (REAL-TIME)
# ==========================================

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
        return {'success': False, 'error': 'Помилка розрахунку'}

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

    socketio.emit('new_order_alert', new_order, room='admin')
    
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

# --- ADMIN SOCKETS ---

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
    socketio.emit('menu_data', updated_menu)

@socketio.on('admin_delete_menu')
def admin_delete_menu(data):
    db.menu.delete_one({"_id": ObjectId(data['id'])})
    updated_menu = [serialize_doc(i) for i in db.menu.find()]
    socketio.emit('menu_data', updated_menu)

# ==========================================
# 5. ЗАПУСК
# ==========================================
if __name__ == '__main__':
    if db.menu.count_documents({}) == 0:
        db.menu.insert_many([
            {"name": "Капучино", "price": 65, "category": "Кава", "description": "Класичний з молоком (250мл)"},
            {"name": "Еспресо", "price": 40, "category": "Кава", "description": "Міцна арабіка 100% (30мл)"},
            {"name": "Круасан Фісташка", "price": 120, "category": "Випічка", "description": "З ніжним фісташковим кремом (150г)"},
            {"name": "Чизкейк Сан-Себастьян", "price": 145, "category": "Десерти", "description": "Опалений чизкейк без скоринки (180г)"}
        ])
    
    print("🚀 NEXUS POS SERVER ONLINE")
    print("Admin Panel: http://127.0.0.1:5000/admin (Pass: admin123)")
    print("Client Menu (Table 3): http://127.0.0.1:5000/?table=3")
    
    socketio.run(app, debug=False, host='0.0.0.0', port=10000)
