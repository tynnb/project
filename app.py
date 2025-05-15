from flask import Flask, request, jsonify, render_template, redirect, url_for
import sqlite3 # для бд
import bcrypt # хеширование паролей
from flask_login import ( # для аутентификации
    current_user,
    LoginManager,
    UserMixin,
    logout_user,
    login_required,
)
from flask_login import login_user as flask_login_user # функция входа пользователя
import csv
import requests # для запросов к внешним API
from datetime import datetime, date # для даты и времени
import pytz
from pytz import timezone as pytz_timezone # для часовых поясов
import time
import json

SECRET_KEY = "21831912"
TIMEZONEDB_API_KEY = "51430042"

# создание экземпляра приложения
app = Flask(__name__, template_folder="Ppr Final!")
app.secret_key = SECRET_KEY

# инициализация flask-login
login_manager = LoginManager()
login_manager.init_app(app) # связывается с приложением
login_manager.login_view = "login"

#загрузчик пользователя для flask-login
@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id) # возвращает по id

# для передачи current_user в html
@app.context_processor
def inject_user():
    return dict(current_user=current_user)

# основной маршрут приложения
@app.route("/")
def index():
    return render_template("base.html")

# устанавливает соединение с бд
def get_db_connection():
    conn = sqlite3.connect("database.db") # создание соединения
    conn.row_factory = sqlite3.Row # возвращает строки
    return conn

# закрывает соединение с бд
def close_db_connection(conn):
    conn.close()

# инициализация бд, создание таблиц, если они не существуют
def init_db():
    with get_db_connection() as conn:
    # таблица пользователей
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            )
        """
        )
        # таблица поездок
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, 
                title TEXT NOT NULL,
                start_date DATETIME,
                end_date DATETIME,
                timezone TEXT,
                base_currency TEXT DEFAULT 'USD',
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """
        )
        # таблица точек маршрута
        conn.execute(
            """
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
                timezone TEXT,
                utc_arrival DATETIME,
                utc_departure DATETIME,
                FOREIGN KEY (trip_id) REFERENCES trips (id)
            )
        """
        )
        # таблица аэропортов
        conn.execute(
            """
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
        """
        )
        # таблица расходов(?)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trip_costs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_point_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                FOREIGN KEY (trip_point_id) REFERENCES trip_points(id) ON DELETE CASCADE
            )
        """
        )
        # таблица курсов валют
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS exchange_rates (
                base_currency TEXT,
                target_currency TEXT,
                rate REAL,
                date TEXT,
                fetched_at DATE,
                UNIQUE(base_currency, target_currency)
            )
        """
        )
    try:
        conn.execute("SELECT fetched_at FROM exchange_rates LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE exchange_rates ADD COLUMN fetched_at DATE")
        conn.commit()
        import_airports_from_csv("iata-icao.csv")

# класс пользователя для работы с flask-login
class User(UserMixin):
    def __init__(self, id, username, email):
        self.id = id
        self.username = username
        self.email = email
    # получение пользователя из бд по id
    @staticmethod
    def get(user_id):
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        if not user:
            return None
        return User(user["id"], user["username"], user["email"])

# хеширование паролей
def hash_data(data):
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(data.encode("utf-8"), salt)

# проверяет соответствие введенного пароля и хешированного
def check_data(data, hashed_data):
    if isinstance(hashed_data, str):
        hashed_data = hashed_data.encode("utf-8")
    return bcrypt.checkpw(data.encode("utf-8"), hashed_data)

# регистрация нового пользователя в системе
def register_user(username, email, password):
    with get_db_connection() as conn:
        # проверка существует ли такой пользователь
        existing = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            raise ValueError("Пользователь с такой почтой уже существует")
        hashed_password = hash_data(password).decode("utf-8") # хеширование пароля
        # создание нового пользователя
        conn.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
            (username, email, hashed_password),
        )
        conn.commit()

# аутентификация по email и паролю
def authenticate_user(email, password):
    with get_db_connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if user and check_data(password, user["password_hash"]):
        return user
    return None

