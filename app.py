from flask import Flask, request, jsonify, render_template, redirect, url_for
import sqlite3 # для бд
import bcrypt # хэширование паролей
from flask_login import current_user, LoginManager, UserMixin, logout_user, login_required
from flask_login import login_user as flask_login_user
import csv
import requests
from datetime import datetime

SECRET_KEY = '21831912'
TIMEZONEDB_API_KEY = '51430042'

# создание приложения
app = Flask(__name__, template_folder='html')
app.secret_key = SECRET_KEY

# инициализация flask-login
login_manager = LoginManager() # для авторизации
login_manager.init_app(app)
login_manager.login_view = 'login' # перенаправляет неавторизованных пользователей

@app.before_first_request
def initialize_exchange_rates():
    fetch_and_store_exchange_rates()
    
# для загрузки пользователя по user_id
@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

# возвращает переменные, автоматически доступные во всех html
@app.context_processor
def inject_user():
    return dict(current_user=current_user)

# маршрут для главной страницы
@app.route('/')
def index():
    return render_template('base.html')

# функция для подключения к бд SQLite, бд встроенная, хранится в файле
def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row #устанавливает формат строк как словарь (обычно кортеж)
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

    # таблица поездок, user_id ссылается на таблицу users
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

    # таблица точек маршрута, trip_id ссылается на таблицу trips
    conn.execute('''
        CREATE TABLE IF NOT EXISTS trip_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            location TEXT NOT NULL,
            arrival_time DATETIME,
            departure_time DATETIME,
            flight_number TEXT,
            departure_icao TEXT,
            arrival_icao TEXT,
            hotel_name TEXT,
            FOREIGN KEY (trip_id) REFERENCES trips (id)
        )
    ''')

    # таблица аэропортов
    conn.execute('''
        CREATE TABLE IF NOT EXISTS airports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country_code TEXT,
            region_name TEXT,
            iata TEXT,
            icao TEXT UNIQUE,
            airport TEXT,
            latitude REAL,
            longitude REAL
        )
    ''')

    # таблица стоимости точек маршрута
    conn.execute('''
        CREATE TABLE IF NOT EXISTS trip_costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_point_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            FOREIGN KEY (trip_point_id) REFERENCES trip_points(id)
        )
    ''')

    # таблица для валют и обменных курсов
    conn.execute('''
        CREATE TABLE IF NOT EXISTS exchange_rates (
            base_currency TEXT,
            target_currency TEXT,
            rate REAL,
            date TEXT,
            fetched_at DATE;
            UNIQUE(base_currency, target_currency)
        )
    ''') # UNIQUE - ограничение чтобы избежать дубликатов курсов между одной и той же парой валют

    conn.commit() # сохранение изменений в бд
    conn.close() # закрытие соединения

# класс пользователя
class User(UserMixin):
    def __init__(self, id, username, email): # сохранение id, username, email
        self.id = id
        self.username = username
        self.email = email

    @staticmethod
    def get(user_id):
        conn = get_db_connection() # получение пользователя по id из бд
        user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()
        if not user:
            return None
        return User(user['id'], user['username'], user['email'])

# хэширование данных
def hash_data(data):
    salt = bcrypt.gensalt() # генерация случайной строки, которая добавляется к данным перед хэшированием
    return bcrypt.hashpw(data.encode('utf-8'), salt) # будет храниться в бд вместо оригинальной

# проверка данных, сравнивает введенные с хэшированными
def check_data(data, hashed_data):
    if isinstance(hashed_data, str):
        hashed_data = hashed_data.encode('utf-8')
    return bcrypt.checkpw(data.encode('utf-8'), hashed_data)

