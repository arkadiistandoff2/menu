import os
import sqlite3
import logging
from flask import Flask, render_template
from flask_socketio import SocketIO, emit

# Налаштування логування для відслідковування сокетів
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
app.config['SECRET_KEY'] = 'super_cafe_secure_key_2026'
socketio = SocketIO(app, cors_allowed_origins="*")

DB_FILE = 'cafe.db'

def get_db_connection():
    """Створення підключення до бази даних SQLite"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Ініціалізація таблиць бази даних, якщо вони не існують"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Таблиця меню
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS menu (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            category TEXT NOT NULL
        )
    ''')
    
    # Таблиця замовлень
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_number TEXT NOT NULL,
            total REAL NOT NULL,
            status TEXT NOT NULL,
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблиця зв'язку замовлення та страв
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            price REAL NOT NULL,
            count INTEGER NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders (id)
        )
    ''')
    
    # Заповнення дефолтного меню, якщо база порожня
    cursor.execute("SELECT COUNT(*) FROM menu")
    if cursor.fetchone()[0] == 0:
        default_menu = [
            ("Еспресо", 40.0, "Кава"),
            ("Капучино", 55.0, "Кава"),
            ("Лате Макіато", 65.0, "Кава"),
            ("Круасан класичний", 45.0, "Випічка"),
            ("Круасан з шоколадом", 60.0, "Випічка"),
            ("Чізкейк Нью-Йорк", 75.0, "Десерти"),
            ("Чай Зелений Сенча", 45.0, "Напої"),
            ("Лимонад Класичний", 50.0, "Напої")
        ]
        cursor.executemany("INSERT INTO menu (name, price, category) VALUES (?, ?, ?)", default_menu)
        conn.commit()
        logging.info("Дефолтне меню успішно завантажено в базу даних.")
        
    conn.close()

# Виклик ініціалізації БД при старті
init_db()

def fetch_full_menu():
    """Отримання всього меню з бази даних"""
    conn = get_db_connection()
    items = conn.execute("SELECT * FROM menu ORDER BY category, name").fetchall()
    conn.close()
    return [dict(item) for item in items]

def fetch_active_orders():
    """Отримання всіх активних та архівованих замовлень разом зі стравами"""
    conn = get_db_connection()
    orders_rows = conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    
    full_orders = []
    for o_row in orders_rows:
        order_dict = dict(o_row)
        items_rows = conn.execute(
            "SELECT item_name, price, count FROM order_items WHERE order_id = ?", 
            (order_dict['id'],)
        ).fetchall()
        order_dict['items'] = [dict(i) for i in items_rows]
        full_orders.append(order_dict)
        
    conn.close()
    return full_orders

def calculate_live_stats():
    """Просунутий розрахунок статистики закладу безпосередньо з SQL"""
    conn = get_db_connection()
    
    total_revenue = conn.execute("SELECT SUM(total) FROM orders WHERE status = 'Завершено'").fetchone()[0] or 0.0
    active_count = conn.execute("SELECT COUNT(*) FROM orders WHERE status != 'Завершено'").fetchone()[0] or 0
    total_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] or 0
    
    top_item_row = conn.execute('''
        SELECT item_name, SUM(count) as total_qty 
        FROM order_items 
        GROUP BY item_name 
        ORDER BY total_qty DESC LIMIT 1
    ''').fetchone()
    
    top_item = top_item_row['item_name'] if top_item_row else "Немає"
    
    conn.close()
    return {
        "revenue": round(total_revenue, 2),
        "active_orders": active_count,
        "total_orders": total_orders,
        "top_item": top_item
    }

@app.route('/')
def index():
    """Рендеринг головної сторінки"""
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    """Стрімінг усіх початкових даних клієнту при конекті"""
    logging.info("Клієнт підключився до системи.")
    emit('init_store', {
        'menu': fetch_full_menu(),
        'orders': fetch_active_orders(),
        'stats': calculate_live_stats()
    })

@socketio.on('add_menu_item')
def handle_add_menu_item(data):
    """Додавання нової позиції в меню через веб-сокети"""
    try:
        name = data.get('name', '').strip()
        price = float(data.get('price', 0))
        category = data.get('category', 'Інше').strip()
        
        if not name or price <= 0:
            emit('error_notification', {'message': 'Некоректні дані назви або ціни страв'})
            return
            
        conn = get_db_connection()
        conn.execute("INSERT INTO menu (name, price, category) VALUES (?, ?, ?)", (name, price, category))
        conn.commit()
        conn.close()
        
        logging.info(f"Додано нову страву: {name} за {price}грн")
        emit('menu_updated', fetch_full_menu(), broadcast=True)
    except Exception as e:
        logging.error(f"Помилка додавання страви: {e}")
        emit('error_notification', {'message': 'Сталася внутрішня помилка сервера.'})

@socketio.on('delete_menu_item')
def handle_delete_menu_item(data):
    """Видалення позиції з меню"""
    try:
        item_id = int(data.get('id', 0))
        conn = get_db_connection()
        conn.execute("DELETE FROM menu WHERE id = ?", (item_id,))
        conn.commit()
        conn.close()
        
        logging.info(f"Видалено страву з ID: {item_id}")
        emit('menu_updated', fetch_full_menu(), broadcast=True)
    except Exception as e:
        logging.error(f"Помилка видалення страви: {e}")

@socketio.on('create_order')
def handle_create_order(data):
    """Створення повноцінного чеку замовлення з транзакцією в БД"""
    try:
        table_num = data.get('table', 'На винос').strip()
        comment = data.get('comment', '').strip()
        cart_items = data.get('items', [])
        
        if not cart_items:
            emit('error_notification', {'message': 'Неможливо створити порожнє замовлення!'})
            return
            
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Обчислення загальної вартості на бекенді задля безпеки
        total_price = 0.0
        parsed_items = []
        
        for cart_item in cart_items:
            menu_id = int(cart_item['id'])
            count = int(cart_item['count'])
            
            db_item = cursor.execute("SELECT name, price FROM menu WHERE id = ?", (menu_id,)).fetchone()
            if db_item:
                cost = db_item['price'] * count
                total_price += cost
                parsed_items.append({
                    'name': db_item['name'],
                    'price': db_item['price'],
                    'count': count
                })
                
        # Вставка основного замовлення
        cursor.execute(
            "INSERT INTO orders (table_number, total, status, comment) VALUES (?, ?, 'Нове', ?)",
            (table_num, total_price, comment)
        )
        order_id = cursor.lastrowid
        
        # Вставка кожної позиції чеку
        for p_item in parsed_items:
            cursor.execute(
                "INSERT INTO order_items (order_id, item_name, price, count) VALUES (?, ?, ?, ?)",
                (order_id, p_item['name'], p_item['price'], p_item['count'])
            )
            
        conn.commit()
        conn.close()
        
        logging.info(f"Створено нове замовлення #{order_id} для столу {table_num} на суму {total_price}грн")
        
        # Моментальний бродкаст оновлень усім підключеним клієнтам
        emit('orders_updated', fetch_active_orders(), broadcast=True)
        emit('stats_updated', calculate_live_stats(), broadcast=True)
        emit('order_success_notification', {'id': order_id})
        
    except Exception as e:
        logging.error(f"Помилка створення замовлення: {e}")
        emit('error_notification', {'message': 'Помилка збереження транзакції замовлення.'})

@socketio.on('change_order_status')
def handle_change_order_status(data):
    """Зміна поточного технологічного статусу замовлення"""
    try:
        order_id = int(data.get('id', 0))
        new_status = data.get('status', '').strip()
        
        valid_statuses = ['Нове', 'Готується', 'Готово', 'Завершено']
        if new_status not in valid_statuses:
            return
            
        conn = get_db_connection()
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        conn.commit()
        conn.close()
        
        logging.info(f"Замовлення #{order_id} переведено в статус: {new_status}")
        
        emit('orders_updated', fetch_active_orders(), broadcast=True)
        emit('stats_updated', calculate_live_stats(), broadcast=True)
    except Exception as e:
        logging.error(f"Помилка зміни статусу замовлення: {e}")

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