# обработка регистрации новых пользователей
@app.route("/register", methods=["GET", "POST"]) # GET отображает форму регистрации, POST обрабатывает отправку формы
def register():
    if request.method == "POST":
        #получение данных из формы
        data = request.form
        username = data.get("username")
        email = data.get("email")
        password = data.get("password")
        if not username or not email or not password:
            return jsonify({"error": "Missing data"}), 400
        try:
            register_user(username, email, password) # при успешной регистрации перенаправление на главную страницу
            return redirect(url_for("index"))
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return render_template("register.html") # отображение шаблона с формой при GET запросе

# обрабатывает вход пользователя в систему
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            data = request.form
            email = data.get("email", "").strip()
            password = data.get("password", "").strip()
            if not email or not password:
                return render_template("login.html", error="Все поля обязательны для заполнения")
            user_data = authenticate_user(email, password)
            if not user_data:
                return render_template("login.html", error="Неверная почта или пароль")
            user = User(user_data["id"], user_data["username"], user_data["email"])
            flask_login_user(user, remember=True)
            next_page = request.args.get('next')
            if not next_page or not next_page.startswith('/'):
                next_page = url_for('index')
            return redirect(next_page)
        except Exception as e:
            print(f"Login error: {str(e)}")
            return render_template("login.html", error="Произошла ошибка при входе")
    return render_template("login.html")

# выход из системы
@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("index"))

# полуяение текущих курсов валют и сохранение их в бд (обновление раз в день)
def fetch_and_store_exchange_rates(base_currency="USD"):
    try:
        with get_db_connection() as conn:
            # Проверяем и добавляем колонку fetched_at если её нет
            try:
                conn.execute("SELECT fetched_at FROM exchange_rates LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE exchange_rates ADD COLUMN fetched_at DATE")
                conn.commit()
            today = date.today().isoformat()
            # Добавляем базовую валюту
            conn.execute(
                "INSERT OR IGNORE INTO exchange_rates (base_currency, target_currency, rate, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                (base_currency, base_currency, 1.0, today)
            )
            # Проверяем, обновлялись ли курсы сегодня
            existing = conn.execute(
                "SELECT 1 FROM exchange_rates WHERE base_currency = ? AND fetched_at = ? LIMIT 1",
                (base_currency, today)
            ).fetchone()
            if existing:
                print("Rates already updated today")
                return True
            # Получаем данные от API
            try:
                url = f"https://open.er-api.com/v6/latest/{base_currency}"
                print(f"Fetching rates from: {url}")
                response = requests.get(url, timeout=15)
                if response.status_code != 200:
                    print(f"API request failed with status {response.status_code}")
                    return False
                response.raise_for_status()
                data = response.json()
                if data.get("result") != "success":
                    error_msg = data.get('error-type', 'unknown error')
                    print(f"API returned error: {error_msg}")
                    return False
                rates = data.get("rates", {})
                if not rates:
                    print("No rates received from API")
                    return False
                # Начинаем транзакцию
                conn.execute("BEGIN TRANSACTION")
                try:
                    for target_currency, rate in rates.items():
                        conn.execute(
                            "INSERT OR REPLACE INTO exchange_rates "
                            "(base_currency, target_currency, rate, fetched_at) "
                            "VALUES (?, ?, ?, ?)",
                            (base_currency, target_currency, rate, today)
                        )
                    conn.commit()
                    print(f"Successfully updated {len(rates)} rates")
                    return True
                except sqlite3.Error as e:
                    conn.rollback()
                    print(f"Database error during update: {e}")
                    return False
            except requests.exceptions.RequestException as e:
                print(f"Request to API failed: {e}")
                return False
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False

# перевод из одной валюты в другую
def convert_currency(amount, from_currency, to_currency):
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()
    if from_currency == to_currency:
        return amount
    with get_db_connection() as conn:
    # курс исходной валюты к USD
        from_rate = conn.execute(
            "SELECT rate FROM exchange_rates WHERE base_currency = ? AND target_currency = ?",
            ("USD", from_currency),
        ).fetchone()
        # курс целевой валюты к USD
        to_rate = conn.execute(
            "SELECT rate FROM exchange_rates WHERE base_currency = ? AND target_currency = ?",
            ("USD", to_currency),
        ).fetchone()
    if not from_rate or not to_rate:
        raise ValueError("Не удалось найти курсы валют для конвертации")
    usd_amount = amount / from_rate["rate"]
    final_amount = usd_amount * to_rate["rate"]
    return final_amount