# регистрация пользователя, проверяет уникальность email, добавляет его данные в бд
def register_user(username, email, password):
    conn = get_db_connection()
    existing = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone() # проверка существует ли такой пользователь
    if existing:
        raise ValueError('Пользователь с такой почтой уже существует')
    hashed_password = hash_data(password).decode('utf-8') # хэширование пароля перед сохранением
    conn.execute('INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)', (username, email, hashed_password)) # добавление нового пользователя в бд
    conn.commit()
    conn.close()

# авторизация пользователя
def authenticate_user(email, password):
    conn = get_db_connection()
    # поиск пользователя по почте
    user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone() # запрашивается по email из бд
    conn.close()
    if user and check_data(password, user['password_hash']):
        return user
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

# вход в систему
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.form
        # получение email и пароля, отправленных пользователем
        email = data.get('email')
        password = data.get('password')
        if not email or not password:
            return jsonify({'error': 'Missing data'}), 400
        user_data = authenticate_user(email, password) # аутентификация пользователя
        if user_data:
            user = User(user_data['id'], user_data['username'], user_data['email'])
            flask_login_user(user)
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Неверная почта или пароль')
    return render_template('login.html')

# выход из аккаунта, поддерживает только GET
@app.route('/logout')
def logout():
    logout_user() # удаляет пользователя из сессии, очищает current_user
    return redirect(url_for('index'))

# получает и сохраняет обменные курсы
def fetch_and_store_exchange_rates(base_currency='USD'):
    conn = get_db_connection()
    today = date.today().isformat()
    existing = conn.execute('SELECT 1 FROM exchange_rates WHERE fetched_at = ? LIMIT 1', (today,)).fetchone()
    if existing:
        return
    url = f'https://open.er-api.com/v6/latest/{base_currency}'
    response = requests.get(url)
    data = response.json()
    rates = data.get('rates', {})
    date = data.get('time_last_update_utc', datetime.utcnow().isoformat()) # получение даты обновления курсов, если ее нет используется текущаю в формате iso
    conn = get_db_connection()
    for target_currency, rate in rates.items():
        conn.execute('''
            INSERT OR REPLACE INTO exchange_rates (base_currency, target_currency, rate, fetched_at)
            VALUES (?, ?, ?, ?)
        ''', (base_currency, target_currency, rate, today))
    conn.commit()
    conn.close()

# конвертация валют
def convert_currency(amount, from_currency, to_currency):
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()
    conn = get_db_connection()
    if from_currency == to_currency:
        return amount
    # получение курса валюты from_currency 
    from_rate = conn.execute('SELECT rate FROM exchange_rates WHERE base_currency = ? AND target_currency = ?', ('USD', from_currency)).fetchone()
    to_rate = conn.execute('SELECT rate FROM exchange_rates WHERE base_currency = ? AND target_currency = ?', ('USD', to_currency)).fetchone()
    conn.close()
    if not from_rate or not to_rate:
        raise ValueError('Не удалось найти курсы валют для конвертации')
    usd_amount = amount / from_rate['rate'] # перевод суммы в USD
    final_amount = usd_amount * to_rate['rate'] # из USD в to_currency
    return final_amount

