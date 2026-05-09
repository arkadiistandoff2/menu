from flask import Flask, render_template, request, redirect, session, url_for
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'super_secret_cafe_key'
socketio = SocketIO(app)

# Наша "база даних" в оперативній пам'яті
data = {
    "tables_count": 5,
    "admin_pass": "1111",
    "menu": [
        {"id": 1, "category": "Піца", "name": "Маргарита", "price": 180},
        {"id": 2, "category": "Піца", "name": "Пепероні", "price": 220},
        {"id": 3, "category": "Напої", "name": "Кава Американо", "price": 45},
        {"id": 4, "category": "Десерти", "name": "Чизкейк", "price": 95}
    ],
    "orders": [], 
    "reviews": [] 
}

@app.route('/')
def index():
    return redirect('/1')

@app.route('/<int:table_id>')
def client_page(table_id):
    if table_id > data["tables_count"]:
        return "Бро, такого столика немає. Звернись до адміністратора.", 404
    return render_template('client.html', table_id=table_id, menu=data["menu"], orders=data["orders"], reviews=data["reviews"])

# --- АДМІНКА ---
@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == data['admin_pass']:
            session['is_admin'] = True
            return redirect(url_for('admin_dashboard'))
        return render_template('admin_login.html', error="Невірний пароль!")
    
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
    return render_template('admin_login.html')

@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    return render_template('admin.html', tables_count=data["tables_count"], orders=data["orders"], reviews=data["reviews"])

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin_login'))

@app.route('/admin/settings', methods=['POST'])
def save_settings():
    if session.get('is_admin'):
        data["tables_count"] = int(request.form.get('count', 5))
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add_dish', methods=['POST'])
def add_dish():
    if session.get('is_admin'):
        cat = request.form.get('category')
        name = request.form.get('name')
        price = request.form.get('price')
        if cat and name and price:
            data["menu"].append({"id": len(data["menu"])+1, "category": cat, "name": name, "price": int(price)})
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/del_review/<int:rev_id>')
def del_review(rev_id):
    if session.get('is_admin') and 0 <= rev_id < len(data["reviews"]):
        data["reviews"].pop(rev_id)
    return redirect(url_for('admin_dashboard'))

# --- SOCKET EVENTS (Реалтайм) ---
@socketio.on('new_order')
def handle_order(payload):
    order_id = str(len(data["orders"]) + 1)
    data["orders"].append({
        "id": order_id, "table": payload['table'], "item_name": payload['item'], "status": "Готується", "comment": ""
    })
    emit('order_received', broadcast=True)

@socketio.on('update_status')
def handle_status(payload):
    for o in data["orders"]:
        if o["id"] == payload["id"]:
            o["status"] = payload["status"]
            o["comment"] = payload.get("comment", "")
            emit('status_updated', {"table": o["table"]}, broadcast=True)

@socketio.on('mass_action')
def handle_mass(payload):
    for o in data["orders"]:
        if o["table"] == payload["table"] and o["status"] == "Готується":
            o["status"] = payload["status"]
            o["comment"] = payload.get("comment", "")
    emit('status_updated', {"table": payload["table"]}, broadcast=True)

@socketio.on('new_review')
def handle_review(payload):
    data["reviews"].append(payload)

if __name__ == '__main__':
    socketio.run(app, debug=True)
