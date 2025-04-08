from flask import Flask, request, jsonify, render_template, redirect, url_for
import sqlite3
import bcrypt
import jwt
from datetime import datetime, timedelta
from functools import wraps
from flask_login import current_user

SECRET_KEY = '21831912'

# создание приложения
app = Flask(__name__, template_folder='html')

@app.context_processor
def inject_user():
    return dict(current_user=current_user)

# маршрут для главной страницы
@app.route('/')
def index():
    return render_template('base.html')

# декоратор для проверки авторизации
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'Authorization token is missing'}), 401
        user_id = verify_token(token)
        if not user_id:
            return jsonify({'error': 'Invalid or expired token'}), 401
        return f(user_id, *args, **kwargs)
    return decorated_function

# функция для подключения к бд SQLite, бд встроенная, хранится в файле
def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row #устанавливает формат строк как словарь
    return conn

# закрытие соединения с бд
def close_db_connection(conn):
    conn.close()

# иницифлизация бд, создает таблицы, если они еще не существуют
def init_db():
    conn = get_db_connection()
    # таблица пользователей
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL
        )
    ''')
    # таблица поездок
    conn.execute('''
        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            start_date DATETIME,
            end_date DATETIME,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    # таблица точек маршрута
    conn.execute('''
        CREATE TABLE IF NOT EXISTS trip_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            location TEXT NOT NULL,
            arrival_time DATETIME,
            departure_time DATETIME,
            flight_number TEXT,
            hotel_name TEXT,
            FOREIGN KEY (trip_id) REFERENCES trips (id)
        )
    ''')
    conn.commit() # сохранение изменений в бд
    conn.close() # закрытие соединения

# хэширование данных
def hash_data(data):
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(data.encode('utf-8'), salt)

# проверка данных, сравнивает введенные с хэшированными
def check_data(data, hashed_data):
    return bcrypt.checkpw(data.encode('utf-8'), hashed_data)

# регистрация пользователя, добавляет его данные в бд
def register_user(username, email, password):
    conn = get_db_connection()
    hased_password = hash_data(password) # хэширование пароля перед сохранением
    conn.execute('INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)', (username, email, hased_password))
    conn.commit()
    conn.close()

# авторизация пользователя
def login_user(email, password):
    conn = get_db_connection()
    # поиск пользователя по почте
    user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
    conn.close()
    if user and check_data(password, user['password_hash']):
        return user
    return None

#функция для создания токена авторизации
def create_token(user_id):
    payload = {
        'user_id': user_id, # id пользователя, будет храниться в токене
        'exp': datetime.utcnow() + timedelta(hours=1)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm='HS256') # кодирование токена

# проверка токена
def verify_token(token):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        return payload['user_id']
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

# маршрут для регистрации пользователя
@app.route('/register', methods=['GET', 'POST']) # GET -отображение формы, POST - обработка данных формы
def register():
    if request.method == 'POST':
        data = request.form
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')
        if not username or not email or not password:
            return jsonify({'error': 'Missing data'}), 400
        try:
            register_user(username, email, password) # регистрация
            return redirect(url_for('index')) # перенаправление на главную страницу
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return render_template('register.html') # отображение формы регистрации

# маршрут для авторизации
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.form
        email = data.get('email')
        password = data.get('password')
        if not email or not password:
            return jsonify({'error': 'Missind data'}), 400
        user = login_user(email, password)
        if user:
            token = create_token(user['id'])
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Неверная почта или пароль')
    return render_template('login.html')