# создание поездки, схраняет точки маршрута и их стоимость, добавляет поездку и точки в бд
def create_trip(title, start_date, end_date, points):
    user_id = current_user.id # получение id авторизованного пользователя
    conn = get_db_connection()
    cursor = conn.execute('''
        INSERT INTO trips (user_id, title, start_date, end_date)
        VALUES (?, ?, ?, ?)
    ''', (user_id, title, start_date, end_date))
    trip_id = cursor.lastrowid # получение id поездки
    fetch_and_store_exchange_rates()
    # добавление точек маршрута в таблицу
    for point in points:
        point_cursor = conn.execute('''
            INSERT INTO trip_points (trip_id, location, arrival_time, departure_time,
flight_number, departure_icao, arrival_icao, hotel_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (trip_id, point['location'], point['arrival_time'], point['departure_time'],
point['flight_number'], point['departure_icao'], point['arrival_icao'], point['hotel_name']))
        trip_point_id = point_cursor.lastrowid
        if 'cost_amount' in point and 'cost_currency' in point:
            conn.execute('''
                INSERT INTO trip_costs (trip_point_id, amount, currency)
                VALUES (?, ?, ?)
            ''', (trip_point_id, point['cost_amount'], point['cost_currency']))
    conn.commit()
    conn.close()

# маршрут для создания поездки, создавать можно если пользователь авторизован
@app.route('/create_trip', methods=['GET', 'POST'])
@login_required
def create_trip_route():
    user_id = current_user.id
    conn = get_db_connection()
    icao_list = conn.execute('SELECT icao, airport FROM airports LIMIT 100').fetchall() # аэропорты в выпадающем списке в форме
    currencies = conn.execute('SELECT DISTINCT target_currency FROM exchange_rates').fetchall()
    conn.close()
    if request.method == 'POST':
        data = request.form
        title = data.get('title')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        departure_icao = data.get('departure_icao')
        arrival_icao = data.get('arrival_icao')
        locations = data.getlist('locations[]')
        arrival_times = data.getlist('arrival_time[]')
        departure_times = data.getlist('departure_time[]')
        flight_numbers = data.getlist('flight_number[]')
        hotel_names = data.getlist('hotel_name[]')
        cost_amounts = data.getlist('cost_amount[]')
        cost_currencies = data.getlist('cost_currency[]')
        if not title or not start_date or not end_date or not locations or not departure_icao or not arrival_icao:
            return jsonify({'error': 'Missing data'}), 400
        try:
            # создание поездки и точек маршрута
            create_trip(title, start_date, end_date, [
                {
                    'location': locations[i],
                    'arrival_time': arrival_times[i],
                    'departure_time': departure_times[i],
                    'flight_number': flight_numbers[i],
                    'hotel_name': hotel_names[i],
                    'departure_icao': departure_icao,
                    'arrival_icao': arrival_icao,
                    'cost_amount': float(cost_amounts[i]) if cost_amounts[i] else 0,
                    'cost_currency': cost_currencies[i] if cost_currencies[i] else 'USD'
                } for i in range(len(locations))
            ])
            return redirect(url_for('index'))
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return render_template('create_trip.html', icao_list=icao_list, availble_currencies=[c['target_currency'] for c in currencies]) # отображение формы создания поездки

# получение списка поездок пользователя
@app.route('/trips', methods=['GET'])
@login_required # защищает от неавторизованного доступа
def get_trips():
    user_id = current_user.id
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
    return render_template('trips.html', trips_list=trips_list)

# получение деталей конкретной поездки
@app.route('/trips/<int:trip_id>', methods=['GET'])
@login_required
def get_trip_details(trip_id):
    user_id = current_user.id
    selected_currency = request.args.get('currency', 'USD')
    conn = get_db_connection()
    # поиск по id поездки и по id пользователя
    trip = conn.execute('''
        SELECT * FROM trips WHERE id = ? AND user_id = ?
    ''', (trip_id, user_id)).fetchone()
    if not trip:
        conn.close()
        return jsonify({'error': 'Trip not found or access denied'}), 404
    fetch_and_store_exchange_rates()
    points = conn.execute('''
        SELECT * FROM trip_points WHERE trip_id = ?
    ''', (trip_id,)).fetchall()
    # рассчет общей стоимости поездки
    total_cost = 0
    points_details = []
    #selected_currency = 'USD'
    for point in points:
        costs = conn.execute('''
            SELECT amount, currency FROM trip_costs WHERE trip_point_id = ?
        ''', (point['id'],)).fetchall()
        point_costs = []
        point_total = 0
        for cost in costs:
            try:
                converted = convert_currency(cost['amount'], cost['currency'], selected_currency)
                point_total += converted
                point_costs.append({
                    'original_amount': cost['amount'],
                    'original_currency': cost['currency'],
                    'converted_amount': round(converted, 2),
                    'converted_currency': selected_currency
                })
            except ValueError as e:
                print(f'Error converting currency: {e}')
        total_cost += point_total
        points_details.append({
            'id': point['id'],
            'location': point['location'],
            'arrival_time': point['arrival_time'],
            'departure_time': point['departure_time'],
            'flight_number': point['flight_number'],
            'departure_icao': point['departure_icao'],
            'arrival_icao': point['arrival_icao'],
            'hotel_name': point['hotel_name'],
            'costs': point_costs,
            'point_total': round(point_total, 2)
        })
    currencies = conn.execute('''
        SELECT DISTINCT target_currency FROM exchange_rates
        WHERE base_currency = 'USD'
    ''').fetchall()
    conn.close()
    return render_template('trip_details.html',
                           trip=trip,
                           points=points_details,
                           total_cost=round(total_cost, 2),
                           selected_currency=selected_currency,
                           availble_currencies=[c['target_currency'] for c in currencies])

# обновление поездки
@app.route('/trips/<int:trip_id>', methods=['PUT'])
@login_required
def update_trip(trip_id):
    user_id = current_user.id
    # извлечение параметров для обновления поездки
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
def delete_trip(trip_id):
    user_id = current_user.id
    conn = get_db_connection()
    trip = conn.execute('''
        SELECT * FROM trips WHERE id = ? AND user_id = ?
    ''', (trip_id, user_id)).fetchone()
    if not trip:
        conn.close()
        return jsonify({'error': 'Trip not found or access denied'}), 404
    conn.execute('DELETE FROM trip_points WHERE trip_id = ?', (trip_id)) # удаление сначала данных, потом самой поездки
    conn.execute('DELETE FROM trips WHERE id = ?', (trip_id))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Trip deleted successfully'}), 200

# поиск поездок
@app.route('/trips/search', methods=['GET'])
@login_required
def search_trips():
    user_id = current_user.id
    title = request.args.get('title') # параметр поиска по названию
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    conn = get_db_connection()
    query = 'SELECT * FROM trips WHERE user_id = ?'
    params = [user_id]
    if title:
        query += ' AND title LIKE ?' # добавление условия поиска по названию
        params.append(f'%{title}%')
    if start_date and end_date:
        query += ' AND start_date >= ? AND end_date <= ?'
        params.extend([start_date, end_date])
    elif start_date:
        query += ' AND start_date >= ?'
        params.append(start_date)
    elif end_date:
        query += ' AND end_date <= ?'
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

# импорт данных об аэропортах из csv в бд
def import_airports_from_csv(csv_file_path):
    conn = get_db_connection()
    cursor = conn.cursor()
    with open(csv_file_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            cursor.execute('''
                INSERT OR IGNORE INTO airports (country_code, region_name, iata, icao, airport, latitude, longitude)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                row['country_code'], row['region_name'], row['iata'],
                row['icao'], row['airport'], row['latitude'], row['longitude']
            ))
    conn.commit()
    conn.close()

def get_timezone_by_city(city_name):
    geo_url = "https://nominatim.openstreetmap.org/search"
    params = {
        'q': city_name,
        'format': 'json',
        'limit': 1
    }
    geo_responce = request.get(geo_url, params=params)
    geo_data = geo_responce.json()
    if not geo_data:
        return None
    lat = geo_data[0]['lat']
    lon = geo_data[0]['lon']
    tz_url = "http://api.timezonedb.com/v2.1/get-time-zone"
    tz_params = {
        'key': TIMEZONEDB_API_KEY,
        'format': 'json',
        'by': 'position',
        'lat': lat,
        'lng': lon
    }
    tz_responce = request.get(tz_url, params=tz_params)
    tz_data = tz_responce.json()
    if tz_data['status'] == 'OK':
        return tz_data['zoneName']
    else:
        return None

# запуск приложения
if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)