# создание новой поездки
def create_trip(title, start_date, end_date, points, base_currency="USD"):
    user_id = current_user.id
    try:
        if not points:
            raise ValueError("Не указаны точки маршрута")
        # Определение временной зоны поездки
        trip_timezone = get_timezone_by_city(points[0]["location"]) if points else "UTC"
        with get_db_connection() as conn:
            # Создание поездки
            cursor = conn.execute(
                """
                INSERT INTO trips (user_id, title, start_date, end_date, timezone, base_currency)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, title, start_date, end_date, trip_timezone, base_currency),
            )
            trip_id = cursor.lastrowid
            # Обработка точек маршрута
            for point in points:
                # Валидация обязательных полей
                if not all(key in point for key in ["location", "arrival_time", "departure_time"]):
                    raise ValueError("Отсутствуют обязательные данные точки")
                # Валидация ICAO-кодов
                for icao_type in ["departure_icao", "arrival_icao"]:
                    if point.get(icao_type):
                        if not conn.execute("SELECT 1 FROM airports WHERE icao = ?", (point[icao_type],)).fetchone():
                            raise ValueError(f"Неизвестный код аэропорта: {point[icao_type]}")
                # Валидация времени
                arrival = datetime.strptime(point["arrival_time"], '%Y-%m-%d %H:%M')
                departure = datetime.strptime(point["departure_time"], '%Y-%m-%d %H:%M')
                if arrival >= departure:
                    raise ValueError("Время прибытия должно быть раньше времени отъезда")
                # Определение временной зоны точки
                point_timezone = point.get("timezone") or get_timezone_by_city(point["location"]) or trip_timezone
                # Конвертация времени в UTC
                utc_arrival = local_to_utc(point["arrival_time"], point_timezone)
                utc_departure = local_to_utc(point["departure_time"], point_timezone)
                # Сохранение точки
                point_cursor = conn.execute(
                    """
                    INSERT INTO trip_points (
                        trip_id, location, arrival_time, departure_time,
                        timezone, utc_arrival, utc_departure,
                        flight_number, departure_icao, arrival_icao, hotel_name
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trip_id,
                        point["location"],
                        point["arrival_time"],
                        point["departure_time"],
                        point_timezone,
                        utc_arrival,
                        utc_departure,
                        point.get("flight_number", ""),
                        point.get("departure_icao", ""),
                        point.get("arrival_icao", ""),
                        point.get("hotel_name", ""),
                    ),
                )
                trip_point_id = point_cursor.lastrowid
                # Обработка расходов
                if "cost_amount" in point and "cost_currency" in point:
                    if not isinstance(point["cost_amount"], (int, float)):
                        raise ValueError("Сумма расхода должна быть числом")
                    if not is_valid_currency(point["cost_currency"]):
                        raise ValueError(f"Неверная валюта: {point['cost_currency']}")
                    
                    conn.execute(
                        "INSERT INTO trip_costs (trip_point_id, amount, currency) VALUES (?, ?, ?)",
                        (trip_point_id, point["cost_amount"], point["cost_currency"]),
                    )
            conn.commit()
            return trip_id
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except sqlite3.Error as e:
        return jsonify({"error": f"Ошибка базы данных: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"Неизвестная ошибка: {str(e)}"}), 500


@app.route("/create_trip", methods=["GET", "POST"])
@login_required # нужна аутентификация
def create_trip_route():
    user_id = current_user.id
    try:
        with get_db_connection() as conn:
            icao_list = conn.execute("SELECT icao, airport FROM airports LIMIT 100").fetchall() # получение списка аэропортов
            # список доступных валют
            currencies = conn.execute(
                "SELECT DISTINCT target_currency FROM exchange_rates"
            ).fetchall()
        if not currencies:
            fetch_and_store_exchange_rates()
            with get_db_connection() as conn:
                currencies = conn.execute("SELECT DISTINCT target_currency FROM exchange_rates").fetchall()
        if request.method == "GET":
            return render_template("create_trip.html", icao_list=icao_list, available_currencies=[c['target_currency'] for c in currencies])
        # создание поездки
        if request.method == "POST":
            data = request.form
            title = data.get("title")
            start_date = data.get("start_date")
            end_date = data.get("end_date")
            base_currency = data.get("base_currency", "USD")
            departure_icao = data.get("departure_icao")
            arrival_icao = data.get("arrival_icao")
            locations = data.getlist("locations[]")
            arrival_times = data.getlist("arrival_time[]")
            departure_times = data.getlist("departure_time[]")
            flight_numbers = data.getlist("flight_number[]")
            hotel_names = data.getlist("hotel_name[]")
            departure_icao = data.getlist("departure_icao[]")
            arrival_icao = data.getlist("arrival_icao[]")     
            cost_amounts = data.getlist("cost_amount[]")      
            cost_currencies = data.getlist("cost_currency[]")
            if not all([title, start_date, end_date]):
                return jsonify({"error": "Missing required trip data"}), 400
            if not locations:
                return jsonify({"error": "At least one location is required"}), 400
            try:
                create_trip(
                    title,
                    start_date,
                    end_date,
                    [
                        {
                            "location": locations[i],
                            "arrival_time": arrival_times[i],
                            "departure_time": departure_times[i],
                            "flight_number": flight_numbers[i],
                            "hotel_name": hotel_names[i],
                            "departure_icao": departure_icao[i] if i < len(departure_icao) else "",
                            "arrival_icao": arrival_icao[i] if i < len(arrival_icao) else "",
                            "cost_amount": float(cost_amounts[i]) if cost_amounts[i] and cost_amounts[i] else 0,
                            "cost_currency": cost_currencies[i] if i <  len(cost_currencies) and cost_currencies[i] else "USD",
                        }
                        for i in range(len(locations))
                    ],
                    base_currency,
                )
                return redirect(url_for("index"))
            except Exception as e:
                return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# отображает список поездок текущего пользователя
@app.route("/trips", methods=["GET"])
@login_required
def get_trips():
    user_id = current_user.id
    with get_db_connection() as conn:
        trips = conn.execute(
            """
            SELECT * FROM trips WHERE user_id = ?
        """,
            (user_id,),
        ).fetchall()
    trips_list = []
    for trip in trips:
        trips_list.append(
            {
                "id": trip["id"],
                "title": trip["title"],
                "start_date": trip["start_date"],
                "end_date": trip["end_date"],
            }
        )
    return render_template("trips.html", trips_list=trips_list)

# просмотр информации о поездке
@app.route("/trips/<int:trip_id>", methods=["GET"])
@login_required
def get_trip_details(trip_id):
    user_id = current_user.id
    selected_currency = request.args.get("currency", "USD")
    timezone = request.args.get("timezone")
    try:
        with get_db_connection() as conn:
            # Получаем данные о поездке
            trip = conn.execute(
                "SELECT * FROM trips WHERE id = ? AND user_id = ?",
                (trip_id, user_id),
            ).fetchone()
            if not trip:
                return jsonify({"error": "Trip not found"}), 404
            trip_data = {
                "id": trip["id"],
                "user_id": trip["user_id"],
                "title": trip["title"],
                "start_date": trip["start_date"],
                "end_date": trip["end_date"],
                "timezone": trip.get("timezone", "UTC"),
                "base_currency": trip.get("base_currency", "USD")
            }
            # Получаем точки маршрута с сортировкой по времени прибытия
            points = conn.execute(
                """SELECT *, timezone as point_timezone 
                   FROM trip_points 
                   WHERE trip_id = ? 
                   ORDER BY arrival_time""",
                (trip_id,),
            ).fetchall()
            points_details = []
            total_cost = 0
            prev_point = None  # Для хранения данных предыдущей точки
            for idx, point in enumerate(points):
                point_data = {
                    "id": point["id"],
                    "trip_id": point["trip_id"],
                    "location": point["location"],
                    "arrival_time": point["arrival_time"],
                    "departure_time": point["departure_time"],
                    "flight_number": point.get("flight_number", ""),
                    "departure_icao": point.get("departure_icao", ""),
                    "arrival_icao": point.get("arrival_icao", ""),
                    "hotel_name": point.get("hotel_name", ""),
                    "timezone": point.get("point_timezone", trip_data["timezone"]),
                    "utc_arrival": point.get("utc_arrival"),
                    "utc_departure": point.get("utc_departure"),
                    "is_first": idx == 0,  # Первая точка маршрута
                    "is_last": idx == len(points) - 1  # Последняя точка
                }
                # Обработка времени для текущей точки
                display_timezone = timezone or point_data["timezone"]
                arrival_local = point_data["arrival_time"]
                departure_local = point_data["departure_time"]
                if point_data["utc_arrival"]:
                    try:
                        arrival_local = utc_to_local(point_data["utc_arrival"], display_timezone)
                    except Exception as e:
                        print(f"Error converting arrival time: {e}")
                if point_data["utc_departure"]:
                    try:
                        departure_local = utc_to_local(point_data["utc_departure"], display_timezone)
                    except Exception as e:
                        print(f"Error converting departure time: {e}")
                # Добавляем информацию о времени в другом часовом поясе
                if prev_point:
                    # Для текущей точки (прибытие) - время вылета в нашем часовом поясе
                    if prev_point["utc_departure"]:
                        departure_at_current_tz = utc_to_local(
                            prev_point["utc_departure"],
                            point_data["timezone"]
                        )
                    else:
                        departure_at_current_tz = prev_point["departure_time"]
                    # Для предыдущей точки (отправление) - время прибытия в её часовом поясе
                    if point_data["utc_arrival"]:
                        arrival_at_prev_tz = utc_to_local(
                            point_data["utc_arrival"],
                            prev_point["timezone"]
                        )
                    else:
                        arrival_at_prev_tz = point_data["arrival_time"]
                    # Добавляем в предыдущую точку информацию о времени прибытия
                    prev_point["arrival_info"] = {
                        "time_at_destination": arrival_at_prev_tz,
                        "destination_timezone": point_data["timezone"]
                    }
                    # Добавляем в текущую точку информацию о времени отправления
                    point_data["departure_info"] = {
                        "time_at_origin": departure_at_current_tz,
                        "origin_timezone": prev_point["timezone"]
                    }
                # Обработка расходов (оставляем без изменений)
                costs = conn.execute(
                    "SELECT amount, currency FROM trip_costs WHERE trip_point_id = ?",
                    (point_data["id"],),
                ).fetchall()
                point_costs = []
                point_total = 0
                for cost in costs:
                    cost_data = {
                        "amount": cost["amount"],
                        "currency": cost["currency"]
                    }
                    try:
                        converted = convert_currency(
                            cost_data["amount"],
                            cost_data["currency"],
                            selected_currency
                        )
                        point_total += converted
                        point_costs.append({
                            "original_amount": cost_data["amount"],
                            "original_currency": cost_data["currency"],
                            "converted_amount": round(converted, 2),
                            "converted_currency": selected_currency,
                        })
                    except ValueError as e:
                        print(f"Currency conversion error: {e}")
                total_cost += point_total
                # Обновляем данные точки
                point_data.update({
                    "arrival_time": arrival_local,
                    "departure_time": departure_local,
                    "costs": point_costs,
                    "point_total": round(point_total, 2),
                })
                # Если это не первая точка, обновляем предыдущую
                if prev_point:
                    points_details[-1] = prev_point
                points_details.append(point_data)
                prev_point = point_data
            # Получаем список валют и временных зон
            currencies = [row["target_currency"] for row in conn.execute(
                "SELECT DISTINCT target_currency FROM exchange_rates WHERE base_currency = 'USD'"
            ).fetchall()]
            available_timezones = list(set(
                p["timezone"] for p in points_details
                if p["timezone"]
            ))
            return render_template(
                "trip_details.html",
                trip=trip_data,
                points=points_details,
                total_cost=round(total_cost, 2),
                selected_currency=selected_currency,
                available_currencies=currencies,
                available_timezones=available_timezones,
                selected_timezone=timezone,
            )
    except Exception as e:
        print(f"Full error in get_trip_details: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500

# обновляет название поездки, даты начала и окончания
@app.route("/trips/<int:trip_id>", methods=["PUT"])
@login_required
def update_trip(trip_id):
    user_id = current_user.id
    data = request.get_json()
    title = data.get("title")
    start_date = data.get("start_date")
    end_date = data.get("end_date")
    with get_db_connection() as conn:
        trip = conn.execute(
            """
            SELECT * FROM trips WHERE id = ? AND user_id = ?
        """,
            (trip_id, user_id),
        ).fetchone()
        if not trip:
            return jsonify({"error": "Trip not found or access denied"}), 404
        # обновление данных
        conn.execute(
            """
            UPDATE trips
            SET title = ?, start_date = ?, end_date = ?
            WHERE id = ?
            """,
            (title, start_date, end_date, trip_id),
        )
        conn.commit()
    return jsonify({"message": "Trip updated successfully"}), 200

# удаление поездки и всех связанных с ней точек
@app.route("/trips/<int:trip_id>", methods=["DELETE"])
@login_required
def delete_trip(trip_id):
    user_id = current_user.id
    try:
        with get_db_connection() as conn:
            # Проверяем существование поездки
            trip = conn.execute(
                "SELECT 1 FROM trips WHERE id = ? AND user_id = ?",
                (trip_id, user_id)
            ).fetchone()
            if not trip:
                return jsonify({"error": "Trip not found or access denied"}), 404
            # Удаляем поездку 
            conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
            conn.commit()
        return jsonify({"message": "Trip deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# поиск поездки по названию и/или датам
@app.route("/trips/search", methods=["GET"])
@login_required
def search_trips():
    # параметры поиска
    user_id = current_user.id
    title = request.args.get("title")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    query = "SELECT * FROM trips WHERE user_id = ?"
    params = [user_id]
    # условия для фильтров
    if title:
        query += " AND title LIKE ?"
        params.append(f"%{title}%")
    if start_date and end_date:
        query += " AND start_date >= ? AND end_date <= ?"
        params.extend([start_date, end_date])
    elif start_date:
        query += " AND start_date >= ?"
        params.append(start_date)
    elif end_date:
        query += " AND end_date <= ?"
        params.append(end_date)
    try:
        with get_db_connection() as conn:
            trips = conn.execute(query, params).fetchall()
        trips_list = []
        for trip in trips:
            trips_list.append(
                {
                    "id": trip["id"],
                    "title": trip["title"],
                    "start_date": trip["start_date"],
                    "end_date": trip["end_date"],
                }
            )
        return jsonify({"trips": trips_list}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# загружает аэропорты из csv в бд
def import_airports_from_csv(csv_file_path):
    try:
        with get_db_connection() as conn, open(csv_file_path, newline="", encoding="utf-8") as csvfile:
            cursor = conn.cursor()
            reader = csv.DictReader(csvfile)
            for row in reader:
                # вставка данных в базу
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO airports (country_code, region_name, iata, icao, airport, latitude, longitude)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["country_code"],
                        row["region_name"],
                        row["iata"],
                        row["icao"],
                        row["airport"],
                        row["latitude"],
                        row["longitude"],
                    ),
                )
            conn.commit()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# возвращает название временной зоны
def get_timezone_by_city(city_name):
    if not city_name:
        return None
    # Настройки запросов
    headers = {
        'User-Agent': 'YourTravelApp/1.0 (tynbb084@gmail.com)'  # Обязательно для Nominatim
    }
    timeout = 5  # Таймаут для всех запросов
    try:
        # Проверка кэша (если у вас есть таблица city_timezones)
        with get_db_connection() as conn:
            cached = conn.execute(
                "SELECT timezone FROM city_timezones WHERE city = ?",
                (city_name,)
            ).fetchone()
            if cached:
                return cached["timezone"]
        # Запрос к Nominatim (с задержкой для соблюдения лимитов)
        time.sleep(1)  # Ожидание 1 сек между запросами (лимит Nominatim)
        geo_response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": city_name,
                "format": "json",
                "limit": 1,
                "accept-language": "en"  # Для англоязычных названий
            },
            headers=headers,
            timeout=timeout
        )
        # Проверка статуса и данных
        if geo_response.status_code == 429:
            print("Превышен лимит запросов к Nominatim")
            return None
        geo_response.raise_for_status()
        geo_data = geo_response.json()
        if not geo_data:
            print(f"Город не найден: {city_name}")
            return None
        # Получение координат
        lat = geo_data[0].get("lat")
        lon = geo_data[0].get("lon")
        if not lat or not lon:
            print(f"Не удалось получить координаты для {city_name}")
            return None
        # Запрос к TimeZoneDB
        tz_response = requests.get(
            "http://api.timezonedb.com/v2.1/get-time-zone",
            params={
                "key": TIMEZONEDB_API_KEY,
                "format": "json",
                "by": "position",
                "lat": lat,
                "lng": lon,
                "fields": "zoneName"
            },
            timeout=timeout
        )
        tz_data = tz_response.json()
        if tz_data.get("status") != "OK":
            print(f"Ошибка TimeZoneDB: {tz_data.get('message')}")
            return None
        timezone = tz_data["zoneName"]
        # Кэширование результата
        with get_db_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO city_timezones (city, timezone) VALUES (?, ?)",
                (city_name, timezone)
            )
            conn.commit()
        return timezone
    except requests.exceptions.RequestException as e:
        print(f"Ошибка сети: {str(e)}")
    except json.JSONDecodeError as e:
        print(f"Ошибка разбора JSON: {str(e)}")
    except sqlite3.Error as e:
        print(f"Ошибка базы данных: {str(e)}")
    except Exception as e:
        print(f"Неожиданная ошибка: {str(e)}")
        return "UTC"
    return None

# перевод из локального часового пояса в UTC    
def local_to_utc(local_time_str, timezone_name):
    try:
        if not local_time_str or not timezone_name:
            return None
        local_tz = pytz_timezone(timezone_name)
        naive_time = datetime.strptime(local_time_str, '%Y-%m-%d %H:%M')
        local_time = local_tz.localize(naive_time)
        return local_time.astimezone(pytz.UTC).strftime('%Y-%m-%d %H:%M')
    except Exception as e:
        print(f'Time conversion error: {e}')
        return None
    
# переводит время из локального часового пояса в UTC
def utc_to_local(utc_time_str, timezone_name):
    try:
        if not utc_time_str or not timezone_name:
            return None
        utc_time = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M").replace(
            tzinfo=pytz.UTC
        )
        local_tz = pytz_timezone(timezone_name)
        return utc_time.astimezone(local_tz).strftime("%Y-%m-%d %H:%M")
    except Exception as e:
        print(f"Time conversion error: {e}")
        return None

# проверка есть ли заданная валюта в таблице
def is_valid_currency(currency):
    with get_db_connection() as conn:
        result = conn.execute(
            """SELECT 1 FROM exchange_rates WHERE base_currency = ? OR target_currency = ? LIMIT 1""",
            (currency.upper(), currency.upper()),
        ).fetchone()
        return result is not None

# поиск аэропортов по icao коду
@app.route("/api/airports")
def search_airports():
    query = request.args.get("q", "").upper()
    with get_db_connection() as conn:
        airports = conn.execute(
            "SELECT icao, airport FROM airports WHERE icao LIKE ? OR airport LIKE ? LIMIT 10",
            (f"%{query}%", f"%{query}%")
        ).fetchall()
    return jsonify([{"icao": a["icao"], "name": a["airport"]} for a in airports])

@app.template_filter('datetimeformat')
def datetimeformat_filter(value, format='%Y-%m-%d %H:%M'):
    if value is None:
        return ""
    return value.strftime(format)

# запуск приложения
if __name__ == "__main__":
    init_db()
    fetch_and_store_exchange_rates()
    app.run(host="0.0.0.0", port=5000, debug=True)