# создание поездки, добавляет поездку и точки в бд
def create_trip(user_id, title, start_date, end_date, points):
    conn = get_db_connection()
    cursor = conn.execute('''
        INSERT INTO trips (user_id, title, start_date, end_date)
        VALUES (?, ?, ?, ?)
    ''', (user_id, title, start_date, end_date))
    trip_id = cursor.lastrowid # получение id поездки
    # добавление точек маршрута в таблицу
    for point in points:
        conn.execute('''
            INSERT INTO trip_points (trip_id, location, arrival_time, departure_time,
flight_number, hotel_name)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (trip_id, point['location'], point['arrival_time'], point['departure_time'],
point['flight_number'], point['hotel_name']))
    conn.commit()
    conn.close()

# маршрут для создания поездки, создавать можно если пользователь авторизован
@app.route('/create_trip', methods=['GET', 'POST'])
@login_required
def create_trip_route(user_id):
    if request.method == 'POST':
        data = request.form
        title = data.get('title')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        locations = data.getlist('locations[]')
        arrival_times = data.getlist('arrival_time[]')
        departure_times = data.getlist('departure_time[]')
        flight_numbers = data.getlist('flight_number[]')
        hotel_names = data.getlist('hotel_name[]')
        if not title or not start_date or not end_date or not locations:
            return jsonify({'error': 'Missing data'}), 400
        try:
            # создание поездки и точек маршрута
            create_trip(user_id, title, start_date, end_date, [
                {
                    'location': locations[i],
                    'arrival_time': arrival_times[i],
                    'departure_time': departure_times[i],
                    'flight_number': flight_numbers[i],
                    'hotel_name': hotel_names[i]
                } for i in range(len(locations))
            ])
            return redirect(url_for('index'))
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return render_template('create_trip.html') # отображение формы создания поездки

# получение списка поездок пользователя
@app.route('/trips', methods=['GET'])
@login_required
def get_trips(user_id):
    conn = get_db_connection()
    trips = conn.execute('''
        SELECT * FROM trips WHERE user_id = ?
    ''', (user_id,)).fetchall()
    conn.close()
    trips_list = []
    for trip in trips:
        trips_list.append({
            'id': trip['id'],
            'title': trip['title'],
            'start_date': trip['start_date'],
            'end_date': trip['end_date']
        })
    #return jsonify({'trips': trips_list}), 200
    return render_template('trips.html', trips=trips)

# получение деталей конкретной поездки
@app.route('/trips/<int:trip_id>', methods=['GET'])
@login_required
def get_trip_details(user_id, trip_id):
    conn = get_db_connection()
    # поиск по id поездки и по id пользователя
    trip = conn.execute('''
        SELECT * FROM trips WHERE id = ? AND user_id = ?
    ''', (trip_id, user_id)).fetchone()
    if not trip:
        conn.close()
        return jsonify({'error': 'Trip not found or access denied'}), 404
    points = conn.execute('''
        SELECT * FROM trip_points WHERE trip_id = ?
    ''', (trip_id)).fetchall()
    conn.close()
    trip_details = {
        'id': trip['id'],
        'title': trip['title'],
        'start_date': trip['start_date'],
        'end_date': trip['end_date'],
        'points': []
    }
    for point in points:
        trip_details['points'].append({
            'location': point['location'],
            'arrival_time': point['arrival_time'],
            'departure_time': point['departure_time'],
            'flight_number': point['flight_number'],
            'hotel_name': point['hotel_name']
        })
    #return jsonify(trip_details), 200
    return render_template('trip_details.html', trip=trip, points=points)

# обновление поездки
@app.route('/trips/<int:trip_id>', methods=['PUT'])
@login_required
def update_trip(user_id, trip_id):
    data = request.get_json()
    title = data.get('title')
    start_date = data.get('start_date')
    end_date = data.get('end_date')
    conn = get_db_connection()
    trip = conn.execute('''
        SELECT * FROM trips WHERE id = ? AND user_id = ?
    ''', (trip_id, user_id)).fetchone()
    if not trip:
        conn.close()
        return jsonify({'error': 'Trip not found or access denied'}), 404
    conn.execute('''
        UPDATE trips
        SET title = ?, start_date = ?, end_date = ?
        WHERE id = ?
        ''', (title, start_date, end_date, trip_id))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Trip updated successfully'}), 200

# удаление поездки
@app.route('/trips/<int:trip_id>', methods=['DELETE'])
@login_required
def delete_trip(user_id, trip_id):
    conn = get_db_connection()
    trip = conn.execute('''
        SELECT * FROM trips WHERE id = ? AND user_id = ?
    ''', (trip_id, user_id)).fetchone()
    if not trip:
        conn.close()
        return jsonify({'error': 'Trip not found or access denied'}), 404
    conn.execute('DELETE FROM trip_points WHERE trip_id = ?', (trip_id))
    conn.execute('DELETE FROM trips WHERE id = ?', (trip_id))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Trip deleted successfully'}), 200

# поиск поездок
@app.route('/trips/search', methods=['GET'])
@login_required
def search_trips(user_id):
    title = request.args.get('title') # параметр поиска по названию
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    conn = get_db_connection()
    query = 'SELECT * FROM trips WHERE user_id = ?'
    params = [user_id]
    if title:
        query += 'AND title LIKE ?' # добавление условия поиска по названию
        params.append(f'%{title}%')
    if start_date and end_date:
        query += 'AND start_date >= ? AND end_date <= ?'
        params.extend([start_date, end_date])
    elif start_date:
        query += 'AND start_date >= ?'
        params.append(start_date)
    elif end_date:
        query += 'AND end_date <= ?'
        params.append(end_date)
    trips = conn.execute(query, params).fetchall() # выполнение запроса
    conn.close()
    trips_list = []
    for trip in trips:
        trips_list.append({
            'id': trip['id'],
            'title': trip['title'],
            'start_date': trip['start_date'],
            'end_date': trip['end_date']
        })
    return jsonify({'trips': trips_list}), 200

# запуск приложения
if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)